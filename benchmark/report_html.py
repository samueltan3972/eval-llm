"""Render a run into a single self-contained HTML report.

No server, no CDN, no JS framework: tables and charts are inline HTML + SVG and collapsible
drill-in uses native <details>, so the file works offline and can be shared or committed.
Build with `python -m benchmark report` or automatically after each `run`.
"""

from __future__ import annotations

import html
import json
import math

# Color palette for score-driven UI: red (0) -> amber -> green (100).
PALETTE = ["#d64545", "#e08a2e", "#e0c52e", "#9bbf3a", "#4f9d4f"]


def _esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _score_color(value: float) -> str:
    """A hue from red->green by score (0-100), as an hsl() string."""
    v = max(0.0, min(100.0, float(value)))
    return f"hsl({v * 1.2:.0f}, 62%, 45%)"


def _heat_color(value: float) -> str:
    v = max(0.0, min(100.0, float(value)))
    return f"hsl({v * 1.2:.0f}, 70%, 86%)"


def _sorted_models(run: dict) -> list[dict]:
    return sorted(run.get("models", []), key=lambda m: m.get("overall", 0), reverse=True)


def _cost_str(m: dict) -> str:
    return f"{'~' if m.get('cost_estimated') else ''}${m.get('avg_cost_usd', 0):.5f}"


# --- pieces ---------------------------------------------------------------------------
def _bar(value: float) -> str:
    v = max(0.0, min(100.0, float(value)))
    return (
        f'<div class="bar"><div class="bar-fill" style="width:{v:.0f}%;'
        f'background:{_score_color(v)}"></div><span class="bar-val">{v:.1f}</span></div>'
    )


def _leaderboard_table(run: dict) -> str:
    models = _sorted_models(run)
    rows = []
    for m in models:
        anchor = _esc(m["name"])
        label = _esc(m.get("display") or m["name"])
        cost = float(m.get("avg_cost_usd", 0) or 0)
        rows.append(
            "<tr>"
            f'<td class="model"><a href="#m-{anchor}">{label}</a></td>'
            f'<td class="num overall" style="color:{_score_color(m.get("overall", 0))}">{m.get("overall", 0):.1f}</td>'
            f"<td>{_bar(m.get('quality', 0))}</td>"
            f"<td>{_bar(m.get('speed', 0))}</td>"
            f"<td>{_bar(m.get('cost', 0))}</td>"
            f'<td class="num">{m.get("avg_latency_ms", 0):.0f}ms</td>'
            f'<td class="num">{m.get("avg_tokens_per_sec", 0):.1f}{_overhead_note(m)}</td>'
            f'<td class="num mult">{_mult_html(m.get("speed_vs_fastest"))}</td>'
            f'<td class="num">{_cost_str(m)}</td>'
            f'<td class="num mult cost-mult" data-cost="{json.dumps(cost)}">{_mult_html(m.get("cost_vs_cheapest"))}</td>'
            f'<td class="num">{m.get("n_errors", 0)}</td>'
            "</tr>"
        )
    return (
        '<table class="lb"><thead><tr>'
        "<th>Model</th><th>Overall</th><th>Quality</th><th>Speed</th><th>Cost</th>"
        "<th>avg latency</th><th>tok/s</th><th>vs fastest</th><th>$/call</th>"
        f"<th class=\"vs-th\">vs {_baseline_select(models)}</th><th>errors</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _mult_html(v) -> str:
    return f"{v:.2f}×" if v is not None else "–"


def _overhead_note(m: dict) -> str:
    """Flag tok/s that had a calibrated CLI startup overhead subtracted (generation-only speed)."""
    ov = m.get("startup_overhead_ms") or 0.0
    if ov <= 0:
        return ""
    return (f'<sup class="ohint" title="generation speed: {ov:.0f}ms of calibrated CLI '
            f'startup subtracted from latency before computing tok/s">−{ov:.0f}ms</sup>')


def _baseline_select(models: list[dict]) -> str:
    """A <select> of all models for the 'vs <baseline>' multiplier; default = cheapest."""
    costs = [(m, float(m.get("avg_cost_usd", 0) or 0)) for m in models]
    positive = [(m, c) for m, c in costs if c > 0]
    cheapest = min(positive, key=lambda mc: mc[1])[0] if positive else (models[0] if models else None)
    opts = []
    for m in models:
        sel = " selected" if cheapest is not None and m["name"] == cheapest["name"] else ""
        opts.append(
            f'<option value="{_esc(m["name"])}" data-cost="{json.dumps(float(m.get("avg_cost_usd", 0) or 0))}"{sel}>'
            f'{_esc(m.get("display") or m["name"])}</option>'
        )
    return f'<select id="baseline-select" onchange="rebaseMult()">{"".join(opts)}</select>'


def _radar(run: dict) -> str:
    """Overlay each model as a triangle across the quality/speed/cost axes."""
    models = _sorted_models(run)
    if not models:
        return ""
    size, cx, cy, R = 320, 160, 165, 120
    axes = ["quality", "speed", "cost"]
    labels = ["Quality", "Speed", "Cost"]
    angles = [-math.pi / 2 + i * 2 * math.pi / 3 for i in range(3)]

    def pt(r, ang):
        return cx + r * math.cos(ang), cy + r * math.sin(ang)

    parts = [f'<svg viewBox="0 0 {size} {size + 20}" width="340" class="radar">']
    # grid rings + axis spokes + labels
    for frac in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(R * frac, a) for a in angles))
        parts.append(f'<polygon points="{pts}" class="grid"/>')
    for a, lab in zip(angles, labels):
        x, y = pt(R, a)
        lx, ly = pt(R + 22, a)
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" class="spoke"/>')
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" class="axis-label" text-anchor="middle">{lab}</text>')

    legend = []
    for i, m in enumerate(models):
        color = _PLOT_COLORS[i % len(_PLOT_COLORS)]
        pts = " ".join(
            f"{x:.1f},{y:.1f}"
            for x, y in (pt(R * max(0, min(100, m.get(ax, 0))) / 100, a) for ax, a in zip(axes, angles))
        )
        parts.append(f'<polygon points="{pts}" fill="{color}" fill-opacity="0.18" stroke="{color}" stroke-width="2"/>')
        legend.append(f'<span class="leg"><i style="background:{color}"></i>{_esc(m.get("display") or m["name"])}</span>')
    parts.append("</svg>")
    return '<div class="radar-wrap">' + "".join(parts) + '<div class="legend">' + "".join(legend) + "</div></div>"


