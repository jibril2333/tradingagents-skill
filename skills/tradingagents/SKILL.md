---
name: tradingagents
description: Run TauricResearch TradingAgents for multi-agent stock or crypto research, analyst reports, bull/bear debate, and risk review. Use when asked to use TradingAgents, 多智能体投研, or generate a TradingAgents research report. Also use to configure or troubleshoot this integration. Requires local Python and a configured LLM provider; does not execute brokerage orders.
---

# TradingAgents

通过实际运行上游 TradingAgents 生成研究报告。遵循用户指定的标的、日期、模型和语言。

## 明确输入

- 确认可被数据源识别的 ticker。公司名称或交易所含糊时先查证。
- 日期使用 `YYYY-MM-DD`。用户要求“今天”时从当前日期解析并在结果中写明。历史日期不等于无前视偏差的回测。
- 使用用户已配置的 provider、deep model 和 quick model；未配置模型时询问型号，不擅自切换供应商或套餐。模型需支持上游工具调用。
- 股票使用 `--asset-type stock`；加密货币研究明确传入 `--asset-type crypto`，例如数据源支持的 `BTC-USD`。
- 输出目录使用任务工作区中的新目录。每次运行隔离缓存和记忆；本封装不恢复旧检查点。

## 运行

把 `SKILL_DIR` 替换为当前 SKILL.md 所在目录的绝对路径；`PYTHON` 是准备好的虚拟环境解释器。

1. 首次使用或缺少依赖时，读取 [环境准备](references/setup.md)。
2. 执行 `PYTHON SKILL_DIR/scripts/run_analysis.py doctor --provider PROVIDER`。它只检查依赖来源和环境变量是否存在，不请求模型、不打印密钥。
3. 使用 `analyze ... --dry-run` 检查有效参数。预览不需要 API key，也不创建输出目录。
4. 用户要求实际研究且运行条件具备时，执行去掉 `--dry-run` 的命令。运行调用模型与行情服务，按供应商规则消耗额度；遵循已有授权，不额外增加确认步骤。

```text
PYTHON SKILL_DIR/scripts/run_analysis.py analyze --ticker NVDA --date YYYY-MM-DD --provider PROVIDER --deep-model DEEP_MODEL --quick-model QUICK_MODEL --language Chinese --output-dir WORKSPACE/outputs/nvda-research --dry-run
```

模型也可从 `TRADINGAGENTS_DEEP_THINK_LLM` 和 `TRADINGAGENTS_QUICK_THINK_LLM` 读取。参数与故障处理见 [环境准备](references/setup.md)；API 和兼容范围见 [上游说明](references/upstream.md)。

## 解读并交付

- 读取 `result.json`、`reports/complete_report.md` 和 `reports/final_decision.md`，再总结实际输出。
- 保留原始评级 `Buy / Overweight / Hold / Underweight / Sell`。`REVIEW` 或未知值表示需要复核，不得改成 Hold 或买卖信号。
- 简述标的、分析日期、评级、多空依据、风险、缺失数据，并链接报告。引用报告中的真实来源；报告没有来源时明确说明，不补造引用。
- 历史分析指出新闻、基本面及模型知识可能包含后来信息。研究结论是模型生成的分析，不是已验证收益或已执行交易。
- 失败时检查 `failure.json` 和 `run.log`；修正具体原因后用新目录重试。不要持续重跑消耗额度，也不要把失败或预览说成研究成功。
- 对安装和配置问题只执行必要检查，不顺带启动付费分析。
