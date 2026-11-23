# 实验：DSPy 优化 understand 节点

> 把 `tally/graph.py` understand 节点的手写查询理解提示词，变成 DSPy 可编译的
> Signature，用仓库自带的真值 eval 集当训练信号，MIPROv2 自动优化指令——
> 对比「手写提示词 vs 编译产物」的端到端分数。
>
> 动机与定位见课程对照：CS329Z L05（DSPy：签名/模块/优化器）与 L09
> （MIPROv2/GEPA 优化器谱系）；本仓 CLAUDE.md「与业界默认的分歧点」之外的
> **第一次提示词自动优化实验**。

## 设计

### 归因：外科补丁注入

understand 节点经 `llm.chat_json(system, query)` 调 LLM，system 以
「你是财报问答系统的查询理解器」开头（`graph.py` understand 节点）。实验只对
**这一个调用**打补丁：候选理解器接管 understand；其余 LLM 调用（grade /
agent_research / …）原样放行。基线与优化共享完全相同的下游管线与守卫栈
（期间覆盖/意图纠偏/公司纠偏全部保留）——分数差异可归因于理解质量。

### 度量：复用 evalx.judge

候选 understanding 注入管线跑 `mode=llm` 全流程，`evalx.judge` 逐题判定：

```
拒答题：拒了=1.0，没拒=0.0
普通题：ok=1.0 / 仅关键词命中=0.5 / 全未中=0.0
```

### 隔离（与 run_eval 同口径 + 一项收紧）

| 开关 | 值 | 理由 |
|---|---|---|
| `TALLY_MEMORY` | off | 隔离 L1 台账，防跨题污染 |
| `TALLY_TREND_ANALYSIS` | off | trend 骨架直出，砍掉叙述层 LLM 方差与延迟（收紧项） |

### 训练集：分层抽样 20 题

`default`×10（单跳+2陷阱）+ `agent`×4（多跳对比+陷阱）+ `quarters`×4
（trend/差分/期间守卫）+ `maotai`×2（简称泛化）。multiturn 需 L0 继承链、
归因复杂，不入实验；innolight 31 题留作扩展集（换 `PICKS` 即启用）。

### 优化器：MIPROv2 instruction-only

demos 关零（`max_bootstrapped_demos=0, max_labeled_demos=0`）——本实验的
metric 一次要跑完整 LLM 管线（30-70s），预算全部留给指令候选；示例优化
（BootstrapFewShot）需要 understanding 标注，留作后续。

## 用法

```bash
# 依赖（venv 内）
uv pip install -p venv -r requirements-dspy.txt

# 冒烟（4 题 + 微型预算，约 20 分钟）——验证闭环
python -m experiments.dspy_understand.runner

# 过夜全量（20 题 + MIPROv2 auto=medium，预计 2-4 小时）
nohup venv/bin/python -m experiments.dspy_understand.runner --full \
  > experiments/dspy_understand/full.log 2>&1 &

# 只评估已编译产物（不重新优化）
python -m experiments.dspy_understand.runner --eval-compiled
```

产物：`compiled/`（DSPy 程序，可 load 复评）+ `report-smoke.json` /
`report-full.json`（基线 vs 优化的逐题分数与 evalx 汇总指标）。

## 预期与判读

- **基线本身很强**：understand 下游有确定性守卫栈兜底（期间规则覆盖、
  trend/metric 争议以规则为准、公司漏检回填），heuristic 全集 1.00/1.00/1.00。
  手写提示词 + 守卫的 llm 模式在 default/agent 集也是满分档。
- **因此本实验测的是「守卫放行的空间里，LLM 理解还能提多少」**：隐式期间
  （query 无期间词时 LLM 判断存活）、意图枚举的边界案例、公司名抽取超越
  子串匹配的部分。若全量跑完提升 < 2 分，结论是「守卫已吃满红利，understand
  不是当前瓶颈」——这同样是有效结论（工程上等于确认 D22 之后该去补 §9 样本 C）。
- **换模型即重编译**：`.env` 改 `TALLY_LLM_MODEL` 后重跑 `--full`，
  这是 DSPy 路线对「换脑不改代码」承诺的直接验证。

## 冒烟实测（2026-11-17，glm-5.3）

闭环全通：基线 4/4 → MIPROv2 编译（11 次 metric 调用，proposer 确实生成了
改写后的指令——「trend=查询多期变化趋势…」即优化器产物）→ 编译产物 4/4。
`1.000 → 1.000` 属预期（4 题都是 simple 案，守卫栈吃满红利，天花板效应）；
冒烟的目的是验证链路，**信息量在过夜全量跑的 20 题分层集上**（含 trend/
差分/陷阱/简称泛化的硬题）。

调试过程中钉死的 dspy 3.5 版本事实（改 runner 前先看）：

| 坑 | 修法 |
|---|---|
| 默认走 OpenAI Responses API，兼容端点 404 | `dspy.LM(..., model_type="chat")` |
| MIPROv2 贝叶斯优化缺可选依赖 | `pip install "dspy[optuna]"` |
| Evaluate 默认多线程，SQLite 连接跨线程炸 | `MIPROv2(..., num_threads=1)`（构造器参数，compile 不收） |
| `auto` 默认非 None，手动预算被拒 | 显式 `auto=None` 再传 num_candidates |
| Example 输入字段名 | `with_inputs("question")` 后取 `example.question` |
| `program.save(path)` | 目录要先 mkdir，路径以 `.json` 结尾 |

## 已知边界

- metric 内 `next(c for c in cases if c["q"] == example.q)` 按题面定位
  case——eval 集题面唯一，无重复（innolight 扩展集启用前需复查）。
- MIPROv2 的 proposer 复用同一 LM（glm-5.3）；未配独立的
  `prompt_model`（大档 proposer + 小档执行的双档实验留作后续）。
- 全量档若 bigmodel 限速触发 429，`llm.py` 的 60s 超时 + 1 次重试之外
  无退避——观察 full.log，必要时分两晚跑。