_PLOT_COLORS = ["#3b6fd6", "#d6643b", "#3bb273", "#9b59b6", "#e0a800", "#16a3a3"]


def _heatmap(run: dict) -> str:
    models = _sorted_models(run)
    domains = sorted({d for m in models for d in (m.get("quality_by_domain") or {})})
    if not domains:
        return ""
    head = "".join(f"<th>{_esc(d)}</th>" for d in domains)
    rows = []
    for m in models:
        qbd = m.get("quality_by_domain") or {}
        cells = []
        for d in domains:
            if d in qbd:
                v = qbd[d]
                cells.append(f'<td class="heat" style="background:{_heat_color(v)}">{v:.0f}</td>')
            else:
                cells.append('<td class="heat na">–</td>')
        rows.append(f'<tr><td class="model">{_esc(m.get("display") or m["name"])}</td>{"".join(cells)}</tr>')
    return (
        '<table class="heatmap"><thead><tr><th>Model</th>' + head + "</tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
    )


def _head_to_head(run: dict) -> str:
    """Per-run pairwise 'local judging': a win-rate bar list + the all-pairs win matrix.

    Relative to the models in this run only (not a cross-run number). Renders nothing when the
    run has no open-ended cases (objective-only runs carry an empty head_to_head)."""
    h2h = run.get("head_to_head") or {}
    models = h2h.get("models") or []
    matrix = h2h.get("matrix") or {}
    win_rate = h2h.get("win_rate") or {}
    if len(models) < 2:
        return ""
    labels = {m["name"]: (m.get("display") or m["name"]) for m in run.get("models", [])}

    # Win-rate bars, best first.
    ranked = sorted(models, key=lambda n: win_rate.get(n, 0), reverse=True)
    bars = "".join(
        f'<tr><td class="model">{_esc(labels.get(n, n))}</td>'
        f"<td>{_bar(100.0 * (win_rate.get(n) or 0))}</td></tr>"
        for n in ranked
    )
    wr_table = (
        '<table class="lb"><thead><tr><th>Model</th><th>Win-rate</th></tr></thead>'
        f"<tbody>{bars}</tbody></table>"
    )

    # Win matrix: cell [row vs col] = row's average win-weight (heat-colored).
    head = "".join(f"<th>{_esc(labels.get(c, c))}</th>" for c in models)
    rows = []
    for a in models:
        cells = []
        for b in models:
            if a == b:
                cells.append('<td class="heat na">–</td>')
                continue
            v = matrix.get(a, {}).get(b)
            if v is None:
                cells.append('<td class="heat na">–</td>')
            else:
                pct = 100.0 * float(v)
                cells.append(f'<td class="heat" style="background:{_heat_color(pct)}">{v:.2f}</td>')
        rows.append(f'<tr><td class="model">{_esc(labels.get(a, a))}</td>{"".join(cells)}</tr>')
    matrix_table = (
        '<table class="heatmap"><thead><tr><th>vs →</th>' + head + "</tr></thead><tbody>"
        + "".join(rows) + "</tbody></table>"
    )

    return (
        '<h2>Head-to-head (open-ended cases)</h2>'
        '<p class="footnote" style="margin-top:-6px">Per-run pairwise judging by the ensemble '
        '(order-swapped, blind). Relative to the models in <em>this</em> run only — not the '
        'cross-run quality score. Matrix cell = row model’s avg win-weight vs the column model.</p>'
        '<div class="cols">'
        f'<div class="card"><h2 style="margin-top:0">Win-rate</h2>{wr_table}</div>'
        f'<div class="card"><h2 style="margin-top:0">Win matrix</h2>{matrix_table}</div>'
        "</div>"
    )


