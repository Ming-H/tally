# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Tally（tally，前身 对账/duizhang）— a Chinese financial-report **closed-world fact Agent**. The name is the contract: 对账 = every answer must be reconcilable against the source — every sentence carries a citation, every citation traces back to a page of the original filing; an answer that can't be reconciled is refused, never fabricated.

v0.7 closed-world contract（不要削弱）:

- **事实只来自内部 RAG 证据** — LLM 参数知识只组织语言，永不生产事实。
- **工具白名单只含内部动作**：`search` / `metrics_table` / `derive` — 永无浏览器、外部 API、模型生成代码。
- **只有 verify:pass 的结构化事实进长期记忆。**

One graph, two brains. The LLM (default = LOCAL Ollama via its native `/api/chat`, zero key/cost/egress; tier user-configurable via `TALLY_LLM_MODEL`, built-in default `qwen3.5:0.8b` — design assumptions never hardcode a tier: stronger tiers improve narration while the gates keep the floor) drives query understanding, routing, retrieval grading, rewriting, multi-hop planning, and grounded generation. **Deterministic code owns the exit gate** (`verify_citations`): degrade or refuse, never fabricate. The rule mode (`heuristic`) is the zero-key fallback and the deterministic regression baseline. 可靠性由架构保证，不靠模型堆料。

All code comments, docstrings, docs, and commit messages are in Chinese — follow that convention.

## Commands

```bash
source venv/bin/activate   # Python 3.12; deps already installed (exact repro: requirements.lock.txt)

# Ingest & query (auto = llm if key in .env, else heuristic)
python -m tally.cli ingest samples/  # incremental ingest (fingerprint diff; idempotent)
python -m tally.cli ingest samples/ --watch   # poll mode: resync every 60s
python -m tally.cli ask "欧科亿 2026 年半年度预计净利润多少"
python -m tally.cli ask "对比欧科亿和海伦哲 2026 一季度谁赚得多"   # compare → decompose
python -m tally.cli chat               # 多轮对话（L0 记忆：追问继承公司/期间/指标/年份）
python -m tally.cli ask "…" -v          # 诊断态：trace 每节点带耗时（stderr）
python -m tally.cli ask "…" --json      # 机器态：整态 JSON
python -m tally.cli docs                # list ingested docs
python -m tally.cli index               # (re)build FTS for existing chunks
python -m tally.cli embed               # 可选向量第三路：嵌入存量 chunks（需 requirements-vec.txt）

# Eval (judge() is a pure function; drop custom datasets into evals/*.json)
python -m tally.cli eval                          # default dataset (10 单跳; CI regression)
python -m tally.cli eval --json                   # summary + 行级 rows（对比脚本用）
python -m tally.cli eval --mode llm --dataset agent     # agent dataset (multi-hop; fine on the default local tier)
python -m tally.cli eval --dataset innolight --min-retrieval 1.0 --min-hit 1.0   # CI 门槛：低于即非零退出
python -m tally.cli eval --dataset maotai         # 贵州茅台真值 9 题（简称/口径/断词泛化）
python -m tally.cli eval --dataset quarters       # v0.7 单季差分 8 题（Q2=H1−Q1 / Q4=FY−Q3）
python -m tally.cli eval --dataset multihop       # v0.7 多跳 8 题（跨年双值/三公司/多指标族）
python -m tally.cli eval --dataset multiturn      # v0.7 多轮 10 轮接力（L0 继承链）

# Tests / lint
pytest                                  # 135 cases; conftest blocks all real LLM calls
pytest tests/test_store.py::test_readd_after_drop_no_ghost   # one test
ruff check .                            # lint (CI enforces)
./run_tests.sh                          # 全量一条龙：ruff + pytest + 七数据集 eval + 冒烟问答断言
                                        #   （全 --mode heuristic 与 CI 同口径，约 5 分钟；报告落 test_reports/latest.log）
QUICK=1 ./run_tests.sh                  # 快速档：ruff + pytest + default 集（约 1 分钟）
```

