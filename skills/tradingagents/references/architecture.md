# 架构说明

## 转换点

上游在 `TradingAgentsGraph.__init__` 创建两个 provider 客户端（quick / deep），再把 `llm` 对象传给各个智能体工厂。本 skill 用 `scripts/skill_llm.py` 的 `SkillLLM` 替换这个对象，其余代码不改。

| 上游调用位置 | 上游行为 | 本 skill |
| --- | --- | --- |
| `market/news/fundamentals_analyst.py`：`prompt \| llm.bind_tools(tools)` | 模型多轮发起 tool call，由 LangGraph `ToolNode` 执行 | 任务文件包含完整消息与工具说明；子代理用 `ta.py tool` 调用同一批上游工具，多轮后写最终报告 |
| `sentiment_analyst.py`：`with_structured_output(SentimentReport)` | 预取新闻/StockTwits/Reddit，结构化输出 | 预取逻辑照常执行；子代理输出 JSON，用同一 Pydantic schema 校验，再由上游 `render_sentiment_report` 渲染 |
| `bull/bear_researcher.py`、三个风险辩手：`llm.invoke(prompt)` | 自由文本 | 子代理写自由文本，上游节点拼接辩论历史与计数 |
| `research_manager.py`、`trader.py`、`portfolio_manager.py`：`invoke_structured_or_freetext` | 结构化输出，失败时回退自由文本 | JSON 校验失败时退回给子代理重写；`--allow-freetext` 走上游回退分支 |
| `reflection.py`：`reflect_on_final_decision` | 对已有收益结果的历史决策做复盘 | 复盘任务，答案经上游 `Reflector` 与 `batch_update_with_outcomes` 写回记忆日志 |

`SkillLLM` 无答案时抛出 `LLMRequest`（继承 `BaseException`，不会被上游的 `except Exception` 回退吞掉），驱动据此写任务文件。答案就绪后同一节点再运行一次，`SkillLLM` 返回答案，节点产出与上游一致的状态更新。

## 执行顺序

LangGraph 编排被 `ta.py` 的步进循环替代，以便在每次模型调用处暂停、跨进程恢复并并行派发。拓扑取自上游：

- 分析师：按 `build_analyst_execution_plan` 生成，**并行**执行。上游串行只是因为 LangGraph 中各分析师共用一条消息列表；每位分析师只读取公共输入、写自己的报告字段，结果互不依赖。首位分析师的输入消息为 ticker，其余为上游 `create_msg_delete` 的占位消息，与上游一致。
- 研究辩论与风险辩论：每步之后调用上游 `ConditionalLogic.should_continue_debate` / `should_continue_risk_analysis` 决定下一个节点。
- 固定边：Research Manager → Trader → Aggressive Analyst；Portfolio Manager → 结束。
- 运行开始：上游 `_resolve_pending_entries`（复盘）、`get_past_context`、`resolve_instrument_context`、`Propagator.create_initial_state`。
- 运行结束：上游 `write_report_tree`、`_log_state`、`TradingMemoryLog.store_decision`、`SignalProcessor.process_signal`。

模型档位与上游 `GraphSetup` 相同：Research Manager、Portfolio Manager 为 deep，其余为 quick。

## 默认值

不带参数时与上游保持一致，来源如下：

| 项目 | 默认值 | 来源 |
| --- | --- | --- |
| 分析师 | 全选四位 | 上游 `selected_analysts` 默认值；加密货币按 `filter_analysts_for_asset_type` 去掉基本面 |
| 辩论 / 风险讨论轮数 | 各 1 轮 | `DEFAULT_CONFIG`；`--research-depth` 对应 CLI 的深度档位，同时设置两者，`TRADINGAGENTS_MAX_*_ROUNDS` 优先 |
| 代码归一化与资产类型 | `normalize_ticker_symbol` + `detect_asset_type` | 上游 `cli.utils`，与 CLI 完全相同 |
| 输出语言 | English | `DEFAULT_CONFIG` |
| 数据源 | yfinance（宏观 FRED、预测市场 Polymarket） | `DEFAULT_CONFIG.data_vendors` |
| 记忆日志 | `~/.tradingagents/memory/trading_memory.md` | `DEFAULT_CONFIG` |
| 缓存 | `~/.tradingagents/cache` | `DEFAULT_CONFIG` |
| run 目录 | `结果目录/TICKER/日期`，报告在其下 | 上游 CLI 的 `results_dir/TICKER/DATE/reports` |
| 状态日志 | `结果目录/TICKER/TradingAgentsStrategy_logs/` | 上游 `_log_state` |
| checkpoint | 关闭 | `DEFAULT_CONFIG` |

