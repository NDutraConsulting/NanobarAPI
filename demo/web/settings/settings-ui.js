// settings-ui.js
// Pure DOM rendering/manipulation functions for the Settings page. Given data, update the DOM.
// No fetch() calls happen in this file.

const statusEl = document.getElementById("settings-status");
const tracingToggleEl = document.getElementById("tracing-toggle");
const tracingToggleErrorEl = document.getElementById("tracing-toggle-error");

//: DOM id prefixes for the three refresh rows -- distinct from the refresh-status API's own
//: kind keys ("api"/"nanobars"/"bricks"; see settings-controller.js's REFRESH_KIND_TO_STATUS_KEY),
//: since "api" alone wouldn't make a sensible element id prefix on its own.
const REFRESH_ROW_KINDS = ["api-routes", "nanobars", "bricks"];

const refreshRowElements = Object.fromEntries(
  REFRESH_ROW_KINDS.map((kind) => [
    kind,
    {
      btn: document.getElementById(`${kind}-refresh-btn`),
      status: document.getElementById(`${kind}-refresh-status`),
      lastRun: document.getElementById(`${kind}-refresh-last-run`),
    },
  ])
);

export const elements = {
  tracingToggle: tracingToggleEl,
  refreshButtons: Object.fromEntries(REFRESH_ROW_KINDS.map((kind) => [kind, refreshRowElements[kind].btn])),
};

/** Show a transient status message (e.g. "Loading..."). */
export function showStatus(message) {
  statusEl.textContent = message;
  statusEl.hidden = false;
  statusEl.classList.remove("settings-status-error");
}

/** Show an error status message (e.g. load failure or envelope error). */
export function showLoadError(message) {
  statusEl.textContent = message;
  statusEl.hidden = false;
  statusEl.classList.add("settings-status-error");
}

/** Hide the status message entirely. */
export function hideStatus() {
  statusEl.hidden = true;
  statusEl.textContent = "";
  statusEl.classList.remove("settings-status-error");
}

/** Reflects the current tracing-enabled value onto the toggle and enables it for interaction. */
export function renderTracingEnabled(tracingEnabled) {
  tracingToggleEl.checked = tracingEnabled;
  tracingToggleEl.disabled = false;
}

/** Disables the toggle while a save request is in flight, to prevent double-submits. */
export function setTracingToggleBusy(isBusy) {
  tracingToggleEl.disabled = isBusy;
}

export function showTracingToggleError(message) {
  tracingToggleErrorEl.textContent = message;
  tracingToggleErrorEl.hidden = false;
}

export function hideTracingToggleError() {
  tracingToggleErrorEl.hidden = true;
  tracingToggleErrorEl.textContent = "";
}

/** Disables a refresh row's button and shows a busy label while its request is in flight. */
export function setRefreshBusy(kind, isBusy) {
  const { btn } = refreshRowElements[kind];
  btn.disabled = isBusy;
  btn.textContent = isBusy ? "Refreshing…" : "Refresh";
}

export function showRefreshResult(kind, message) {
  const { status } = refreshRowElements[kind];
  status.textContent = message;
  status.hidden = false;
  status.classList.remove("refresh-status-error");
}

export function showRefreshError(kind, message) {
  const { status } = refreshRowElements[kind];
  status.textContent = message;
  status.hidden = false;
  status.classList.add("refresh-status-error");
}

/**
 * Renders a refresh row's "last run" line from the refresh-status API's per-kind entry.
 * @param {string} kind one of REFRESH_ROW_KINDS
 * @param {{last_run_at: string, summary: string} | null} info null if it's never run yet
 */
export function renderLastRun(kind, info) {
  const { lastRun } = refreshRowElements[kind];
  if (!info) {
    lastRun.textContent = "Never run yet.";
    return;
  }
  lastRun.textContent = `Last run ${new Date(info.last_run_at).toLocaleString()} -- ${info.summary}`;
}
