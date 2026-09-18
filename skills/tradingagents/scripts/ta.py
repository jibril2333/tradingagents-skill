"""TradingAgents driver for agent skills.

Upstream agents, prompts, data tools, routing logic, rating parser, report
writer and memory log run unchanged in this process. Every point where upstream
calls an LLM becomes a task file answered by a host subagent (for example a
Claude Code subagent on a subscription), so no provider API key is used.

Commands print one JSON object to stdout; see references/protocol.md.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_mirror  # noqa: E402

UPSTREAM_COMMIT = "be952b8eccb49720509af544c6675233bc1f10d0"
UPSTREAM_URL = "https://github.com/TauricResearch/TradingAgents"
SCRIPT = Path(__file__).resolve()
STATE_FILE = "state.json"
ANALYSTS = ("market", "social", "news", "fundamentals")
RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
DEFAULT_MODELS = {"quick": "sonnet", "deep": "opus"}

# Same model tier per node as upstream GraphSetup.setup_graph.
NODE_TIERS = {
    "Market Analyst": "quick",
    "Sentiment Analyst": "quick",
    "News Analyst": "quick",
    "Fundamentals Analyst": "quick",
    "Bull Researcher": "quick",
    "Bear Researcher": "quick",
    "Research Manager": "deep",
    "Trader": "quick",
    "Aggressive Analyst": "quick",
    "Conservative Analyst": "quick",
    "Neutral Analyst": "quick",
    "Portfolio Manager": "deep",
}
DEBATE_NODES = ("Bull Researcher", "Bear Researcher")
RISK_NODES = ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst")


# --------------------------------------------------------------------------
# Upstream access
# --------------------------------------------------------------------------


def load_upstream():
    # cli.utils holds the symbol/asset-type behaviour the upstream CLI applies
    # before a run; reusing it keeps this skill's defaults identical.
    from cli.models import AnalystType, AssetType
    from cli.utils import (
        ANALYST_ORDER,
        detect_asset_type,
        filter_analysts_for_asset_type,
        is_valid_ticker_input,
        normalize_ticker_symbol,
    )
    from tradingagents import agents
    from tradingagents.agents import schemas
    from tradingagents.dataflows.utils import safe_ticker_component
    from tradingagents.agents.utils.agent_utils import create_msg_delete
    from tradingagents.agents.utils.memory import TradingMemoryLog
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.analyst_execution import build_analyst_execution_plan
    from tradingagents.graph.conditional_logic import ConditionalLogic
    from tradingagents.graph.propagation import Propagator
    from tradingagents.graph.reflection import Reflector
    from tradingagents.graph.signal_processing import SignalProcessor
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.reporting import write_report_tree

    factories = {
        "Market Analyst": agents.create_market_analyst,
        "Sentiment Analyst": agents.create_sentiment_analyst,
        "News Analyst": agents.create_news_analyst,
        "Fundamentals Analyst": agents.create_fundamentals_analyst,
        "Bull Researcher": agents.create_bull_researcher,
        "Bear Researcher": agents.create_bear_researcher,
        "Research Manager": agents.create_research_manager,
        "Trader": agents.create_trader,
        "Aggressive Analyst": agents.create_aggressive_debator,
        "Conservative Analyst": agents.create_conservative_debator,
        "Neutral Analyst": agents.create_neutral_debator,
        "Portfolio Manager": agents.create_portfolio_manager,
    }
    return SimpleNamespace(
        AnalystType=AnalystType, AssetType=AssetType, detect_asset_type=detect_asset_type,
        filter_analysts_for_asset_type=filter_analysts_for_asset_type,
        normalize_ticker_symbol=normalize_ticker_symbol, safe_ticker_component=safe_ticker_component,
        is_valid_ticker_input=is_valid_ticker_input, ANALYST_ORDER=ANALYST_ORDER,
        factories=factories, schemas=schemas, create_msg_delete=create_msg_delete,
        TradingMemoryLog=TradingMemoryLog, set_config=set_config, DEFAULT_CONFIG=DEFAULT_CONFIG,
        build_analyst_execution_plan=build_analyst_execution_plan,
        ConditionalLogic=ConditionalLogic, Propagator=Propagator, Reflector=Reflector,
        SignalProcessor=SignalProcessor, TradingAgentsGraph=TradingAgentsGraph,
        write_report_tree=write_report_tree,
    )


def build_config(u, overrides: dict) -> dict:
    """DEFAULT_CONFIG plus overrides; dict values merge one level deep, as in upstream set_config."""
    config = deepcopy(u.DEFAULT_CONFIG)
    for key, value in deepcopy(overrides).items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    return config


def run_config(u, st: dict) -> dict:
    """The config fixed when the run was created (older runs stored only overrides)."""
    return deepcopy(st["config"]) if "config" in st else build_config(u, st["config_overrides"])


def graph_shell(u, config: dict):
    """A TradingAgentsGraph without LLM clients.

    ``__init__`` builds provider clients, which is exactly what this skill
    replaces; the helper methods used here only need ``config`` and the memory
    log, so they run as upstream wrote them.
    """
    u.set_config(config)
    os.makedirs(config["data_cache_dir"], exist_ok=True)
    os.makedirs(config["results_dir"], exist_ok=True)
    graph = u.TradingAgentsGraph.__new__(u.TradingAgentsGraph)
    graph.config = config
    graph.debug = False
    graph.memory_log = u.TradingMemoryLog(config)
    graph.conditional_logic = u.ConditionalLogic(
        max_debate_rounds=config["max_debate_rounds"],
        max_risk_discuss_rounds=config["max_risk_discuss_rounds"],
    )
    graph.log_states_dict = {}
    graph.ticker = None
    return graph


def all_tools(u) -> dict:
    tools = {}
    for tool_node in u.TradingAgentsGraph._create_tool_nodes(None).values():
        tools.update(tool_node.tools_by_name)
    return tools


def cache_calls(function, cache_dir: Path, label: str):
    """Disk-memoise a data fetch so re-running a node reuses the same data."""
    if getattr(function, "_ta_cached", False):
        return function

    def wrapper(*args, **kwargs):
        key = json.dumps([label, args, kwargs], sort_keys=True, default=str)
        path = cache_dir / f"{label}-{hashlib.sha256(key.encode()).hexdigest()[:16]}.txt"
        if path.exists():
            return path.read_text(encoding="utf-8")
        value = function(*args, **kwargs)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value), encoding="utf-8")
        return value

    wrapper._ta_cached = True
    return wrapper


def install_fetch_cache(run_dir: Path):
    # The sentiment analyst fetches its data inside the node; the node runs once
    # to produce the prompt and again to apply the answer.
    module = importlib.import_module("tradingagents.agents.analysts.sentiment_analyst")
    cache_dir = run_dir / "cache" / "sentiment"
    module.fetch_stocktwits_messages = cache_calls(module.fetch_stocktwits_messages, cache_dir, "stocktwits")
    module.fetch_reddit_posts = cache_calls(module.fetch_reddit_posts, cache_dir, "reddit")
    if not getattr(module.get_news, "_ta_cached", False):
        module.get_news = SimpleNamespace(func=cache_calls(module.get_news.func, cache_dir, "news"),
                                          _ta_cached=True)


# --------------------------------------------------------------------------
# Run state
# --------------------------------------------------------------------------


def write_json(path: Path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_state(run_dir: Path) -> dict:
    path = run_dir / STATE_FILE
    if not path.is_file():
        raise UsageError(f"No TradingAgents run at {run_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(run_dir: Path, st: dict):
    write_json(run_dir / STATE_FILE, st)


class UsageError(Exception):
    pass


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def command_prefix(*parts: str) -> str:
    args = [sys.executable, str(SCRIPT), *parts]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


# --------------------------------------------------------------------------
# Task files
# --------------------------------------------------------------------------


def render_tools(tools, run_dir: Path, task_id: str) -> str:
    prefix = command_prefix("tool", "--run-dir", str(run_dir), "--task", task_id)
    lines = [
        "## Data tools",
        "",
        "These replace the provider tool-calling interface. Run a tool with Bash; the command prints its result:",
        "",
        "```",
        f"{prefix} <tool_name> name=value [name=value ...]",
        "```",
        "",
        "Quote values that contain spaces. Omit optional arguments to use their defaults.",
        "Call tools as many times as the task needs, then write the final report.",
    ]
    for tool in tools:
        try:
            required = set(tool.tool_call_schema.model_json_schema().get("required", []))
        except Exception:  # noqa: BLE001 - schema introspection is best effort
            required = set()
        lines += ["", f"### {tool.name}", "", (tool.description or "").strip(), "", "Arguments:"]
        for name, spec in tool.args.items():
            kind = spec.get("type") or "/".join(
                option.get("type", "") for option in spec.get("anyOf", []) if option.get("type") != "null"
            )
            flag = "required" if name in required else f"optional, default {spec.get('default')!r}"
            lines.append(f"- `{name}` ({kind}, {flag}): {spec.get('description', '')}")
    return "\n".join(lines)


def render_task(run_dir: Path, node: str, request, output_file: Path, task_id: str) -> str:
    lines = [
        f"# TradingAgents task: {node}",
        "",
        f"You are the **{node}** in a TradingAgents run. The messages under \"Messages\" are exactly "
        "what the TradingAgents framework sends to its language model at this step. "
        "Respond as that model: follow the system message and answer the final user message.",
        "",
        "## Rules",
        "",
        f"- Save only your final answer, in UTF-8, to `{output_file}`. Do not add remarks about these rules.",
    ]
    if request.tools:
        lines.append("- Get data only through the tools under \"Data tools\". Do not search the web or use "
                     "other sources. If a tool returns no data, say so in the report instead of estimating.")
        lines.append("- If a tool prints `RUN_FAILED`, stop: do not write the answer file, and reply with "
                     "the single word: failed.")
    else:
        lines.append("- Use only the content of this file. Do not run tools, search the web or read other files.")
    if request.schema is not None:
        lines.append("- The answer must be one JSON object that validates against the schema under "
                     "\"Output format\". Write all prose inside the JSON string fields.")
    if request.tools:
        lines += ["", render_tools(request.tools, run_dir, task_id)]
    if request.schema is not None:
        schema = json.dumps(request.schema.model_json_schema(), ensure_ascii=False, indent=2)
        lines += ["", "## Output format", "", "```json", schema, "```"]
    lines += ["", "## Messages"]
    for role, content in request.messages:
        lines += ["", f"### {role}", "", content]
    return "\n".join(lines) + "\n"


def add_task(run_dir: Path, st: dict, node: str, request, kind: str, meta: dict | None = None) -> str:
    st["seq"] += 1
    task_id = f"{st['seq']:02d}-{slug(node)}"
    tasks_dir = run_dir / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    prompt_file = tasks_dir / f"{task_id}.prompt.md"
    output_file = tasks_dir / f"{task_id}.response.md"
    prompt_file.write_text(render_task(run_dir, node, request, output_file, task_id), encoding="utf-8")
    tier = "quick" if kind == "reflection" else NODE_TIERS[node]
    st["pending"][task_id] = {
        "node": node, "kind": kind, "tier": tier,
        "schema": request.schema.__name__ if request.schema is not None else None,
        "uses_tools": bool(request.tools),
        "prompt_file": str(prompt_file), "output_file": str(output_file),
        "meta": meta or {}, "attempts": 0, "generation": 0, "created_at": time.time(),
    }
    return task_id


def rewrite_as_freetext(u, run_dir: Path, st: dict, task_id: str):
    """Upstream ``invoke_structured_or_freetext`` fallback: same prompt, plain answer."""
    task = st["pending"][task_id]
    request = request_for(lambda: run_node(u, st["run"], task["node"], st["graph"], None)[0])
    request.schema = None
    prompt_file, output_file = Path(task["prompt_file"]), Path(task["output_file"])
    prompt_file.write_text(render_task(run_dir, task["node"], request, output_file, task_id), encoding="utf-8")
    task["fallback_from"], task["schema"] = task["schema"], None


def describe_task(st: dict, task_id: str) -> dict:
    task = st["pending"][task_id]
    prompt = (
        f"You are one agent in a TradingAgents research run. Read the task file {task['prompt_file']} "
        f"and follow it exactly. Save only your final answer to {task['output_file']}. "
        "When the file is saved, reply with the single word: done."
    )
    if task.get("error"):
        prompt += f" A previous answer was rejected; fix this problem: {task['error']}"
    return {
        "id": task_id, "agent": task["node"], "tier": task["tier"],
        "model": st["run"]["models"][task["tier"]], "uses_tools": task["uses_tools"],
        "output_format": "json" if task["schema"] else "markdown",
        "prompt_file": task["prompt_file"], "output_file": task["output_file"],
        "subagent_prompt": prompt, **({"error": task["error"]} if task.get("error") else {}),
    }


def request_for(call):
    """Run ``call`` expecting it to stop at an LLM call; return that request."""
    from skill_llm import LLMRequest

    try:
        call()
    except LLMRequest as request:
        return request
    raise RuntimeError("Upstream step finished without requesting a model response")


# --------------------------------------------------------------------------
# Graph steps
# --------------------------------------------------------------------------


def run_node(u, run: dict, node: str, state: dict, response: str | None) -> tuple[dict, list]:
    """Run one upstream node; return its state update and the messages it emitted."""
    from skill_llm import SkillLLM

    agent = u.factories[node](SkillLLM(response))
    node_input = dict(state)
    analyst = run["analyst_nodes"].get(node)
    if analyst is not None:
        node_input["messages"] = analyst_messages(u, run, state, analyst)
    update = agent(node_input)
    # Messages only feed the analyst tool loop, which the subagent runs itself;
    # they are returned for message_tool.log instead of being kept in state.
    messages = list(update.get("messages") or [])
    return {key: value for key, value in update.items() if key not in ("messages", "sender")}, messages


def analyst_messages(u, run: dict, state: dict, analyst_key: str):
    from langchain_core.messages import HumanMessage

    # Upstream starts the first analyst with the ticker as the human message and
    # every later analyst with the placeholder left by its message-clear node.
    if run["analysts"][0] == analyst_key:
        return [HumanMessage(content=state["company_of_interest"])]
    return u.create_msg_delete()({**state, "messages": []})["messages"]


def next_node(graph, node: str, state: dict) -> str | None:
    """Edges from upstream GraphSetup.setup_graph."""
    if node in DEBATE_NODES:
        return graph.conditional_logic.should_continue_debate(state)
    if node == "Research Manager":
        return "Trader"
    if node == "Trader":
        return "Aggressive Analyst"
    if node in RISK_NODES:
        return graph.conditional_logic.should_continue_risk_analysis(state)
    if node == "Portfolio Manager":
        return None
    raise ValueError(f"Unknown node: {node}")


def prepare_reflections(u, graph, run_dir: Path, st: dict):
    """Upstream ``_resolve_pending_entries`` up to its LLM call."""
    from skill_llm import SkillLLM

    run = st["run"]
    ticker = run["ticker"]
    pending = [e for e in graph.memory_log.get_pending_entries() if e["ticker"] == ticker]
    if not pending:
        return
    benchmark = graph._resolve_benchmark(ticker)
    for entry in pending:
        raw, alpha, days, resolution_date = graph._fetch_returns(ticker, entry["date"], benchmark=benchmark)
        if raw is None:
            continue
        meta = {"ticker": ticker, "trade_date": entry["date"], "raw_return": raw, "alpha_return": alpha,
                "holding_days": days, "resolution_date": resolution_date,
                "decision": entry.get("decision", ""), "benchmark": benchmark}
        request = request_for(lambda: u.Reflector(SkillLLM()).reflect_on_final_decision(
            final_decision=meta["decision"], raw_return=raw, alpha_return=alpha, benchmark_name=benchmark))
        add_task(run_dir, st, f"Reflection {entry['date']}", request, "reflection", meta)


def apply_reflections(u, graph, tasks: list[dict]):
    from skill_llm import SkillLLM

    updates = []
    for task in tasks:
        meta = task["meta"]
        reflection = u.Reflector(SkillLLM(task["response"])).reflect_on_final_decision(
            final_decision=meta["decision"], raw_return=meta["raw_return"],
            alpha_return=meta["alpha_return"], benchmark_name=meta["benchmark"])
        updates.append({key: meta[key] for key in
                        ("ticker", "trade_date", "raw_return", "alpha_return", "holding_days", "resolution_date")}
                       | {"reflection": reflection})
    graph.memory_log.batch_update_with_outcomes(updates)


def prepare_analysts(u, graph, run_dir: Path, st: dict):
    """Upstream ``_run_graph`` initial state, then every selected analyst."""
    run = st["run"]
    ticker, trade_date = run["ticker"], run["trade_date"]
    past_context = graph.memory_log.get_past_context(ticker, as_of=graph._memory_as_of(trade_date))
    instrument_context = graph.resolve_instrument_context(ticker, run["asset_type"])
    state = u.Propagator().create_initial_state(
        ticker, trade_date, asset_type=run["asset_type"],
        past_context=past_context, instrument_context=instrument_context)
    state.pop("messages")
    st["graph"] = state
    # Analysts only read the shared inputs and write their own report key, so
    # they run in parallel; upstream chains them only because LangGraph shares
    # one message list between them.
    for node in run["analyst_nodes"]:
        request = request_for(lambda node=node: run_node(u, run, node, state, None))
        add_task(run_dir, st, node, request, "analyst")
    # The initial graph input the CLI logs before the first analyst starts.
    from langchain_core.messages import HumanMessage

    cli_mirror.log_langchain_message(run_dir, HumanMessage(content=ticker))


def prepare_node(u, run_dir: Path, st: dict):
    node = st["cursor"]
    request = request_for(lambda: run_node(u, st["run"], node, st["graph"], None))
    add_task(run_dir, st, node, request, "node")


def analyst_wall_time_summary(u, run: dict, timings: dict) -> str:
    """The CLI's ``AnalystWallTimeTracker`` summary from recorded task times."""
    from tradingagents.graph.analyst_execution import AnalystWallTimeTracker

    tracker = AnalystWallTimeTracker(u.build_analyst_execution_plan(run["analysts"]))
    for node, key in run["analyst_nodes"].items():
        if node in timings:
            started, finished = timings[node]
            tracker.mark_started(key, started_at=started)
            tracker.mark_completed(key, completed_at=finished)
    return tracker.format_summary()


