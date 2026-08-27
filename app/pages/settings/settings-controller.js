// settings-controller.js
// Orchestrates the Settings page: calls settings-api.js and calls settings-ui.js to render the
// result (or an error state). This is the only file with top-level "run on page load" logic.

import {
  fetchSettings,
  updateSettings,
  fetchRefreshStatus,
  refreshApiRoutes,
  refreshNanobars,
  refreshBricks,
} from "./settings-api.js";
import {
  showStatus,
  showLoadError,
  hideStatus,
  renderTracingEnabled,
  setTracingToggleBusy,
  showTracingToggleError,
  hideTracingToggleError,
  setRefreshBusy,
  showRefreshResult,
  showRefreshError,
  renderLastRun,
  elements,
} from "./settings-ui.js";

//: Maps each refresh row's UI kind (matching its DOM id prefix) to (a) the refresh-status API's
//: own key for it, and (b) the settings-api.js function that triggers it -- one table instead
//: of three near-identical click handlers.
const REFRESH_ROWS = {
  "api-routes": {
    statusKey: "api",
    trigger: refreshApiRoutes,
    summarize: (d) => `${d.routes_scanned} route(s) across ${d.domains} domain(s)`,
  },
  nanobars: {
    statusKey: "nanobars",
    trigger: refreshNanobars,
    summarize: (d) => `${d.nanobars_created} nanobar(s) created, ${d.domains_updated} domain(s) updated`,
  },
  bricks: {
    statusKey: "bricks",
    trigger: refreshBricks,
    summarize: (d) => `${d.new_bricks} new brick(s), ${d.nanobars_created} nanobar(s) created`,
  },
};

async function loadSettings() {
  showStatus("Loading settings…");
  try {
    const envelope = await fetchSettings();
    if (envelope.status !== "success") {
      showLoadError(envelope.msg || "Could not load settings.");
      return;
    }
    hideStatus();
    renderTracingEnabled(envelope.result.data.tracing_enabled);
  } catch (err) {
    showLoadError("Could not reach the server. Please refresh the page and try again.");
  }
}

async function handleTracingToggleChange() {
  const desired = elements.tracingToggle.checked;
  hideTracingToggleError();
  setTracingToggleBusy(true);
  try {
    const envelope = await updateSettings({ tracingEnabled: desired });
    if (envelope.status !== "success") {
      elements.tracingToggle.checked = !desired;
      showTracingToggleError(envelope.msg || "Could not save this setting.");
      return;
    }
    renderTracingEnabled(envelope.result.data.tracing_enabled);
  } catch (err) {
    elements.tracingToggle.checked = !desired;
    showTracingToggleError("Could not reach the server. Please refresh the page and try again.");
  } finally {
    setTracingToggleBusy(false);
  }
}

async function loadRefreshStatus() {
  try {
    const envelope = await fetchRefreshStatus();
    if (envelope.status !== "success") return; // best-effort: rows just show "Never run yet."
    for (const [kind, { statusKey }] of Object.entries(REFRESH_ROWS)) {
      renderLastRun(kind, envelope.result.data[statusKey]);
    }
  } catch (err) {
    // Best-effort, same as above.
  }
}

function handleRefreshClick(kind) {
  const { trigger, summarize } = REFRESH_ROWS[kind];
  return async () => {
    setRefreshBusy(kind, true);
    try {
      const envelope = await trigger();
      if (envelope.status !== "success") {
        showRefreshError(kind, envelope.msg || "Could not run this refresh.");
        return;
      }
      showRefreshResult(kind, summarize(envelope.result.data));
      loadRefreshStatus();
    } catch (err) {
      showRefreshError(kind, "Could not reach the server. Please refresh the page and try again.");
    } finally {
      setRefreshBusy(kind, false);
    }
  };
}

function init() {
  elements.tracingToggle.addEventListener("change", handleTracingToggleChange);
  for (const kind of Object.keys(REFRESH_ROWS)) {
    elements.refreshButtons[kind].addEventListener("click", handleRefreshClick(kind));
  }
  loadSettings();
  loadRefreshStatus();
}

init();
