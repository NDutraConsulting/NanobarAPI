// trace-ui.js
// Pure DOM rendering/manipulation functions for the single-trace master-detail page: a
// clickable span list on the left, that span's full detail on the right. Given data, update
// the DOM. No fetch() calls happen in this file.

const titleEl = document.getElementById("trace-title");
const statusEl = document.getElementById("trace-status");
const disclaimerEl = document.getElementById("trace-disclaimer");
const layoutEl = document.getElementById("trace-layout");
const spansListEl = document.getElementById("spans-list");
const emptyEl = document.getElementById("trace-empty");
const rowTemplate = document.getElementById("span-row-template");
const detailEmptyEl = document.getElementById("span-detail-empty");
const detailEl = document.getElementById("span-detail");
const detailFieldTemplate = document.getElementById("span-detail-field-template");

export const elements = {
  spansList: spansListEl,
};

/** Set the page heading/title to reflect which trace is being viewed. */
export function setTraceId(traceId) {
  titleEl.textContent = `Trace ${traceId}`;
  document.title = `Trace ${traceId} · Nanobar Dashboard`;
}

function hideContent() {
  disclaimerEl.hidden = true;
  layoutEl.hidden = true;
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
  statusEl.textContent = message || "Could not reach the server. Please refresh the page and try again.";
  hideContent();
}

/**
 * Render the span list (left pane) for a trace, given its events already ordered by
 * monotonic_ns (ascending) by the backend. Each row is a button carrying the event's id as
 * `data-event-id`, clicked to select it -- `onSelect` is the caller's job (network-free; it
 * already has every event in memory from the one fetch this page makes).
 *
 * These are completion-time offsets, not span durations: EventBusTraceMiddleware records one
 * monotonic_ns timestamp per span, not a start+end pair, so no individual span duration is
 * ever computed here.
 *
 * @param {Array<{event_id: string, span_id: string|null, monotonic_ns: number, payload: object}>} events
 * @param {(eventId: string) => void} onSelect
 */
export function renderSpans(events, onSelect) {
  statusEl.hidden = true;
  spansListEl.textContent = "";

  if (!events || events.length === 0) {
    disclaimerEl.hidden = true;
    layoutEl.hidden = true;
    emptyEl.hidden = false;
    return;
  }

  emptyEl.hidden = true;
  disclaimerEl.hidden = false;
  layoutEl.hidden = false;

  const firstNs = events[0].monotonic_ns;

  for (const event of events) {
    spansListEl.appendChild(buildSpanRow(event, firstNs, onSelect));
  }
}

function buildSpanRow(event, firstNs, onSelect) {
  const fragment = rowTemplate.content.cloneNode(true);
  const row = fragment.querySelector(".span-row");
  const btn = fragment.querySelector(".span-row-btn");
  const offsetEl = fragment.querySelector(".span-offset");
  const nameEl = fragment.querySelector(".span-name");
  const badgeEl = fragment.querySelector(".span-nanobar-type-badge");
  const statusCodeEl = fragment.querySelector(".span-status-code");

  const payload = event.payload || {};
  const offsetMs = (event.monotonic_ns - firstNs) / 1e6;

  offsetEl.textContent = `+${offsetMs.toFixed(2)} ms`;
  nameEl.textContent = payload.name || "(unnamed span)";
  statusCodeEl.textContent = payload.status_code != null ? String(payload.status_code) : "—";

  if (payload.nanobar_type) {
    badgeEl.textContent = payload.nanobar_type;
    badgeEl.hidden = false;
  }

  if (payload.error) {
    row.classList.add("span-row-error");
  }

  btn.dataset.eventId = event.event_id;
  btn.addEventListener("click", () => onSelect(event.event_id));

  return row;
}

/** Marks exactly one span row as selected (visual highlight), clearing any previous selection. */
export function highlightSelectedSpan(eventId) {
  for (const btn of spansListEl.querySelectorAll(".span-row-btn")) {
    btn.classList.toggle("span-row-btn-selected", btn.dataset.eventId === eventId);
  }
}

/** Show the right pane's "nothing selected yet" prompt. */
export function showEmptyDetail() {
  detailEmptyEl.hidden = false;
  detailEl.hidden = true;
}

//: Known payload fields rendered as labeled rows before the raw-JSON fallback -- covers both
//: EventBusTraceMiddleware's HTTP-layer shape and NanobarTelemetry's span shape (see
//: nanobar_api/telemetry.py's _emit()). A field absent from a given span's payload is simply
//: skipped, not shown as blank -- not every span has every field.
const _DETAIL_FIELDS = [
  ["name", "Name"],
  ["http.request.method", "Method"],
  ["http.route", "Route"],
  ["status_code", "Status code"],
  ["status", "Status"],
  ["nanobar_type", "Nanobar type"],
  ["nanobar_label", "Nanobar label"],
  ["nanobar_scenario_description", "Scenario"],
  ["nanobar_component_source_description", "Component source"],
  ["nanobar_domain", "Domain"],
  ["code.function.name", "Function"],
  ["code.file.path", "File"],
  ["code.line.number", "Line"],
  ["error.type", "Error type"],
];

/**
 * Render one span's full detail in the right pane: known fields as labeled rows, then the
 * complete raw payload as a JSON fallback so nothing captured is ever hidden.
 * @param {{event_id: string, span_id: string|null, trace_id: string|null, recorded_at_ns: number, monotonic_ns: number, payload: object}} event
 */
export function renderSpanDetail(event) {
  detailEmptyEl.hidden = true;
  detailEl.hidden = false;
  detailEl.textContent = "";

  const payload = event.payload || {};

  const identityFields = [
    ["Event ID", event.event_id],
    ["Span ID", event.span_id || "—"],
    ["Trace ID", event.trace_id || "—"],
  ];
  for (const [label, value] of identityFields) {
    detailEl.appendChild(buildDetailField(label, value));
  }

  for (const [key, label] of _DETAIL_FIELDS) {
    if (key in payload && payload[key] !== null && payload[key] !== undefined) {
      detailEl.appendChild(buildDetailField(label, String(payload[key])));
    }
  }

  const rawHeading = document.createElement("h3");
  rawHeading.className = "span-detail-raw-heading";
  rawHeading.textContent = "Raw payload";
  detailEl.appendChild(rawHeading);

  const pre = document.createElement("pre");
  pre.className = "span-detail-raw";
  pre.textContent = JSON.stringify(payload, null, 2);
  detailEl.appendChild(pre);
}

function buildDetailField(label, value) {
  const fragment = detailFieldTemplate.content.cloneNode(true);
  fragment.querySelector(".span-detail-field-label").textContent = label;
  fragment.querySelector(".span-detail-field-value").textContent = value;
  return fragment;
}