**CI**（`.github/workflows/ci.yml`，push/PR 触发）：ruff → pytest → README 冒烟（ingest samples → ask → default eval `--min-* 1.0`）→ 真值三集 innolight / quarters / multihop（heuristic；`TALLY_DB=/tmp/truth.db` 隔离库重摄取双语料；`--min-retrieval 1.0 --min-hit 1.0`，跌破即非零退出）。

**After changing retrieval/graph/answer code**, run `python -m tally.cli eval --mode heuristic` (deterministic baseline — plain `eval` defaults to `auto`, which silently goes llm when Ollama is up and adds small-model variance) and verify the headline metrics don't regress (heuristic 全集 1.00/1.00/1.00); `evalx.py` additionally reports citation_coverage (pass/coverage; v0.7 前叫 faithfulness) and chunk-level P/R@8 — check those too on retrieval changes. Bug fixes follow TDD — failing test first, watch it fail, then fix (DESIGN-v0.4 §5 and DESIGN-v0.5.md record two such rounds).

### Corpus

| 根 | 规模 | 角色 |
|---|---|---|
| `samples/` | 3 家公司 5 份 | 演示 + CI 回归最小集 |
| `data/中际旭创/` | 10 docs / 7,873 chunks，**26MB 随 git 入库** | innolight 31 题真值集的承重墙（没它该 eval 不可复现） |
| `data/贵州茅台/` | 10 docs / 6,518 chunks，~10MB 随 git | 多公司泛化的磨刀石（maotai 9 题） |
| `data/corpus/` | 全量 1,104 docs / 60.7万 chunks（本机自备，不入 git） | 容量验证（DESIGN-scale.md） |

多根共存一库（multi-root ingest；删除/孤儿清理只对本次扫描根负责）。工作库现状可 `cli docs` 查看；双语料同库基线 = 24 docs / 14,701 chunks。

### Eval datasets（按题计共 84 题）

| dataset | 题量（陷阱） | 考什么 | CI |
|---|---|---|---|
| default | 10（2） | 单跳检索/口径基线 | ✓ 冒烟 + 门槛 |
| agent | 8（2） | 多跳对比 + 陷阱（llm 模式跑） | — |
| innolight | 31（4） | 事实/趋势/陷阱/鲁棒（真值逐题人工对库核对） | ✓ 门槛 1.0 |
| maotai | 9（1） | 简称（茅台→贵州茅台）/口径/断词泛化 | — |
| quarters | 8（1） | 单季差分派生 + 期间守卫 | ✓ 门槛 1.0 |
| multihop | 8（1） | 跨年双值/三公司/多指标族（触发 plan） | ✓ 门槛 1.0 |
| multiturn | 1×10 轮 | L0 继承链/口径切换/代词指代 | —（refusal=N/A 属正常） |

## Environment

**Secrets**: `TALLY_LLM_API_KEY` lives ONLY in `.env`（gitignored；`llm._load_dotenv` 装载，环境变量优先）. Never write the key into code, docs, sample commands, or git history. Local endpoints need no key (`llm_enabled` is True for localhost).

**LLM 通道实测事实**（DESIGN-v0.5 D7，改动 llm.py 前先读）: the local channel calls Ollama's NATIVE `/api/chat`（`llm._local_chat`）— the OpenAI-compat `/v1` endpoint ignores the `think` param, the `/no_think` soft switch is dead in Qwen3.5, and thinking starves max_tokens leaving content empty (all three measured). `think` defaults OFF locally（开约 ×5 延迟）. Requires Ollama ≥ 0.40. Remote via `.env`: any OpenAI-compatible endpoint, or Anthropic-compatible (`_use_anthropic`；thinking counts toward max_tokens → budget ×8, unless `TALLY_LLM_MAX_TOKENS` explicitly set). The `extra_body.thinking` param goes ONLY to bigmodel endpoints.

