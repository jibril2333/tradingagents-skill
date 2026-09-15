---
name: tradingagents
description: Run the TauricResearch TradingAgents multi-agent research workflow (market, sentiment, news and fundamentals analysts, bull/bear debate, trader, risk debate, portfolio manager) for a stock or crypto ticker, with subagents answering every LLM step instead of a paid model API. Use when asked for TradingAgents, 多智能体投研, or a TradingAgents research report or rating. Does not place orders.
---

# TradingAgents

上游 TradingAgents 的智能体代码、提示词、数据工具、辩论路由、评级解析、报告与记忆日志在本地 Python 中原样运行。上游原本调用 LLM API 的每一步被转换成任务文件，由子代理作答。你是编排者：运行驱动脚本、派发子代理、交付结果。不需要任何 LLM API key。

下文 `TA` 指 `SKILL_DIR/scripts/ta`（Windows 为 `SKILL_DIR\scripts\ta.cmd`），`SKILL_DIR` 是本文件所在目录的绝对路径。

## 准备

1. 执行 `TA doctor`，`ready` 为 `true` 即可继续。
2. 输出 `setup_required` 或 `ready: false` 时，用 Python 3.10+ 执行 `python3 SKILL_DIR/scripts/setup_runtime.py`（默认安装到 `~/.tradingagents-skill/venv`，需要网络，只需一次），然后重新 doctor。细节见 [环境与故障](references/setup.md)。

## 明确输入

- ticker 使用数据源代码（`NVDA`、`7203.T`、`BTC-USD`）。只给公司名或交易所含糊时先查证。代码按上游规则归一化（`BTCUSD` → `BTC-USD`，`XAUUSD` → `GC=F`）。
- 日期 `YYYY-MM-DD`，默认今天，不接受未来日期。历史日期不等于无前视偏差的回测；分析历史日期时，上游会拒绝提供市值、市盈率等只有当前口径的字段，报告据实说明即可。
- 资产类型按 ticker 自动判定，与上游 CLI 一致；加密货币自动去掉基本面分析师。需要覆盖时用 `--asset-type`。
- 报告语言跟随用户：中文对话传 `--language Chinese`。
- 其余保持默认，除非用户提出：分析师全选、辩论与风险讨论各 1 轮（上游默认值；`--research-depth` 同时设置两者，上游 CLI 的档位是 1、3、5）、`--quick-model sonnet`、`--deep-model opus`（Research Manager 与 Portfolio Manager 使用 deep）、记忆日志开启（`--no-memory` 关闭）。

## 运行循环

1. `TA init --ticker NVDA --date YYYY-MM-DD --language Chinese`，得到 JSON，记下 `run_dir`（默认与上游 CLI 相同：结果目录下的 `TICKER/日期`）。
2. 当 `status` 为 `tasks` 或 `waiting`，为 `tasks` 中每一项派发一个子代理：
   - Agent 工具，`subagent_type: general-purpose`，`model` 用该项的 `model`，`description` 用该项的 `agent`，`prompt` 原样使用 `subagent_prompt`。
   - `parallel` 为 `true` 时，在同一条消息中派发全部任务。
   - 不要自己读取 `prompt_file` 或答案文件，它们很长；子代理回复 `done` 即表示已写入。
3. 本轮子代理全部结束后执行 `TA step --run-dir RUN_DIR`，回到第 2 步。
   - `waiting` 且任务带 `error`：答案未通过上游 schema 校验，已被移到 `.rejected-N.md`；按返回的新 `subagent_prompt` 重新派发该项。同一任务被拒 3 次后，改用 `TA step --run-dir RUN_DIR --allow-freetext`，等同上游的自由文本回退。
   - `waiting` 且无 `error`：答案文件缺失，重新派发该项。
4. `status` 为 `done` 时结束循环；`error` 或 `failed` 时报告错误原因，修正后再 `step`，不要反复重跑。

默认一次运行 12 个任务：4 个分析师并行，之后按上游顺序串行（Bull、Bear、Research Manager、Trader、Aggressive、Conservative、Neutral、Portfolio Manager）。记忆日志里有待复盘的同一 ticker 历史决策时，会先出现复盘任务。run 目录保存全部状态，中断后用 `TA status --run-dir RUN_DIR` 查看、`TA step --run-dir RUN_DIR` 继续。

当前环境没有子代理工具时，自己逐项完成：读取 `prompt_file`，按其中规则把答案写入 `output_file`，再执行 `step`。

分析师子代理通过 Bash 调用任务文件里打印的 `ta.py tool` 命令取数。权限确认过多时，可建议用户在 Claude Code 权限设置中允许该命令前缀。

## 解读并交付

- 读取 `RUN_DIR/result.json` 和 `reports/final_decision.md`；需要细节时再读 `reports/complete_report.md` 的相关章节。
- 保留原始评级 `Buy / Overweight / Hold / Underweight / Sell`。`status: needs_review`（信号 `REVIEW`）表示未解析出评级，不得改成 Hold 或买卖信号。
- 简述标的、分析日期、评级、多空依据、风险、缺失数据，并给出报告路径。只引用报告中的真实数据；报告未给来源时明确说明，不补造。
- `historical_data_warning` 为 `true` 时指出新闻、基本面和模型知识可能包含分析日之后的信息。
- 结论是模型生成的研究材料，不是投资建议、已验证收益或已执行交易。

协议字段、目录结构和与上游的对应关系见 [架构说明](references/architecture.md)。
