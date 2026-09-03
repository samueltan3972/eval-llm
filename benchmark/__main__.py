"""Command-line entry point.

  python -m benchmark run [--config benchmark.yaml] [--models a,b] [--filter k=v ...]
                          [--sample N] [--dry-run] [--no-cache] [--by-domain]
  python -m benchmark compare <runA.json> <runB.json>
  python -m benchmark leaderboard [--runs-dir runs]
  python -m benchmark dashboard [--runs-dir runs] [--port 8501]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser

from rich.console import Console

from .config import load_config
from .persistence import (
    compare_runs,
    latest_run,
    list_runs,
    load_run,
    save_html_report,
    save_run,
    write_leaderboard,
)
from .report import (
    print_comparison,
    print_leaderboard,
    print_quality_by_domain,
)
from .runner import run_benchmark
from .suite import filter_cases, load_suite

console = Console()


def _parse_filters(items: list[str]) -> dict:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--filter expects key=value, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def cmd_run(args) -> int:
    config = load_config(args.config)
    cases = load_suite(config.suite_glob)
    cases = filter_cases(cases, _parse_filters(args.filter), args.sample, seed=args.seed)
    if not cases:
        console.print("[red]No test cases matched the filters.[/red]")
        return 1

    only = [m.strip() for m in args.models.split(",")] if args.models else None
    console.print(
        f"[bold]Running[/bold] {len(cases)} cases"
        + (f" · models={only}" if only else "")
        + (" · [yellow]dry-run[/yellow]" if args.dry_run else "")
    )

    run = run_benchmark(
        config, cases,
        dry_run=args.dry_run,
        only_models=only,
        use_cache=not args.no_cache,
    )

    path = save_run(run, config.runs_dir)
    lb_path = write_leaderboard(run, config.runs_dir)
    html_path = save_html_report(run, config.runs_dir)

    print_leaderboard(run, console)
    if args.by_domain:
        print_quality_by_domain(run, console)
    if run.get("total_judge_cost_usd"):
        console.print(f"[dim]judge cost this run: ${run['total_judge_cost_usd']:.4f}[/dim]")
    console.print(f"[green]saved[/green] {path}")
    console.print(f"[green]leaderboard[/green] {lb_path}")
    console.print(f"[green]report[/green] {html_path}")
    if args.open:
        webbrowser.open(f"file://{os.path.abspath(html_path)}")
    return 0


def cmd_report(args) -> int:
    if args.run:
        run = load_run(args.run)
        # args.run is runs/<ts>__<label>/run.json -> the top-level runs dir is its grandparent,
        # so save_html_report rebuilds report.html inside the same per-run folder (no nesting).
        runs_dir = os.path.dirname(os.path.dirname(os.path.abspath(args.run)))
    else:
        runs = list_runs(args.runs_dir)
        if not runs:
            console.print("[yellow]No runs found. Run `python -m benchmark run` first.[/yellow]")
            return 1
        run = load_run(runs[-1])
        runs_dir = args.runs_dir
    html_path = save_html_report(run, runs_dir)
    console.print(f"[green]report[/green] {html_path}")
    if args.open:
        webbrowser.open(f"file://{os.path.abspath(html_path)}")
    return 0


def cmd_compare(args) -> int:
    run_a = load_run(args.run_a)
    run_b = load_run(args.run_b)
    print_comparison(compare_runs(run_a, run_b), console)
    return 0


def cmd_leaderboard(args) -> int:
    run = latest_run(args.runs_dir)
    if run is None:
        console.print("[yellow]No runs found. Run `python -m benchmark run` first.[/yellow]")
        return 1
    print_leaderboard(run, console)
    if args.by_domain:
        print_quality_by_domain(run, console)
    return 0


def cmd_dashboard(args) -> int:
    """Launch the Streamlit cross-run leaderboard (a thin UI over benchmark/leaderboard.py)."""
    dashboard_py = os.path.join(os.path.dirname(__file__), "dashboard.py")
    env = {**os.environ, "BENCHMARK_RUNS_DIR": args.runs_dir}
    cmd = [sys.executable, "-m", "streamlit", "run", dashboard_py,
           "--server.port", str(args.port)]
    if args.headless:
        cmd += ["--server.headless", "true"]
    console.print(f"[green]launching dashboard[/green] http://localhost:{args.port}")
    try:
        return subprocess.run(cmd, env=env).returncode
    except FileNotFoundError:
        console.print("[red]streamlit not installed.[/red] Run `pipenv install streamlit`.")
        return 1
    except KeyboardInterrupt:
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="benchmark", description="LLM quality/speed/cost benchmark.")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run the suite across configured models")
    r.add_argument("--config", default="benchmark.yaml")
    r.add_argument("--models", help="comma-separated subset of model names to run")
    r.add_argument("--filter", action="append", default=[], help="metadata filter key=value (repeatable), e.g. tier=core")
    r.add_argument("--sample", type=int, help="randomly sample N cases")
    r.add_argument("--seed", type=int, default=0, help="sample seed (default 0)")
    r.add_argument("--dry-run", action="store_true", help="use mock models/judge; no spend")
    r.add_argument("--no-cache", action="store_true", help="ignore the response cache")
    r.add_argument("--by-domain", action="store_true", help="also print a per-domain quality table")
    r.add_argument("--open", action="store_true", help="open the HTML report in a browser")
    r.set_defaults(func=cmd_run)

    rep = sub.add_parser("report", help="(re)generate the HTML report for a saved run")
    rep.add_argument("run", nargs="?", help="run JSON path (default: latest)")
    rep.add_argument("--runs-dir", default="runs")
    rep.add_argument("--open", action="store_true", help="open the report in a browser")
    rep.set_defaults(func=cmd_report)

    c = sub.add_parser("compare", help="compare two saved run files")
    c.add_argument("run_a")
    c.add_argument("run_b")
    c.set_defaults(func=cmd_compare)

    lb = sub.add_parser("leaderboard", help="print the latest run's leaderboard")
    lb.add_argument("--runs-dir", default="runs")
    lb.add_argument("--by-domain", action="store_true")
    lb.set_defaults(func=cmd_leaderboard)

    db = sub.add_parser("dashboard", help="launch the Streamlit cross-run leaderboard")
    db.add_argument("--runs-dir", default="runs")
    db.add_argument("--port", type=int, default=8501)
    db.add_argument("--headless", action="store_true", help="run without opening a browser")
    db.set_defaults(func=cmd_dashboard)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
