# 架构说明

## 转换点

上游在 `TradingAgentsGraph.__init__` 创建两个 provider 客户端（quick / deep），再把 `llm` 对象传给各个智能体工厂。本 skill 用 `scripts/skill_llm.py` 的 `SkillLLM` 替换这个对象，其余代码不改。

| 上游调用位置 | 上游行为 | 本 skill |
| --- | --- | --- |
| `market/news/fundamentals_analyst.py`：`prompt \| llm.bind_tools(tools)` | 模型多轮发起 tool call，由该分析师的 LangGraph `ToolNode` 执行 | 任务文件包含完整消息与工具说明；子代理用 `ta.py tool --task` 调用，命令把调用交给同一个上游 `ToolNode` 执行，多轮后写最终报告 |
| `sentiment_analyst.py`：`with_structured_output(SentimentReport)` | 预取新闻/StockTwits/Reddit，结构化输出 | 预取逻辑照常执行；子代理输出 JSON，用同一 Pydantic schema 校验，再由上游 `render_sentiment_report` 渲染 |
| `bull/bear_researcher.py`、三个风险辩手：`llm.invoke(prompt)` | 自由文本 | 子代理写自由文本，上游节点拼接辩论历史与计数 |
| `research_manager.py`、`trader.py`、`portfolio_manager.py`：`invoke_structured_or_freetext` | 结构化输出；失败时用同一提示再要一次自由文本 | JSON 首次未通过校验时退回改正；再次失败时任务改为同一提示的纯文本作答，节点走上游自由文本分支 |
| `reflection.py`：`reflect_on_final_decision` | 对已有收益结果的历史决策做复盘 | 复盘任务，答案经上游 `Reflector` 与 `batch_update_with_outcomes` 写回记忆日志 |

`SkillLLM` 无答案时抛出 `LLMRequest`（继承 `BaseException`，不会被上游的 `except Exception` 回退吞掉），驱动据此写任务文件。答案就绪后同一节点再运行一次，`SkillLLM` 返回答案，节点产出与上游一致的状态更新。

### 工具调用

`ta.py tool --task ID` 取该分析师在上游 `_create_tool_nodes` 中的 `ToolNode`，构造与模型输出相同的 tool call，放进只含这一个节点的 LangGraph 图执行（`ToolNode` 需要图运行时）。因此：

- 返回内容与上游模型看到的 `ToolMessage` 相同。
- 参数错误、调用不属于该分析师的工具，都以 `ToolNode` 的错误文本返回，模型可以改正。
- 数据源抛出的其他异常，上游 `ToolNode` 会重新抛出并中止整次运行。这里记录为运行失败：命令输出 `RUN_FAILED`，`step` 返回 `failed`；`step --retry` 让受影响的任务从头重跑，相当于上游从检查点恢复后重跑该节点。

## 执行顺序

LangGraph 编排被 `ta.py` 的步进循环替代，以便在每次模型调用处暂停、跨进程恢复并并行派发。拓扑取自上游：

- 分析师：按 `build_analyst_execution_plan` 生成，顺序与 CLI 的 `ANALYST_ORDER` 一致，**并行**执行。上游串行只是因为 LangGraph 中各分析师共用一条消息列表；每位分析师只读取公共输入、写自己的报告字段，结果互不依赖。首位分析师的输入消息为 ticker，其余为上游 `create_msg_delete` 的占位消息，与上游一致。
- 研究辩论与风险辩论：每步之后调用上游 `ConditionalLogic.should_continue_debate` / `should_continue_risk_analysis` 决定下一个节点。
- 固定边：Research Manager → Trader → Aggressive Analyst；Portfolio Manager → 结束。
- 运行开始：上游 `_resolve_pending_entries`（复盘）、`get_past_context`、`resolve_instrument_context`、`Propagator.create_initial_state`。
- 运行结束：上游 `_log_state`、`TradingMemoryLog.store_decision`、`SignalProcessor.process_signal`，以及 CLI 的收尾（分节文件终值、完成消息、分析师耗时、`write_report_tree` 保存）。

模型档位与上游 `GraphSetup` 相同：Research Manager、Portfolio Manager 为 deep，其余为 quick。

### CLI 与 `propagate()` 的取舍

上游有两条入口：交互式 CLI 与 Python `propagate()`。两者图相同，外围不同：

| 行为 | CLI | `propagate()` | 本 skill |
| --- | --- | --- | --- |
| 代码归一化、资产类型、分析师过滤与排序、默认 SPY | 有 | 无 | 按 CLI |
| `results_dir/TICKER/DATE/reports/<分节>.md`、`message_tool.log` | 有 | 无 | 按 CLI |
| 结束时保存报告树到 `./reports/TICKER_时间戳` | 有（默认是） | `save_reports` 需手动调用 | 按 CLI |
| 复盘、`past_context` 注入、记忆日志、状态日志、信号 | 无 | 有 | 按 `propagate()`（README 写明记忆日志始终开启） |

## 默认值

不带参数时与上游保持一致，来源如下：

