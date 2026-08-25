// trace-ui.js
// Pure DOM rendering/manipulation functions for the single-trace span
// timeline page. Given data, update the DOM. No fetch() calls happen in
// this file.

const titleEl = document.getElementById("trace-title");
const statusEl = document.getElementById("trace-status");
const disclaimerEl = document.getElementById("trace-disclaimer");
const spansWrapEl = document.getElementById("spans-wrap");
const spansListEl = document.getElementById("spans-list");
const emptyEl = document.getElementById("trace-empty");
const rowTemplate = document.getElementById("span-row-template");

export const elements = {};

/** Set the page heading/title to reflect which trace is being viewed. */
export function setTraceId(traceId) {
  titleEl.textContent = `Trace ${traceId}`;
  document.title = `Trace ${traceId} · Nanobar Dashboard`;
}

function hideContent() {
  disclaimerEl.hidden = true;
  spansWrapEl.hidden = true;
  emptyEl.hidden = true;
}

/** Show a transient loading message and hide any previously rendered content. */
export function showLoading() {
  statusEl.hidden = false;
  statusEl.className = "trace-status";
  statusEl.textContent = "Loading spans…";
  hideContent();
}

/** Show a not-found state (envelope error / 404, or an empty spans list). */
export function showNotFound(message) {
  statusEl.hidden = false;
  statusEl.className = "trace-status trace-status-error";
  statusEl.textContent = message || "Trace not found.";
  hideContent();
}

/** Show a network-failure state, distinct from an envelope-level error. */
export function showNetworkError(message) {
  statusEl.hidden = false;
  statusEl.className = "trace-status trace-status-error";
  statusEl.textContent = message || "Could not reach the server. Please try again.";
  hideContent();
}

/**
 * Render the full span timeline for a trace, given its events already
 * ordered by monotonic_ns (ascending) by the backend. Each span's offset
 * in ms is computed relative to the first event's monotonic_ns; the visual
 * marker position is that offset as a percentage of the total range across
 * all events in the list (guarded against a zero-width range).
 *
 * These are completion-time offsets, not span durations:
 * EventBusTraceMiddleware records one monotonic_ns timestamp per span, not
 * a start+end pair, so no individual span duration is ever computed here.
 *
 * @param {Array<{event_id: string, span_id: string|null, monotonic_ns: number, payload: object}>} events
 */
export function renderSpans(events) {
  statusEl.hidden = true;
  spansListEl.textContent = "";

  if (!events || events.length === 0) {
    disclaimerEl.hidden = true;
    spansWrapEl.hidden = true;
    emptyEl.hidden = false;
    return;
  }

  emptyEl.hidden = true;
  disclaimerEl.hidden = false;
  spansWrapEl.hidden = false;

  const firstNs = events[0].monotonic_ns;
  const lastNs = events[events.length - 1].monotonic_ns;
  const totalRangeNs = lastNs - firstNs;

  for (const event of events) {
    spansListEl.appendChild(buildSpanRow(event, firstNs, totalRangeNs));
  }
}

function buildSpanRow(event, firstNs, totalRangeNs) {
  const fragment = rowTemplate.content.cloneNode(true);
  const row = fragment.querySelector(".span-row");
  const offsetEl = fragment.querySelector(".span-offset");
  const nameEl = fragment.querySelector(".span-name");
  const statusCodeEl = fragment.querySelector(".span-status-code");
  const marker = fragment.querySelector(".span-marker");
  const methodEl = fragment.querySelector(".span-method");
  const routeEl = fragment.querySelector(".span-route");
  const spanIdEl = fragment.querySelector(".span-id");

  const payload = event.payload || {};
  const offsetMs = (event.monotonic_ns - firstNs) / 1e6;
  const percent = totalRangeNs === 0 ? 0 : ((event.monotonic_ns - firstNs) / totalRangeNs) * 100;

  offsetEl.textContent = `+${offsetMs.toFixed(2)} ms`;
  marker.style.left = `${percent}%`;

  nameEl.textContent = payload.name || "(unnamed span)";
  methodEl.textContent = payload["http.request.method"] || "—";
  routeEl.textContent = payload["http.route"] || "—";
  statusCodeEl.textContent = payload.status_code != null ? String(payload.status_code) : "—";
  spanIdEl.textContent = event.span_id || "—";

  if (payload.error) {
    row.classList.add("span-row-error");
  }

  return row;
}