| 变量 | 默认 | 作用与注意 |
|---|---|---|
| `TALLY_LLM_API_KEY` | — | 见 Secrets；本地端点免填 |
| `TALLY_LLM_BASE_URL` | `http://localhost:11434/v1` | 通道识别看 host：localhost → 本地原生通道；其余按 `_use_anthropic` 分 OpenAI 兼容 / Anthropic 兼容 |
| `TALLY_LLM_MODEL` | `qwen3.5:0.8b` | 换档只改这一行；编排假设不绑档位 |
| `TALLY_LLM_MAX_TOKENS` | 按档位 | 生成预算（Anthropic 兼容通道自动 ×8 的例外见上） |
| `TALLY_LLM_THINKING` | `disabled` | 仅影响生成；结构化调用永远关（`llm._thinking_off` 分层） |
| `TALLY_DB` | `./tally.db` | SQLite 路径（CI 真值冒烟用 `/tmp/truth.db` 隔离） |
| `TALLY_TREND_ANALYSIS` | 未设 = 叙述开 | 三态：未设 = trend 叙述层默认开，躲在**双闸**后（数字闸 + `direction_gate` 方向闸——方向词 vs 骨架同比符号，拦「+90.05% 被说成大幅回落」）；`enabled` = 加深度投资分析；`off` = 骨架直出（逃生门）。任一闸违例退骨架 = 保守失败 |
| `TALLY_SELF_CHECK` | 开 | 叙述存活后的 LLM 裁判，结构化 `{faithful/on_topic/sensitive}`；**非对称信任**——只能否决不能解锁（sensitive 剥句不重写；不忠实/跑题有界修复 1 次再退骨架），永远在确定性闸门之后 |
| `TALLY_VEC` | 关 | 向量第三路（bge-small-zh @ fastembed，同 RRF 融合）；需 `requirements-vec.txt` + `cli embed`。消融实测（D19）数字/口径题词法双轨已饱和、向量净负 → 默认关、opt-in 实验 |
| `TALLY_MEMORY` | 开 | 三层记忆总闸；`off` = 退回 v0.6.5 无记忆行为 |
| `TALLY_EVAL_MEMORY` | 隔离 | eval 内默认关台账（防把检索能力测成记忆命中）；`on` 显式放开（记忆专项评估） |

## Architecture

Two planes, one SQLite file — tables `docs` / `chunks` / `fts_w` / `fts_c` / `manifest` / `verified_facts` (+ optional `vecs` / `vecmeta`). `verified_facts` keys on (company, period, metric, caliber), append-only + supersede（永不物理删除；部分索引 `vf_live WHERE superseded_by IS NULL` 只索引活行，死行留存审计轨迹）.

**Offline ingest** — `source.py` scans a directory into `{path: mtime_ns-size fingerprint}` and three-way diffs against `manifest`（add/update/delete + orphan cleanup scoped to the scan root）. `ingest.py` extracts metadata from the **filename**（巨潮 convention → company/period/kind — never model guessing; broker naming `日期-券商-公司` also parses; md/txt with a CJK company-name stem ingest as ANALYSIS, **anything else rejected with ⚠** — a digit-stem like `20261009_…分析.md` used to become garbage doc-id `20261009_MD`）, strips repeated header/footer lines（frequency-based, digits stripped in normalization — headers share lines with page numbers）, chunks with **table-awareness**（表格是原子块，绝不拦腰切断）, and appends **num_notes** number-normalization notes（`41,914.72 万元` → `≈4.19亿`）so lexical search hits 「多少亿」-style queries.

**Online QA graph**（`graph.py`，LangGraph state machine over `GraphState` — narrow-interface TypedDict in `schemas.py`；每个节点只读所需、只写所责，`trace` 是 append 语义）:

