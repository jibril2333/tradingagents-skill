# Validation record

Date: 2026-09-16 (JST). Host: macOS, Python 3.12.14, Claude Code.

## Offline checks

| Check | Result |
| --- | --- |
| `setup_runtime.py` into a fresh virtual environment | Passed; TradingAgents 0.4.0, commit be952b8eccb49720509af544c6675233bc1f10d0; `pip check` clean |
| `ta.py doctor` | `ready: true`; 12 upstream data tools importable |
| `python -m unittest discover -s tests -v` | 11 tests passed |
| `claude plugin validate --strict .claude-plugin/plugin.json` | Passed |
| `claude plugin validate .claude-plugin/marketplace.json` | Passed |

The offline tests import the real upstream package, block socket connections and
replace subagents with fixed answers. They cover the full node order, parallel
analyst tasks, schema rejection and resubmission, the free-text fallback,
multi-round debates, model tier selection, memory reflection and the tool
command. They do not measure research quality.

## Live run

One end-to-end run driven from Claude Code with real subagents, live yfinance,
StockTwits, Reddit and Polymarket data, and no LLM API key in the environment.

- Request: `init --ticker NVDA --date 2026-09-15 --language Chinese --no-memory`, all four analysts, one debate round, one risk round.
- Result: `outcome: completed`, `signal: Hold`, 12 tasks, full upstream report tree, `final_decision.md` and state log written.
- Analysts made 24 tool calls through `ta.py tool`, all successful. Reddit returned HTTP 429 for one subreddit; the upstream fetcher degraded to a placeholder as designed.
- Every structured answer (sentiment, research manager, trader, portfolio manager) passed schema validation on the first attempt.
- The trader's entry and stop levels matched values from `get_verified_market_snapshot` (50 SMA 213.01, lower Bollinger band 205.83).

| Task | Model | Subagent tokens | Duration |
| --- | --- | ---: | ---: |
| Market Analyst | sonnet | 82,359 | 102 s |
| Sentiment Analyst | sonnet | 89,140 | 200 s |
| News Analyst | sonnet | 75,825 | 82 s |
| Fundamentals Analyst | sonnet | 80,177 | 106 s |
| Bull Researcher | sonnet | 83,211 | 61 s |
| Bear Researcher | sonnet | 91,784 | 86 s |
| Research Manager | opus | 66,740 | 58 s |
| Trader | sonnet | 69,413 | 16 s |
| Aggressive Analyst | sonnet | 85,117 | 79 s |
| Conservative Analyst | sonnet | 88,732 | 64 s |
| Neutral Analyst | sonnet | 95,121 | 64 s |
| Portfolio Manager | opus | 69,773 | 49 s |
| Total | | 977,392 | about 12 min wall time |

Subagent token counts are as reported by Claude Code and include each
subagent's own system context. How they map to subscription limits depends on
the plan. The report content is model-generated research, not investment advice.

Selected installed dependencies: langchain-core 1.6.3, langgraph 1.2.11,
pydantic 2.13.5, yfinance 1.7.0, pandas 3.0.5. This is a record, not a lockfile.
