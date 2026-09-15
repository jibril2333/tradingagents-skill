# 环境、参数与故障处理

## 环境准备

使用 Python 3.10+（推荐 3.12）及 Git。在可写工作区运行：

```text
python SKILL_DIR/scripts/setup_runtime.py --env-dir WORKSPACE/.venv-tradingagents
```

脚本创建虚拟环境，从 requirements.txt 安装固定上游提交并执行 `pip check`。可重用现有虚拟环境，但不要指向含其他项目的环境；不会修改全局 Python。网络或权限失败时保留错误，不改换未核实的软件源。

后续命令始终使用该环境的解释器：Windows 为 `ENV/Scripts/python.exe`；macOS/Linux 为 `ENV/bin/python`。

## 供应商与密钥

本封装提供下列供应商，其余上游供应商需要另行扩展适配器。

| `--provider` | 环境变量 | 说明 |
| --- | --- | --- |
| `openai` | `OPENAI_API_KEY` | 使用账户可用的模型 |
| `anthropic` | `ANTHROPIC_API_KEY` | 同上 |
| `google` | `GOOGLE_API_KEY` | 采用上游规范变量名 |
| `deepseek` | `DEEPSEEK_API_KEY` | 工具调用能力取决于模型 |
| `openrouter` | `OPENROUTER_API_KEY` | 传入路由服务的完整模型标识 |
| `ollama` | 无 | 必须给出 `--backend-url`，采用上游 OpenAI 兼容 `/v1` 接口 |
| `openai_compatible` | `OPENAI_COMPATIBLE_API_KEY`（按服务需要） | 必须给出 `--backend-url`；本地无密钥服务可留空 |

凭证通过进程环境提供，不作为命令行参数、不写入仓库。脚本不自动加载 `.env`；若用户使用 `.env`，由其现有环境管理方式加载。不要要求把密钥贴进对话。

行情默认 `yfinance`。`--data-vendor alpha_vantage` 将四类股票数据入口切换到 Alpha Vantage，并检查 `ALPHA_VANTAGE_API_KEY`。宏观工具仍采用上游 FRED 配置，需要 `FRED_API_KEY`；缺失时上游可返回数据不可用提示。预测市场工具沿用上游 Polymarket。crypto 分支还可能调用其他公开数据源，不代表所有工具都被该选项重定向。

密钥存在不代表模型权限或额度可用。`doctor` 是离线检查，不验证网络、模型可用性或完整导入。

## 参数

```text
PYTHON SKILL_DIR/scripts/run_analysis.py analyze --help
```

必需：`--ticker`、`--date`、`--output-dir`，以及两个模型（可通过环境提供）。

- `--provider`：默认读取 `TRADINGAGENTS_LLM_PROVIDER`，否则为 `openai`。
- `--deep-model` / `--quick-model`：覆盖 `TRADINGAGENTS_DEEP_THINK_LLM` / `TRADINGAGENTS_QUICK_THINK_LLM`。
- `--backend-url`：覆盖 `TRADINGAGENTS_LLM_BACKEND_URL`。只使用用户已选择的端点；禁止在 URL 中夹带凭证、查询参数或片段。
- `--language`：覆盖 `TRADINGAGENTS_OUTPUT_LANGUAGE`，否则为 English；中文用 `Chinese`。
- `--analysts market social news fundamentals`：可选择其子集；上游 crypto 路由会适配分析师。
- `--debate-rounds` / `--risk-rounds`：默认各 1，正整数。越多通常越慢且消耗更多额度。
- `--max-tokens`：可选，限制单次模型输出；不是整次研究总 token 或费用上限。
- `--asset-type stock|crypto`：默认 stock；crypto 必须明确指定。
- `--dry-run`：只输出配置预览，不校验安装、不导入上游、不调用服务、不写文件。

本封装固定每个 LLM 客户端最多 1 次 SDK 重试，禁用 checkpoint，并使用新目录隔离研究记忆。其他上游默认值及 provider 专用环境设置沿用安装版本。不要以为 `request.json` 的 overrides 是完整配置快照。

## 输出

```text
OUTPUT_DIR/
  request.json                 请求与覆盖配置，不含密钥
  result.json                  成功后的原始评级、状态、报告路径、依赖版本
  failure.json                 运行异常时的错误类型及排查提示
  run.log                      上游控制台输出
  reports/complete_report.md   上游整合报告
  reports/final_decision.md    原始最终决策（独立保留）
  reports/1_analysts/...       其他章节按上游输出生成
  logs/                       上游状态日志
  cache/                      数据缓存
  memory/                     本次运行研究记忆
```

已有输出目录会被拒绝，避免覆盖报告或混用记忆。`REVIEW` 或未知评级返回 `needs_review`，脚本仍可成功导出报告；调用方须检查 JSON 状态，不能只看退出码。

退出码：`0` 为检查通过、预览成功或报告导出完成；`2` 为参数/配置/依赖检查未通过；`1` 为真实运行异常。

## 排错

| 现象 | 下一步 |
| --- | --- |
| revision 不匹配或缺依赖 | 用选定虚拟环境重新执行 setup_runtime.py；不要删掉固定提交绕过检查 |
| 缺少环境变量 | 在实际启动该脚本的进程环境配置对应变量 |
| 身份验证/模型不可用 | 检查该供应商凭证和模型 ID，不切换到另一服务 |
| 429/额度不足 | 遵循供应商限制，停止无边界重试 |
| 日志显示数据缺失 | 如实标注，检查供应商或标的支持范围 |
| 日期/交易所含糊 | 核对标的身份和交易日；脚本验证日期格式，不验证交易所日历 |
| 导入错误 | 执行 `PYTHON -m pip check` 和 `PYTHON tools/smoke_upstream.py`（后者在仓库内） |

分享日志前检查是否包含凭证或私人研究内容。失败文件只记录异常类型，避免把供应商认证 URL 写进结构化结果。