```
START → understand ─→ memory_probe ─→ route ───metric/compare(llm)──多跳信号──→ plan ─→ agent_research ─┐
     （LLM 抽 intent/公司/   （L1 台账探针；     │（期间守卫：年份来自          （≥3公司/跨年/≥2指标族）  （ReAct 工具循环）   │
       期间，规则兜底）        命中不豁免 verify：  │  文档元数据不靠模型；                        ↑ 校验失败/零命中回退      │
                               hit→END / 否则→route）│  语料无该期间直接拒→END                        agent 环继续执行）        │
                                                 ├─metric/compare(heuristic)→ retrieve / decompose（按公司分解）           │
                                                 │                              ↓                                     │
                                                 │                        grade_docs ⇄ rewrite_query（纠错环 ≤1）    │
                                                 │                              ↓ sufficient                          │
                                                 ├─trend（双模式共用）→ trend_research（确定性遍历 ≤6 期升序 ──────────────┤
                                                 │        + Q2=H1−Q1 / Q4=FY−Q3 差分派生）                              │
                                                 ├─summary → summarize_doc → END                                       ↓
                                                 └─other ──→ refuse → END                                    generate → verify_citations → END
                                                                      grade 两次不足 → refuse ↗              （确定性引用闸门；fail → 抽取式降级/拒答）
```

**Memory 三层**（v0.7 Phase 3，`memory.py`；六条设计公理见 DESIGN-v0.7 §7.1，动记忆前先读：①事实不可压缩、叙述才可压缩 ②缓存证据不缓存结论 ③写闸严于读闸 ④爆的是上下文不是存储 ⑤记忆是错误放大器 ⑥失效=语料指纹）:

- **L0 会话记忆** — `cli chat` / multiturn eval 把轮摘要传进 `new_state`：近 3 轮全量（q+答案截断），更早压缩为**结构化轮摘要**（丢自由文本不丢理解字段）。继承只取 understanding 字段（companies/period/years）与指标词，**永不继承自由文本**；裸追问（「呢/它」）带任何数据信号时意图救援为 metric 而非 other。
- **L1 事实台账** — `verified_facts` 表键寻址 (company, period, metric, caliber)；合并算子 noop/insert/conflict/restated/reingest（比较 value_std，append-only supersede）。**写闸** = verify:pass ∧ 口径闸未触发（trace 含 caliber-mismatch/fallback/no-evidence/derived-mismatch 一律不写）；**读不豁免**——memory_probe 命中拼出的答案内联过 verify（缓存证据不缓存结论）；失效 = 语料指纹（probe 时校验，指纹漂移自动 supersede reingest）。业绩预告文档（E 后缀期间）是区间值非单值——metric_rows 解析不出单值，不落账（保守失败，正确行为）。
- **L2 固定语义资产** — `tally/assets/semantic_assets.json`（alias 词典等检索导航知识，非答案事实；保持 ≤2KB；晋升闸 = 人工编辑 + eval 全绿）。

**Planner**（v0.7 Phase 2，llm 模式）— 多跳信号（≥3 公司 / 跨年 / ≥2 指标族）触发 `plan` 节点：LLM 输出结构化子目标 JSON，`_validate_plan` **只删不造**（白名单外动作丢、期间规约、去重、`MAX_SUBGOALS=5` 封顶、单公司回填）；执行后子目标检查点（`_ckpt_ok`：财报级数字在场 ∧ 指标词命中）逐个验证，未过的进 `plan_gaps`（部分答案不硬凑）。

**derive 差分算子**（v0.7 Phase 4）— 原文不披露单季时：Q2 = H1(半年累计) − Q1、Q4 = FY − Q3(前三季累计)，操作数同为年初至期末累计（口径对齐），**原币精度**算术（亿值四舍五入差分会到百万级误差）；`_derived_ok` 出口重算：操作数必须真在场于证据池且 |a−b−value| ≤ 0.01；`_derive_quarters.find` 按 **tag+年份** 匹配（只按 tag 会把 2024Q2 派生到 2025 行——实测回归）。**禁止模型心算**（封闭世界：算术也是事实）。

