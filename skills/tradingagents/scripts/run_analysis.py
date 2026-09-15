"""Headless TradingAgents adapter. Preflight and dry-run never call an API."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import date, datetime, timezone
from importlib import metadata
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

UPSTREAM_COMMIT = "be952b8eccb49720509af544c6675233bc1f10d0"
UPSTREAM_URL = "https://github.com/TauricResearch/TradingAgents"
PROVIDERS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "ollama": None,
    "openai_compatible": None,
}
ANALYSTS = ("market", "social", "news", "fundamentals")
RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")


def emit(value):
    print(json.dumps(value, ensure_ascii=True, indent=2))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def installed_source():
    try:
        dist = metadata.distribution("tradingagents")
    except metadata.PackageNotFoundError:
        return {"installed": False, "verified_revision": False}
    try:
        direct = json.loads(dist.read_text("direct_url.json") or "{}")
    except (ValueError, TypeError):
        direct = {}
    commit = direct.get("vcs_info", {}).get("commit_id")
    source_url = direct.get("url", "").removesuffix(".git").lower()
    return {
        "installed": True, "version": dist.version, "commit": commit,
        "verified_revision": commit == UPSTREAM_COMMIT and source_url == UPSTREAM_URL.lower(),
    }


def required_credentials(provider, vendor="yfinance"):
    names = [PROVIDERS[provider]] if PROVIDERS[provider] else []
    if vendor == "alpha_vantage":
        names.append("ALPHA_VANTAGE_API_KEY")
    return names


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--provider", choices=PROVIDERS,
                        default=os.getenv("TRADINGAGENTS_LLM_PROVIDER") or "openai")
    common.add_argument("--data-vendor", choices=("yfinance", "alpha_vantage"), default="yfinance")
    commands.add_parser("doctor", parents=[common], help="Check installation and key presence")
    run = commands.add_parser("analyze", parents=[common], help="Run one instrument/date")
    run.add_argument("--ticker", required=True)
    run.add_argument("--date", required=True)
    run.add_argument("--asset-type", choices=("stock", "crypto"), default="stock")
    run.add_argument("--analysts", nargs="+", choices=ANALYSTS, default=list(ANALYSTS))
    run.add_argument("--deep-model", default=os.getenv("TRADINGAGENTS_DEEP_THINK_LLM"))
    run.add_argument("--quick-model", default=os.getenv("TRADINGAGENTS_QUICK_THINK_LLM"))
    run.add_argument("--backend-url", default=os.getenv("TRADINGAGENTS_LLM_BACKEND_URL"))
    run.add_argument("--language", default=os.getenv("TRADINGAGENTS_OUTPUT_LANGUAGE") or "English")
    run.add_argument("--debate-rounds", type=positive, default=1)
    run.add_argument("--risk-rounds", type=positive, default=1)
    run.add_argument("--max-tokens", type=positive)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--dry-run", action="store_true")
    return parser


def make_plan(args):
    if args.provider not in PROVIDERS:
        raise ValueError("Unsupported provider; pass a supported --provider.")
    ticker = args.ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9^][A-Z0-9.^=\-]{0,39}", ticker) or ".." in ticker:
        raise ValueError("Use a data-provider ticker, not a name, path, or URL.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
        raise ValueError("Date must have YYYY-MM-DD format.")
    requested_date = date.fromisoformat(args.date)
    if requested_date > date.today():
        raise ValueError("Future analysis dates are not supported.")
    if not args.deep_model or not args.quick_model:
        raise ValueError("Set --deep-model and --quick-model, or their TRADINGAGENTS_* env vars.")
    if args.backend_url:
        url = urlsplit(args.backend_url)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError("Backend URL must be an http(s) endpoint.")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("Backend URL must not contain credentials, query parameters, or fragments.")
    if args.provider in ("ollama", "openai_compatible") and not args.backend_url:
        raise ValueError("Specify --backend-url for the chosen local/compatible endpoint.")
    output = args.output_dir.expanduser().resolve()
    config = {
        "llm_provider": args.provider, "deep_think_llm": args.deep_model,
        "quick_think_llm": args.quick_model, "backend_url": args.backend_url,
        "output_language": args.language, "max_debate_rounds": args.debate_rounds,
        "max_risk_discuss_rounds": args.risk_rounds,
        "max_recur_limit": max(100, 20 * (args.debate_rounds + args.risk_rounds)),
        "llm_max_retries": 1, "checkpoint_enabled": False,
        "results_dir": str(output / "logs"), "data_cache_dir": str(output / "cache"),
        "memory_log_path": str(output / "memory" / "trading_memory.md"),
        "data_vendors": {key: args.data_vendor for key in
                         ("core_stock_apis", "technical_indicators", "fundamental_data", "news_data")},
    }
    if args.max_tokens is not None:
        config["max_tokens"] = args.max_tokens
    missing = [name for name in required_credentials(args.provider, args.data_vendor)
               if not os.getenv(name)]
    return {
        "ticker": ticker, "analysis_date": args.date, "asset_type": args.asset_type,
        "analysts": list(dict.fromkeys(args.analysts)), "output_dir": str(output),
        "config_overrides": config, "missing_credentials": missing,
        "historical": requested_date < date.today(), "expected_upstream_commit": UPSTREAM_COMMIT,
    }


def execute(plan, graph_class, defaults, source):
    """The real graph and the offline test double use the same public contract."""
    output = Path(plan["output_dir"])
    output.mkdir(parents=True, exist_ok=False)
    config = deepcopy(defaults)
    overrides = deepcopy(plan["config_overrides"])
    config.setdefault("data_vendors", {}).update(overrides.pop("data_vendors"))
    config.update(overrides)
    write_json(output / "request.json", plan)
    try:
        with (output / "run.log").open("w", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                graph = graph_class(selected_analysts=plan["analysts"], debug=False, config=config)
                state, raw_signal = graph.propagate(plan["ticker"], plan["analysis_date"],
                                                   asset_type=plan["asset_type"])
                report = graph.save_reports(state, plan["ticker"], save_path=output / "reports")
        signal = str(raw_signal)
        decision = state.get("final_trade_decision", "")
        (output / "reports" / "final_decision.md").write_text(str(decision), encoding="utf-8")
        result = {
            "status": "needs_review" if signal not in RATINGS else "completed",
            "signal": signal, "ticker": plan["ticker"], "analysis_date": plan["analysis_date"],
            "asset_type": plan["asset_type"], "generated_at": datetime.now(timezone.utc).isoformat(),
            "upstream": source, "config_overrides": plan["config_overrides"],
            "report": str(report), "final_decision": str(output / "reports" / "final_decision.md"),
            "historical_data_warning": plan["historical"],
            "dependencies": sorted({f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions()
                                    if d.metadata.get("Name")}),
        }
        write_json(output / "result.json", result)
        return result
    except Exception as exc:
        write_json(output / "failure.json", {
            "status": "failed", "error_type": type(exc).__name__,
            "hint": "Inspect run.log locally; check provider credentials, models, quotas and data access.",
        })
        raise


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.provider not in PROVIDERS:
        parser.error("Unsupported provider in environment; pass --provider explicitly.")
    if args.command == "doctor":
        source = installed_source()
        keys = {name: bool(os.getenv(name)) for name in required_credentials(args.provider, args.data_vendor)}
        ready = source["verified_revision"] and all(keys.values())
        emit({"preflight_ready": ready, "upstream": source, "credentials_present": keys,
              "fred_key_present": bool(os.getenv("FRED_API_KEY")),
              "note": "Offline check only; model access, imports and live data are not verified."})
        return 0 if ready else 2
    try:
        plan = make_plan(args)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.dry_run:
        emit({"status": "dry_run", **plan})
        return 0
    source = installed_source()
    if not source["verified_revision"]:
        parser.error("Install the pinned upstream with setup_runtime.py using this Python interpreter.")
    if plan["missing_credentials"]:
        parser.error("Missing environment variables: " + ", ".join(plan["missing_credentials"]))
    if Path(plan["output_dir"]).exists():
        parser.error("Output directory already exists; choose a new run directory.")
    try:
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        result = execute(plan, TradingAgentsGraph, DEFAULT_CONFIG, source)
    except Exception as exc:
        emit({"status": "failed", "error_type": type(exc).__name__, "output_dir": plan["output_dir"],
              "hint": "Inspect run.log/failure.json if present; otherwise check runtime imports/config."})
        return 1
    emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
