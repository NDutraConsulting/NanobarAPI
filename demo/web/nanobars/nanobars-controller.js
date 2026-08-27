// nanobars-controller.js
// Orchestrates the Nanobar Dashboard main list page: calls nanobars-api.js, groups the current
// page's results client-side by nanobar_type, and calls nanobars-ui.js to render them (or an
// error state). This is the only file with top-level "run on page load" logic.
//
// nanobar_type (not monitor_target_refs[].target_type) is the real navigation axis: it's the
// taxonomy layer type (validator-request-response, orm-request-response, ...) that a
// regression-brick span actually belongs to. target_type ("route") is a per-request detail, not
// a way to navigate the application's layers -- every nanobar this demo produces shares the same
// target_type, so grouping/filtering by it always collapsed everything into one meaningless
// bucket. See nanobar_api/taxonomy.py and demo/dashboard/api.py's list_nanobars docstring.

import { fetchNanobars, generateBricks } from "./nanobars-api.js";
import {
  showStatus,
  showLoadError,
  hideStatus,
  renderGroups,
  renderPagination,
  populateNanobarTypeFilter,
  populateDomainFilter,
  ALL_DOMAINS_VALUE,
  setGenerateBricksBusy,
  showGenerateBricksResult,
  showGenerateBricksError,
  elements,
} from "./nanobars-ui.js";

const PAGE_SIZE = 50;

//: Matches `nanobar_api.bricks.store.UNMAPPED_DOMAIN` -- selects nanobars with no domain at
//: all (`domain IS NULL`), e.g. anything created before `get_or_create_nanobar_by_route_key`
//: gained a `domain` parameter and never backfilled by a "Nanobar refresh".
const UNMAPPED_DOMAIN = "(unmapped)";

// Current filter/pagination state -- every change re-fetches from the server rather than
// filtering client-side, since only the current page's items are ever in memory once real
// pagination is in play.
let currentNanobarType = "";
let currentDomain; // undefined = no filter ("All domains"); "" is itself a real domain value
let currentQuery = "";
let currentPage = 1;
let lastPageInfo = { page: 1, pageSize: PAGE_SIZE, total: 0 };

/**
 * Distinct `nanobar_type` values present in the data, sorted.
 * @param {Array<{nanobar_type: string}>} nanobars
 * @returns {string[]}
 */
function distinctNanobarTypes(nanobars) {
  return [...new Set(nanobars.map((n) => n.nanobar_type).filter(Boolean))].sort();
}

/**
 * Distinct `domain` values present in the data, sorted -- `null`/`undefined` (never stamped)
 * become the `UNMAPPED_DOMAIN` sentinel, and `""` (a real root-level domain) is kept, unlike
 * `distinctNanobarTypes`'s `.filter(Boolean)`, which would incorrectly drop it.
 * @param {Array<{domain: string | null}>} nanobars
 * @returns {string[]}
 */
function distinctDomains(nanobars) {
  const domains = nanobars.map((n) => (n.domain === null || n.domain === undefined ? UNMAPPED_DOMAIN : n.domain));
  return [...new Set(domains)].sort();
}

/**
 * Groups the given (already server-filtered/paginated) nanobars by their `nanobar_type` --
 * a single required field, so (unlike the old target_type refs list) each nanobar lands in
 * exactly one group.
 * @param {Array<{nanobar_type: string}>} nanobars
 * @returns {Array<{nanobarType: string, nanobars: Array}>} groups sorted by nanobar type
 */
function groupByNanobarType(nanobars) {
  const groups = new Map();

  for (const nanobar of nanobars) {
    const nanobarType = nanobar.nanobar_type;
    if (!groups.has(nanobarType)) {
      groups.set(nanobarType, []);
    }
    groups.get(nanobarType).push(nanobar);
  }

  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([nanobarType, groupNanobars]) => ({ nanobarType, nanobars: groupNanobars }));
}

/** Loads the nanobar-type and domain filters' option lists from one unfiltered, large-page-
 * size probe fetch -- independent of the main paginated load, so both dropdowns always
 * reflect every value that exists, not just whatever happens to be on the current
 * filtered/paginated page. */
async function loadFilterOptions() {
  try {
    const envelope = await fetchNanobars({ page: 1, pageSize: 1000 });
    if (envelope.status === "success") {
      populateNanobarTypeFilter(distinctNanobarTypes(envelope.result.data.items));
      populateDomainFilter(distinctDomains(envelope.result.data.items));
    }
  } catch (err) {
    // Best-effort: the dropdowns just stay at their "All ..." defaults if this fails.
  }
}

/** Load the current page/filter/query state from the API and render it. */
async function loadNanobars() {
  showStatus("Loading nanobars…");
  try {
    const envelope = await fetchNanobars({
      nanobarType: currentNanobarType,
      domain: currentDomain,
      q: currentQuery,
      page: currentPage,
      pageSize: PAGE_SIZE,
    });
    if (envelope.status !== "success") {
      showLoadError(envelope.msg || "Could not load nanobars.");
      return;
    }
    hideStatus();
    const { items, page, page_size: pageSize, total } = envelope.result.data;
    lastPageInfo = { page, pageSize, total };
    renderGroups(groupByNanobarType(items));
    renderPagination(lastPageInfo);
  } catch (err) {
    showLoadError("Could not reach the server. Please refresh the page and try again.");
  }
}

function handleFilterChange() {
  currentNanobarType = elements.nanobarTypeFilter.value;
  const domainValue = elements.domainFilter.value;
  currentDomain = domainValue === ALL_DOMAINS_VALUE ? undefined : domainValue;
  currentPage = 1;
  loadNanobars();
}

let searchDebounceTimer;
function handleSearchInput() {
  clearTimeout(searchDebounceTimer);
  searchDebounceTimer = setTimeout(() => {
    currentQuery = elements.search.value.trim();
    currentPage = 1;
    loadNanobars();
  }, 250);
}

async function handleGenerateBricksClick() {
  setGenerateBricksBusy(true);
  try {
    const envelope = await generateBricks();
    if (envelope.status !== "success") {
      showGenerateBricksError(envelope.msg || "Could not generate bricks.");
      return;
    }
    const { new_bricks: newBricks, nanobars_created: nanobarsCreated, bindings_created: bindingsCreated } =
      envelope.result.data;
    showGenerateBricksResult(
      `Processed ${newBricks} new brick(s) -- ${nanobarsCreated} nanobar(s) created, ${bindingsCreated} binding(s) created.`
    );
    loadNanobars();
  } catch (err) {
    showGenerateBricksError("Network error while generating bricks. Please try again.");
  } finally {
    setGenerateBricksBusy(false);
  }
}

function handlePrevPage() {
  if (currentPage > 1) {
    currentPage -= 1;
    loadNanobars();
  }
}

function handleNextPage() {
  const pageCount = Math.max(1, Math.ceil(lastPageInfo.total / lastPageInfo.pageSize));
  if (currentPage < pageCount) {
    currentPage += 1;
    loadNanobars();
  }
}

function init() {
  elements.nanobarTypeFilter.addEventListener("change", handleFilterChange);
  elements.domainFilter.addEventListener("change", handleFilterChange);
  elements.search.addEventListener("input", handleSearchInput);
  elements.generateBricksBtn.addEventListener("click", handleGenerateBricksClick);
  elements.paginationPrevBtn.addEventListener("click", handlePrevPage);
  elements.paginationNextBtn.addEventListener("click", handleNextPage);
  loadFilterOptions();
  loadNanobars();
}

init();