def finalize(u, graph, run_dir: Path, st: dict) -> dict:
    """End of a run: upstream ``_run_graph`` bookkeeping plus the CLI's outputs."""
    run, state = st["run"], st["graph"]
    ticker, trade_date = run["ticker"], run["trade_date"]

    # propagate(): state log, decision log entry, signal.
    decision = state["final_trade_decision"]
    graph.ticker = ticker
    graph._log_state(trade_date, state)
    graph.memory_log.store_decision(ticker=ticker, trade_date=trade_date, final_trade_decision=decision)
    signal = u.SignalProcessor().process_signal(decision)

    # CLI: final section files, completion messages, then "Save report?" (default yes)
    # to ./reports/TICKER_YYYYmmdd_HHMMSS relative to where the run was started.
    cli_mirror.mirror_final(run_dir, run["analysts"], state)
    cli_mirror.log_message(run_dir, "System", f"Completed analysis for {trade_date}")
    wall_times = analyst_wall_time_summary(u, run, st["timings"])
    cli_mirror.log_message(run_dir, "System", wall_times)
    report = None
    if run["save_dir"]:
        save_path = Path(run["save_dir"]) / f"{ticker}_{datetime.now():%Y%m%d_%H%M%S}"
        report = str(u.write_report_tree(state, ticker, save_path))

    result = {
        "status": "completed" if signal in RATINGS else "needs_review",
        "signal": signal, "ticker": ticker, "analysis_date": trade_date, "asset_type": run["asset_type"],
        "analysts": run["analysts"], "language": run["language"],
        "debate_rounds": run["debate_rounds"], "risk_rounds": run["risk_rounds"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report": report, "reports_dir": str(run_dir / "reports"),
        "final_decision": str(run_dir / "reports" / "final_trade_decision.md"),
        "message_log": str(run_dir / "message_tool.log"),
        "state_log": str(Path(graph.config["results_dir"]) / u.safe_ticker_component(ticker)
                         / "TradingAgentsStrategy_logs" / f"full_states_log_{trade_date}.json"),
        "historical_data_warning": trade_date < date.today().isoformat(),
        "memory_log": graph.config.get("memory_log_path"),
        "analyst_wall_time": wall_times, "llm_tasks": st["seq"], "tool_calls": count_tool_calls(run_dir),
        "upstream": installed_source(),
    }
    write_json(run_dir / "result.json", result)
    return result


def count_tool_calls(run_dir: Path) -> int:
    log = run_dir / "logs" / "tool_calls.jsonl"
    return sum(1 for _ in log.open(encoding="utf-8")) if log.is_file() else 0


# --------------------------------------------------------------------------
# Step loop
# --------------------------------------------------------------------------


def ingest(u, run_dir: Path, st: dict, task_id: str, allow_freetext: bool) -> str:
    """Return 'ok', 'missing' or 'rejected' for one pending task.

    A structured answer that fails the upstream schema gets one correction
    attempt (the role a provider's native structured output plays). After that
    the task follows upstream ``invoke_structured_or_freetext``: the same
    prompt is asked again for a plain-text answer, which the node then renders
    through its free-text path.
    """
    from skill_llm import parse_structured

    task = st["pending"][task_id]
    if "response" in task:
        return "ok"
    path = Path(task["output_file"])
    text = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    if not text:
        return "missing"
    if task["schema"] and not allow_freetext:
        try:
            parse_structured(getattr(u.schemas, task["schema"]), text)
        except Exception as exc:  # noqa: BLE001 - report any parse/validation failure
            task["attempts"] += 1
            path.replace(path.with_name(f"{path.stem}.rejected-{task['attempts']}.md"))
            if task["attempts"] == 1:
                task["error"] = f"{type(exc).__name__}: {str(exc)[:1200]}"
            else:
                rewrite_as_freetext(u, run_dir, st, task_id)
                task["error"] = ("The JSON answer failed validation again. The task file now asks for a "
                                 "plain-text answer (the upstream free-text fallback); reread it.")
            return "rejected"
    task.pop("error", None)
    task["response"] = text
    task["answered_at"] = path.stat().st_mtime
    return "ok"


def load_failures(run_dir: Path) -> list[dict]:
    path = run_dir / "logs" / "tool_failures.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def failed_tasks(run_dir: Path, st: dict) -> dict:
    """Pending tasks whose current attempt hit a tool exception."""
    failures = {}
    for failure in load_failures(run_dir):
        task = st["pending"].get(failure["task"])
        if task is not None and failure["generation"] == task["generation"]:
            failures[failure["task"]] = failure
    return failures


def apply_node(u, run_dir: Path, st: dict, node: str, response: str):
    """Apply one answered node and mirror what the CLI stream loop records."""
    run = st["run"]
    update, messages = run_node(u, run, node, st["graph"], response)
    st["graph"].update(update)
    for message in messages:
        cli_mirror.log_langchain_message(run_dir, message)
    if node in run["analyst_nodes"]:
        # The analyst's message-clear node leaves the CLI's next user placeholder.
        for message in u.create_msg_delete()({**st["graph"], "messages": []})["messages"]:
            cli_mirror.log_langchain_message(run_dir, message)
    cli_mirror.mirror_chunk(run_dir, run["analysts"], st["graph"])


def apply_pending(u, graph, run_dir: Path, st: dict):
    tasks = [st["pending"][task_id] | {"id": task_id} for task_id in sorted(st["pending"])]
    kinds = {task["kind"] for task in tasks}
    if kinds == {"reflection"}:
        apply_reflections(u, graph, tasks)
        st["phase"] = "analysts"
    elif kinds == {"analyst"}:
        for task in tasks:
            apply_node(u, run_dir, st, task["node"], task["response"])
        st["phase"], st["cursor"] = "graph", "Bull Researcher"
    elif kinds == {"node"}:
        (task,) = tasks
        apply_node(u, run_dir, st, task["node"], task["response"])
        st["cursor"] = next_node(graph, task["node"], st["graph"])
        st["phase"] = "graph" if st["cursor"] else "finalize"
    else:
        raise RuntimeError(f"Unexpected pending task mix: {sorted(kinds)}")
    for task in tasks:
        st["timings"][task["node"]] = [task["created_at"], task.get("answered_at", time.time())]
    st["completed"].extend({"id": task["id"], "agent": task["node"]} for task in tasks)
    st["pending"] = {}


def done_payload(run_dir: Path, result: dict) -> dict:
    return {"status": "done", "run_dir": str(run_dir), "outcome": result["status"], "signal": result["signal"],
            "result_file": str(run_dir / "result.json"), "final_decision": result["final_decision"],
            "report": result["report"], "reports_dir": result["reports_dir"],
            "analyst_wall_time": result["analyst_wall_time"], "llm_tasks": result["llm_tasks"]}


def step(run_dir: Path, allow_freetext: bool = False, retry: bool = False) -> dict:
    u = load_upstream()
    st = load_state(run_dir)
    graph = graph_shell(u, run_config(u, st))
    install_fetch_cache(run_dir)

    if st["phase"] == "done":
        return done_payload(run_dir, json.loads((run_dir / "result.json").read_text(encoding="utf-8")))

    failures = failed_tasks(run_dir, st)
    if failures and not retry:
        # Upstream ToolNode re-raises data-vendor exceptions, which aborts the run.
        first = failures[sorted(failures)[0]]
        return {"status": "failed", "run_dir": str(run_dir), "error_type": first["error_type"],
                "error": first["error"], "failed_tasks": sorted(failures),
                "hint": "A data tool raised, which stops an upstream run. Resume with `step --retry`, "
                        "which reruns the affected tasks from the start."}
    for task_id in failures:
        task = st["pending"][task_id]
        task["generation"] += 1
        task["created_at"] = time.time()
        output = Path(task["output_file"])
        if output.exists():
            output.replace(output.with_name(f"{output.stem}.failed-{task['generation']}.md"))
    if failures:
        save_state(run_dir, st)

    if st["pending"]:
        outcomes = {task_id: ingest(u, run_dir, st, task_id, allow_freetext) for task_id in sorted(st["pending"])}
        waiting = [task_id for task_id, outcome in outcomes.items() if outcome != "ok"]
        if waiting:
            save_state(run_dir, st)
            return {"status": "waiting", "run_dir": str(run_dir), "phase": st["phase"],
                    "parallel": len(waiting) > 1, "tasks": [describe_task(st, t) for t in waiting]}
        apply_pending(u, graph, run_dir, st)
        save_state(run_dir, st)

    while True:
        if st["phase"] == "start":
            prepare_reflections(u, graph, run_dir, st)
            st["phase"] = "reflect" if st["pending"] else "analysts"
        elif st["phase"] == "analysts":
            prepare_analysts(u, graph, run_dir, st)
            st["phase"] = "analysts_wait"
        elif st["phase"] == "graph":
            prepare_node(u, run_dir, st)
            st["phase"] = "graph_wait"
        elif st["phase"] == "finalize":
            result = finalize(u, graph, run_dir, st)
            st["phase"] = "done"
            save_state(run_dir, st)
            return done_payload(run_dir, result)
        save_state(run_dir, st)
        if st["pending"]:
            return {"status": "tasks", "run_dir": str(run_dir), "phase": st["phase"],
                    "parallel": len(st["pending"]) > 1,
                    "tasks": [describe_task(st, t) for t in sorted(st["pending"])]}


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def installed_source() -> dict:
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
    return {"installed": True, "version": dist.version, "commit": commit,
            "verified_revision": commit == UPSTREAM_COMMIT and source_url == UPSTREAM_URL.lower()}


def load_config_file(path: Path | None) -> dict:
    """Upstream config overrides from a JSON file, as a Python caller would set them."""
    if path is None:
        return {}
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"Cannot read config file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise UsageError("The config file must contain a JSON object of upstream config keys.")
    return value


def _depth_round(research_depth: int | None, env_var: str) -> int | None:
    """Research depth applies unless the matching upstream env override is set."""
    return None if os.environ.get(env_var) else research_depth


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="Check the runtime without network or model calls")

    init = commands.add_parser("init", help="Create a run and return its first tasks")
    init.add_argument("--ticker", default="", help="Default: SPY, as the upstream CLI")
    init.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD, default today")
    init.add_argument("--asset-type", choices=("stock", "crypto"),
                      help="Default: detected from the ticker, as the upstream CLI does")
    init.add_argument("--analysts", nargs="+", choices=ANALYSTS, default=list(ANALYSTS),
                      help="Upstream drops the fundamentals analyst for crypto")
    init.add_argument("--language", help="Default: upstream output_language (English, or "
                                         "TRADINGAGENTS_OUTPUT_LANGUAGE from the environment or .env)")
    init.add_argument("--research-depth", type=positive,
                      help="Upstream depth: sets both round counts (CLI offers 1, 3, 5)")
    init.add_argument("--debate-rounds", type=positive)
    init.add_argument("--risk-rounds", type=positive)
    init.add_argument("--config", type=Path,
                      help="JSON object of upstream config keys (data_vendors, tool_vendors, news limits, "
                           "benchmark, results_dir, ...), merged over DEFAULT_CONFIG like the Python API")
    init.add_argument("--data-vendor", choices=("yfinance", "alpha_vantage"),
                      help="Shortcut: use one vendor for prices, indicators, fundamentals and news")
    init.add_argument("--quick-model", default=DEFAULT_MODELS["quick"],
                      help="Subagent model for upstream quick-think nodes")
    init.add_argument("--deep-model", default=DEFAULT_MODELS["deep"],
                      help="Subagent model for Research Manager and Portfolio Manager")
    memory = init.add_mutually_exclusive_group()
    memory.add_argument("--memory-log", type=Path, help="Decision log path (default: upstream)")
    memory.add_argument("--no-memory", action="store_true", help="Do not read or write the decision log")
    init.add_argument("--run-dir", type=Path,
                      help="Default: RESULTS_DIR/TICKER/DATE, the upstream CLI layout")
    save = init.add_mutually_exclusive_group()
    save.add_argument("--save-dir", type=Path,
                      help="Where the final report tree is saved as TICKER_YYYYmmdd_HHMMSS "
                           "(default: ./reports, the upstream CLI's 'Save report' default)")
    save.add_argument("--no-save", action="store_true", help="Skip saving the report tree")

    for name, text in (("step", "Ingest saved answers and return the next tasks"),
                       ("status", "Show run progress without advancing")):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--run-dir", type=Path, required=True)
        if name == "step":
            sub.add_argument("--allow-freetext", action="store_true",
                             help="Accept invalid JSON now as upstream's free-text fallback")
            sub.add_argument("--retry", action="store_true",
                             help="Rerun tasks stopped by a data tool exception")

    tool = commands.add_parser("tool", help="Run an upstream data tool for an analyst task")
    tool.add_argument("--run-dir", type=Path, required=True)
    tool.add_argument("--task", help="Analyst task id; limits tools to that analyst's upstream ToolNode")
    tool.add_argument("name")
    tool.add_argument("params", nargs="*", metavar="name=value")
    return parser


