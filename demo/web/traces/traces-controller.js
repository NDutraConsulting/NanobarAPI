// traces-controller.js
// Orchestrates the trace list page: calls traces-api.js, hands results to
// traces-ui.js to render, and surfaces errors. This is the only file with
// top-level "run on page load" logic.

import { fetchTraces } from "./traces-api.js";
import { renderTraces, showTracesStatus, showTracesLoadError, hideTracesStatus } from "./traces-ui.js";

/** Load the trace list from the API and render it. */
async function loadTraces() {
  showTracesStatus("Loading traces…");
  try {
    const envelope = await fetchTraces();
    if (envelope.status !== "success") {
      showTracesLoadError(envelope.msg || "Could not load traces.");
      return;
    }
    hideTracesStatus();
    renderTraces(envelope.result.data);
  } catch (err) {
    showTracesLoadError("Could not reach the server. Please try again.");
  }
}

function init() {
  loadTraces();
}

init();