def _assertion_rows(assertions: list[dict]) -> str:
    if not assertions:
        return '<div class="muted">no assertions</div>'
    items = []
    for a in assertions:
        score = a.get("score")
        if score is None:
            dot, sc = '<span class="dot skip"></span>', "–"
        else:
            dot = f'<span class="dot {"pass" if a.get("pass") else "fail"}"></span>'
            sc = f"{score:.2f}"
        items.append(
            f'<li>{dot}<code>{_esc(a.get("type"))}</code> '
            f'<b>{sc}</b> <span class="reason">{_esc(a.get("reason"))}</span>'
            f"{_per_judge_html(a)}</li>"
        )
    return '<ul class="asserts">' + "".join(items) + "</ul>"


def _per_judge_html(a: dict) -> str:
    """Ensemble breakdown for an llm-rubric assert. The summary shows each judge's score + the
    disagreement spread (compact); expanding reveals each judge's individual vote and reasoning."""
    per_judge = a.get("per_judge")
    if not per_judge:
        return ""
    dis = a.get("disagreement") or 0.0

    def _score(p) -> str:
        s = p.get("score")
        return "–" if s is None else f"{s:.2f}"

    votes = " ".join(
        f'<span class="jvote"><b>{_esc(p.get("judge"))}</b> {_score(p)}</span>'
        for p in per_judge
    )
    rows = "".join(
        f'<li><span class="jvote"><b>{_esc(p.get("judge"))}</b> {_score(p)}</span>'
        f'<span class="reason">{_esc(p.get("reason")) or "—"}</span></li>'
        for p in per_judge
    )
    return (
        '<details class="ensemble"><summary>'
        f'{votes}<span class="dis">Δ {dis:.2f}</span>'
        '<span class="jhint">judge votes</span></summary>'
        f'<ul class="jvotes">{rows}</ul>'
        "</details>"
    )


def _details_section(run: dict) -> str:
    details = run.get("details", [])
    by_model: dict[str, list[dict]] = {}
    for d in details:
        by_model.setdefault(d["model"], []).append(d)

    blocks = []
    for i, m in enumerate(_sorted_models(run)):
        name = m["name"]
        label = m.get("display") or name
        cases = sorted(by_model.get(name, []), key=lambda d: (d.get("domain", ""), d.get("description", "")))
        case_html = []
        for c in cases:
            q = c.get("quality")
            q_label = "err" if c.get("error") else ("–" if q is None else f"{q * 100:.0f}")
            badge_color = "#888" if (q is None or c.get("error")) else _score_color((q or 0) * 100)
            err = f'<div class="err">error: {_esc(c["error"])}</div>' if c.get("error") else ""
            pw = c.get("pairwise_win")
            pw_badge = (
                f'<span class="pwin" title="pairwise win-weight vs other models on this case">'
                f"win {pw:.2f}</span>" if pw is not None else ""
            )
            case_html.append(
                "<details class=\"case\">"
                f'<summary><span class="qbadge" style="background:{badge_color}">{q_label}</span>'
                f'<span class="cdomain">{_esc(c.get("domain"))}</span> {_esc(c.get("description"))}'
                f"{pw_badge}"
                f'<span class="cmeta">{c.get("latency_ms", 0):.0f}ms · '
                f'{c.get("output_tokens", 0)} out tok · ${c.get("cost_usd", 0):.5f}</span></summary>'
                f"{err}"
                f'<div class="sub">Output</div><pre class="output">{_esc(c.get("output"))}</pre>'
                f'<div class="sub">Assertions</div>{_assertion_rows(c.get("assertions", []))}'
                "</details>"
            )
        blocks.append(
            f'<details class="model-block" id="m-{_esc(name)}"{" open" if i == 0 else ""}>'
            f'<summary class="model-sum"><b>{_esc(label)}</b>'
            f'<span class="ov" style="color:{_score_color(m.get("overall", 0))}">overall {m.get("overall", 0):.1f}</span>'
            f'<span class="cmeta">{len(cases)} cases · {m.get("n_errors", 0)} errors</span></summary>'
            + "".join(case_html) + "</details>"
        )
    return "".join(blocks)