**trend 意图**（v0.6.2 起，v0.6.5 升级）— `trend_research` deterministically walks the company's reporting periods（≤6 期，时间升序——小模型自己遍历会漏期/循环，实测）；`trend_skeleton` 行级解析每期为结构化行（营收/归母净利 + 同比 + 引用；Q3 双列行锚定累计列 — D26）。LLM 叙述层默认开、躲在**双闸**后（数字闸 + 方向闸）+ LLM 裁判（`_self_check` 非对称信任）；任一闸触发退骨架 = v0.6.5 前行为（保守失败）。两道入口护栏：query 必须含趋势词否则意图降级 metric（小模型给「预计…多少」幻觉过 trend）；规则层可覆盖 LLM 误判 trend 为 summary。无事实行 → refuse。多期证据仍走 generate→verify 出口。

**Retrieval**（`fts.py` + `index.py`）— dual-track FTS5 + RRF（k=60）：word track（jieba + unicode61）+ char track（trigram，whitespace-stripped，≤200 chars — 按设计截断 14.2% 的块，词轨覆盖全文兜底）。可选向量第三路（`embed.py`）默认关（见 `TALLY_VEC`）。缺依赖/模型不匹配/未嵌入 → `ranking()` 返回 None → 静默双轨回退（CI 不装 fastembed 也绿）。`understanding` 驱动 `doc_scope`：公司精确匹配 + doc 级期间限制（期间**类型** + **年份**——`query_years` 抽 20xx 和口语两位年份「15年」→2015，必须后跟「年」防金额误命中）。`expand_query` 把口语映射到报告术语（营收→营业收入、赚→净利润、原因→主要系）。生成上下文由 `page_evidence` 组装（small-to-big：去重首命中页 → ±2 兄弟块窗口 → 硬帽）；metric 意图额外前置「主要会计数据」表页（真值行在词法上输给无数字的表头块——实测）。

### Invariants — violate these and you reintroduce past bugs

