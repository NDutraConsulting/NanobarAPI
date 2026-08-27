// trace-controller.js
// Orchestrates the single-trace master-detail page: parses the trace id out of the URL, calls
// trace-api.js once, hands results to trace-ui.js to render the span list, and wires
// click-to-select (with the selection also reflected in the URL hash, #span-{event_id}, so a
// specific span's detail view is directly linkable/refreshable). This is the only file with
// top-level "run on page load" logic.

import { fetchTraceSpans } from "./trace-api.js";
import {
  highlightSelectedSpan,
  renderSpanDetail,
  renderSpans,
  setTraceId,
  showEmptyDetail,
  showLoading,
  showNetworkError,
  showNotFound,
} from "./trace-ui.js";

/** Extract the trace id from a path like /admin/nanobar/traces/{trace_id}. */
function getTraceIdFromPath() {
  const segments = window.location.pathname.split("/").filter(Boolean);
  return segments[segments.length - 1];
}

const traceId = getTraceIdFromPath();

// Every event from the one fetch this page makes, keyed by event_id -- span selection is a
// pure client-side lookup into this, no repeat network calls.
let eventsById = new Map();

function eventIdFromHash() {
  const match = /^#span-(.+)$/.exec(window.location.hash);
  return match ? decodeURIComponent(match[1]) : null;
}

function selectSpan(eventId, { updateHash = true } = {}) {
  const event = eventsById.get(eventId);
  if (!event) {
    return;
  }
  highlightSelectedSpan(eventId);
  renderSpanDetail(event);
  if (updateHash) {
    window.location.hash = `span-${encodeURIComponent(eventId)}`;
  }
}

/** Load this trace's spans from the API and render the list. */
async function loadTrace() {
  if (!traceId) {
    showNotFound("No trace specified.");
    return;
  }

  setTraceId(traceId);
  showLoading();

  try {
    const envelope = await fetchTraceSpans(traceId);
    if (envelope.status !== "success") {
      showNotFound(envelope.msg || "Trace not found.");
      return;
    }
    const events = envelope.result.data;
    eventsById = new Map(events.map((event) => [event.event_id, event]));
    renderSpans(events, (eventId) => selectSpan(eventId));

    if (events.length === 0) {
      return;
    }
    const hashEventId = eventIdFromHash();
    if (hashEventId && eventsById.has(hashEventId)) {
      selectSpan(hashEventId, { updateHash: false });
    } else {
      showEmptyDetail();
    }
  } catch (err) {
    showNetworkError("Could not reach the server. Please refresh the page and try again.");
  }
}

function handleHashChange() {
  const hashEventId = eventIdFromHash();
  if (hashEventId && eventsById.has(hashEventId)) {
    selectSpan(hashEventId, { updateHash: false });
  }
}

function init() {
  window.addEventListener("hashchange", handleHashChange);
  loadTrace();
}

init();
