"""Offline tests: the pinned upstream runs for real, network and models are stubbed."""

import atexit
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "tradingagents" / "scripts"))

# Upstream reads results_dir from the environment when its config module is
# first imported, so redirect it before importing anything upstream: the state
# log and the default run directory must not land in the real home directory.
_RESULTS = tempfile.TemporaryDirectory()
atexit.register(_RESULTS.cleanup)
RESULTS_DIR = Path(_RESULTS.name).resolve()
os.environ["TRADINGAGENTS_RESULTS_DIR"] = str(RESULTS_DIR)

try:
    import tradingagents  # noqa: F401

    HAVE_UPSTREAM = True
except ImportError:
    HAVE_UPSTREAM = False

import ta  # noqa: E402

STRUCTURED_ANSWERS = {
    "Sentiment Analyst": {"overall_band": "Mixed", "overall_score": 5.0, "confidence": "low",
                          "narrative": "Sentiment narrative for NVDA."},
    "Research Manager": {"recommendation": "Overweight", "rationale": "The bull case carried the debate.",
                         "strategic_actions": "Build the position gradually."},
    "Trader": {"action": "Buy", "reasoning": "The plan supports an entry.", "entry_price": 210.5,
               "stop_loss": 198.0, "position_sizing": "3% of portfolio"},
    "Portfolio Manager": {"rating": "Overweight", "executive_summary": "Add gradually.",
                          "investment_thesis": "Evidence favours upside.", "price_target": 240,
                          "time_horizon": "3-6 months"},
}
EXPECTED_ORDER = [
    "Market Analyst", "Sentiment Analyst", "News Analyst", "Fundamentals Analyst",
    "Bull Researcher", "Bear Researcher", "Research Manager", "Trader",
    "Aggressive Analyst", "Conservative Analyst", "Neutral Analyst", "Portfolio Manager",
]


def run_cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = ta.main([str(arg) for arg in argv])
    text = out.getvalue()
    return code, (json.loads(text) if text.strip().startswith("{") else text)


def answer(task, text=None):
    if text is None:
        structured = STRUCTURED_ANSWERS.get(task["agent"])
        text = json.dumps(structured) if structured else f"{task['agent']} report for NVDA."
    Path(task["output_file"]).write_text(text, encoding="utf-8")


class ParamsTest(unittest.TestCase):
    def test_values_keep_strings_and_parse_scalars(self):
        self.assertEqual(
            ta.parse_params(["symbol=NVDA", "look_back_days=30", "curr_date=2026-09-15", "flag=true", "x=[1]"]),
            {"symbol": "NVDA", "look_back_days": 30, "curr_date": "2026-09-15", "flag": True, "x": "[1]"},
        )

    def test_rejects_positional_values(self):
        with self.assertRaises(ta.UsageError):
            ta.parse_params(["NVDA"])


@unittest.skipUnless(HAVE_UPSTREAM, "pinned upstream is not installed")
class BridgeTest(unittest.TestCase):
    def test_pending_request_escapes_upstream_fallback(self):
        from skill_llm import LLMRequest, SkillLLM
        from tradingagents.agents.schemas import ResearchPlan, render_research_plan
        from tradingagents.agents.utils.structured import bind_structured, invoke_structured_or_freetext

        llm = SkillLLM()
        with self.assertRaises(LLMRequest) as ctx:
            invoke_structured_or_freetext(bind_structured(llm, ResearchPlan, "RM"), llm, "prompt",
                                          render_research_plan, "RM")
        self.assertIs(ctx.exception.schema, ResearchPlan)
        self.assertEqual(ctx.exception.messages, [("user", "prompt")])

    def test_fenced_json_and_freetext_fallback(self):
        from skill_llm import SkillLLM
        from tradingagents.agents.schemas import ResearchPlan, render_research_plan
        from tradingagents.agents.utils.structured import bind_structured, invoke_structured_or_freetext

        fenced = "```json\n" + json.dumps(STRUCTURED_ANSWERS["Research Manager"]) + "\n```"
        llm = SkillLLM(fenced)
        rendered = invoke_structured_or_freetext(bind_structured(llm, ResearchPlan, "RM"), llm, "p",
                                                 render_research_plan, "RM")
        self.assertTrue(rendered.startswith("**Recommendation**: Overweight"))

        llm = SkillLLM("plain prose")
        self.assertEqual(invoke_structured_or_freetext(bind_structured(llm, ResearchPlan, "RM"), llm, "p",
                                                       render_research_plan, "RM"), "plain prose")


