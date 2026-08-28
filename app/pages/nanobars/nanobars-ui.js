// nanobars-ui.js
// Pure DOM rendering/manipulation functions for the Nanobar Dashboard main
// list page. Given data, update the DOM. No fetch() calls happen in this file.

const statusEl = document.getElementById("nanobars-status");
const groupsEl = document.getElementById("nanobars-groups");
const emptyEl = document.getElementById("nanobars-empty");
const groupTemplate = document.getElementById("nanobar-type-group-template");
const itemTemplate = document.getElementById("nanobar-item-template");
const nanobarTypeFilterEl = document.getElementById("nanobar-type-filter");
const domainFilterEl = document.getElementById("domain-filter");
const appBoxFilterEl = document.getElementById("app-box-filter");
const searchEl = document.getElementById("nanobar-search");
const generateBricksBtnEl = document.getElementById("generate-bricks-btn");
const generateBricksStatusEl = document.getElementById("generate-bricks-status");
const paginationEl = document.getElementById("pagination");
const paginationPrevBtnEl = document.getElementById("pagination-prev-btn");
const paginationNextBtnEl = document.getElementById("pagination-next-btn");
const paginationSummaryEl = document.getElementById("pagination-summary");

export const elements = {
  nanobarTypeFilter: nanobarTypeFilterEl,
  domainFilter: domainFilterEl,
  appBoxFilter: appBoxFilterEl,
  search: searchEl,
  generateBricksBtn: generateBricksBtnEl,
  paginationPrevBtn: paginationPrevBtnEl,
  paginationNextBtn: paginationNextBtnEl,
};

/** Show/hide + enable/disable the "Generate bricks" button while a run is in flight. */
export function setGenerateBricksBusy(isBusy) {
  generateBricksBtnEl.disabled = isBusy;
  generateBricksBtnEl.textContent = isBusy ? "Generating…" : "Generate bricks";
}

export function showGenerateBricksResult(message) {
  generateBricksStatusEl.textContent = message;
  generateBricksStatusEl.hidden = false;
  generateBricksStatusEl.classList.remove("generate-bricks-status-error");
}

export function showGenerateBricksError(message) {
  generateBricksStatusEl.textContent = message;
  generateBricksStatusEl.hidden = false;
  generateBricksStatusEl.classList.add("generate-bricks-status-error");
}

/**
 * Renders pagination controls from the envelope's own page/page_size/total. Hidden entirely
 * when everything fits on one page -- a "Page 1 of 1" control that can never do anything is
 * just noise.
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

/**
 * Fills the nanobar-type filter <select> with one option per distinct nanobar type present in
 * the data, plus the "All nanobar types" default. Preserves the currently selected value if
 * it's still one of the options.
 * @param {string[]} nanobarTypes sorted, deduplicated nanobar types
 */
export function populateNanobarTypeFilter(nanobarTypes) {
  const previousValue = nanobarTypeFilterEl.value;
  nanobarTypeFilterEl.textContent = "";

  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = "All nanobar types";
  nanobarTypeFilterEl.appendChild(allOption);

  for (const nanobarType of nanobarTypes) {
    const option = document.createElement("option");
    option.value = nanobarType;
    option.textContent = nanobarType;
    nanobarTypeFilterEl.appendChild(option);
  }

  if (nanobarTypes.includes(previousValue)) {
    nanobarTypeFilterEl.value = previousValue;
  }
}

//: The domain filter's "no filter" option can't use "" as its value -- "" is itself a real,
//: meaningful domain (a root-level, un-Mounted route), so it must stay distinguishable from
//: "don't filter at all."
export const ALL_DOMAINS_VALUE = "__all__";

/**
 * Fills the domain filter <select> with one option per distinct domain present in the data,
 * plus the "All domains" default. A domain of `""` (root-level routes) renders as "(root)";
 * the backend's `"(unmapped)"` sentinel (nanobars with no domain at all) renders as-is.
 * Preserves the currently selected value if it's still one of the options.
 * @param {string[]} domains sorted, deduplicated domain values (`""` included when present)
 */
export function populateDomainFilter(domains) {
  const previousValue = domainFilterEl.value;
  domainFilterEl.textContent = "";

  const allOption = document.createElement("option");
  allOption.value = ALL_DOMAINS_VALUE;
  allOption.textContent = "All domains";
  domainFilterEl.appendChild(allOption);

  for (const domain of domains) {
    const option = document.createElement("option");
    option.value = domain;
    option.textContent = domain === "" ? "(root)" : domain;
    domainFilterEl.appendChild(option);
  }

  if (previousValue === ALL_DOMAINS_VALUE || domains.includes(previousValue)) {
    domainFilterEl.value = previousValue;
  }
}

