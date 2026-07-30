"""Render an evaluation report (the dict harness.py produces) as a single
self-contained HTML file -- no external assets, so it opens directly from disk.
Gitignored (evaluation/reports/*.html) since it's a generated artifact, regenerated
per run.
"""

from __future__ import annotations

import html


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _row(record: dict) -> str:
    score = record.get("score")
    if score is None:
        return (
            f"<tr class='fail'><td>{html.escape(record['scenario_id'])}</td>"
            f"<td>{record['seed']}</td><td colspan='6'>FAILED: {html.escape(str(record.get('error')))}</td></tr>"
        )
    diagnosis = record.get("diagnosis", {})
    ok = lambda b: "✓" if b else "✗"  # noqa: E731
    return (
        "<tr>"
        f"<td>{html.escape(record['scenario_id'])}</td>"
        f"<td>{record['seed']}</td>"
        f"<td class='{'pass' if score['root_cause_correct'] else 'bad'}'>{ok(score['root_cause_correct'])}</td>"
        f"<td class='{'pass' if score['affected_service_correct'] else 'bad'}'>{ok(score['affected_service_correct'])}</td>"
        f"<td class='{'pass' if score['triggering_change_correct'] else 'bad'}'>{ok(score['triggering_change_correct'])}</td>"
        f"<td>{diagnosis.get('confidence', 'n/a')}</td>"
        f"<td>{record.get('diagnosis_time_seconds', 'n/a')}s</td>"
        f"<td class='{'pass' if not score['unsupported_claims'] else 'bad'}'>{len(score['unsupported_claims'])}</td>"
        "</tr>"
    )


def render(report: dict) -> str:
    summary = report["summary"]
    rows = "\n".join(_row(r) for r in report["runs"])
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>FaultLine evaluation report</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; max-width: 900px; margin: 2rem auto; color: #1a1a1a; }}
  h1 {{ font-size: 1.4rem; }}
  .summary {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.75rem; margin: 1.5rem 0; }}
  .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 0.75rem 1rem; }}
  .card .label {{ font-size: 0.8rem; color: #666; }}
  .card .value {{ font-size: 1.4rem; font-weight: 600; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
  th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #eee; }}
  tr.fail td {{ color: #a00; }}
  td.pass {{ color: #0a7a2f; font-weight: 600; }}
  td.bad {{ color: #a00; font-weight: 600; }}
  .meta {{ color: #888; font-size: 0.85rem; }}
</style></head>
<body>
  <h1>FaultLine evaluation report</h1>
  <div class="meta">Generated {html.escape(report['generated_at'])}</div>
  <div class="summary">
    <div class="card"><div class="label">Root-cause accuracy</div><div class="value">{_fmt_pct(summary['root_cause_accuracy'])}</div></div>
    <div class="card"><div class="label">Affected-service accuracy</div><div class="value">{_fmt_pct(summary['affected_service_accuracy'])}</div></div>
    <div class="card"><div class="label">Triggering-change accuracy</div><div class="value">{_fmt_pct(summary['triggering_change_accuracy'])}</div></div>
    <div class="card"><div class="label">Runs scored / total</div><div class="value">{summary['scored_runs']} / {summary['total_runs']}</div></div>
    <div class="card"><div class="label">Avg diagnosis time</div><div class="value">{summary['avg_diagnosis_time_seconds']}s</div></div>
    <div class="card"><div class="label">Unsupported claims (total)</div><div class="value">{summary['total_unsupported_claims']}</div></div>
  </div>
  <table>
    <thead><tr><th>Scenario</th><th>Seed</th><th>Root cause</th><th>Service</th><th>Trigger</th><th>Confidence</th><th>Time</th><th>Unsupported</th></tr></thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body></html>
"""


def write_html_report(report: dict, out_path) -> None:
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(render(report))
