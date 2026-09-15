"""Offline tests: the pinned upstream runs for real, network and models are stubbed."""

import contextlib
import io
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills" / "tradingagents" / "scripts"))

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
        self.root = Path(tmp.name)
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
        code, out = run_cli("init", "--ticker", "nvda", "--date", "2026-09-15", "--run-dir", run_dir,
                            *memory, *extra)
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

        reports = run_dir / "reports"
        self.assertIn("**Rating**: Overweight", (reports / "final_decision.md").read_text(encoding="utf-8"))
        complete = (reports / "complete_report.md").read_text(encoding="utf-8")
        self.assertIn("## V. Portfolio Manager Decision", complete)
        self.assertIn("**Overall Sentiment:** **Mixed**", complete)
        self.assertIn("FINAL TRANSACTION PROPOSAL: **BUY**", complete)
        self.assertTrue(list((run_dir / "logs").rglob("full_states_log_2026-09-15.json")))
        self.assertIn("[2026-09-15 | NVDA | Overweight | pending]", self.memory.read_text(encoding="utf-8"))

        code, again = run_cli("step", "--run-dir", run_dir)
        self.assertEqual(again["status"], "done")

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
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "NVDA 2026-09-01 2026-09-15")
        self.assertIn('"tool": "get_stock_data"', (run_dir / "logs" / "tool_calls.jsonl").read_text())

    def test_rejects_bad_inputs(self):
        code, out = run_cli("init", "--ticker", "../etc", "--run-dir", self.root / "x")
        self.assertEqual((code, out["status"]), (2, "error"))
        code, out = run_cli("init", "--ticker", "NVDA", "--date", "2999-01-01", "--run-dir", self.root / "y")
        self.assertEqual((code, out["status"]), (2, "error"))


if __name__ == "__main__":
    unittest.main()