1. **`Store`（`store.py`）是六张表的唯一写入口**（含 vecs/vecmeta — `add_vecs`/`set_vec_model` 是仅有的向量写者；换 vecmeta 模型即清全部旧向量）。fts rowids 对齐 `chunks.rowid`；re-add = drop-then-insert（对 chunks 永不 `INSERT OR REPLACE`），drop_doc 连带清 vecs。`chunks(doc_id)` 已入 SCHEMA 索引（旧库下次打开自动迁移）。`tests/test_store.py` 是永久回归护栏。
2. **一张图，两颗脑。** 每个 LLM 决策点失败即退规则**并留 trace 标记**（`understand:llm→fallback` 等）。`--mode auto` 在 `new_state` 解析：有 key 走 llm，否则 heuristic。测试永不真调 LLM（conftest 剥 key 封网）。
3. **纠正环有界**（heuristic `MAX_RETRIES=1`；agent 环 `MAX_AGENT_STEPS=6`——无界自主是状态机反模式）。llm 模式 metric/compare 进 `agent_research` — 工程化 **ReAct 环**（v0.7.1 起双通道：本地 Ollama 原生 tools 与远端 OpenAI 兼容 function calling，经 `_remote_chat_with_tools` + `_normalize_tool_messages`；Anthropic 兼容通道 tools 仍 NotImplementedError → 固定管线兜底；智谱 glm-5.3 实测见 DESIGN-v0.7 §8.3）。`AGENT_TOOLS` 注册表 = `search` / `metrics_table` / `derive`；tool call → observation 以 tool role 回灌 → 下一步 → 自停/预算停。步级观测（v0.7 起）：每步四元组 `{thought, action, obs(条数+首doc), gate(new/dup/empty)}` + 步级毫秒进 `state["agent_steps"]`（`-v` 打印、`--json` 序列化、evalx 聚合 waste_rate = (dup+empty)/calls）。trend 永不进环（确定性遍历，双模式同）。小模型护栏（全部实测，DESIGN-v0.6）：幂等调用缓存 + 整批重复即收手；understanding 先验**覆盖**工具参数的期间（小模型幻觉 FY）、缺省回填公司/期间；agent 检索词写进 `effective_query`；证据池按 doc 轮转出水。Agent 收集的证据仍走 generate→verify——自主永不豁免闸门。
4. **宁拒答不编造。** Gates: route 内期间守卫（年份来自 doc 元数据，不靠模型）；doc 级期间过滤（`period_hints` 互斥——「半年度」包含「年度」，子串序 if 会让 FY 泄漏，round-4 bug R-1；裸年份无期间词 → FY，D18）；`verify_citations`（证据池 = 页级集 **∪ hits**——页窗口只覆盖 hits[:4] 而抽取式引句取 hits[:8]；引用匹配自 D13 起空白符归一——小模型引用格式漂移「第 8 页」；被引块含 num_notes，双侧 meta-token 过滤、只取数字句、换行感知切分；coverage < 0.45 → 降级抽取式；LLM 无证据 → 抽取式而非拒答）。**口径五层闸**（D16/D20–D22）：`stitch_adjacent` 同页标签/数字表块重接（retrieve 与 agent 路径都跑）；trend `pick()` **行级**（splitlines，指标词后首个大数，标签上下文口径守卫 = 指标词前 20 字符，最佳行解析失败退位次优）+ 抽取式评分双双向：不问扣非拒扣非行；**配对降权 ×5**（问 X 时对共享标签前缀 token 的兄弟句 Y 降权：扣非↔归母 / 净利润↔净资产 / 总收入↔营业收入 / 总资产↔净利润族 / 总资产↔营收族）；`caliber_mismatch`（D22，LLM 模式数字闸）——问命名口径（扣非/总收入/净资产/总资产）时答案大数必须出现在该口径的带标签行（来源 = 页集 ∪ hits，标签/数值分行时下一行捡起，营业总收入段切），否则降级抽取式——gate≠correctness 边界的第一处收口（§9）。`ans.SPLIT_FIX` 断词修复（`tally/answer.py`，imported as `ans`）为抽取式 / trend pick / generate 证据组装三方共用——保持同步。前置（`front_key_table`，cap 8 ∪ 指标词命中尾块）retrieve 与 agent 路径都跑；抽取窗 hits[:12]；`page_evidence` 按页分层（每首命中页 ±2 窗——union 窗实测净负，上下文稀释）。
5. **SQLite PRAGMA 按连接生效**（`fts._apply_pragmas` 每次搜索；Store 设 WAL + synchronous=NORMAL；变更摄取后 `store.analyze()`）。
6. **多根摄取**：删除/孤儿清理只对扫描根负责（`cli.py` 的 `under_root`）。`--rebuild` 仍是全清。
7. **Thinking 分层**（`llm._thinking_off`）：结构化调用关（廉价枚举），生成开（碎片表格证据上的数字关联需要它；flash 不思考时把增幅区间当指标值引用）。

### Long-horizon reliability（eₖ·gₖ，DESIGN-v0.7 §3）

v0.7 把链拉长（多子目标/多轮/跨会话记忆）——单步 95% 可靠的任务 20 步只剩 ~36%（0.95²⁰）。**该改的不是单步精度，是传播结构**：

```
P(错误答案) ≤ Σₖ eₖ·gₖ    eₖ = 第k步出错率（模型决定）
                           gₖ = 错误穿过第k步闸门的存活率（代码决定）
e=5%、g=2%、20 步 → ≤2%（朴素模型 36% 失败），且失败形态 = 降级/拒答（安全失败）
```

四个杠杆：①**缩短乘法链**——能确定化的环节不给模型（trend 遍历/差分算子）；②**切断传播边**——verify-as-you-go（子目标检查点把拦截推进到步级）；③**结构化传递**——字段复制不文本重释（GraphState、subgoals=JSON、记忆=结构化台账——不是洁癖，是传播率设计）；④**并行分支不乘法**——compare 按公司分解、各自过闸。**相关性警告**：闸门与模型犯同一类系统错误时该界失效——双脑回归 + 双档验收的深层理由是相关性探测，不是冗余。**度量先行**：`eval --json` 的 `steps.waste_rate` 是 eₖ 的可测代理，verdict 分布是 gₖ 的出口。

