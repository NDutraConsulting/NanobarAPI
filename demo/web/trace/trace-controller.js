// trace-controller.js
// Orchestrates the single-trace span timeline page: parses the trace id out
// of the URL, calls trace-api.js, hands results to trace-ui.js to render,
// and surfaces errors. This is the only file with top-level "run on page
// load" logic.

import { fetchTraceSpans } from "./trace-api.js";
import { setTraceId, showLoading, showNotFound, showNetworkError, renderSpans } from "./trace-ui.js";

/** Extract the trace id from a path like /admin/nanobar/traces/{trace_id}. */
function getTraceIdFromPath() {
  const segments = window.location.pathname.split("/").filter(Boolean);
  return segments[segments.length - 1];
}

const traceId = getTraceIdFromPath();

/** Load this trace's spans from the API and render the timeline. */
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
    renderSpans(envelope.result.data);
  } catch (err) {
    showNetworkError("Could not reach the server. Please try again.");
  }
}

function init() {
  loadTrace();
}

init();
