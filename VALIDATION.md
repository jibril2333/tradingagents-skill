# Validation record

Latest update: 2026-09-19 (JST). Host: macOS, Python 3.12.14, Claude Code.

## Offline checks

| Check | Result |
| --- | --- |
| `setup_runtime.py` into a fresh virtual environment | Passed; TradingAgents 0.4.0, commit be952b8eccb49720509af544c6675233bc1f10d0; `pip check` clean |
| `ta.py doctor` | `ready: true`; 12 upstream data tools importable |
| `python -m unittest discover -s tests -v` | 26 tests passed locally and in CI (Ubuntu and Windows, Python 3.10 and 3.12) |
| `claude plugin validate --strict .claude-plugin/plugin.json` | Passed |
| `claude plugin validate .claude-plugin/marketplace.json` | Passed |

The tests import the real upstream package and block socket connections.

### Equivalence with upstream

`UpstreamEquivalenceTest` runs the real `TradingAgentsGraph.propagate()` with a
scripted chat model, then runs the skill with the same answers. Each case
requires the same model prompts in the same order, the same final state and
signal, identical state-log JSON, an identical decision log, and an identical
report tree apart from the generation timestamp.

| Case | Covers |
| --- | --- |
| Default run | Four analysts, one debate and one risk round |
| Deep run | `--research-depth 3` with two analysts given out of order (CLI reorders them) |
| Crypto run | CLI input `btcusd` normalised to `BTC-USD`, crypto detected, fundamentals analyst dropped |
| Memory run | A pending same-ticker decision is reflected on first; cross-ticker lessons reach the Portfolio Manager prompt |

Other tests cover the CLI outputs (section files, `message_tool.log`, report
tree location), ToolNode error handling, vendor exceptions and `--retry`, the
structured-output correction and free-text fallback, `--config` merging, and
the run config staying fixed across processes.

## Live runs

### NVDA, 2026-09-16

One end-to-end run driven from Claude Code with real subagents, live yfinance,
StockTwits, Reddit and Polymarket data, and no LLM API key in the environment.

- Request: `init --ticker NVDA --date 2026-09-15 --language Chinese --no-memory`, all four analysts, one debate round, one risk round.
- Result: `outcome: completed`, `signal: Hold`, 12 tasks, full upstream report tree and state log written (output layout of that version, before the CLI-parity changes).
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

### BTC-USD, 2026-09-19

Second end-to-end run after the CLI-parity changes, started from CLI-style
input: `init --ticker btcusd --language Chinese`, all other options default.
Results, the saved report tree and the decision log were redirected to a
scratch directory.

- The ticker was normalised to `BTC-USD`, detected as crypto, and the fundamentals analyst was dropped, as the upstream CLI does.
- Result: `outcome: completed`, `signal: Hold`, 11 tasks.
- 20 tool calls went through the analysts' upstream ToolNodes, all successful.
- Outputs matched the CLI layout: six section files and `message_tool.log` under `RESULTS/BTC-USD/2026-09-19/`, the report tree under `reports/BTC-USD_20260919_041207/`, the state log under `RESULTS/BTC-USD/TradingAgentsStrategy_logs/`, and a pending decision-log entry.
- Every structured answer passed schema validation on the first attempt.

| Task | Model | Subagent tokens | Duration |
| --- | --- | ---: | ---: |
| Market Analyst | sonnet | 94,145 | 175 s |
| Sentiment Analyst | sonnet | 79,423 | 137 s |
| News Analyst | sonnet | 76,626 | 119 s |
| Bull Researcher | sonnet | 76,180 | 56 s |
| Bear Researcher | sonnet | 82,708 | 55 s |
| Research Manager | opus | 65,709 | 62 s |
| Trader | sonnet | 68,272 | 25 s |
| Aggressive Analyst | sonnet | 77,726 | 71 s |
| Conservative Analyst | sonnet | 80,726 | 51 s |
| Neutral Analyst | sonnet | 87,950 | 60 s |
| Portfolio Manager | opus | 75,691 | 110 s |
| Total | | 865,156 | about 16 min wall time |

Subagent token counts are as reported by Claude Code and include each
subagent's own system context. How they map to subscription limits depends on
the plan. The report content is model-generated research, not investment advice.

Selected installed dependencies: langchain-core 1.6.3, langgraph 1.2.11,
pydantic 2.13.5, yfinance 1.7.0, pandas 3.0.5. This is a record, not a lockfile.