def cmd_init(args) -> dict:
    u = load_upstream()
    # Upstream CLI order: normalise the symbol, classify the asset from the
    # canonical form, then drop analysts that do not apply to it.
    if not u.is_valid_ticker_input(args.ticker):
        raise UsageError("Please enter a valid ticker symbol, e.g. AAPL, 000404.SZ, 0700.HK, GC=F.")
    ticker = u.normalize_ticker_symbol(args.ticker) if args.ticker.strip() else "SPY"
    try:
        u.safe_ticker_component(ticker)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc
    try:
        trade_date = date.fromisoformat(args.date)
    except ValueError as exc:
        raise UsageError("Date must be YYYY-MM-DD.") from exc
    if trade_date > date.today():
        raise UsageError("Future analysis dates are not supported.")

    asset_type = args.asset_type or u.detect_asset_type(ticker).value
    # The CLI checkbox returns analysts in its fixed ANALYST_ORDER.
    requested = [key.value for _, key in u.ANALYST_ORDER if key.value in args.analysts]
    analysts = [analyst.value for analyst in u.filter_analysts_for_asset_type(
        [u.AnalystType(key) for key in requested], u.AssetType(asset_type))]
    dropped = [key for key in requested if key not in analysts]
    if not analysts:
        raise UsageError(f"No analyst applies to a {asset_type} run; upstream drops {', '.join(dropped)}.")
    plan = u.build_analyst_execution_plan(analysts)

    file_config = load_config_file(args.config)

    # Round counts: explicit flags, then research depth (which an upstream env
    # override suppresses, as in the CLI), then the config file, then
    # DEFAULT_CONFIG (which already carries env overrides).
    debate_rounds = (args.debate_rounds or _depth_round(args.research_depth, "TRADINGAGENTS_MAX_DEBATE_ROUNDS")
                     or file_config.get("max_debate_rounds") or u.DEFAULT_CONFIG["max_debate_rounds"])
    risk_rounds = (args.risk_rounds or _depth_round(args.research_depth, "TRADINGAGENTS_MAX_RISK_ROUNDS")
                   or file_config.get("max_risk_discuss_rounds") or u.DEFAULT_CONFIG["max_risk_discuss_rounds"])

    # Same layout the upstream CLI writes: results_dir/TICKER/DATE, with the
    # section reports and message log inside it and the state log under
    # results_dir/TICKER.
    results_dir = file_config.get("results_dir") or u.DEFAULT_CONFIG["results_dir"]
    default_dir = Path(results_dir).expanduser() / u.safe_ticker_component(ticker) / trade_date.isoformat()
    run_dir = (args.run_dir or default_dir).expanduser().resolve()
    if run_dir.exists():
        raise UsageError(f"Run directory already exists: {run_dir}. Continue it with "
                         f"`step --run-dir`, or start a separate run with `--run-dir`.")

    language = args.language or file_config.get("output_language") or u.DEFAULT_CONFIG["output_language"]
    save_dir = None if args.no_save else str((args.save_dir or Path.cwd() / "reports").expanduser().resolve())
    overrides = {
        **file_config,
        "output_language": language,
        "max_debate_rounds": debate_rounds,
        "max_risk_discuss_rounds": risk_rounds,
        "checkpoint_enabled": False,
        "llm_provider": "skill-subagent",
        "deep_think_llm": args.deep_model,
        "quick_think_llm": args.quick_model,
    }
    if args.data_vendor:
        overrides["data_vendors"] = {**file_config.get("data_vendors", {}), **{
            key: args.data_vendor for key in
            ("core_stock_apis", "technical_indicators", "fundamental_data", "news_data")}}
    if args.no_memory:
        overrides["memory_log_path"] = None
    elif args.memory_log:
        overrides["memory_log_path"] = str(args.memory_log.expanduser().resolve())

    # Upstream holds one config object for the whole run; resolve it once here
    # so later processes are unaffected by a different environment or .env.
    config = build_config(u, overrides)
    run_dir.mkdir(parents=True)
    st = {
        "version": 1,
        "run": {
            "ticker": ticker, "trade_date": trade_date.isoformat(), "asset_type": asset_type,
            "asset_type_detected": args.asset_type is None,
            "analysts": analysts, "dropped_analysts": dropped, "language": language,
            "debate_rounds": debate_rounds, "risk_rounds": risk_rounds,
            "analyst_nodes": {spec.agent_node: spec.key for spec in plan.specs},
            "models": {"quick": args.quick_model, "deep": args.deep_model},
            "save_dir": save_dir,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "config": config,
        "phase": "start", "seq": 0, "pending": {}, "completed": [], "graph": {}, "cursor": None,
        "timings": {},
    }
    save_state(run_dir, st)
    # The CLI's opening System messages.
    cli_mirror.log_message(run_dir, "System", f"Selected ticker: {ticker}")
    if asset_type != "stock":
        cli_mirror.log_message(run_dir, "System", f"Detected asset type: {asset_type}")
    cli_mirror.log_message(run_dir, "System", f"Analysis date: {trade_date.isoformat()}")
    cli_mirror.log_message(run_dir, "System", f"Selected analysts: {', '.join(analysts)}")
    return step(run_dir)


def cmd_status(args) -> dict:
    run_dir = args.run_dir.expanduser().resolve()
    st = load_state(run_dir)
    return {"status": "done" if st["phase"] == "done" else "in_progress", "run_dir": str(run_dir),
            "phase": st["phase"], "run": st["run"], "completed": st["completed"],
            "tasks": [describe_task(st, t) for t in sorted(st["pending"])]}


def _arg_types(spec: dict) -> set[str]:
    types = {spec["type"]} if "type" in spec else {option.get("type") for option in spec.get("anyOf", [])}
    return {kind for kind in types if kind}


def parse_params(params: list[str], arg_specs: dict | None = None) -> dict:
    """Turn ``name=value`` words into tool-call arguments.

    A model sends typed JSON arguments; here values arrive as text, so each one
    follows the tool's declared type: string parameters keep the text as is
    (``symbol=7203`` stays ``"7203"``), other values are read as JSON scalars.
    """
    values = {}
    for item in params:
        name, sep, raw = item.partition("=")
        if not sep or not name:
            raise UsageError(f"Tool arguments use name=value, got {item!r}")
        types = _arg_types((arg_specs or {}).get(name, {}))
        if "string" in types and not types & {"integer", "number", "boolean"}:
            values[name] = None if raw == "null" and "null" in types else raw
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        values[name] = value if isinstance(value, (int, float, bool, type(None))) else raw
    return values


def tool_node_for(u, st: dict, task_id: str | None):
    """The upstream ToolNode an analyst task's tool calls go through."""
    nodes = u.TradingAgentsGraph._create_tool_nodes(None)
    if task_id is None:
        from langgraph.prebuilt import ToolNode

        return ToolNode(list(all_tools(u).values()))
    task = st["pending"].get(task_id)
    analyst = st["run"]["analyst_nodes"].get(task["node"]) if task else None
    if analyst is None:
        raise UsageError(f"{task_id!r} is not a pending analyst task")
    return nodes[analyst]


def run_tool_call(tool_node, name: str, args: dict):
    """Execute one tool call exactly as the analyst's ToolNode does inside the graph.

    ToolNode needs the LangGraph runtime, so it runs as the only node of a
    graph. Invalid calls come back as error ToolMessages for the model; any
    other exception propagates, which in upstream aborts the run.
    """
    from langchain_core.messages import AIMessage
    from langgraph.graph import END, START, MessagesState, StateGraph

    graph = StateGraph(MessagesState)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    call = {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}
    ai_message = AIMessage(content="", tool_calls=[call])
    return graph.compile().invoke({"messages": [ai_message]})["messages"][-1]


def cmd_tool(args) -> int:
    run_dir = args.run_dir.expanduser().resolve()
    st = load_state(run_dir)
    u = load_upstream()
    u.set_config(run_config(u, st))
    tool_node = tool_node_for(u, st, args.task)
    tool = tool_node.tools_by_name.get(args.name)
    params = parse_params(args.params, tool.args if tool is not None else None)
    generation = st["pending"][args.task]["generation"] if args.task else None
    log = run_dir / "logs" / "tool_calls.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc)
    cli_mirror.log_tool_call(run_dir, args.name, params)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            message = run_tool_call(tool_node, args.name, params)
    except Exception as exc:  # noqa: BLE001 - upstream lets these abort the run
        failure = {"at": started.isoformat(), "task": args.task, "generation": generation,
                   "tool": args.name, "args": params, "error_type": type(exc).__name__, "error": str(exc)[:2000]}
        with (run_dir / "logs" / "tool_failures.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(failure, ensure_ascii=False) + "\n")
        print(f"RUN_FAILED: {type(exc).__name__}: {exc}")
        return 1
    cli_mirror.log_langchain_message(run_dir, message)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": started.isoformat(), "task": args.task, "tool": args.name,
                                 "args": params, "status": message.status,
                                 "chars": len(str(message.content))}, ensure_ascii=False) + "\n")
    print(message.content)
    return 0


