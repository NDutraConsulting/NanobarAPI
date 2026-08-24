"""Server-rendered HTML pages for the Nanobar Dashboard demo app.

Plain f-string HTML generation with `html.escape` for every embedded value, following
`nanobar_api.openapi.get_swagger_ui_html`'s pattern — no template engine dependency.
"""

from __future__ import annotations

import datetime
import html
import json
from typing import Any

from nanobar_api.bricks.schema import REVIEW_STATUSES, BrickReviewStatus, Nanobar, RegressionBrick
from nanobar_api.eventbus.events import Event, TraceSummary

_STYLE = """
<style>
  :root { color-scheme: light dark; }
  body {
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    max-width: 960px;
    margin: 0 auto;
    padding: 1.5rem;
    line-height: 1.5;
  }
  h1, h2 { margin-top: 0; }
  a { color: #2563eb; }
  nav.crumbs { margin-bottom: 1rem; font-size: 0.9rem; }
  nav.crumbs a { margin-right: 0.75rem; }
  .muted { color: #666; font-size: 0.9em; }
  .count { color: #666; font-weight: normal; font-size: 0.8em; }
  table { border-collapse: collapse; width: 100%; margin: 1rem 0; }
  th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #ddd; }
  code, pre { font-family: ui-monospace, "SF Mono", Menlo, monospace; }
  pre {
    background: #f5f5f5;
    padding: 0.75rem;
    border-radius: 6px;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .status-pill {
    display: inline-block;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    font-size: 0.8em;
    background: #e5e7eb;
  }
  .triage-board {
    display: flex;
    gap: 1rem;
    align-items: flex-start;
    overflow-x: auto;
  }
  .triage-column {
    flex: 1 0 220px;
    min-width: 220px;
    background: rgba(127, 127, 127, 0.08);
    border-radius: 8px;
    padding: 0.5rem;
  }
  .triage-column h2 { font-size: 1rem; margin: 0.25rem 0.25rem 0.5rem; }
  .triage-column-dropzone { min-height: 60px; }
  .triage-column-dropzone.drag-over { background: rgba(37, 99, 235, 0.12); border-radius: 6px; }
  .triage-card {
    background: Canvas;
    color: CanvasText;
    border: 1px solid #ccc;
    border-radius: 6px;
    padding: 0.5rem;
    margin-bottom: 0.5rem;
    cursor: grab;
    font-size: 0.85rem;
  }
  .triage-card:hover { border-color: #2563eb; }
  .triage-card.dragging { opacity: 0.4; }
  .triage-card.busy { opacity: 0.6; pointer-events: none; }
  .triage-card.error { border-color: #dc2626; }
  .triage-card .brick-id { font-weight: 600; display: block; }
  .triage-card .brick-path { word-break: break-all; }
  .trace-track {
    position: relative;
    width: 200px;
    height: 10px;
    background: rgba(127, 127, 127, 0.15);
    border-radius: 4px;
  }
  .trace-marker {
    position: absolute;
    top: -3px;
    width: 10px;
    height: 16px;
    border-radius: 3px;
    background: #2563eb;
    transform: translateX(-50%);
  }
  .trace-marker.error { background: #dc2626; }
</style>
"""


