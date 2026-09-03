"""Render leaderboards and comparisons — a rich table for the terminal and Markdown for
the saved leaderboard file. Three scores are always shown side by side with the raw metrics
behind them; estimated cost is marked with `~` so it's never mistaken for an exact figure.
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

_SCORE_COLS = [
    ("overall", "Overall"),
    ("quality", "Quality"),
    ("speed", "Speed"),
    ("cost", "Cost"),
]


def _cost_str(m: dict) -> str:
    prefix = "~" if m.get("cost_estimated") else ""
    return f"{prefix}${m.get('avg_cost_usd', 0):.5f}"


def _mult_str(m: dict) -> str:
    v = m.get("cost_vs_cheapest")
    return f"{v:.2f}×" if v is not None else "–"


def _sorted_models(run: dict) -> list[dict]:
    return sorted(run.get("models", []), key=lambda m: m.get("overall", 0), reverse=True)


def print_leaderboard(run: dict, console: Console | None = None) -> None:
    console = console or Console()
    title = f"Leaderboard — {run.get('n_cases', '?')} cases · suite {run.get('suite_hash', '?')[:8]}"
    table = Table(title=title, header_style="bold")
    table.add_column("Model", style="bold", overflow="fold", min_width=14)
    for _, header in _SCORE_COLS:
        table.add_column(header, justify="right")
    table.add_column("avg latency", justify="right")
    table.add_column("tok/s", justify="right")
    table.add_column("$/call", justify="right")
    table.add_column("vs cheapest", justify="right")
    table.add_column("errors", justify="right")

    for m in _sorted_models(run):
        table.add_row(
            m.get("display") or m["name"],
            f"{m.get('overall', 0):.1f}",
            f"{m.get('quality', 0):.1f}",
            f"{m.get('speed', 0):.1f}",
            f"{m.get('cost', 0):.1f}",
            f"{m.get('avg_latency_ms', 0):.0f}ms",
            f"{m.get('avg_tokens_per_sec', 0):.1f}",
            _cost_str(m),
            _mult_str(m),
            str(m.get("n_errors", 0)),
        )
    console.print(table)
    if any(m.get("cost_estimated") for m in run.get("models", [])):
        console.print("[dim]~ cost estimated from a tokenizer (CLI tools report no usage).[/dim]")


def leaderboard_markdown(run: dict) -> str:
    lines = [
        f"# Benchmark leaderboard",
        "",
        f"- Run: `{run.get('timestamp', '?')}` ({run.get('label', 'run')})",
        f"- Cases: {run.get('n_cases', '?')} · suite hash `{run.get('suite_hash', '?')}`",
        f"- Scoring: quality/speed/cost each 0-100, overall = weighted mean ({run.get('weights_note', 'equal')}).",
        "",
        "| Model | Overall | Quality | Speed | Cost | avg latency | tok/s | $/call | vs cheapest | errors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m in _sorted_models(run):
        lines.append(
            f"| {m.get('display') or m['name']} | {m.get('overall', 0):.1f} | {m.get('quality', 0):.1f} | "
            f"{m.get('speed', 0):.1f} | {m.get('cost', 0):.1f} | {m.get('avg_latency_ms', 0):.0f}ms | "
            f"{m.get('avg_tokens_per_sec', 0):.1f} | {_cost_str(m)} | {_mult_str(m)} | {m.get('n_errors', 0)} |"
        )
    if any(m.get("cost_estimated") for m in run.get("models", [])):
        lines += ["", "_`~` = cost estimated from a tokenizer; CLI tools expose no token usage._"]
    return "\n".join(lines)


def print_quality_by_domain(run: dict, console: Console | None = None) -> None:
    console = console or Console()
    domains = sorted({d for m in run.get("models", []) for d in (m.get("quality_by_domain") or {})})
    if not domains:
        return
    table = Table(title="Quality by domain (0-100)", header_style="bold")
    table.add_column("Model", style="bold")
    for d in domains:
        table.add_column(d, justify="right")
    for m in _sorted_models(run):
        qbd = m.get("quality_by_domain") or {}
        label = m.get("display") or m["name"]
        table.add_row(label, *[f"{qbd.get(d, 0):.0f}" if d in qbd else "-" for d in domains])
    console.print(table)


def print_comparison(cmp: dict, console: Console | None = None) -> None:
    console = console or Console()
    a, b = cmp["a"], cmp["b"]
    table = Table(
        title=f"Compare: {a.get('label')} ({a.get('timestamp')}) -> {b.get('label')} ({b.get('timestamp')})",
        header_style="bold",
    )
    table.add_column("Model", style="bold")
    for _, header in _SCORE_COLS:
        table.add_column(f"Δ {header}", justify="right")
    for row in cmp["rows"]:
        label = row.get("display") or row["name"]
        if not (row["in_a"] and row["in_b"]):
            only = "only in A" if row["in_a"] else "only in B"
            table.add_row(label, only, "", "", "")
            continue
        d = row["deltas"]
        cells = []
        for key, _ in _SCORE_COLS:
            v = d.get(key, 0)
            sign = "+" if v > 0 else ""
            style = "green" if v > 0 else ("red" if v < 0 else "dim")
            cells.append(f"[{style}]{sign}{v:.1f}[/{style}]")
        table.add_row(label, *cells)
    console.print(table)
    if not cmp["suite_match"]:
        console.print("[yellow]⚠ suites differ between runs — scores are not directly comparable.[/yellow]")