本 skill 只覆盖这些配置项：`output_language`、两个轮数、`checkpoint_enabled`、`llm_provider`、`deep_think_llm`、`quick_think_llm`，以及显式指定时的 `memory_log_path` 与 `data_vendors`。provider 相关参数（temperature、max_tokens、reasoning effort、重试次数）不适用，保持上游默认。

## 与上游的差异

- 模型由子代理承担，不经过 provider SDK；`temperature`、`max_tokens`、reasoning effort 等 provider 参数不适用。
- 结构化输出从 provider 原生模式改为「JSON + 同一 schema 校验」。
- 分析师并行执行；不使用 LangGraph checkpoint，run 目录本身可恢复。
- 分析师的工具调用轮次由子代理决定，没有 `max_recur_limit`。
- 情绪分析师的预取数据缓存在 run 目录，节点两次运行看到同一份数据。

## 命令与 JSON

所有命令向 stdout 输出一个 JSON 对象（`tool` 输出工具原文），上游日志走 stderr。

| 命令 | 作用 |
| --- | --- |
| `doctor` | 检查 Python 版本、上游固定提交、导入与工具列表，不联网 |
| `init ...` | 创建 run 并直接返回第一批任务 |
| `step --run-dir D [--allow-freetext]` | 收取答案、推进流程、返回下一批任务或结果 |
| `status --run-dir D` | 查看进度，不推进 |
| `tool --run-dir D NAME k=v ...` | 以该 run 的配置执行上游数据工具，调用记录写入 `logs/tool_calls.jsonl` |

`status` 取值：

- `tasks` / `waiting`：`tasks[]` 每项含 `id`、`agent`、`tier`、`model`、`uses_tools`、`output_format`（`markdown` / `json`）、`prompt_file`、`output_file`、`subagent_prompt`，被拒时另含 `error`；`parallel` 表示可同时派发。
- `done`：`outcome`（`completed` / `needs_review`）、`signal`、`result_file`、`final_decision`、`report`、`llm_tasks`。
- `error`（退出码 2）：输入或用法错误。`failed`（退出码 1）：运行异常，含 `error_type` 与 `error`。

## run 目录

```text
RUN_DIR/
  state.json                     流程状态、配置覆盖、上游 AgentState（不含 messages）
  tasks/NN-agent.prompt.md       发给子代理的任务
  tasks/NN-agent.response.md     子代理答案（被拒的改名为 .rejected-N.md）
  cache/sentiment/               情绪分析师预取数据
  logs/tool_calls.jsonl          分析师工具调用记录
  reports/complete_report.md     上游整合报告
  reports/1_analysts ... 5_portfolio   上游分节报告
  reports/final_decision.md      Portfolio Manager 决策原文
  result.json                    评级、状态、路径、上游版本
```

默认 run 目录为 `结果目录/TICKER/日期`（结果目录取 `DEFAULT_CONFIG.results_dir`，默认 `~/.tradingagents/logs`，可用 `TRADINGAGENTS_RESULTS_DIR` 覆盖），与上游 CLI 的报告位置相同；同一 ticker 和日期重跑时用 `--run-dir` 另开目录，或直接 `step` 继续原有 run。记忆日志默认沿用上游 `~/.tradingagents/memory/trading_memory.md`（或 `TRADINGAGENTS_MEMORY_LOG_PATH`），跨运行积累复盘经验；`--memory-log PATH` 指定其他文件，`--no-memory` 关闭。
