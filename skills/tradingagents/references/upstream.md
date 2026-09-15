# 上游接口与维护

核对日期：2026-09-15。

- 项目：[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
- 固定提交：`be952b8eccb49720509af544c6675233bc1f10d0`
- 该提交的 `pyproject.toml`：版本 `0.4.0`，Python `>=3.10`。
- 许可证：Apache-2.0。适配器通过安装依赖调用上游，不复制其多智能体实现。

## 实际使用的 API

```python
from copy import deepcopy
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = deepcopy(DEFAULT_CONFIG)
# 在构造之前设置 provider、两个模型、输出路径及其他必要配置。
graph = TradingAgentsGraph(selected_analysts=["market", "news"], debug=False, config=config)
state, signal = graph.propagate("NVDA", "2026-09-14", asset_type="stock")
report_path = graph.save_reports(state, "NVDA", save_path="reports")
```

本封装在调用前深复制配置，合并 `data_vendors`，并覆盖三个默认用户目录路径。每次使用新的输出目录，防止上游记忆反思机制跨研究意外复用。

评级是五档字符串或 `REVIEW`；不是旧示例中的三档大写 BUY/HOLD/SELL。`save_reports` 不保证包含 `final_trade_decision` 字段，因此封装单独导出原始决策。

## 证据位置

- [Graph、propagate、save_reports](https://github.com/TauricResearch/TradingAgents/blob/be952b8eccb49720509af544c6675233bc1f10d0/tradingagents/graph/trading_graph.py)
- [默认配置与环境覆盖](https://github.com/TauricResearch/TradingAgents/blob/be952b8eccb49720509af544c6675233bc1f10d0/tradingagents/default_config.py)
- [API key 对照表](https://github.com/TauricResearch/TradingAgents/blob/be952b8eccb49720509af544c6675233bc1f10d0/tradingagents/llm_clients/api_key_env.py)
- [评级解析](https://github.com/TauricResearch/TradingAgents/blob/be952b8eccb49720509af544c6675233bc1f10d0/tradingagents/agents/utils/rating.py)
- [报告导出](https://github.com/TauricResearch/TradingAgents/blob/be952b8eccb49720509af544c6675233bc1f10d0/tradingagents/reporting.py)

## 升级

用户要求升级时，先核对目标提交的上述接口及依赖，再同时更新 requirements.txt 和 run_analysis.py 中的提交常量及文档。重新运行离线测试、skill 验证器和真实上游接口 smoke test。模型/数据服务的端到端验证需单独记录，不能用 mock 结果代替。

仅固定顶层 Git 提交；间接依赖遵循上游范围。需要严格依赖复现的团队可在自己的平台生成并维护完整锁文件。
