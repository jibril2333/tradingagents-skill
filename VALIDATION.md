# Validation record

Date: 2026-09-15. Host: Windows, Python 3.12.9.

| Check | Result |
| --- | --- |
| Skill Creator `quick_validate.py skills/tradingagents` | Passed |
| `python -m unittest discover -s tests -v` | 11 tests passed |
| Pinned upstream installation in an isolated virtual environment | Passed; TradingAgents 0.4.0, commit be952b8eccb49720509af544c6675233bc1f10d0 |
| `python -m pip check` in that environment | No broken requirements |
| `python tools/smoke_upstream.py` in that environment | Passed; real graph construction, propagate signature, real Markdown export, five-tier/REVIEW parsing |
| `doctor --provider ollama` | Pinned VCS source verified; this does not probe an Ollama server |

The smoke test uses placeholder credentials and prevents Python socket connections.
It does not call `propagate` or any paid model/data service. Its sample report is a
fixture, not an investment analysis. The upstream client warned that the smoke
test's example model IDs were not in its known-model list, then constructed the
graph successfully; model availability was not tested.

Live provider authentication, live market data, paid inference and an end-to-end
research run have not been tested. The GitHub Actions workflow has been authored
for Windows/Linux and Python 3.10/3.12; those remote jobs have not been run here.

Selected installed dependencies in the validated environment:

- langchain-core 1.6.3
- langchain-openai 1.6.2
- langgraph 1.2.11
- openai 3.14.0
- yfinance 1.7.0
- pandas 3.0.5

This is a validation record, not a complete dependency lockfile.
