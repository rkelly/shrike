"""
Report generation: HTML and JSON.

Part of the Shrike SQL test runner.
Built with Claude Opus 4.6 (Anthropic). Licensed under MIT.
"""

import datetime
import json
from pathlib import Path

from .engine import TestResult


def generate_json_report(results: list[TestResult], path: Path):
    """Write results as JSON."""
    data = {
        "run_timestamp": datetime.datetime.now().isoformat(),
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
        },
        "tests": [
            {
                "test_name": r.test_name,
                "file": r.file_path,
                "passed": r.passed,
                "message": r.message,
                "duration_ms": round(r.duration_ms, 2),
                "tags": r.tags,
                "steps": [
                    {
                        "step": s.step_name,
                        "server": s.server,
                        "database": s.database,
                        "rows_returned": len(s.rows),
                        "duration_ms": round(s.duration_ms, 2),
                        "error": s.error,
                    }
                    for s in r.steps
                ],
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def generate_html_report(results: list[TestResult], path: Path):
    """Write a self-contained HTML report."""
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    rows_html = []
    for r in results:
        status = "✅ PASS" if r.passed else "❌ FAIL"
        color = "#e6ffe6" if r.passed else "#ffe6e6"
        steps_detail = "<br>".join(
            f"<small>{s.step_name}: {s.server}/{s.database} "
            f"({len(s.rows)} rows, {s.duration_ms:.0f}ms)"
            f"{'  ⚠️ ' + s.error if s.error else ''}</small>"
            for s in r.steps
        )
        rows_html.append(
            f'<tr style="background:{color}">'
            f"<td>{status}</td>"
            f"<td><strong>{r.test_name}</strong><br><small>{r.file_path}</small></td>"
            f"<td>{r.message}</td>"
            f"<td>{steps_detail}</td>"
            f"<td>{r.duration_ms:.0f}ms</td>"
            f"</tr>"
        )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Shrike Test Report</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #fafafa; }}
  h1 {{ color: #333; }}
  .summary {{ font-size: 1.2rem; margin: 1rem 0; }}
  .pass {{ color: #2d7a2d; }} .fail {{ color: #c0392b; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
  th, td {{ border: 1px solid #ccc; padding: 8px 12px; text-align: left; vertical-align: top; }}
  th {{ background: #333; color: white; }}
  small {{ color: #666; }}
</style></head><body>
<h1>Shrike Test Report</h1>
<p class="summary">
  Run: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")} &mdash;
  <span class="pass">{passed} passed</span> /
  <span class="fail">{failed} failed</span> /
  {len(results)} total
</p>
<table>
<tr><th>Status</th><th>Test</th><th>Message</th><th>Steps</th><th>Duration</th></tr>
{"".join(rows_html)}
</table></body></html>"""
    path.write_text(html, encoding="utf-8")