# --- top level ------------------------------------------------------------------------
def render_report(run: dict) -> str:
    estimated = any(m.get("cost_estimated") for m in run.get("models", []))
    calibrated = any((m.get("startup_overhead_ms") or 0) > 0 for m in run.get("models", []))
    note_bits = []
    if estimated:
        note_bits.append(
            "~ cost is <b>estimated</b> from a tokenizer (CLI tools report no token usage); "
            "set real prices in benchmark.yaml."
        )
    note_bits.append("Speed is tokens/sec vs a fixed anchor.")
    if calibrated:
        note_bits.append(
            "A <b>−Nms</b> tag on tok/s means that much calibrated CLI startup (process boot, "
            "auth, handshake) was subtracted from latency, so speed reflects generation rather "
            "than process boot. Raw latency is shown unmodified."
        )
    note = f'<p class="footnote">{" ".join(note_bits)}</p>'
    meta = (
        f'<span>{run.get("n_cases", "?")} cases</span>'
        f'<span>suite <code>{_esc(run.get("suite_hash", "?"))[:8]}</code></span>'
        f'<span>judges: {_esc(", ".join(run.get("judges") or []) or "—")}</span>'
        f'<span>weights {_esc(run.get("weights_note", "equal"))}</span>'
        f'<span>{_esc(run.get("timestamp", ""))}</span>'
    )
    return _TEMPLATE.format(
        title=_esc(f"Benchmark — {run.get('label', 'run')} — {run.get('timestamp', '')}"),
        style=_STYLE,
        label=_esc(run.get("label", "run")),
        meta=meta,
        leaderboard=_leaderboard_table(run),
        radar=_radar(run),
        heatmap=_heatmap(run),
        head_to_head=_head_to_head(run),
        details=_details_section(run),
        note=note,
        script=_LEADERBOARD_JS,
    )


