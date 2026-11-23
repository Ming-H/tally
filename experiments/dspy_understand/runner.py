"""DSPy 优化 understand 节点：把手写查询理解提示词变成可编译的 Signature。

设计（详见同目录 README.md）：
· 注入 seam —— tally.graph 的 understand 节点经 llm.chat_json(system, query)
  调用 LLM，system 以「你是财报问答系统的查询理解器」开头。实验对该调用
  打外科补丁：候选理解器接管 understand，其余 LLM 调用（grade/agent/…）
  原样放行 → 基线与优化共享完全相同的下游，分数差异归因于理解质量。
· 度量 —— 复用 evalx.judge：候选 understanding 注入管线跑 llm 模式全流程，
  按逐题 ok 给分（ok=1.0 / 仅关键词命中=0.5 / 拒答题按拒答对错 1 或 0）。
· 归因隔离 —— TALLY_MEMORY=off（台账不污染跨题），TREND_ANALYSIS=off
  （trend 骨架直出，砍叙述层 LLM 方差）；下游 LLM 噪声基线/优化同分布。

用法（仓库根目录）：
  python -m experiments.dspy_understand.runner            # 冒烟档（SMOKE）
  python -m experiments.dspy_understand.runner --full    # 过夜全量 MIPROv2
  python -m experiments.dspy_understand.runner --eval-compiled  # 只评估已编译产物
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# ── 环境开关须在建图前落位（read at import / build 时）─────────────
os.environ.setdefault("TALLY_MEMORY", "off")            # eval 同口径：隔离台账
os.environ.setdefault("TALLY_TREND_ANALYSIS", "off")    # 骨架直出：去叙述层方差

# tally.llm import 即装载 .env（密钥只在那里）
from tally import llm  # noqa: E402
from tally.evalx import judge, load_dataset, summarize  # noqa: E402

# new_state 延迟导入（run_one 内）：与 evalx.run_eval 同习，避免模块级环

UNDERSTAND_PREFIX = "你是财报问答系统的查询理解器"
REPORT_DIR = Path(__file__).resolve().parent

# 分层抽样：覆盖 metric/compare/trend/陷阱/简称泛化（multiturn 需 L0 继承链，
# 归因复杂不入实验；innolight 与 default 语料重叠度高，留作扩展集）
PICKS: list[tuple[str, int | None]] = [
    ("default", None),    # 10 题全取（单跳 + 2 陷阱）
    ("agent", 4),         # 多跳对比 + 陷阱
    ("quarters", 4),      # trend/差分 + 期间守卫
    ("maotai", 2),        # 简称泛化（茅台→贵州茅台）
]
SMOKE_PICKS: list[tuple[str, int | None]] = [("default", 3), ("agent", 1)]
SMOKE = "--full" not in sys.argv


# ── DSPy Signature：忠实镜像 graph.py understand 的手写提示词 ─────────


def build_signature():
    import dspy

    class Understand(dspy.Signature):
        """你是财报问答系统的查询理解器。从用户问题抽取结构化字段。
        注意「XX变动的原因」是 metric 不是 summary，「最近几个季度的趋势」
        是 trend 不是 summary。"""

        question: str = dspy.InputField(desc="用户问题")
        intent: str = dspy.OutputField(
            desc="五选一枚举：metric=问具体数字/指标/事实/原因/归因；"
            "trend=跨期趋势/多期对比（最近几个季度/近几年的指标变化，"
            "问投资建议也归 trend）；summary=要整体概览/总结全篇；"
            "compare=对比多家公司；other=闲聊或超纲")
        companies: str = dspy.OutputField(
            desc="问题中的公司名，逗号分隔，原样摘取；没有则空字符串")
        period: str = dspy.OutputField(
            desc="财报日历枚举：一季度/一季报/Q1→Q1，半年度/中报/二季度→H1，"
            "三季→Q3，年度/年报/四季度→FY；仅提年份（如「2025 年营收」）"
            "也算 FY；未提及期间则空字符串")
        reason: str = dspy.OutputField(desc="一句话判断依据")

    return dspy.Predict(Understand)


def candidate_from_pred(pred) -> dict:
    """DSPy 预测 → understand 节点期望的 chat_json 返回形态。"""
    companies = [c.strip() for c in str(pred.companies)
                 .replace("，", ",").replace("、", ",").split(",")
                 if c.strip() and c.strip() != "无"][:4]
    return {"intent": str(pred.intent).strip(),
            "companies": companies,
            "period": str(pred.period).strip(),
            "reason": str(pred.reason).strip()[:80]}


# ── 外科补丁：只接管 understand 调用，其余放行 ────────────────────────

_ORIG_CHAT_JSON = llm.chat_json
_CURRENT: dict = {"fn": None}


def _patched_chat_json(system: str, user: str, max_tokens: int = 512) -> dict:
    if system.startswith(UNDERSTAND_PREFIX) and _CURRENT["fn"] is not None:
        out = _CURRENT["fn"](user)
        if out is not None:
            return out
    return _ORIG_CHAT_JSON(system, user, max_tokens)


def install_patch() -> None:
    llm.chat_json = _patched_chat_json


def with_candidate(fn, invoke):
    """补丁作用域内执行 invoke（结束后恢复直通，杜绝串题）。"""
    _CURRENT["fn"] = fn
    try:
        return invoke()
    finally:
        _CURRENT["fn"] = None


# ── 数据集与度量 ──────────────────────────────────────────────────────


def load_cases(picks: list[tuple[str, int | None]]) -> list[dict]:
    cases: list[dict] = []
    for name, n in picks:
        data = load_dataset(name)
        for c in (data if n is None else data[:n]):
            cases.append({**c, "_dataset": name})
    return cases


def run_one(app, case: dict, understand_fn) -> dict:
    """understand_fn(query)->dict|None（None=直通原始提示词）→ 管线终态。"""
    from tally.schemas import new_state

    def _invoke():
        return app.invoke(new_state(case["q"], "llm"))

    if understand_fn is None:
        return _invoke()
    return with_candidate(understand_fn, _invoke)


def score(case: dict, st: dict) -> float:
    row = judge(case, st)
    if case.get("expect_refused"):
        return 1.0 if row["refused"] else 0.0
    if row["ok"]:
        return 1.0
    return 0.5 if row["kw_hit"] else 0.0


def evaluate(app, cases, understand_fn=None, tag=""):
    """逐题跑分（基线传 understand_fn=None）。返回 rows/states/scores。"""
    rows, states, scores = [], [], []
    for c in cases:
        t0 = time.perf_counter()
        st = run_one(app, c, understand_fn)
        row = judge(c, st)
        rows.append(row)
        states.append(st)
        scores.append(score(c, st))
        print(f"  [{tag}] {'✓' if row['ok'] else '✗'} {c['q'][:34]:<36} "
              f"score={scores[-1]}  ({time.perf_counter() - t0:.1f}s)", flush=True)
    return rows, states, scores


# ── 主流程 ────────────────────────────────────────────────────────────


def configure_lm():
    """DSPy 的 LM 复用 Tally 的 .env 通道（OpenAI 兼容端点）。"""
    import dspy
    base = os.environ.get("TALLY_LLM_BASE_URL", llm.DEFAULT_BASE_URL)
    model = os.environ.get("TALLY_LLM_MODEL", llm.DEFAULT_MODEL)
    if "localhost" in base or "127.0.0.1" in base:
        sys.exit("本实验需远端 OpenAI 兼容端点（.env 配 TALLY_LLM_*；"
                 "优化器反思不适合本地 0.8b 档）")
    # model_type="chat" 必须显式：dspy 3.5 默认走 OpenAI Responses API，
    # 非 OpenAI 的兼容端点（如智谱）未实现 /responses → 404
    lm = dspy.LM(f"openai/{model}", model_type="chat", api_base=base,
                 api_key=os.environ.get("TALLY_LLM_API_KEY"),
                 temperature=0, max_tokens=1024)
    dspy.configure(lm=lm)
    return lm


def to_examples(cases):
    import dspy
    return [dspy.Example(question=c["q"]).with_inputs("question")
            for c in cases]


def main() -> None:
    import dspy

    compiled_path = REPORT_DIR / "compiled" / "understand.json"
    if "--eval-compiled" in sys.argv and not compiled_path.exists():
        sys.exit("未找到 compiled/ 产物，先跑 --full")
    if not llm.llm_enabled():
        sys.exit("远端端点需要 TALLY_LLM_API_KEY（只放 .env）")
    # eval-compiled 档同样要 LM：program(question=…) 推理时调用
    configure_lm()

    from tally.graph import build_graph
    from tally.index import Corpus

    db = Path(os.environ.get("TALLY_DB", "tally.db"))
    corpus = Corpus.load(db)
    app = build_graph(corpus)
    install_patch()

    picks = SMOKE_PICKS if SMOKE else PICKS
    cases = load_cases(picks)
    print(f"实验配置：{'冒烟' if SMOKE else '全量'} · {len(cases)} 题 · "
          f"model={os.environ.get('TALLY_LLM_MODEL', llm.DEFAULT_MODEL)}", flush=True)

    # 1) 基线：手写提示词直通（补丁装着但 fn=None，语义等同未打补丁）
    print("\n[1/3] 基线评测（手写提示词）", flush=True)
    b_rows, b_states, b_scores = evaluate(app, cases, None, "base")
    b_mean = sum(b_scores) / len(b_scores)

    if "--eval-compiled" in sys.argv:
        program = build_signature()
        program.load(str(compiled_path))
        print("\n[3/3] 已编译产物评测", flush=True)
        o_rows, o_states, o_scores = evaluate(
            app, cases, lambda q: candidate_from_pred(program(question=q)),
            "opt")
    else:
        program = build_signature()

        # 2) MIPROv2 编译（instruction-only：demos 关零——评估调用太贵，
        #    预算全部留给指令候选；冒烟档再压到最小可行）
        print("\n[2/3] MIPROv2 编译中…", flush=True)
        metric_calls = {"n": 0}

        def metric(example, pred, trace=None):
            # with_inputs("question") 后输入字段名是 question（不是 q）
            case = next(c for c in cases if c["q"] == example.question)
            metric_calls["n"] += 1
            cand = candidate_from_pred(pred)
            st = run_one(app, case, lambda q, _c=cand: _c)
            s = score(case, st)
            print(f"    [metric#{metric_calls['n']}] {case['q'][:30]:<32} "
                  f"→ {s}", flush=True)
            return s

        # auto 默认非 None（dspy 3.5）——手动预算必须显式 auto=None；
        # num_threads=1 强制串行：Tally 的 SQLite 连接绑定主线程，
        # 多线程 Evaluate 会炸「SQLite objects created in a thread」
        if SMOKE:
            opt = dspy.MIPROv2(metric=metric, auto=None, num_candidates=2,
                               init_temperature=0.3, num_threads=1)
            program = opt.compile(program, trainset=to_examples(cases),
                                  minibatch_size=2, num_trials=2,
                                  max_bootstrapped_demos=0,
                                  max_labeled_demos=0)
        else:
            opt = dspy.MIPROv2(metric=metric, auto="medium", num_threads=1)
            program = opt.compile(program, trainset=to_examples(cases),
                                  minibatch_size=8,
                                  max_bootstrapped_demos=0,
                                  max_labeled_demos=0)
        compiled_path.parent.mkdir(parents=True, exist_ok=True)
        program.save(str(compiled_path))

        # 3) 优化后全量评测
        print("\n[3/3] 优化后评测（编译产物）", flush=True)
        o_rows, o_states, o_scores = evaluate(
            app, cases, lambda q: candidate_from_pred(program(question=q)),
            "opt")

    o_mean = sum(o_scores) / len(o_scores)

    # 汇总（复用 evalx.summarize 的口径，便于与 CI 指标对照）
    b_sum = summarize(b_rows, b_states, "llm", "dspy-exp", [])
    o_sum = summarize(o_rows, o_states, "llm", "dspy-exp", [])
    print("\n════════ 结果对比 ════════")
    print(f"  基线均分 {b_mean:.3f}  →  优化均分 {o_mean:.3f}   "
          f"({'提升' if o_mean > b_mean else '未提升'} "
          f"{abs(o_mean - b_mean) * 100:.1f} 分)")
    print(f"  基线  hit={b_sum['answer_hit']} refusal={b_sum['refusal']} "
          f"coverage={b_sum['citation_coverage']}")
    print(f"  优化  hit={o_sum['answer_hit']} refusal={o_sum['refusal']} "
          f"coverage={o_sum['citation_coverage']}")

    report = {
        "config": {"smoke": SMOKE, "n_cases": len(cases), "picks": picks,
                   "model": os.environ.get("TALLY_LLM_MODEL", llm.DEFAULT_MODEL)},
        "baseline": {"mean": b_mean, "summary": b_sum, "scores": b_scores},
        "optimized": {"mean": o_mean, "summary": o_sum, "scores": o_scores},
        "questions": [c["q"] for c in cases],
    }
    out = REPORT_DIR / ("report-smoke.json" if SMOKE else "report-full.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n报告落盘 {out}（编译产物在 compiled/）")
    llm.chat_json = _ORIG_CHAT_JSON


if __name__ == "__main__":
    main()
