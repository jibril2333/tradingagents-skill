# TradingAgents Skill

把 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 转换为 Claude Code skill：上游的智能体、提示词、数据工具、辩论路由、报告和记忆日志在本地原样运行，原本发往 LLM API 的每一次调用改由 Claude Code 子代理完成，因此在 Claude 订阅额度内运行，不需要任何 LLM API key。

## 工作方式

```text
ta.py init ──► 4 个分析师任务 ──(并行子代理)──► ta.py step
          ──► Bull ⇄ Bear（上游 ConditionalLogic 决定轮次）──► Research Manager
          ──► Trader ──► Aggressive → Conservative → Neutral ──► Portfolio Manager
          ──► 上游报告目录、状态日志、记忆日志、五档评级
```

- `scripts/skill_llm.py` 替换上游传给智能体的 `llm` 对象：没有答案时把完整消息写成任务文件；子代理写回答案后，上游节点再次运行并得到与 API 调用相同形状的结果。
- 分析师的工具调用通过 `ta.py tool` 执行上游同一批数据工具（yfinance、FRED、Polymarket 等）。
- Research Manager、Trader、Portfolio Manager、Sentiment Analyst 的结构化输出用上游 Pydantic schema 校验，不合格会退回重写。
- 模型档位沿用上游：Research Manager、Portfolio Manager 用 deep（默认 `opus`），其余用 quick（默认 `sonnet`）。
- 默认一次运行 12 次子代理调用；run 目录保存全部状态，中断后可继续。

逐个调用点的对应关系和与上游的差异见 [architecture.md](skills/tradingagents/references/architecture.md)。

## 安装

作为 Claude Code 插件：

```text
/plugin marketplace add jibril2333/tradingagents-skill
/plugin install tradingagents@tradingagents-skill
```

或复制为个人 skill（默认 `~/.claude/skills/tradingagents`）：

```shell
python3 tools/install_skill.py
```

## 准备运行环境

需要 Python 3.10+（推荐 3.12）、Git 和网络，只需执行一次：

```shell
python3 skills/tradingagents/scripts/setup_runtime.py
skills/tradingagents/scripts/ta doctor
```

虚拟环境默认位于 `~/.tradingagents-skill/venv`，安装固定提交的上游包。`FRED_API_KEY`（宏观数据）和 `ALPHA_VANTAGE_API_KEY`（可选数据源）为可选项。

## 使用

在 Claude Code 中直接提出需求，例如：

> 用 TradingAgents 分析 NVDA，日期今天，输出中文报告。

skill 会依次执行 `ta init`、派发子代理、`ta step`，完成后读取 `result.json` 与最终决策并总结。输出与上游 CLI 相同：`~/.tradingagents/logs/TICKER/日期/` 下的分节报告与 `message_tool.log`，以及当前目录 `reports/TICKER_时间戳/` 下的完整报告树；另有 `propagate()` 的状态日志与记忆日志。

不带参数时的行为与上游默认一致：代码归一化、按 ticker 判定资产类型（加密货币自动去掉基本面分析师）、分析师全选、辩论与风险讨论各 1 轮、英文输出、yfinance 数据源、跨运行记忆日志。数据工具经上游 `ToolNode` 执行，报错规则与上游相同；其他上游配置项可用 `--config` 传入。常用参数：`--research-depth`（对应上游 CLI 的 1/3/5 档，同时设置两个轮数）、`--analysts`、`--quick-model`、`--deep-model`、`--language`、`--no-memory`。完整说明见 [SKILL.md](skills/tradingagents/SKILL.md) 与 [setup.md](skills/tradingagents/references/setup.md)。

## 额度

费用由 LLM API 计费变为占用订阅额度。实测一次默认运行（NVDA，4 个分析师，辩论与风险讨论各 1 轮）的子代理用量记录在 [VALIDATION.md](VALIDATION.md)。减少额度占用的方式：减少分析师、保持辩论轮数为 1、将 `--deep-model` 设为 `sonnet`。

## 验证

```shell
~/.tradingagents-skill/venv/bin/python -m unittest discover -s tests -v
```

离线测试运行真实上游代码，封锁网络并以固定答案代替子代理，覆盖完整流程顺序、结构化答案校验与回退、多轮辩论、记忆复盘和工具命令。真实运行记录见 [VALIDATION.md](VALIDATION.md)。

## 范围

独立的适配仓库，非 TauricResearch 官方产品。历史日期分析可能受当前基本面、新闻覆盖和模型知识影响，不能视作无前视偏差回测。输出是研究材料，不是投资建议；不连接券商、不下单。

采用 Apache-2.0，来源说明见 [NOTICE](NOTICE)。上游版本与升级步骤见 [upstream.md](skills/tradingagents/references/upstream.md)。
