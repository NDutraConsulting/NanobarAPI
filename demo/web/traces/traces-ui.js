// traces-ui.js
// Pure DOM rendering/manipulation functions for the trace list page.
// Given data, update the DOM. No fetch() calls happen in this file.

const statusEl = document.getElementById("traces-status");
const tableWrapEl = document.getElementById("traces-table-wrap");
const tableBodyEl = document.getElementById("traces-table-body");
const emptyEl = document.getElementById("traces-empty");

/**
 * Format a nanosecond Unix epoch integer into a human-readable date/time.
 * Falls back to a dash if the value is missing or not a finite number.
 * @param {number} recordedAtNs
 * @returns {string}
 */
export function formatNsTimestamp(recordedAtNs) {
  if (typeof recordedAtNs !== "number" || !Number.isFinite(recordedAtNs)) {
    return "—";
  }
  const date = new Date(recordedAtNs / 1e6);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

/**
 * Render the list of trace summaries as table rows. Each row links to
 * /traces/{trace_id}. Rows where any_error is true get a visible error
 * flag (row highlight + badge). Shows the empty state when there are none.
 * @param {Array<{trace_id: string, span_count: number, first_recorded_at_ns: number, last_recorded_at_ns: number, any_error: boolean}>} traces
 */
export function renderTraces(traces) {
  tableBodyEl.innerHTML = "";

  if (!traces || traces.length === 0) {
    tableWrapEl.hidden = true;
    emptyEl.hidden = false;
    return;
  }

  emptyEl.hidden = true;
  tableWrapEl.hidden = false;

  for (const trace of traces) {
    const row = document.createElement("tr");
    if (trace.any_error) {
      row.className = "trace-row-error";
    }

    const idCell = document.createElement("td");
    const idLink = document.createElement("a");
    idLink.className = "trace-id-link";
    idLink.href = `/admin/nanobar/traces/${encodeURIComponent(trace.trace_id)}`;
    idLink.textContent = trace.trace_id;
    idCell.appendChild(idLink);

    const spanCell = document.createElement("td");
    spanCell.className = "trace-span-count";
    spanCell.textContent = String(trace.span_count);

    const firstCell = document.createElement("td");
    firstCell.className = "trace-timestamp";
    firstCell.textContent = formatNsTimestamp(trace.first_recorded_at_ns);

    const lastCell = document.createElement("td");
    lastCell.className = "trace-timestamp";
    lastCell.textContent = formatNsTimestamp(trace.last_recorded_at_ns);

    const statusCell = document.createElement("td");
    const badge = document.createElement("span");
    if (trace.any_error) {
      badge.className = "error-badge";
      badge.textContent = "Error";
    } else {
      badge.className = "ok-badge";
      badge.textContent = "OK";
    }
    statusCell.appendChild(badge);

    row.appendChild(idCell);
    row.appendChild(spanCell);
    row.appendChild(firstCell);
    row.appendChild(lastCell);
    row.appendChild(statusCell);
    tableBodyEl.appendChild(row);
  }
}

/** Show a transient status message above the table (e.g. "Loading..."). */
export function showTracesStatus(message) {
  statusEl.textContent = message;
  statusEl.hidden = false;
  statusEl.classList.remove("traces-status-error");
}

/** Show an error status message above the table (e.g. load failure). */
export function showTracesLoadError(message) {
  statusEl.textContent = message;
  statusEl.hidden = false;
  statusEl.classList.add("traces-status-error");
}

/** Hide the status message entirely. */
export function hideTracesStatus() {
  statusEl.hidden = true;
  statusEl.textContent = "";
}

export const elements = {};
