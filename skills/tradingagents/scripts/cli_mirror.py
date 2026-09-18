"""Upstream CLI run outputs: ``reports/<section>.md`` and ``message_tool.log``.

The upstream CLI streams the graph with ``stream_mode="values"`` and, for every
state chunk, rewrites per-section report files under
``results_dir/TICKER/DATE/reports`` and appends messages and tool calls to
``message_tool.log``. The helpers below apply the same rules to the skill's
state after each step, using the CLI's own section table, analyst mappings and
message classifier from ``cli.main``.
"""

from __future__ import annotations

import datetime
from pathlib import Path


def _cli():
    import cli.main as cli_main

    return cli_main


def allowed_sections(analysts: list[str]) -> list[str]:
    """``MessageBuffer.init_for_analysis``: sections kept for the selected analysts."""
    return [section for section, (key, _) in _cli().MessageBuffer.REPORT_SECTIONS.items()
            if key is None or key in analysts]


def write_section(run_dir: Path, analysts: list[str], section: str, content):
    """``save_report_section_decorator``: write a section file when it has content."""
    if section not in allowed_sections(analysts) or not content:
        return
    text = "\n".join(str(item) for item in content) if isinstance(content, list) else content
    report_dir = run_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{section}.md").write_text(text, encoding="utf-8")


def _append(run_dir: Path, line: str):
    with open(run_dir / "message_tool.log", "a", encoding="utf-8") as handle:
        handle.write(line)


def _timestamp() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def log_message(run_dir: Path, message_type: str, content: str):
    """``save_message_decorator`` line format."""
    _append(run_dir, f"{_timestamp()} [{message_type}] {content.replace(chr(10), ' ')}\n")


def log_langchain_message(run_dir: Path, message):
    """The CLI stream loop: classify a graph message and log it when it has text."""
    message_type, content = _cli().classify_message_type(message)
    if content and content.strip():
        log_message(run_dir, message_type, content)


def log_tool_call(run_dir: Path, name: str, args: dict):
    """``save_tool_call_decorator`` line format."""
    args_str = ", ".join(f"{key}={value}" for key, value in args.items())
    _append(run_dir, f"{_timestamp()} [Tool Call] {name}({args_str})\n")


def mirror_chunk(run_dir: Path, analysts: list[str], state: dict):
    """Section updates the CLI stream loop makes for one full-state chunk."""
    cli = _cli()
    for key in cli.ANALYST_ORDER:
        report_key = cli.ANALYST_REPORT_MAP[key]
        if key in analysts and state.get(report_key):
            write_section(run_dir, analysts, report_key, state[report_key])

    debate = state.get("investment_debate_state") or {}
    for field, title in (("bull_history", "Bull Researcher Analysis"),
                         ("bear_history", "Bear Researcher Analysis"),
                         ("judge_decision", "Research Manager Decision")):
        text = (debate.get(field) or "").strip()
        if text:
            write_section(run_dir, analysts, "investment_plan", f"### {title}\n{text}")

    if state.get("trader_investment_plan"):
        write_section(run_dir, analysts, "trader_investment_plan", state["trader_investment_plan"])

    risk = state.get("risk_debate_state") or {}
    for field, title in (("aggressive_history", "Aggressive Analyst Analysis"),
                         ("conservative_history", "Conservative Analyst Analysis"),
                         ("neutral_history", "Neutral Analyst Analysis"),
                         ("judge_decision", "Portfolio Manager Decision")):
        text = (risk.get(field) or "").strip()
        if text:
            write_section(run_dir, analysts, "final_trade_decision", f"### {title}\n{text}")


def mirror_final(run_dir: Path, analysts: list[str], state: dict):
    """End of the CLI stream: every section is overwritten with its final value."""
    for section in allowed_sections(analysts):
        if section in state:
            write_section(run_dir, analysts, section, state[section])