def _page(title: str, body: str, *, extra_head: str = "") -> str:
    safe_title = html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
{_STYLE}
{extra_head}
</head>
<body>
{body}
</body>
</html>
"""


def _crumbs(*links: tuple[str, str]) -> str:
    items = "".join(f'<a href="{html.escape(href, quote=True)}">{html.escape(text)}</a>' for href, text in links)
    return f'<nav class="crumbs">{items}</nav>'


def _not_found_page(title: str, message: str) -> str:
    return _page(title, f"<h1>Not found</h1><p>{html.escape(message)}</p>{_crumbs(('/', 'Dashboard'))}")


def render_not_found(message: str) -> str:
    return _not_found_page("Not found", message)


def _brick_summary(brick: RegressionBrick) -> tuple[str, str, str]:
    """Returns (method, path, status_code) as display strings, tolerant of missing keys."""
    method = str(brick.request.get("method") or "")
    path = str(brick.request.get("path") or "")
    status_code = str(brick.response.get("status_code") if brick.response.get("status_code") is not None else "")
    return method, path, status_code


def render_dashboard_page(groups: dict[str, list[Nanobar]]) -> str:
    body = "<h1>Nanobar Dashboard</h1>" + _crumbs(("/triage", "Triage board"), ("/traces", "Traces"))

    if not groups:
        body += "<p>No nanobars found.</p>"
        return _page("Nanobar Dashboard", body)

    for target_type, nanobars in groups.items():
        items = "".join(
            f'<li><a href="/nanobars/{html.escape(n.nanobar_id, quote=True)}">'
            f"{html.escape(n.nanobar_id)}</a> "
            f'<span class="muted">{html.escape(n.system_name)} / {html.escape(n.regression_scenario_type)}</span>'
            f"</li>"
            for n in nanobars
        )
        body += (
            f"<section><h2>{html.escape(target_type)} "
            f'<span class="count">({len(nanobars)})</span></h2><ul>{items}</ul></section>'
        )

    return _page("Nanobar Dashboard", body)


def _format_ns(recorded_at_ns: int) -> str:
    dt = datetime.datetime.fromtimestamp(recorded_at_ns / 1e9, tz=datetime.UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"


def render_traces_list_page(summaries: list[TraceSummary]) -> str:
    rows = ""
    for summary in summaries:
        error_marker = (
            ' <span class="status-pill" style="background:#fecaca;">error</span>' if summary.any_error else ""
        )
        rows += (
            "<tr>"
            f'<td><a href="/traces/{html.escape(summary.trace_id, quote=True)}">'
            f"{html.escape(summary.trace_id)}</a>{error_marker}</td>"
            f"<td>{summary.span_count}</td>"
            f"<td>{html.escape(_format_ns(summary.first_recorded_at_ns))}</td>"
            f"<td>{html.escape(_format_ns(summary.last_recorded_at_ns))}</td>"
            "</tr>"
        )

    table = (
        "<table><thead><tr><th>Trace</th><th>Spans</th><th>First seen</th><th>Last seen</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        if summaries
        else "<p>No traces captured yet.</p>"
    )
    body = "<h1>Traces</h1>" + _crumbs(("/", "Dashboard"), ("/triage", "Triage board")) + table
    return _page("Traces", body)


def render_trace_detail_page(trace_id: str, events: list[Event]) -> str:
    first_ns = events[0].monotonic_ns
    last_ns = events[-1].monotonic_ns
    total_span_ns = max(last_ns - first_ns, 1)

    rows = ""
    for event in events:
        offset_ms = (event.monotonic_ns - first_ns) / 1e6
        offset_pct = (event.monotonic_ns - first_ns) / total_span_ns * 100
        name = str(event.payload.get("name") or "")
        method = str(event.payload.get("http.request.method") or "")
        route = str(event.payload.get("http.route") or "")
        status_code = event.payload.get("status_code")
        status_display = str(status_code) if status_code is not None else ""
        marker_class = "trace-marker error" if event.payload.get("error") else "trace-marker"
        span_id = event.span_id or ""
        rows += (
            "<tr>"
            f"<td>{offset_ms:.2f} ms</td>"
            f'<td><div class="trace-track"><span class="{marker_class}" '
            f'style="left:{offset_pct:.1f}%"></span></div></td>'
            f"<td>{html.escape(name)}</td>"
            f"<td>{html.escape(method)} {html.escape(route)}</td>"
            f"<td>{html.escape(status_display)}</td>"
            f"<td><code>{html.escape(span_id)}</code></td>"
            "</tr>"
        )

    body = (
        f"<h1>Trace {html.escape(trace_id)}</h1>"
        + _crumbs(("/", "Dashboard"), ("/traces", "Traces"))
        + '<p class="muted">Spans ordered by completion time, offset relative to the first span '
        "in this trace. This reflects when each span finished, not its individual duration — "
        "this project's tracing doesn't record per-span start times yet, only a single "
        "completion timestamp per span.</p>"
        + "<table><thead><tr><th>Offset</th><th>Timeline</th><th>Span</th><th>Method / route</th>"
        "<th>Status</th><th>Span ID</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )
    return _page(f"Trace {trace_id}", body)


def render_nanobar_page(nanobar: Nanobar, bricks: list[tuple[RegressionBrick, BrickReviewStatus]]) -> str:
    refs = (
        ", ".join(f"{html.escape(r.target_type)}:{html.escape(r.stable_name)}" for r in nanobar.monitor_target_refs)
        or "(none)"
    )

    rows = ""
    for brick, status in bricks:
        method, path, status_code = _brick_summary(brick)
        rows += (
            "<tr>"
            f'<td><a href="/bricks/{html.escape(brick.regression_brick_id, quote=True)}">'
            f"{html.escape(brick.regression_brick_id)}</a></td>"
            f"<td>{html.escape(method)}</td>"
            f"<td>{html.escape(path)}</td>"
            f"<td>{html.escape(status_code)}</td>"
            f"<td><code>{html.escape(brick.content_hash[:12])}</code></td>"
            f'<td><span class="status-pill">{html.escape(status.status)}</span></td>'
            "</tr>"
        )

    table = (
        "<table><thead><tr><th>Brick</th><th>Method</th><th>Path</th><th>Status</th>"
        f"<th>Content hash</th><th>Review status</th></tr></thead><tbody>{rows}</tbody></table>"
        if bricks
        else "<p>No bricks bound to this nanobar yet.</p>"
    )

    body = (
        f"<h1>{html.escape(nanobar.nanobar_id)}</h1>"
        + _crumbs(("/", "Dashboard"), ("/triage", "Triage board"), ("/traces", "Traces"))
        + "<dl>"
        + f"<dt>System</dt><dd>{html.escape(nanobar.system_name)} ({html.escape(nanobar.system_version)})</dd>"
        + f"<dt>Scenario type</dt><dd>{html.escape(nanobar.regression_scenario_type)}</dd>"
        + f"<dt>Regression weight</dt><dd>{nanobar.regression_weight}</dd>"
        + f"<dt>Monitor targets</dt><dd>{refs}</dd>"
        + "</dl>"
        + "<h2>Bound RegressionBricks</h2>"
        + table
    )
    return _page(f"Nanobar {nanobar.nanobar_id}", body)


def render_brick_page(brick: RegressionBrick, status: BrickReviewStatus, nanobars: list[Nanobar]) -> str:
    method, path, status_code = _brick_summary(brick)
    nanobar_links = (
        ", ".join(
            f'<a href="/nanobars/{html.escape(n.nanobar_id, quote=True)}">{html.escape(n.nanobar_id)}</a>'
            for n in nanobars
        )
        or "(unbound)"
    )

    def _pretty(data: dict[str, Any]) -> str:
        return html.escape(json.dumps(data, indent=2, sort_keys=True))

    body = (
        f"<h1>{html.escape(brick.regression_brick_id)}</h1>"
        + _crumbs(("/", "Dashboard"), ("/triage", "Triage board"), ("/traces", "Traces"))
        + "<dl>"
        + f"<dt>Method / Path</dt><dd>{html.escape(method)} {html.escape(path)}</dd>"
        + f"<dt>Status code</dt><dd>{html.escape(status_code)}</dd>"
        + f"<dt>Content hash</dt><dd><code>{html.escape(brick.content_hash)}</code></dd>"
        + f"<dt>Schema / brick version</dt><dd>{html.escape(brick.schema_version)} / {brick.brick_version}</dd>"
        + f"<dt>Created by</dt><dd>{html.escape(brick.created_by)}</dd>"
        + f'<dt>Review status</dt><dd><span class="status-pill">{html.escape(status.status)}</span> '
        + f"(updated by {html.escape(status.updated_by)})</dd>"
        + f"<dt>Bound nanobars</dt><dd>{nanobar_links}</dd>"
        + "</dl>"
        + f"<h2>Request</h2><pre>{_pretty(brick.request)}</pre>"
        + f"<h2>Response</h2><pre>{_pretty(brick.response)}</pre>"
        + f"<h2>Source</h2><pre>{_pretty(brick.source)}</pre>"
        + f"<h2>Trace refs</h2><pre>{html.escape(json.dumps(brick.trace_refs, indent=2))}</pre>"
    )
    return _page(f"Brick {brick.regression_brick_id}", body)


def render_triage_page(bricks_by_status: dict[str, list[RegressionBrick]]) -> str:
    columns = ""
    for status in REVIEW_STATUSES:
        bricks = bricks_by_status.get(status, [])
        cards = ""
        for brick in bricks:
            method, path, _status_code = _brick_summary(brick)
            cards += (
                f'<article class="triage-card" draggable="true" '
                f'data-brick-id="{html.escape(brick.regression_brick_id, quote=True)}">'
                f'<span class="brick-id">{html.escape(brick.regression_brick_id)}</span>'
                f'<span class="brick-path">{html.escape(method)} {html.escape(path)}</span> '
                f"<code>{html.escape(brick.content_hash[:8])}</code>"
                f"</article>"
            )
        columns += (
            f'<section class="triage-column" data-status="{html.escape(status, quote=True)}">'
            f'<h2>{html.escape(status)} <span class="triage-column-count count">({len(bricks)})</span></h2>'
            f'<div class="triage-column-dropzone" data-status="{html.escape(status, quote=True)}">{cards}</div>'
            f"</section>"
        )

    body = (
        "<h1>Triage board</h1>"
        + _crumbs(("/", "Dashboard"), ("/traces", "Traces"))
        + f'<div class="triage-board">{columns}</div>'
        + '<script src="/static/triage.js"></script>'
    )
    return _page("Triage board", body)
