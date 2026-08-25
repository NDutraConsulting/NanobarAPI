// nanobar-ui.js
// Pure DOM rendering/manipulation for the nanobar detail page.
// No fetch() calls happen in this file.

const titleEl = document.getElementById("nanobar-title");
const subtitleEl = document.getElementById("nanobar-subtitle");

const pageStatusEl = document.getElementById("page-status");

const summarySectionEl = document.getElementById("summary-section");
const summaryGridEl = document.getElementById("summary-grid");
const summaryTargetsEl = document.getElementById("summary-targets");

const bricksStatusEl = document.getElementById("bricks-status");
const bricksTableWrapEl = document.getElementById("bricks-table-wrap");
const bricksTableBodyEl = document.getElementById("bricks-table-body");
const bricksEmptyEl = document.getElementById("bricks-empty");

const brickRowTemplate = document.getElementById("brick-row-template");

const REVIEW_PILL_CLASSES = {
  new: "review-pill-new",
  reviewed: "review-pill-reviewed",
  flagged: "review-pill-flagged",
  promoted: "review-pill-promoted",
};

const CONTENT_HASH_PREFIX_LENGTH = 12;

/* ---------------------------------------------------------- page status */

/** Whole-page loading state: shown before either fetch has resolved. */
export function showLoading() {
  titleEl.textContent = "Loading nanobar…";
  subtitleEl.hidden = true;

  pageStatusEl.hidden = false;
  pageStatusEl.className = "page-status";
  pageStatusEl.textContent = "Loading…";

  summarySectionEl.hidden = true;
  hideBricksContent();
  bricksStatusEl.hidden = true;
}

/** The nanobar itself doesn't exist (404 from the bricks endpoint). */
export function showNotFound(message) {
  titleEl.textContent = "Nanobar not found";
  subtitleEl.hidden = true;

  pageStatusEl.hidden = false;
  pageStatusEl.className = "page-status page-status-error";
  pageStatusEl.textContent = message || "This nanobar could not be found.";

  summarySectionEl.hidden = true;
  hideBricksContent();
  bricksStatusEl.hidden = true;
}

/** The network request itself failed (offline, DNS, etc) — distinct from a 404. */
export function showNetworkError(message) {
  titleEl.textContent = "Could not load nanobar";
  subtitleEl.hidden = true;

  pageStatusEl.hidden = false;
  pageStatusEl.className = "page-status page-status-error";
  pageStatusEl.textContent = message || "Could not reach the server. Please try again.";

  summarySectionEl.hidden = true;
  hideBricksContent();
  bricksStatusEl.hidden = true;
}

/** Clears the page-level status banner once real content is ready to show. */
export function clearPageStatus() {
  pageStatusEl.hidden = true;
}

function hideBricksContent() {
  bricksTableWrapEl.hidden = true;
  bricksEmptyEl.hidden = true;
}

/* --------------------------------------------------------------- summary */

/**
 * Renders the nanobar's own top-level fields, found client-side from the
 * full /api/nanobars list. If `nanobar` is null (couldn't be found in that
 * list, e.g. it was deleted between requests), the summary section stays
 * hidden and only the page title/subtitle fall back to the id.
 */
export function renderSummary(nanobarId, nanobar) {
  document.title = `${nanobar ? nanobar.system_name : nanobarId} · NanobarAPI`;
  titleEl.textContent = nanobar ? nanobar.system_name : nanobarId;

  subtitleEl.hidden = false;
  subtitleEl.textContent = `Nanobar ${nanobarId}`;

  if (!nanobar) {
    summarySectionEl.hidden = true;
    return;
  }

  summarySectionEl.hidden = false;
  summaryGridEl.textContent = "";

  const fields = [
    ["System version", nanobar.system_version],
    ["Scenario type", nanobar.regression_scenario_type],
    ["Regression weight", nanobar.regression_weight],
    ["Scenario frequency", nanobar.endpoint_scenario_frequency],
    ["Created by", nanobar.created_by],
    ["Schema version", nanobar.schema_version],
  ];

  for (const [label, value] of fields) {
    if (value === undefined || value === null || value === "") continue;
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = String(value);
    summaryGridEl.append(dt, dd);
  }

  summaryTargetsEl.textContent = "";
  const targets = nanobar.monitor_target_refs || [];
  for (const ref of targets) {
    const chip = document.createElement("span");
    chip.className = "target-chip";
    const strong = document.createElement("strong");
    strong.textContent = ref.target_type;
    chip.append(strong, document.createTextNode(` · ${ref.stable_name}`));
    summaryTargetsEl.appendChild(chip);
  }
}

/* ---------------------------------------------------------------- bricks */

export function showBricksLoading() {
  bricksStatusEl.hidden = false;
  bricksStatusEl.className = "bricks-status";
  bricksStatusEl.textContent = "Loading bricks…";
  hideBricksContent();
}

export function showBricksError(message) {
  bricksStatusEl.hidden = false;
  bricksStatusEl.className = "bricks-status bricks-status-error";
  bricksStatusEl.textContent = message || "Could not load bricks.";
  hideBricksContent();
}

/** Renders the bricks table (or the empty state) from an array of RegressionBrick objects
 * (each already carrying a `review_status` object, per the bricks-for-nanobar endpoint). */
export function renderBricks(bricks) {
  bricksStatusEl.hidden = true;

  if (!bricks || bricks.length === 0) {
    bricksTableWrapEl.hidden = true;
    bricksEmptyEl.hidden = false;
    return;
  }

  bricksEmptyEl.hidden = true;
  bricksTableWrapEl.hidden = false;
  bricksTableBodyEl.textContent = "";

  for (const brick of bricks) {
    bricksTableBodyEl.appendChild(buildBrickRow(brick));
  }
}

function buildBrickRow(brick) {
  const fragment = brickRowTemplate.content.cloneNode(true);
  const rowEl = fragment.querySelector(".brick-row");
  const link = fragment.querySelector(".brick-link");
  const methodEl = fragment.querySelector(".brick-method");
  const pathEl = fragment.querySelector(".brick-path");
  const statusCodeEl = fragment.querySelector(".brick-status-code");
  const hashEl = fragment.querySelector(".brick-hash");
  const pillEl = fragment.querySelector(".review-pill");

  const request = brick.request || {};
  const response = brick.response || {};
  const reviewStatus = (brick.review_status && brick.review_status.status) || "new";

  link.href = `/bricks/${encodeURIComponent(brick.regression_brick_id)}`;
  methodEl.textContent = request.method || "?";
  pathEl.textContent = request.path || "(no path)";

  statusCodeEl.textContent =
    response.status_code === undefined || response.status_code === null ? "—" : String(response.status_code);

  hashEl.textContent = brick.content_hash ? brick.content_hash.slice(0, CONTENT_HASH_PREFIX_LENGTH) : "—";
  hashEl.title = brick.content_hash || "";

  pillEl.textContent = reviewStatus;
  pillEl.className = `review-pill ${REVIEW_PILL_CLASSES[reviewStatus] || "review-pill-new"}`;

  return rowEl;
}
