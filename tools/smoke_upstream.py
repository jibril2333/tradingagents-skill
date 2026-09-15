"""Offline checks against the installed real upstream graph; no LLM requests."""

from copy import deepcopy
import inspect
import os
from pathlib import Path
import socket
import tempfile
from unittest.mock import patch


def blocked(*args, **kwargs):
    raise AssertionError("Network access is forbidden in this offline smoke test")


def main():
    scratch = Path(__file__).resolve().parents[1] / "work"
    scratch.mkdir(exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("TRADINGAGENTS_") and not key.endswith("API_KEY")}
    env["OPENAI_API_KEY"] = "offline-smoke-placeholder"
    with tempfile.TemporaryDirectory(dir=scratch) as temp:
        with patch.dict(os.environ, env, clear=True), patch.object(socket.socket, "connect", blocked), patch.object(socket, "create_connection", blocked):
            from tradingagents.default_config import DEFAULT_CONFIG
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            config = deepcopy(DEFAULT_CONFIG)
            config.update({"llm_provider": "openai", "backend_url": None,
                           "deep_think_llm": "gpt-4.1", "quick_think_llm": "gpt-4.1-mini",
                           "results_dir": str(Path(temp) / "logs"),
                           "data_cache_dir": str(Path(temp) / "cache"),
                           "memory_log_path": str(Path(temp) / "memory" / "memory.md"),
                           "checkpoint_enabled": False})
            graph = TradingAgentsGraph(selected_analysts=["market", "news"], config=config, debug=False)
            inspect.signature(graph.propagate).bind("NVDA", "2025-01-15", asset_type="stock")
            path = graph.save_reports({"market_report": "Offline smoke fixture", "news_report": "Fixture only"},
                                      "NVDA", save_path=Path(temp) / "reports")
            assert Path(path).is_file()
            assert "Offline smoke fixture" in Path(path).read_text(encoding="utf-8")
            assert graph.process_signal("Rating: Overweight") == "Overweight"
            assert graph.process_signal("No parseable rating.") == "REVIEW"
    print("PASS: real graph construction, propagate signature, report export, and rating parsing (offline)")


if __name__ == "__main__":
    main()
