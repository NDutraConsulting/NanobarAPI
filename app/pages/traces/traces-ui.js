// traces-ui.js
// Pure DOM rendering/manipulation functions for the trace list page.
// Given data, update the DOM. No fetch() calls happen in this file.

const statusEl = document.getElementById("traces-status");
const tableWrapEl = document.getElementById("traces-table-wrap");
const tableBodyEl = document.getElementById("traces-table-body");
const emptyEl = document.getElementById("traces-empty");
const paginationEl = document.getElementById("pagination");
const paginationPrevBtnEl = document.getElementById("pagination-prev-btn");
const paginationNextBtnEl = document.getElementById("pagination-next-btn");
const paginationSummaryEl = document.getElementById("pagination-summary");
const filterSummaryTextEl = document.getElementById("filter-summary-text");
const createdAfterEl = document.getElementById("filter-created-after");
const createdBeforeEl = document.getElementById("filter-created-before");
const sinceHoursEl = document.getElementById("filter-since-hours");
const showAllEl = document.getElementById("filter-show-all");
const nanobarTypesListEl = document.getElementById("filter-nanobar-types");
const componentsListEl = document.getElementById("filter-components");
const checkboxTemplate = document.getElementById("filter-checkbox-template");

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
  paginationEl.hidden = true;
}

/** Show an error status message above the table (e.g. load failure). */
export function showTracesLoadError(message) {
  statusEl.textContent = message;
  statusEl.hidden = false;
  statusEl.classList.add("traces-status-error");
  paginationEl.hidden = true;
}

/** Hide the status message entirely. */
export function hideTracesStatus() {
  statusEl.hidden = true;
  statusEl.textContent = "";
}

export const elements = {
  createdAfter: createdAfterEl,
  createdBefore: createdBeforeEl,
  sinceHours: sinceHoursEl,
  showAll: showAllEl,
  applyBtn: document.getElementById("filter-apply-btn"),
  resetBtn: document.getElementById("filter-reset-btn"),
  paginationPrevBtn: paginationPrevBtnEl,
  paginationNextBtn: paginationNextBtnEl,
};

/**
 * Renders pagination controls from the envelope's own page/page_size/total. Hidden entirely
 * when everything fits on one page.
 * @param {{page: number, pageSize: number, total: number}} info
 */
export function renderPagination({ page, pageSize, total }) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  if (pageCount <= 1) {
    paginationEl.hidden = true;
    return;
  }
  paginationEl.hidden = false;
  paginationSummaryEl.textContent = `Page ${page} of ${pageCount} (${total} total)`;
  paginationPrevBtnEl.disabled = page <= 1;
  paginationNextBtnEl.disabled = page >= pageCount;
}

/** A short, human-readable description of the date window actually applied to the last load --
 * always visible via the filter panel's <summary>, even while the panel itself is collapsed, so
 * the active default is never a silent, undiscoverable constraint.
 * @param {{showAll: boolean, createdAfter: string, createdBefore: string, sinceHours: string, isDefault: boolean}} state
 */
export function updateFilterSummaryText(state) {
  if (state.showAll) {
    filterSummaryTextEl.textContent = "showing all time";
  } else if (state.isDefault) {
    filterSummaryTextEl.textContent = "showing today";
  } else if (state.sinceHours) {
    filterSummaryTextEl.textContent = `showing last ${state.sinceHours} hour(s)`;
  } else {
    const parts = [];
    if (state.createdAfter) parts.push(`after ${state.createdAfter}`);
    if (state.createdBefore) parts.push(`before ${state.createdBefore}`);
    filterSummaryTextEl.textContent = parts.length > 0 ? `showing ${parts.join(", ")}` : "showing today";
  }
}

/**
 * Fills a checkbox-list container with one checkbox per value, preserving whichever of
 * `selected` are still present among `values`.
 * @param {HTMLElement} container
 * @param {string[]} values
 * @param {Set<string>} selected
 */
function renderCheckboxList(container, values, selected) {
  container.textContent = "";
  for (const value of values) {
    const node = checkboxTemplate.content.cloneNode(true);
    const input = node.querySelector(".filter-checkbox-input");
    input.value = value;
    input.checked = selected.has(value);
    node.querySelector(".filter-checkbox-label").textContent = value;
    container.appendChild(node);
  }
}

/** @param {string[]} nanobarTypes @param {Set<string>} selected */
export function renderNanobarTypeCheckboxes(nanobarTypes, selected) {
  renderCheckboxList(nanobarTypesListEl, nanobarTypes, selected);
}

/** @param {string[]} components @param {Set<string>} selected */
export function renderComponentCheckboxes(components, selected) {
  renderCheckboxList(componentsListEl, components, selected);
}

/** @param {HTMLElement} container @returns {string[]} the checked values within it */
export function getCheckedValues(container) {
  return [...container.querySelectorAll(".filter-checkbox-input:checked")].map((input) => input.value);
}

export const nanobarTypesList = nanobarTypesListEl;
export const componentsList = componentsListEl;
