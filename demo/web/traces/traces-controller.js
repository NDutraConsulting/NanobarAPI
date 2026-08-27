// traces-controller.js
// Orchestrates the trace list page: calls traces-api.js, hands results to traces-ui.js to
// render, and wires the collapsible filter panel (date range/last-N-hours/show-all,
// nanobar-type + component checkboxes) and pagination. This is the only file with top-level
// "run on page load" logic.

import { fetchTraceFacets, fetchTraces } from "./traces-api.js";
import {
  elements,
  getCheckedValues,
  hideTracesStatus,
  nanobarTypesList,
  componentsList,
  renderComponentCheckboxes,
  renderNanobarTypeCheckboxes,
  renderPagination,
  renderTraces,
  showTracesLoadError,
  showTracesStatus,
  updateFilterSummaryText,
} from "./traces-ui.js";

// Applied filter/pagination state -- only changes when "Apply filters"/"Reset to
// default"/pagination buttons are clicked, not on every keystroke/checkbox click (composing a
// multi-field filter shouldn't re-fetch after every intermediate change).
let state = {
  page: 1,
  createdAfter: "",
  createdBefore: "",
  sinceHours: "",
  showAll: false,
  nanobarTypes: [],
  components: [],
};

let lastPageInfo = { page: 1, pageSize: 100, total: 0 };

/** `datetime-local`'s value has no timezone -- interpreted as local time by `new Date(...)`,
 * then serialized to a real ISO 8601 (offset-bearing) string the backend can parse, same
 * conversion `demo/web/admin-app`'s scheduled-post field already uses. */
function toIsoOrEmpty(datetimeLocalValue) {
  return datetimeLocalValue ? new Date(datetimeLocalValue).toISOString() : "";
}

async function loadFacets() {
  try {
    const envelope = await fetchTraceFacets({
      createdAfter: state.createdAfter,
      createdBefore: state.createdBefore,
      sinceHours: state.sinceHours,
      showAll: state.showAll,
    });
    if (envelope.status === "success") {
      renderNanobarTypeCheckboxes(envelope.result.data.nanobar_types, new Set(state.nanobarTypes));
      renderComponentCheckboxes(envelope.result.data.components, new Set(state.components));
    }
  } catch (err) {
    // Best-effort: the checkbox lists just stay empty if this fails.
  }
}

async function loadTraces() {
  showTracesStatus("Loading traces…");
  try {
    const envelope = await fetchTraces({
      page: state.page,
      createdAfter: state.createdAfter,
      createdBefore: state.createdBefore,
      sinceHours: state.sinceHours,
      showAll: state.showAll,
      nanobarTypes: state.nanobarTypes,
      components: state.components,
    });
    if (envelope.status !== "success") {
      showTracesLoadError(envelope.msg || "Could not load traces.");
      return;
    }
    hideTracesStatus();
    const { items, page, page_size: pageSize, total } = envelope.result.data;
    lastPageInfo = { page, pageSize, total };
    renderTraces(items);
    renderPagination(lastPageInfo);
    updateFilterSummaryText({
      showAll: state.showAll,
      createdAfter: state.createdAfter,
      createdBefore: state.createdBefore,
      sinceHours: state.sinceHours,
      isDefault: !state.showAll && !state.createdAfter && !state.createdBefore && !state.sinceHours,
    });
  } catch (err) {
    showTracesLoadError("Could not reach the server. Please refresh the page and try again.");
  }
}

function readFiltersFromForm() {
  state = {
    ...state,
    page: 1,
    createdAfter: toIsoOrEmpty(elements.createdAfter.value),
    createdBefore: toIsoOrEmpty(elements.createdBefore.value),
    sinceHours: elements.sinceHours.value.trim(),
    showAll: elements.showAll.checked,
    nanobarTypes: getCheckedValues(nanobarTypesList),
    components: getCheckedValues(componentsList),
  };
}

function handleApplyFilters() {
  readFiltersFromForm();
  loadFacets();
  loadTraces();
}

function handleResetFilters() {
  elements.createdAfter.value = "";
  elements.createdBefore.value = "";
  elements.sinceHours.value = "";
  elements.showAll.checked = false;
  state = { page: 1, createdAfter: "", createdBefore: "", sinceHours: "", showAll: false, nanobarTypes: [], components: [] };
  loadFacets();
  loadTraces();
}

function handlePrevPage() {
  if (state.page > 1) {
    state = { ...state, page: state.page - 1 };
    loadTraces();
  }
}

function handleNextPage() {
  const pageCount = Math.max(1, Math.ceil(lastPageInfo.total / lastPageInfo.pageSize));
  if (state.page < pageCount) {
    state = { ...state, page: state.page + 1 };
    loadTraces();
  }
}

function init() {
  elements.applyBtn.addEventListener("click", handleApplyFilters);
  elements.resetBtn.addEventListener("click", handleResetFilters);
  elements.paginationPrevBtn.addEventListener("click", handlePrevPage);
  elements.paginationNextBtn.addEventListener("click", handleNextPage);
  loadFacets();
  loadTraces();
}

init();