_STYLE = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1f2733;--muted:#6b7480;--line:#e6e8ec}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:15px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:30px 0 12px}
.meta{display:flex;flex-wrap:wrap;gap:14px;color:var(--muted);font-size:13px;margin-bottom:6px}
.meta code{color:var(--ink)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;overflow-x:auto}
table{border-collapse:collapse;width:100%}
th{text-align:left;font-size:12px;color:var(--muted);font-weight:600;padding:6px 10px;border-bottom:1px solid var(--line)}
td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:middle}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.overall{font-weight:700;font-size:15px}
.model a{color:var(--ink);text-decoration:none;font-weight:600}
.model a:hover{text-decoration:underline}
.bar{position:relative;background:#eef0f3;border-radius:5px;height:20px;min-width:120px}
.bar-fill{height:100%;border-radius:5px}
.bar-val{position:absolute;right:7px;top:0;line-height:20px;font-size:12px;font-variant-numeric:tabular-nums}
.cols{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}
.cols>.card{flex:1;min-width:320px}
.radar-wrap{display:flex;flex-direction:column;align-items:center}
.radar .grid{fill:none;stroke:#e3e6ea}
.radar .spoke{stroke:#d4d8dd}
.radar .axis-label{font-size:12px;fill:var(--muted)}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:8px}
.leg{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:5px}
.leg i{width:11px;height:11px;border-radius:2px;display:inline-block}
.heatmap td.heat{text-align:center;font-variant-numeric:tabular-nums}
.heatmap td.na{color:var(--muted);background:#f4f5f7}
.model-block{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:10px 0;padding:4px 14px}
.model-sum{cursor:pointer;padding:10px 0;display:flex;gap:14px;align-items:center;font-size:15px}
.model-sum .ov{font-weight:700}
.cmeta{color:var(--muted);font-size:12px;margin-left:auto;font-weight:400}
.case{border-top:1px solid var(--line);padding:6px 0}
.case summary{cursor:pointer;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.qbadge{color:#fff;font-size:11px;font-weight:700;padding:2px 7px;border-radius:10px;min-width:30px;text-align:center}
.cdomain{color:var(--muted);font-size:12px;font-family:ui-monospace,monospace}
.sub{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin:10px 0 4px}
pre.output{background:#0f1620;color:#d7e0ea;padding:12px;border-radius:8px;overflow-x:auto;white-space:pre-wrap;word-break:break-word;font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;max-height:340px}
ul.asserts{list-style:none;margin:0;padding:0}
ul.asserts li{padding:4px 0;border-bottom:1px dashed var(--line)}
ul.asserts code{background:#eef0f3;padding:1px 6px;border-radius:4px}
.reason{color:var(--muted)}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px}
.dot.pass{background:#4f9d4f}.dot.fail{background:#d64545}.dot.skip{background:#c2c8d0}
.err{color:#d64545;font-size:13px;margin:6px 0}
.muted{color:var(--muted)}
.footnote{color:var(--muted);font-size:12px;margin-top:18px}
.vs-th select{font:inherit;color:var(--ink);border:1px solid var(--line);border-radius:5px;padding:1px 4px;background:#fff}
td.mult{font-weight:600}
.pwin{font-size:11px;font-weight:600;color:var(--muted);background:#eef0f3;border-radius:10px;padding:1px 7px}
sup.ohint{color:var(--muted);font-size:9px;font-weight:600;margin-left:2px;cursor:help}
.ensemble{margin:4px 0 0 17px;font-size:12px;color:var(--muted)}
.ensemble>summary{display:flex;flex-wrap:wrap;gap:8px;align-items:center;cursor:pointer;list-style:none}
.ensemble>summary::-webkit-details-marker{display:none}
.ensemble>summary::before{content:"▸ ";color:var(--muted);font-size:10px}
.ensemble[open]>summary::before{content:"▾ "}
.jhint{color:var(--muted);font-style:italic}
.ensemble[open] .jhint{display:none}
.jvote{background:#eef0f3;border-radius:4px;padding:1px 6px;font-variant-numeric:tabular-nums;white-space:nowrap}
.jvote b{color:var(--ink);font-weight:600}
.dis{color:#d6643b}
ul.jvotes{list-style:none;margin:6px 0 2px;padding:0 0 0 14px}
ul.jvotes li{border-bottom:none;padding:3px 0;display:flex;gap:8px;align-items:baseline}
ul.jvotes .reason{color:var(--muted)}
.heatmap th:first-child,.heatmap td.model{white-space:nowrap}
"""

# Recomputes the "vs <baseline>" multiplier column from each cell's data-cost when the baseline
# <select> changes. Self-contained (no library); data lives in data-cost attributes.
_LEADERBOARD_JS = """
<script>
function rebaseMult(){
  var sel=document.getElementById('baseline-select'); if(!sel) return;
  var base=parseFloat(sel.options[sel.selectedIndex].getAttribute('data-cost'));
  document.querySelectorAll('td.cost-mult').forEach(function(td){
    var c=parseFloat(td.getAttribute('data-cost'));
    if(!isFinite(base)||base<=0){ td.textContent='–'; return; }
    td.textContent=(c/base).toFixed(2)+'×';
  });
}
document.addEventListener('DOMContentLoaded', rebaseMult);
</script>
"""

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{style}</style></head>
<body><div class="wrap">
<h1>Benchmark — {label}</h1>
<div class="meta">{meta}</div>
<h2>Leaderboard</h2>
<div class="card">{leaderboard}</div>
<div class="cols">
  <div class="card"><h2 style="margin-top:0">Score profile</h2>{radar}</div>
  <div class="card"><h2 style="margin-top:0">Quality by domain</h2>{heatmap}</div>
</div>
{head_to_head}
<h2>Per-case drill-in</h2>
{details}
{note}
</div>{script}</body></html>
"""
