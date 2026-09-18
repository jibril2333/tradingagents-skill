# 环境与故障处理

## 运行环境

需要 Python 3.10+（推荐 3.12）、Git 和网络。

```text
python3 SKILL_DIR/scripts/setup_runtime.py
```

默认在 `~/.tradingagents-skill/venv` 创建虚拟环境，从 `requirements.txt` 安装固定提交的上游 TradingAgents，并执行 `pip check`。`--env-dir PATH` 可指定其他位置；此时设置环境变量 `TRADINGAGENTS_SKILL_PYTHON` 指向该环境的解释器，`scripts/ta` 会使用它。脚本不修改全局 Python。

上游依赖中包含 OpenAI、Anthropic、Google 等 SDK，这是上游包的安装要求；本 skill 不创建这些客户端，也不读取 LLM API key。

## 数据源

| 数据 | 默认来源 | 密钥 |
| --- | --- | --- |
| 行情、技术指标、基本面、新闻 | yfinance | 无 |
| 宏观指标 `get_macro_indicators` | FRED | `FRED_API_KEY`，缺失时工具返回不可用提示，流程继续 |
| 预测市场 `get_prediction_markets` | Polymarket | 无 |
| 情绪 | Yahoo 新闻、StockTwits、Reddit | 无 |

`init --data-vendor alpha_vantage` 将行情、指标、基本面、新闻切换到 Alpha Vantage，需要 `ALPHA_VANTAGE_API_KEY`。密钥只从进程环境读取，不要写进对话或仓库。

## 权限

分析师子代理通过 Bash 执行任务文件中打印的命令，例如：

```text
/Users/NAME/.tradingagents-skill/venv/bin/python /PATH/skills/tradingagents/scripts/ta.py tool --run-dir RUN_DIR --task 01-market-analyst get_stock_data symbol=NVDA ...
```

每次调用都需确认时，可在 Claude Code 的权限设置中允许以 `.../venv/bin/python .../scripts/ta.py tool` 开头的 Bash 命令。

## 排错

| 现象 | 处理 |
| --- | --- |
| `setup_required` | 运行 setup_runtime.py，或设置 `TRADINGAGENTS_SKILL_PYTHON` |
| doctor 显示 `verified_revision: false` | 用同一解释器重新执行 setup_runtime.py，不要改用 PyPI 上的同名包 |
| 某任务反复 `waiting` 且无 `error` | 子代理没有写入 `output_file`；重新派发，确认子代理有写文件权限 |
| 结构化任务被拒 | 第一次按 `error` 改正；第二次自动改为纯文本作答（上游回退）。`step --allow-freetext` 可直接接受当前答案 |
| 工具返回 `NO_DATA_AVAILABLE` / `DATA_UNAVAILABLE` | 标的代码、交易所后缀或日期不被数据源覆盖；报告应如实标注 |
| Reddit 429 | 上游会退避重试一次后给出占位文本，情绪报告的置信度会降低 |
| `failed` 且有 `failed_tasks` | 数据工具抛出异常，与上游中止运行的情况相同；确认数据源可用后 `step --retry` 重跑受影响任务 |
| 其他 `failed` | 查看 `error_type`/`error`；修正后对同一 run 执行 `step`，已完成的任务不会重做 |
| 同一 ticker 同一日期重新开始 | 默认目录已存在时用新的 `--run-dir`，或对原 run 执行 `step` 继续 |