def cmd_doctor() -> dict:
    source = installed_source()
    result = {"python": sys.version.split()[0], "python_ok": sys.version_info >= (3, 10),
              "upstream": source, "fred_api_key_present": bool(os.getenv("FRED_API_KEY")),
              "alpha_vantage_api_key_present": bool(os.getenv("ALPHA_VANTAGE_API_KEY"))}
    if source["installed"]:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                u = load_upstream()
                result["tools"] = sorted(all_tools(u))
            result["import_ok"] = True
        except Exception as exc:  # noqa: BLE001
            result["import_ok"], result["import_error"] = False, f"{type(exc).__name__}: {exc}"
    result["ready"] = bool(result["python_ok"] and source["verified_revision"] and result.get("import_ok"))
    return result


def emit(value: dict):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "tool":
            return cmd_tool(args)
        if args.command == "doctor":
            result = cmd_doctor()
            emit(result)
            return 0 if result["ready"] else 2
        # Upstream data libraries print progress to stdout; keep stdout pure JSON.
        with contextlib.redirect_stdout(sys.stderr):
            if args.command == "init":
                result = cmd_init(args)
            elif args.command == "step":
                result = step(args.run_dir.expanduser().resolve(), args.allow_freetext, args.retry)
            else:
                result = cmd_status(args)
    except UsageError as exc:
        emit({"status": "error", "error": str(exc)})
        return 2
    except Exception as exc:  # noqa: BLE001 - surface failures as JSON for the orchestrator
        emit({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:2000]})
        return 1
    emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
