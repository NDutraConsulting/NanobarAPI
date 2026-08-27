// workers-controller.js
// Orchestrates the workers page: loads the worker list, and for whichever worker is currently
// selected in the left pane, loads and renders its recent failure log. This is the only file
// with top-level "run on page load" logic.

import { fetchWorkerLog, fetchWorkers } from "./workers-api.js";
import {
  highlightSelectedWorker,
  renderWorkerDetail,
  renderWorkerLog,
  renderWorkersList,
  showLoadError,
  showLoading,
  showWorkerDetailEmpty,
  showWorkerLogError,
  showWorkerLogLoading,
} from "./workers-ui.js";

// Every worker from the last successful list fetch, keyed by worker_id -- selecting a worker
// is a pure client-side lookup into this, no repeat network call for the config/liveness part.
let workersById = new Map();

async function loadWorkerLog(workerId) {
  showWorkerLogLoading();
  try {
    const envelope = await fetchWorkerLog(workerId);
    if (envelope.status !== "success") {
      showWorkerLogError(envelope.msg || "Could not load the failure log.");
      return;
    }
    renderWorkerLog(envelope.result.data);
  } catch (err) {
    showWorkerLogError("Could not reach the server. Please refresh the page and try again.");
  }
}

function selectWorker(workerId) {
  const worker = workersById.get(workerId);
  if (!worker) {
    return;
  }
  highlightSelectedWorker(workerId);
  renderWorkerDetail(worker);
  loadWorkerLog(workerId);
}

async function loadWorkers() {
  showLoading();
  try {
    const envelope = await fetchWorkers();
    if (envelope.status !== "success") {
      showLoadError(envelope.msg || "Could not load workers.");
      return;
    }
    const workers = envelope.result.data;
    workersById = new Map(workers.map((worker) => [worker.worker_id, worker]));
    renderWorkersList(workers, selectWorker);
    if (workers.length === 0) {
      return;
    }
    showWorkerDetailEmpty();
  } catch (err) {
    showLoadError("Could not reach the server. Please refresh the page and try again.");
  }
}

function init() {
  loadWorkers();
}

init();