| 项目 | 默认值 | 来源 |
| --- | --- | --- |
| ticker | SPY | CLI `get_ticker` 空输入 |
| 分析师 | 全选四位，按 `ANALYST_ORDER` 排序 | CLI；加密货币按 `filter_analysts_for_asset_type` 去掉基本面 |
| 辩论 / 风险讨论轮数 | 各 1 轮 | `DEFAULT_CONFIG`；`--research-depth` 对应 CLI 的深度档位，同时设置两者，`TRADINGAGENTS_MAX_*_ROUNDS` 优先 |
| 代码归一化与资产类型 | `normalize_ticker_symbol` + `detect_asset_type`，输入校验 `is_valid_ticker_input` | 上游 `cli.utils` |
| 输出语言 | English | `DEFAULT_CONFIG`（含 `TRADINGAGENTS_OUTPUT_LANGUAGE` 与 `.env`） |
| 数据源 | yfinance（宏观 FRED、预测市场 Polymarket） | `DEFAULT_CONFIG.data_vendors` |
| 记忆日志 | `~/.tradingagents/memory/trading_memory.md` | `DEFAULT_CONFIG` |
| 缓存 | `~/.tradingagents/cache` | `DEFAULT_CONFIG` |
| run 目录 | `结果目录/TICKER/日期` | CLI 的 `results_dir/TICKER/DATE` |
| 报告树 | `./reports/TICKER_YYYYmmdd_HHMMSS` | CLI「Save report」默认 |
| 状态日志 | `结果目录/TICKER/TradingAgentsStrategy_logs/` | 上游 `_log_state` |
| checkpoint | 关闭 | `DEFAULT_CONFIG` |

`tradingagents` 包在导入时从当前目录加载 `.env`，本 skill 照常受其影响。`--config 配置.json` 中的键以与上游 `set_config` 相同的方式合并（字典值合并一层），用于数据源链、`tool_vendors`、新闻条数与回看窗口、基准指数、结果目录、缓存目录、记忆条数上限等。命令行参数优先于配置文件。

本 skill 自身只覆盖 `llm_provider`、`deep_think_llm`、`quick_think_llm`（记录子代理模型）与 `checkpoint_enabled`（run 目录本身可恢复）。provider 相关参数（temperature、max_tokens、reasoning effort、重试次数）不适用。

## 与上游的差异

- 模型由子代理承担，不经过 provider SDK。
- 结构化输出从 provider 原生模式改为「JSON + 同一 schema 校验」，多一次改正机会后再走上游自由文本回退。
- 分析师并行执行；`message_tool.log` 中不同分析师的行按实际时间交错。模型在发起工具调用前写的中间文字不会记录，因为子代理的中间推理不经过驱动。
- 不使用 LangGraph checkpoint；run 目录本身可恢复。
- 分析师的工具调用轮次由子代理决定，没有 `max_recur_limit`。
- 情绪分析师的预取数据缓存在 run 目录，节点两次运行看到同一份数据。
- CLI 结束时询问是否在终端显示完整报告；对话中改为完整展示最终决策，其余章节按需读取。

## 命令与 JSON

所有命令向 stdout 输出一个 JSON 对象（`tool` 输出工具原文），上游日志走 stderr。

| 命令 | 作用 |
| --- | --- |
| `doctor` | 检查 Python 版本、上游固定提交、导入与工具列表，不联网 |
| `init ...` | 创建 run 并直接返回第一批任务 |
| `step --run-dir D [--allow-freetext] [--retry]` | 收取答案、推进流程、返回下一批任务或结果 |
| `status --run-dir D` | 查看进度，不推进 |
| `tool --run-dir D --task ID NAME k=v ...` | 经该分析师的上游 `ToolNode` 执行工具，记录到 `message_tool.log` 与 `logs/tool_calls.jsonl` |

`status` 取值：

- `tasks` / `waiting`：`tasks[]` 每项含 `id`、`agent`、`tier`、`model`、`uses_tools`、`output_format`（`markdown` / `json`）、`prompt_file`、`output_file`、`subagent_prompt`，被拒或改为纯文本时另含 `error`；`parallel` 表示可同时派发。
- `done`：`outcome`（`completed` / `needs_review`）、`signal`、`result_file`、`final_decision`、`report`、`reports_dir`、`analyst_wall_time`、`llm_tasks`。
- `failed` 且含 `failed_tasks`：数据工具异常，见「工具调用」。
- `error`（退出码 2）：输入或用法错误。其他 `failed`（退出码 1）：运行异常，含 `error_type` 与 `error`。

## run 目录

```text
RUN_DIR/                         默认 结果目录/TICKER/日期
  reports/market_report.md       CLI 分节文件（按所选分析师）
  reports/sentiment_report.md
  reports/news_report.md
  reports/fundamentals_report.md
  reports/investment_plan.md
  reports/trader_investment_plan.md
  reports/final_trade_decision.md
  message_tool.log               CLI 消息与工具调用日志
  result.json                    评级、状态、各输出路径、上游版本
  state.json                     流程状态、运行配置（init 时固定，与上游单进程使用同一份配置一致）、上游 AgentState（不含 messages）
  tasks/NN-agent.prompt.md       发给子代理的任务
  tasks/NN-agent.response.md     子代理答案（被拒的改名为 .rejected-N.md，工具失败的为 .failed-N.md）
  cache/sentiment/               情绪分析师预取数据
  logs/tool_calls.jsonl          工具调用记录
  logs/tool_failures.jsonl       工具异常记录

./reports/TICKER_YYYYmmdd_HHMMSS/   报告树：complete_report.md 与 1_analysts … 5_portfolio
结果目录/TICKER/TradingAgentsStrategy_logs/full_states_log_DATE.json   上游状态日志
```

结果目录取 `DEFAULT_CONFIG.results_dir`（默认 `~/.tradingagents/logs`，可用 `TRADINGAGENTS_RESULTS_DIR` 或 `--config` 覆盖）。同一 ticker 和日期再次运行时，用 `--run-dir` 另开目录，或对原有 run 执行 `step` 继续。记忆日志默认沿用上游路径（或 `TRADINGAGENTS_MEMORY_LOG_PATH`），跨运行积累复盘经验；`--memory-log PATH` 指定其他文件，`--no-memory` 关闭。