@unittest.skipUnless(HAVE_UPSTREAM, "pinned upstream is not installed")
class DriverTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.memory = self.root / "memory.md"
        patches = [
            mock.patch.object(socket.socket, "connect", side_effect=OSError("network disabled in tests")),
            mock.patch("tradingagents.graph.trading_graph.resolve_instrument_identity",
                       return_value={"company_name": "NVIDIA Corporation"}),
            mock.patch("tradingagents.agents.analysts.sentiment_analyst.fetch_stocktwits_messages",
                       return_value="<unavailable>"),
            mock.patch("tradingagents.agents.analysts.sentiment_analyst.fetch_reddit_posts",
                       return_value="<unavailable>"),
            mock.patch("tradingagents.agents.analysts.sentiment_analyst.get_news",
                       SimpleNamespace(func=lambda *args: "No news in fixture.")),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def init(self, *extra):
        run_dir = self.root / "run"
        memory = () if "--no-memory" in extra else ("--memory-log", self.memory)
        save = () if "--no-save" in extra else ("--save-dir", self.root / "saved")
        code, out = run_cli("init", "--ticker", "nvda", "--date", "2026-09-15", "--run-dir", run_dir,
                            *memory, *save, *extra)
        self.assertEqual(code, 0, out)
        return run_dir, out

    def test_full_run_follows_upstream_graph(self):
        run_dir, out = self.init()
        self.assertEqual(out["status"], "tasks")
        self.assertTrue(out["parallel"])
        self.assertEqual([t["model"] for t in out["tasks"]], ["sonnet"] * 4)

        code, waiting = run_cli("step", "--run-dir", run_dir)
        self.assertEqual(waiting["status"], "waiting")
        self.assertEqual(len(waiting["tasks"]), 4)

        order = []
        while out["status"] != "done":
            for task in out["tasks"]:
                order.append(task["agent"])
                answer(task)
            code, out = run_cli("step", "--run-dir", run_dir)
            self.assertEqual(code, 0, out)

        self.assertEqual(order, EXPECTED_ORDER)
        self.assertEqual(out["signal"], "Overweight")
        self.assertEqual(out["outcome"], "completed")
        self.assertEqual(out["llm_tasks"], 12)
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        self.assertEqual((result["status"], result["signal"]), ("completed", "Overweight"))

        trader_prompt = next((run_dir / "tasks").glob("*-trader.prompt.md")).read_text(encoding="utf-8")
        self.assertIn("Technical Market Report:\nMarket Analyst report for NVDA.", trader_prompt)
        deep = [p.name for p in (run_dir / "tasks").glob("*.prompt.md")
                if "manager" in p.name]
        self.assertEqual(len(deep), 2)

        # CLI results directory: one file per report section, final values.
        reports = run_dir / "reports"
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))["graph"]
        self.assertEqual(sorted(p.name for p in reports.iterdir()), sorted(
            f"{s}.md" for s in ("market_report", "sentiment_report", "news_report", "fundamentals_report",
                                "investment_plan", "trader_investment_plan", "final_trade_decision")))
        self.assertEqual((reports / "investment_plan.md").read_text(encoding="utf-8"), state["investment_plan"])
        self.assertIn("**Rating**: Overweight", (reports / "final_trade_decision.md").read_text(encoding="utf-8"))
        self.assertEqual(out["final_decision"], str(reports / "final_trade_decision.md"))

        # CLI "Save report?" default: ./reports/TICKER_YYYYmmdd_HHMMSS via write_report_tree.
        saved = Path(out["report"])
        self.assertEqual(saved.parent.parent, (self.root / "saved").resolve())
        self.assertRegex(saved.parent.name, r"^NVDA_\d{8}_\d{6}$")
        complete = saved.read_text(encoding="utf-8")
        self.assertIn("## V. Portfolio Manager Decision", complete)
        self.assertIn("**Overall Sentiment:** **Mixed**", complete)
        self.assertIn("FINAL TRANSACTION PROPOSAL: **BUY**", complete)
        self.assertTrue((RESULTS_DIR / "NVDA" / "TradingAgentsStrategy_logs"
                         / "full_states_log_2026-09-15.json").is_file())
        self.assertIn("[2026-09-15 | NVDA | Overweight | pending]", self.memory.read_text(encoding="utf-8"))

        # CLI message_tool.log: opening System lines, graph messages, closing summary.
        log = (run_dir / "message_tool.log").read_text(encoding="utf-8").splitlines()
        bodies = [line.split(" ", 1)[1] for line in log]
        self.assertEqual(bodies[:5], [
            "[System] Selected ticker: NVDA", "[System] Analysis date: 2026-09-15",
            "[System] Selected analysts: market, social, news, fundamentals", "[User] NVDA",
            "[Agent] Market Analyst report for NVDA."])
        self.assertEqual(sum(b.startswith("[User] Proceed with your assigned analysis") for b in bodies), 4)
        self.assertIn("[Agent] **Action**: Buy", "\n".join(bodies))
        self.assertEqual(bodies[-2], "[System] Completed analysis for 2026-09-15")
        self.assertTrue(bodies[-1].startswith("[System] Analyst wall time: Market "))
        self.assertRegex(log[0], r"^\d{2}:\d{2}:\d{2} \[System\]")

        code, again = run_cli("step", "--run-dir", run_dir)
        self.assertEqual(again["status"], "done")

    def test_second_invalid_answer_takes_upstream_freetext_fallback(self):
        run_dir, out = self.init("--analysts", "social")
        task = out["tasks"][0]
        prompt = Path(task["prompt_file"])
        self.assertIn("## Output format", prompt.read_text(encoding="utf-8"))
        answer(task, "not json")
        code, out = run_cli("step", "--run-dir", run_dir)
        answer(out["tasks"][0], "still not json")
        code, out = run_cli("step", "--run-dir", run_dir)
        self.assertEqual(out["status"], "waiting")
        self.assertEqual(out["tasks"][0]["output_format"], "markdown")
        self.assertIn("free-text fallback", out["tasks"][0]["error"])
        rewritten = prompt.read_text(encoding="utf-8")
        self.assertNotIn("## Output format", rewritten)
        self.assertIn("financial market sentiment analyst", rewritten)
        answer(out["tasks"][0], "Plain sentiment report.")
        code, out = run_cli("step", "--run-dir", run_dir)
        self.assertEqual(out["tasks"][0]["agent"], "Bull Researcher")
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["graph"]["sentiment_report"], "Plain sentiment report.")

    def test_invalid_structured_answer_is_rejected_then_accepted(self):
        run_dir, out = self.init("--analysts", "social")
        (task,) = out["tasks"]
        self.assertEqual(task["output_format"], "json")
        answer(task, "not json")
        code, out = run_cli("step", "--run-dir", run_dir)
        self.assertEqual(out["status"], "waiting")
        self.assertIn("error", out["tasks"][0])
        self.assertIn("previous answer was rejected", out["tasks"][0]["subagent_prompt"])
        self.assertTrue(list((run_dir / "tasks").glob("*.rejected-1.md")))
        answer(out["tasks"][0])
        code, out = run_cli("step", "--run-dir", run_dir)
        self.assertEqual([t["agent"] for t in out["tasks"]], ["Bull Researcher"])

    def test_allow_freetext_uses_upstream_fallback(self):
        run_dir, out = self.init("--analysts", "social")
        answer(out["tasks"][0], "Free-text sentiment.")
        code, out = run_cli("step", "--run-dir", run_dir, "--allow-freetext")
        self.assertEqual(out["tasks"][0]["agent"], "Bull Researcher")
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["graph"]["sentiment_report"], "Free-text sentiment.")

    def test_debate_rounds_and_models(self):
        run_dir, out = self.init("--analysts", "market", "--debate-rounds", "2", "--risk-rounds", "2",
                                 "--deep-model", "fable", "--no-memory")
        agents = []
        while out["status"] != "done":
            for task in out["tasks"]:
                agents.append((task["agent"], task["model"]))
                answer(task)
            code, out = run_cli("step", "--run-dir", run_dir)
        names = [name for name, _ in agents]
        self.assertEqual(names.count("Bull Researcher"), 2)
        self.assertEqual(names.count("Neutral Analyst"), 2)
        self.assertIn(("Portfolio Manager", "fable"), agents)
        self.assertFalse(self.memory.exists())

    def test_pending_memory_entry_is_reflected_first(self):
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        self.memory.write_text(
            "[2026-09-01 | NVDA | Buy | pending]\n\nDECISION:\n**Rating**: Buy\n\n<!-- ENTRY_END -->\n\n",
            encoding="utf-8")
        with mock.patch.object(TradingAgentsGraph, "_fetch_returns", return_value=(0.05, 0.02, 5, "2026-09-08")):
            run_dir, out = self.init()
        (task,) = out["tasks"]
        self.assertEqual(task["agent"], "Reflection 2026-09-01")
        self.assertIn("Alpha vs SPY: +2.0%", Path(task["prompt_file"]).read_text(encoding="utf-8"))
        answer(task, "The call was right; keep sizing modest.")
        code, out = run_cli("step", "--run-dir", run_dir)
        self.assertEqual(len(out["tasks"]), 4)
        log = self.memory.read_text(encoding="utf-8")
        self.assertIn("[2026-09-01 | NVDA | Buy | +5.0% | +2.0% | 5d | resolved:2026-09-08]", log)
        self.assertIn("REFLECTION:\nThe call was right; keep sizing modest.", log)

    def test_analyst_tools_go_through_upstream_tool_node(self):
        run_dir, out = self.init("--analysts", "market", "--no-memory")
        task = out["tasks"][0]["id"]
        prompt = Path(out["tasks"][0]["prompt_file"]).read_text(encoding="utf-8")
        self.assertIn(f"tool --run-dir {run_dir} --task {task} <tool_name>", prompt)
        with mock.patch("tradingagents.agents.utils.core_stock_tools.route_to_vendor",
                        return_value="Date,Close\n2026-09-15,211.56"):
            code, text = run_cli("tool", "--run-dir", run_dir, "--task", task, "get_stock_data",
                                 "symbol=NVDA", "start_date=2026-09-01", "end_date=2026-09-15")
        self.assertEqual((code, text.strip()), (0, "Date,Close\n2026-09-15,211.56"))

        # A tool outside this analyst's ToolNode and a bad argument come back as
        # error messages for the model, exactly as ToolNode returns them.
        code, text = run_cli("tool", "--run-dir", run_dir, "--task", task, "get_news", "ticker=NVDA")
        self.assertEqual(code, 0)
        self.assertIn("Error: get_news is not a valid tool", text)
        code, text = run_cli("tool", "--run-dir", run_dir, "--task", task, "get_indicators", "symbol=NVDA")
        self.assertIn("Error invoking tool 'get_indicators'", text)

        log = (run_dir / "message_tool.log").read_text(encoding="utf-8")
        self.assertIn("[Tool Call] get_stock_data(symbol=NVDA, start_date=2026-09-01, end_date=2026-09-15)", log)
        self.assertIn("[Data] Date,Close 2026-09-15,211.56", log)

    def test_vendor_exception_fails_the_run_until_retried(self):
        run_dir, out = self.init("--analysts", "market", "--no-memory")
        task = out["tasks"][0]
        with mock.patch("tradingagents.agents.utils.core_stock_tools.route_to_vendor",
                        side_effect=RuntimeError("vendor down")):
            code, text = run_cli("tool", "--run-dir", run_dir, "--task", task["id"], "get_stock_data",
                                 "symbol=NVDA", "start_date=2026-09-01", "end_date=2026-09-15")
        self.assertEqual(code, 1)
        self.assertIn("RUN_FAILED: RuntimeError: vendor down", text)
        answer(task)
        code, out = run_cli("step", "--run-dir", run_dir)
        self.assertEqual((out["status"], out["failed_tasks"]), ("failed", [task["id"]]))
        code, out = run_cli("step", "--run-dir", run_dir, "--retry")
        self.assertEqual((out["status"], out["tasks"][0]["id"]), ("waiting", task["id"]))
        self.assertTrue(list((run_dir / "tasks").glob("*.failed-1.md")))
        answer(out["tasks"][0])
        code, out = run_cli("step", "--run-dir", run_dir)
        self.assertEqual(out["tasks"][0]["agent"], "Bull Researcher")

    def test_config_file_merges_like_python_api(self):
        config = self.root / "config.json"
        config.write_text(json.dumps({
            "results_dir": str(self.root / "results"), "max_debate_rounds": 2, "output_language": "Chinese",
            "news_article_limit": 5, "tool_vendors": {"get_news": "alpha_vantage"},
            "data_vendors": {"macro_data": "fred"},
        }), encoding="utf-8")
        code, out = run_cli("init", "--ticker", "NVDA", "--date", "2026-09-15", "--no-memory", "--no-save",
                            "--analysts", "market", "--config", config, "--data-vendor", "alpha_vantage")
        self.assertEqual(code, 0, out)
        run_dir = Path(out["run_dir"])
        self.assertEqual(run_dir, self.root / "results" / "NVDA" / "2026-09-15")
        st = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual((st["run"]["debate_rounds"], st["run"]["risk_rounds"], st["run"]["language"]),
                         (2, 1, "Chinese"))
        u = ta.load_upstream()
        merged = ta.build_config(u, st["config_overrides"])
        self.assertEqual(merged["news_article_limit"], 5)
        self.assertEqual(merged["tool_vendors"], {"get_news": "alpha_vantage"})
        self.assertEqual(merged["data_vendors"]["core_stock_apis"], "alpha_vantage")
        self.assertEqual(merged["data_vendors"]["macro_data"], "fred")
        self.assertEqual(merged["data_vendors"]["prediction_markets"], "polymarket")

    def test_cli_input_rules(self):
        code, out = run_cli("init", "--date", "2026-09-15", "--no-memory", "--no-save",
                            "--analysts", "news", "market", "--run-dir", self.root / "spy")
        self.assertEqual(code, 0, out)
        state = json.loads((self.root / "spy" / "state.json").read_text(encoding="utf-8"))["run"]
        self.assertEqual(state["ticker"], "SPY")
        self.assertEqual(state["analysts"], ["market", "news"])
        self.assertIsNone(state["save_dir"])

    def test_tool_command_runs_upstream_tool(self):
        from langchain_core.tools import tool

        @tool
        def get_stock_data(symbol: str, start_date: str, end_date: str) -> str:
            """Fixture tool."""
            return f"{symbol} {start_date} {end_date}"

        run_dir, _ = self.init("--analysts", "market")
        with mock.patch.object(ta, "all_tools", return_value={"get_stock_data": get_stock_data}):
            code, out = run_cli("tool", "--run-dir", run_dir, "get_stock_data", "symbol=NVDA",
                                "start_date=2026-09-01", "end_date=2026-09-15")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.strip(), "NVDA 2026-09-01 2026-09-15")
        self.assertIn('"tool": "get_stock_data"', (run_dir / "logs" / "tool_calls.jsonl").read_text())

    def test_crypto_ticker_follows_upstream_cli_defaults(self):
        code, out = run_cli("init", "--ticker", "btcusd", "--date", "2026-09-15", "--no-memory")
        self.assertEqual(code, 0, out)
        run_dir = Path(out["run_dir"])
        # Upstream normalises the symbol, detects crypto from the canonical
        # form, drops the fundamentals analyst, and writes to RESULTS/TICKER/DATE.
        self.assertEqual(run_dir, RESULTS_DIR / "BTC-USD" / "2026-09-15")
        self.assertEqual([t["agent"] for t in out["tasks"]],
                         ["Market Analyst", "Sentiment Analyst", "News Analyst"])
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))["run"]
        self.assertEqual((state["ticker"], state["asset_type"]), ("BTC-USD", "crypto"))
        self.assertEqual(state["dropped_analysts"], ["fundamentals"])
        self.assertTrue(state["asset_type_detected"])

        code, again = run_cli("init", "--ticker", "BTC-USD", "--date", "2026-09-15", "--no-memory")
        self.assertEqual((code, again["status"]), (2, "error"))

    def test_research_depth_sets_both_round_counts(self):
        run_dir, out = self.init("--analysts", "market", "--research-depth", "3", "--no-memory")
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual((state["run"]["debate_rounds"], state["run"]["risk_rounds"]), (3, 3))
        self.assertEqual(state["config_overrides"]["max_debate_rounds"], 3)
        # An explicit flag still wins over the depth setting.
        code, out = run_cli("init", "--ticker", "NVDA", "--date", "2026-09-15", "--no-memory",
                            "--research-depth", "3", "--risk-rounds", "1",
                            "--run-dir", self.root / "depth")
        state = json.loads((self.root / "depth" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual((state["run"]["debate_rounds"], state["run"]["risk_rounds"]), (3, 1))

    def test_defaults_match_upstream_config(self):
        run_dir, _ = self.init("--analysts", "market", "--no-memory")
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        from tradingagents.default_config import DEFAULT_CONFIG

        self.assertEqual(state["run"]["debate_rounds"], DEFAULT_CONFIG["max_debate_rounds"])
        self.assertEqual(state["run"]["risk_rounds"], DEFAULT_CONFIG["max_risk_discuss_rounds"])
        self.assertEqual(state["run"]["language"], DEFAULT_CONFIG["output_language"])
        self.assertEqual(state["run"]["asset_type"], "stock")
        # Only knobs this skill must control are overridden.
        self.assertEqual(set(state["config_overrides"]) - {"memory_log_path"},
                         {"output_language", "max_debate_rounds", "max_risk_discuss_rounds",
                          "checkpoint_enabled", "llm_provider", "deep_think_llm", "quick_think_llm"})

    def test_rejects_bad_inputs(self):
        code, out = run_cli("init", "--ticker", "../etc", "--run-dir", self.root / "x")
        self.assertEqual((code, out["status"]), (2, "error"))
        code, out = run_cli("init", "--ticker", "NVDA", "--date", "2999-01-01", "--run-dir", self.root / "y")
        self.assertEqual((code, out["status"]), (2, "error"))


if __name__ == "__main__":
    unittest.main()
