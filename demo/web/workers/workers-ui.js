// workers-ui.js
// Pure DOM rendering/manipulation for the workers page: a clickable worker list on the left,
// the selected worker's full configuration + recent failure log on the right. No fetch() calls
// happen in this file.

const statusEl = document.getElementById("workers-status");
const layoutEl = document.getElementById("workers-layout");
const listEl = document.getElementById("workers-list");
const emptyEl = document.getElementById("workers-empty");
const rowTemplate = document.getElementById("worker-row-template");

const detailEmptyEl = document.getElementById("worker-detail-empty");
const detailEl = document.getElementById("worker-detail");
const detailHeadingEl = document.getElementById("worker-detail-heading");
const statusPillEl = document.getElementById("worker-status-pill");
const configGridEl = document.getElementById("worker-config-grid");
const configFieldTemplate = document.getElementById("worker-config-field-template");

const logStatusEl = document.getElementById("worker-log-status");
const logListEl = document.getElementById("worker-log-list");
const logEmptyEl = document.getElementById("worker-log-empty");
const logRowTemplate = document.getElementById("worker-log-row-template");

export const elements = {
  workersList: listEl,
};

function hideContent() {
  layoutEl.hidden = true;
  emptyEl.hidden = true;
}

export function showLoading() {
  statusEl.hidden = false;
  statusEl.className = "workers-status";
  statusEl.textContent = "Loading workers…";
  hideContent();
}

export function showLoadError(message) {
  statusEl.hidden = false;
  statusEl.className = "workers-status workers-status-error";
  statusEl.textContent = message || "Could not reach the server. Please refresh the page and try again.";
  hideContent();
}

/**
 * Renders the worker list (left pane). Each row is a button carrying the worker's id as
 * `data-worker-id`, clicked to select it -- `onSelect` is the caller's job.
 * @param {Array<{worker_id: string, channels: string[], is_stale: boolean}>} workers
 * @param {(workerId: string) => void} onSelect
 */
export function renderWorkersList(workers, onSelect) {
  statusEl.hidden = true;

  if (!workers || workers.length === 0) {
    layoutEl.hidden = true;
    emptyEl.hidden = false;
    return;
  }

  emptyEl.hidden = true;
  layoutEl.hidden = false;
  listEl.textContent = "";

  for (const worker of workers) {
    const fragment = rowTemplate.content.cloneNode(true);
    const btn = fragment.querySelector(".worker-row-btn");
    const dot = fragment.querySelector(".worker-status-dot");

    fragment.querySelector(".worker-id").textContent = worker.worker_id;
    fragment.querySelector(".worker-channels").textContent = (worker.channels || []).join(", ");
    dot.classList.add(worker.is_stale ? "worker-status-dot-stale" : "worker-status-dot-healthy");

    btn.dataset.workerId = worker.worker_id;
    btn.addEventListener("click", () => onSelect(worker.worker_id));

    listEl.appendChild(fragment);
  }
}

/** Marks exactly one worker row as selected, clearing any previous selection. */
export function highlightSelectedWorker(workerId) {
  for (const btn of listEl.querySelectorAll(".worker-row-btn")) {
    btn.classList.toggle("worker-row-btn-selected", btn.dataset.workerId === workerId);
  }
}

export function showWorkerDetailEmpty() {
  detailEmptyEl.hidden = false;
  detailEl.hidden = true;
}

const CONFIG_FIELDS = [
  ["channels", "Channels", (w) => (w.channels || []).join(", ")],
  ["mode", "Mode", (w) => w.mode],
  ["schedule", "Schedule", (w) => w.schedule],
  ["poll_interval_s", "Poll interval (s)", (w) => w.poll_interval_s],
  ["claim_limit", "Claim limit", (w) => w.claim_limit],
  ["lease_seconds", "Lease (s)", (w) => w.lease_seconds],
  ["started_at", "Started at", (w) => w.started_at],
  ["last_heartbeat_at", "Last heartbeat", (w) => w.last_heartbeat_at],
];

/**
 * Renders one worker's full configuration + liveness (right pane) from a worker record
 * (`{worker_id, channels, mode, schedule, poll_interval_s, claim_limit, lease_seconds,
 * started_at, last_heartbeat_at, is_stale}`).
 */
export function renderWorkerDetail(worker) {
  detailEmptyEl.hidden = true;
  detailEl.hidden = false;

  detailHeadingEl.textContent = worker.worker_id;
  statusPillEl.textContent = worker.is_stale ? "Stale" : "Healthy";
  statusPillEl.className = `worker-status-pill ${worker.is_stale ? "worker-status-pill-stale" : "worker-status-pill-healthy"}`;

  configGridEl.textContent = "";
  for (const [key, label, getValue] of CONFIG_FIELDS) {
    const value = getValue(worker);
    if (value === undefined || value === null || value === "") continue;
    const fragment = configFieldTemplate.content.cloneNode(true);
    fragment.querySelector(".worker-config-field-label").textContent = label;
    fragment.querySelector(".worker-config-field-value").textContent = String(value);
    configGridEl.appendChild(fragment);
  }
}

export function showWorkerLogLoading() {
  logStatusEl.hidden = false;
  logStatusEl.className = "worker-log-status";
  logStatusEl.textContent = "Loading…";
  logListEl.hidden = true;
  logEmptyEl.hidden = true;
}

export function showWorkerLogError(message) {
  logStatusEl.hidden = false;
  logStatusEl.className = "worker-log-status worker-log-status-error";
  logStatusEl.textContent = message || "Could not load the failure log.";
  logListEl.hidden = true;
  logEmptyEl.hidden = true;
}

/** Renders a worker's recent failure log entries (`{worker_id, event_id, error, logged_at}`),
 * most recent first (the order the API already returns them in). */
export function renderWorkerLog(entries) {
  logStatusEl.hidden = true;

  if (!entries || entries.length === 0) {
    logListEl.hidden = true;
    logEmptyEl.hidden = false;
    return;
  }

  logEmptyEl.hidden = true;
  logListEl.hidden = false;
  logListEl.textContent = "";

  for (const entry of entries) {
    const fragment = logRowTemplate.content.cloneNode(true);
    fragment.querySelector(".worker-log-time").textContent = entry.logged_at;
    fragment.querySelector(".worker-log-event-id").textContent = entry.event_id || "—";
    fragment.querySelector(".worker-log-error").textContent = entry.error;
    logListEl.appendChild(fragment);
  }
}