### Industry map（业界对照——动机制前先认亲）

| 本仓机制 | 业界对应 | 出处 |
|---|---|---|
| `agent_research` 工具循环（thought→action→obs，步级四元组观测） | **ReAct**；Anthropic agentic loop | [ReAct, Yao et al. 2022](https://arxiv.org/abs/2210.03629)；[Building Effective Agents, Anthropic 2024](https://www.anthropic.com/engineering/building-effective-agents) |
| `plan` 节点（子目标 JSON + 只删不造校验 + 检查点） | **Plan-and-Execute**（planner/executor 解耦以降误差累积）；orchestrator-workers 模式 | [Plan-and-Solve, Wang et al. 2023](https://arxiv.org/abs/2305.04091)；[LangChain Plan-and-Execute](https://www.langchain.com/blog/plan-and-execute-agents) |
| `grade_docs ⇄ rewrite_query`（有界 ≤1） | **Corrective RAG / Self-RAG** 的 grade-and-retry——本仓加重试上限 | [CRAG, Yan et al. 2024](https://arxiv.org/abs/2401.15884)；[Self-RAG, Asai et al. 2023](https://arxiv.org/abs/2310.11511) |
| understand 驱动路由 | **Adaptive-RAG**（按查询复杂度分流检索路径） | [Jeong et al. 2024](https://arxiv.org/abs/2403.14403) |
| 双轨 FTS5 + RRF(k=60) | **Hybrid retrieval + Reciprocal Rank Fusion** | [RRF, Cormack et al. SIGIR'09](https://dl.acm.org/doi/10.1145/1571941.1572114)（不在 arXiv）；[Kuzi et al. 2020](https://arxiv.org/abs/2010.01195) |
| num_notes / stitch_adjacent / 表格感知分块 | **Contextual Retrieval**（给 chunk 补检索所需上下文）；structure-aware chunking | [Anthropic 2024](https://www.anthropic.com/research/contextual-retrieval)；[Unstructured](https://unstructured.io/blog/chunking-for-rag-best-practices) |
| `page_evidence`（首命中页 ±2 窗） | **sentence-window / small-to-big** retrieval | [LlamaIndex](https://developers.llamaindex.ai/python/examples/node_postprocessor/metadatareplacementdemo) |
| `verify_citations` + 拒答优先 | **Attributed QA / citation grounding；abstention** | [Bohnet et al. 2022](https://arxiv.org/abs/2212.08037)；[Anthropic Citations API](https://platform.claude.com/docs/en/build-with-claude/citations)；[SQuAD 2.0, 2018](https://arxiv.org/abs/1806.03822) |
| `TALLY_SELF_CHECK` 裁判（只能否决） | **LLM-as-judge 的 veto-only 变体**（judge 有偏 → 永不当正面权威；业界无 canonical 论文，实践用法） | [MT-Bench, Zheng et al. 2023](https://arxiv.org/abs/2306.05685) |
| derive 差分（出口重算，禁心算） | **tool-augmented arithmetic**（工具描述详尽 + 强类型参数） | [Anthropic define-tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)；[OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling) |
| L0/L1/L2 三层记忆 | **MemGPT** 分层记忆；LangGraph store（semantic/episodic/procedural） | [MemGPT, Packer et al. 2023](https://arxiv.org/abs/2310.08560)；[LangGraph memory](https://docs.langchain.com/oss/python/concepts/memory) |
| eₖ·gₖ 公理 + waste_rate 度量 | 多步 **compounding errors** 的传播结构治理 | [SAUP, 2024](https://arxiv.org/abs/2412.01033)；[MAST, 2025](https://arxiv.org/abs/2503.13657)；Anthropic BEA 同警告 |
| trend 确定性遍历（不进 agent 环） | **workflows over agents**（可预测路径不给模型） | [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) |

2024–2025 业界共识（LangChain / Weaviate / Qdrant / NVIDIA）已把「自主检索 + 分级重试 + **护栏即模式的一部分**」收为 agentic RAG 的标准形态——本仓的 verify 闸门 + 有界步数 + waste_rate 正是那个共识的度量面（[Weaviate 定义](https://weaviate.io/blog/what-is-agentic-rag)）。

**与业界默认的分歧点**（改动前先读对应实测，别按业界直觉「纠正」）:

- **向量检索默认关**（业界默认 dense 或 hybrid）：31 题真值消融（D19）实测数字/口径任务上词法双轨已饱和、向量净负——语义近 ≠ 口径对。BEIR 同现象：BM25 在精确匹配/罕见词上是稳健基线（[arXiv:2104.08663](https://arxiv.org/abs/2104.08663)）。D19 留了口子：语义问法（「为什么/怎么样」）或受益，未测。
- **trend 不进 agent 环**：确定性遍历——小模型自遍历漏期/循环（实测）；Anthropic「能 workflows 不 agents」。
- **闸门全确定性代码，LLM 裁判只能否决**：MT-Bench 证明 judge 有位置/冗长/自增强偏——judge 不配当解锁侧，只配当否决侧。
- **重试/自主有界**（rewrite ≤1 / agent ≤6 步 / 叙述修复 ≤1）：无界自主在状态机里是反模式。

## Docs

| 文档 | 是什么的权威 |
|---|---|
| `docs/INTRODUCTION.md` | 新读者导览（what/why/architecture 白话 + 三条阅读路径——首读指这里） |
| `docs/DESIGN-v0.7-agent.md` | **v0.7 Agent 升级，已全部落地验证**：四支柱（ReAct 观测/Planner/Memory/derive）+ §3 长程可靠性公理 + §7 记忆六公理 + §8 eₖ/gₖ 真机实测（回归护栏 = tests/test_v07.py + quarters/multihop/multiturn 三集） |
| `docs/DESIGN-v0.6.md` | agent 环与小模型护栏（D9–D12）、trend 层（§6）、v0.6.3 真值修复（§7：D13/D14/D16/D18）、P1 批（§8：CI 真值冒烟、评估扩容 + faithfulness/chunk-P/R、向量消融 D19）、**§9 已知边界账本（gate ≠ correctness——动 verify_citations 前必读；样本 C = 每股收益断词，未修）**、§10 多公司泛化批（D20 简称/D21 行级抽取/D22 口径数值闸——茅台语料实测） |
| `docs/DESIGN-v0.5.md` | agentic 地基（D1–D6；D7 = LLM 通道实测——本文 Environment 节的出处） |
| `docs/DESIGN-v0.4.md` | 检索/写入架构（§5 = round-4 评审） |
| `docs/DESIGN-incremental.md` / `docs/DESIGN-scale.md` | 增量摄取指纹 diff / 容量（60.7 万块实测） |
| `docs/EXPLAINER.md` | 一条 query 的完整旅程（narrated walkthrough） |
| `docs/TEST-CASES.md` / `docs/RAG-EVAL-CASES.md` | 测试手册（四层/验收/trace 读法）/ 评估集设计方法论 |

(v0.6.5 — D26 Q3 双列修复、方向闸、trend LLM-in-the-loop 默认开、裁判自检 — 记录在作者源仓，不在本仓。) 设计文档记录 *why*——动前先查（别 re-litigate），改架构后更新。`docs/plans/` 存实施计划。配套教程[《动手学 Agent RAG》](https://github.com/Ming-H/hands-on-agent-rag)（五部 14 章 / 16 个破坏性实验 / 40 道真值题，结构定案见 `docs/plans/2026-10-27-教程五部14章重构-design.md`）以本仓为唯一载体——改公开行为时保持仓库 tutorial-ready。
