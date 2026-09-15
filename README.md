# TradingAgents Skill

把 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 封装成可安装的 Codex skill，通过 Python 调用实际投研流程，生成 Markdown 报告与 JSON 结果。

## 能做什么

- 编排市场、情绪、新闻、基本面分析，以及多空辩论和风险评估。
- 支持股票和上游的 crypto 模式；可选择分析师、模型、语言及辩论轮数。
- 提供不调用 API 的 `doctor` 和 `--dry-run`。
- 固定上游源码版本，每次运行隔离缓存、日志、记忆和结果。
- 保存五档评级以及 `REVIEW` 状态；不连接券商或提交订单。

## 安装 skill

仓库内 `skills/tradingagents/` 是完整、自包含的 skill。需要 Python 3.10+，推荐 3.12；正式运行还需要 Git、网络和所选 LLM 服务配置。

在目标项目中安装：

```shell
python tools/install_skill.py --dest /path/to/project/.agents/skills
```

Windows PowerShell 示例：

```powershell
python tools/install_skill.py --dest 'C:\path\to\project\.agents\skills'
```

也可以手动把整个 `skills/tradingagents` 目录复制到你的 skill 搜索目录。当前官方文档列出用户级 `~/.agents/skills`；已有 Codex 配置采用其他目录时，传入该目录即可。安装器不会覆盖已有 skill。

安装方式依据 [OpenAI 官方 skill 文档](https://learn.chatgpt.com/docs/build-skills)。在支持技能选择的界面选择 TradingAgents，或使用 `$tradingagents`。

示例请求：

> 用 $tradingagents 分析 NVDA，使用我配置的模型，分析日期为今天，输出中文报告。先检查运行配置。

## 准备运行环境

以下命令在本仓库根目录执行。复制安装后，也可用已安装 skill 中对应脚本的绝对路径。

```shell
python skills/tradingagents/scripts/setup_runtime.py --env-dir .venv
```

虚拟环境解释器：Windows 为 `.venv/Scripts/python.exe`；macOS/Linux 为 `.venv/bin/python`。下文的 `PYTHON` 替换为该路径。

```text
PYTHON skills/tradingagents/scripts/run_analysis.py doctor --provider openai
PYTHON skills/tradingagents/scripts/run_analysis.py analyze --ticker NVDA --date 2026-09-14 --provider openai --deep-model YOUR_DEEP_MODEL --quick-model YOUR_QUICK_MODEL --language Chinese --output-dir outputs/nvda-2026-09-14 --dry-run
```

`YOUR_DEEP_MODEL` 和 `YOUR_QUICK_MODEL` 必须替换为你的账户可用、支持工具调用的模型。预览不需要密钥、不创建文件。正式研究时配置对应环境变量并去掉 `--dry-run`；模型及数据服务可能产生费用。

详细配置、供应商表、输出结构和常见故障见 [setup.md](skills/tradingagents/references/setup.md)。

## 兼容性与验证

- 固定上游提交：[`be952b8eccb49720509af544c6675233bc1f10d0`](https://github.com/TauricResearch/TradingAgents/tree/be952b8eccb49720509af544c6675233bc1f10d0)，其包版本为 `0.4.0`。
- 固定源码不等于所有间接依赖都锁定；安装器遵循上游依赖范围，成功结果记录实际安装版本。
- `doctor` 校验 pip 的 VCS 来源与提交，避免误用同名 PyPI 包或不同版本。
- 离线测试验证参数、评级保留、输出隔离、失败记录和安装行为，不代表真实模型或行情服务已通过端到端测试。

```shell
python -m unittest discover -s tests -v
```

可选安装环境后的离线接口检查：

```text
PYTHON tools/smoke_upstream.py
```

它构建真实上游 graph、验证入口签名及报告导出，并封锁 socket 网络连接，不请求模型或行情。

## 范围

这是独立的 skill 适配仓库，非 TauricResearch 官方产品。历史日期分析仍可能受当前基本面、新闻覆盖和模型知识影响，不能直接视作无前视偏差回测。输出是研究材料。

本仓库采用 Apache-2.0，来源说明见 [NOTICE](NOTICE)。上游源码在安装时获取，不在本仓库重复分发。维护与升级说明见 [upstream.md](skills/tradingagents/references/upstream.md)。
