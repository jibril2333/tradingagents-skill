import contextlib
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "tradingagents" / "scripts" / "run_analysis.py"
spec = importlib.util.spec_from_file_location("adapter", SCRIPT)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class FakeGraph:
    signal = "Overweight"
    last_config = None

    def __init__(self, selected_analysts, debug, config):
        self.__class__.last_config = config

    def propagate(self, ticker, trade_date, asset_type="stock"):
        return {"final_trade_decision": "研究决策：保留原始评级"}, self.signal

    def save_reports(self, state, ticker, save_path):
        save_path.mkdir()
        report = save_path / "complete_report.md"
        report.write_text("# 离线测试报告\n", encoding="utf-8")
        return report


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "research"

    def arguments(self, *extra):
        return ["analyze", "--ticker", "NVDA", "--date", "2025-01-15",
                "--provider", "openai", "--deep-model", "test-deep",
                "--quick-model", "test-quick", "--output-dir", str(self.out), *extra]

    def plan(self, *extra):
        with patch.dict(os.environ, {}, clear=True):
            return adapter.make_plan(adapter.build_parser().parse_args(self.arguments(*extra)))

    def test_dry_run_has_no_import_dependency_or_side_effects(self):
        # -S disables site-packages, including any locally installed TradingAgents.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("TRADINGAGENTS_") and not k.endswith("API_KEY")}
        process = subprocess.run([sys.executable, "-S", str(SCRIPT), *self.arguments("--dry-run")],
                                 capture_output=True, text=True, env=env, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["missing_credentials"], ["OPENAI_API_KEY"])
        self.assertFalse(self.out.exists())

    def test_rejects_invalid_symbols_dates_and_secret_endpoints(self):
        cases = [("--ticker", "../private"), ("--ticker", "A/B"),
                 ("--date", "2025-02-30"), ("--date", "20250115"),
                 ("--date", (date.today() + timedelta(days=1)).isoformat()),
                 ("--backend-url", "https://user:secret@example.com/v1"),
                 ("--backend-url", "https://example.com/v1?key=secret")]
        for option, value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.plan(option, value)

    def test_exchange_and_crypto_tickers(self):
        for ticker in ("7203.T", "0700.HK", "BRK-B", "BTC-USD", "^GSPC", "GC=F"):
            with self.subTest(ticker=ticker):
                self.assertEqual(self.plan("--ticker", ticker)["ticker"], ticker)
        self.assertEqual(self.plan("--ticker", "BTC-USD", "--asset-type", "crypto")["asset_type"], "crypto")

    def test_real_run_missing_keys_fails_before_writing(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(adapter, "installed_source", return_value={"verified_revision": True}):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                adapter.main(self.arguments())
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.out.exists())

    def test_preserves_defaults_and_isolates_all_paths(self):
        original = {"data_vendors": {"macro_data": "fred"}, "checkpoint_enabled": True}
        before = deepcopy(original)
        result = adapter.execute(self.plan(), FakeGraph, original, {"verified_revision": True})
        self.assertEqual(original, before)
        self.assertEqual(FakeGraph.last_config["data_vendors"]["macro_data"], "fred")
        self.assertFalse(FakeGraph.last_config["checkpoint_enabled"])
        for key in ("results_dir", "data_cache_dir", "memory_log_path"):
            self.assertTrue(Path(FakeGraph.last_config[key]).is_relative_to(self.out))
        self.assertEqual(result["signal"], "Overweight")
        self.assertEqual(result["status"], "completed")
        self.assertIn("研究决策", Path(result["final_decision"]).read_text(encoding="utf-8"))
        self.assertTrue((self.out / "result.json").is_file())

    def test_review_and_unknown_rating_are_never_coerced_to_hold(self):
        for i, signal in enumerate(("REVIEW", "unparseable rating")):
            with self.subTest(signal=signal):
                plan = self.plan("--output-dir", str(Path(self.tmp.name) / str(i)))
                class ReviewGraph(FakeGraph):
                    pass
                ReviewGraph.signal = signal
                result = adapter.execute(plan, ReviewGraph, {}, {})
                self.assertEqual(result["signal"], signal)
                self.assertEqual(result["status"], "needs_review")

    def test_existing_output_is_not_overwritten(self):
        self.out.mkdir()
        marker = self.out / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            adapter.execute(self.plan(), FakeGraph, {}, {})
        self.assertEqual(marker.read_text(), "keep")
        self.assertEqual(list(self.out.iterdir()), [marker])

    def test_failure_is_recorded_without_provider_secret(self):
        class BrokenGraph(FakeGraph):
            def propagate(self, *args, **kwargs):
                raise RuntimeError("https://api.example.com?key=secret-test-value")
        with self.assertRaises(RuntimeError):
            adapter.execute(self.plan(), BrokenGraph, {}, {})
        failure = (self.out / "failure.json").read_text()
        self.assertNotIn("secret-test-value", failure)
        self.assertEqual(json.loads(failure)["status"], "failed")
        self.assertFalse((self.out / "result.json").exists())

    def test_doctor_does_not_echo_credentials(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret-test-value"}, clear=True):
            with patch.object(adapter, "installed_source", return_value={"verified_revision": True}):
                with contextlib.redirect_stdout(output):
                    status = adapter.main(["doctor", "--provider", "openai"])
        self.assertEqual(status, 0)
        self.assertNotIn("secret-test-value", output.getvalue())
        self.assertTrue(json.loads(output.getvalue())["credentials_present"]["OPENAI_API_KEY"])

    def test_upstream_requires_matching_commit_and_repository(self):
        class Distribution:
            version = "0.4.0"
            url = adapter.UPSTREAM_URL + ".git"
            commit = adapter.UPSTREAM_COMMIT
            def read_text(self, name):
                return json.dumps({"url": self.url, "vcs_info": {"commit_id": self.commit}})
        dist = Distribution()
        with patch.object(adapter.metadata, "distribution", return_value=dist):
            self.assertTrue(adapter.installed_source()["verified_revision"])
            dist.commit = "0" * 40
            self.assertFalse(adapter.installed_source()["verified_revision"])
            dist.commit = adapter.UPSTREAM_COMMIT
            dist.url = "https://example.com/unrelated.git"
            self.assertFalse(adapter.installed_source()["verified_revision"])

    def test_installer_copies_self_contained_skill_and_refuses_overwrite(self):
        command = [sys.executable, str(ROOT / "tools" / "install_skill.py"), "--dest", self.tmp.name]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        installed = Path(self.tmp.name) / "tradingagents"
        self.assertTrue((installed / "SKILL.md").is_file())
        self.assertTrue((installed / "scripts" / "requirements.txt").is_file())
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(second.returncode, 2)


if __name__ == "__main__":
    unittest.main()