//: `ALL_DOMAINS_VALUE`'s exact counterpart for the AppBox filter -- unlike domain, a real
//: app_box value is never itself "" (it's always "admin/app"/"admin/nanobar"/"api"/"workers"),
//: but the same sentinel shape is used anyway for consistency with the domain filter.
export const ALL_APP_BOXES_VALUE = "__all__";

/**
 * Fills the AppBox filter <select> with one option per distinct app_box present in the data,
 * plus the "All AppBoxes" default. The backend's `"(unmapped)"` sentinel (a nanobar with no
 * app_box yet -- created before this field existed, not yet refreshed) renders as-is.
 * Preserves the currently selected value if it's still one of the options.
 * @param {string[]} appBoxes sorted, deduplicated app_box values
 */
export function populateAppBoxFilter(appBoxes) {
  const previousValue = appBoxFilterEl.value;
  appBoxFilterEl.textContent = "";

  const allOption = document.createElement("option");
  allOption.value = ALL_APP_BOXES_VALUE;
  allOption.textContent = "All AppBoxes";
  appBoxFilterEl.appendChild(allOption);

  for (const appBox of appBoxes) {
    const option = document.createElement("option");
    option.value = appBox;
    option.textContent = appBox;
    appBoxFilterEl.appendChild(option);
  }

  if (previousValue === ALL_APP_BOXES_VALUE || appBoxes.includes(previousValue)) {
    appBoxFilterEl.value = previousValue;
  }
}

/** Show a transient status message (e.g. "Loading..."). */
export function showStatus(message) {
  statusEl.textContent = message;
  statusEl.hidden = false;
  statusEl.classList.remove("nanobars-status-error");
  groupsEl.hidden = true;
  emptyEl.hidden = true;
  paginationEl.hidden = true;
}

/** Show an error status message (e.g. load failure or envelope error). */
export function showLoadError(message) {
  statusEl.textContent = message;
  statusEl.hidden = false;
  statusEl.classList.add("nanobars-status-error");
  groupsEl.hidden = true;
  emptyEl.hidden = true;
  paginationEl.hidden = true;
}

/** Hide the status message entirely. */
export function hideStatus() {
  statusEl.hidden = true;
  statusEl.textContent = "";
  statusEl.classList.remove("nanobars-status-error");
}

/**
 * Render the nanobars-by-nanobar-type groups. Each group becomes its own section with a
 * heading, a count, and a list of nanobar links. Shows the empty state when there are no
 * groups at all.
 * @param {Array<{nanobarType: string, nanobars: Array<{nanobar_id: string, system_name: string, label: string | null}>}>} groups
 */
export function renderGroups(groups) {
  groupsEl.innerHTML = "";

  if (!groups || groups.length === 0) {
    groupsEl.hidden = true;
    emptyEl.hidden = false;
    return;
  }

  emptyEl.hidden = true;

  for (const group of groups) {
    const groupNode = groupTemplate.content.cloneNode(true);

    const titleEl = groupNode.querySelector(".nanobar-type-group-title");
    titleEl.textContent = group.nanobarType;

    const countEl = groupNode.querySelector(".nanobar-type-group-count");
    const count = group.nanobars.length;
    countEl.textContent = `${count} nanobar${count === 1 ? "" : "s"}`;

    const listEl = groupNode.querySelector(".nanobar-list");
    for (const nanobar of group.nanobars) {
      const itemNode = itemTemplate.content.cloneNode(true);

      const link = itemNode.querySelector(".nanobar-link");
      link.href = `/admin/nanobar/nanobars/${nanobar.nanobar_id}`;

      itemNode.querySelector(".nanobar-id").textContent = nanobar.nanobar_id;

      // The API endpoint (or other monitor target) this nanobar's class of tests is anchored
      // to -- `monitor_target_refs` can, in principle, hold more than one ref, but every nanobar
      // this app's own domains produce has exactly one, `{target_type: "route", stable_name:
      // "METHOD /path"}` (see nanobar_api.route_manifest); shown when present, the element
      // removed entirely otherwise (an externally-seeded nanobar, e.g. from
      // examples/seed_kahnban_bricks.py, may have no route-shaped ref at all) rather than
      // leaving a visibly empty span, same pattern .nanobar-label already uses below.
      const endpointEl = itemNode.querySelector(".nanobar-endpoint");
      const routeRef = (nanobar.monitor_target_refs || []).find((ref) => ref.target_type === "route");
      if (routeRef) {
        endpointEl.textContent = routeRef.stable_name;
      } else {
        endpointEl.remove();
      }

      itemNode.querySelector(".nanobar-system").textContent = nanobar.system_name;
      const labelEl = itemNode.querySelector(".nanobar-label");
      if (nanobar.label) {
        labelEl.textContent = nanobar.label;
      } else {
        labelEl.remove();
      }

      listEl.appendChild(itemNode);
    }

    groupsEl.appendChild(groupNode);
  }

  groupsEl.hidden = false;
}
