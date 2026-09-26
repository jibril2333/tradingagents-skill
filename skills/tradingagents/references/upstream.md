# 上游依赖与升级

核对日期：2026-09-16。

- 项目：[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
- 固定提交：`be952b8eccb49720509af544c6675233bc1f10d0`（包版本 `0.4.0`，Python `>=3.10`）
- 许可证：Apache-2.0。上游源码在安装时获取，本仓库不重复分发。

## 直接使用的上游接口

| 模块 | 用途 |
| --- | --- |
| `tradingagents.agents.create_*` | 全部 12 个智能体节点，原样调用 |
| `agents.schemas` | 结构化输出 schema 与 `render_*` |
| `agents.utils.agent_utils.create_msg_delete` | 非首位分析师的占位消息 |
| `agents.utils.memory.TradingMemoryLog` | 记忆日志读写 |
| `graph.analyst_execution.build_analyst_execution_plan` | 分析师选择与节点名 |
| `graph.conditional_logic.ConditionalLogic` | 辩论与风险讨论路由 |
| `graph.propagation.Propagator.create_initial_state` | 初始状态 |
| `graph.reflection.Reflector` | 复盘提示与调用 |
| `graph.signal_processing.SignalProcessor` | 五档评级或 `REVIEW` |
| `graph.trading_graph.TradingAgentsGraph` | 以 `__new__` 创建不含 LLM 客户端的实例，调用 `_create_tool_nodes`、`_resolve_benchmark`、`_fetch_returns`、`_memory_as_of`、`resolve_instrument_context`、`_log_state` |
| `reporting.write_report_tree` | 报告目录 |
| `dataflows.config.set_config` | 语言、数据源、目录配置 |
| `dataflows.utils.safe_ticker_component` | 目录名中的代码校验 |
| `cli.utils.normalize_ticker_symbol` / `detect_asset_type` / `filter_analysts_for_asset_type`、`cli.models` | 运行前的代码归一化、资产类型判定与分析师过滤，与上游 CLI 相同 |

未使用：`llm_clients`（被 `SkillLLM` 取代）、LangGraph 图编译与 checkpoint（被 `ta.py` 步进循环取代）、`cli` 的交互式问答与终端展示。

## 升级

1. 在新提交上核对上表接口，尤其是 `GraphSetup.setup_graph` 的节点、模型档位与边，以及各智能体调用 `llm` 的方式（`invoke`、`with_structured_output`、`bind_tools`）。
2. 同步 `ta.py` 中的 `NODE_TIERS`、`next_node` 和 `UPSTREAM_COMMIT`，以及 `requirements.txt`。
3. 运行 `python -m unittest discover -s tests -v` 与 `ta.py doctor`，再做一次真实子代理运行并更新 VALIDATION.md。
