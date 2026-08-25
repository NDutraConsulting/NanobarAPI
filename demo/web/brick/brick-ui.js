// brick-ui.js
// Pure DOM rendering/manipulation for the brick detail page. Given data
// (+ callback-free primitives), builds/updates the DOM. No fetch() calls
// happen in this file.

const titleEl = document.getElementById("brick-title");
const subtitleEl = document.getElementById("brick-subtitle");
const pageStatusEl = document.getElementById("page-status");
const brickDetailEl = document.getElementById("brick-detail");

const fieldBrickIdEl = document.getElementById("field-brick-id");
const fieldContentHashEl = document.getElementById("field-content-hash");
const fieldSchemaVersionEl = document.getElementById("field-schema-version");
const fieldBrickVersionEl = document.getElementById("field-brick-version");
const fieldCreatedByEl = document.getElementById("field-created-by");
const fieldSourceEl = document.getElementById("field-source");

const reviewPillEl = document.getElementById("review-pill");
const reviewButtonEls = Array.from(document.querySelectorAll(".review-btn"));
const reviewErrorEl = document.getElementById("review-error");

const requestJsonEl = document.getElementById("request-json");
const responseJsonEl = document.getElementById("response-json");

const STATUS_LABELS = {
  new: "New",
  reviewed: "Reviewed",
  flagged: "Flagged",
  promoted: "Promoted",
};

/* ------------------------------------------------------------- statuses */

/** Show the "loading" placeholder state; hides the detail view. */
export function showLoading() {
  pageStatusEl.hidden = false;
  pageStatusEl.className = "page-status";
  pageStatusEl.textContent = "Loading brick…";
  brickDetailEl.hidden = true;
}

/** Show a "brick not found" state (404 from the API). */
export function showNotFound(message) {
  titleEl.textContent = "Brick not found";
  pageStatusEl.hidden = false;
  pageStatusEl.className = "page-status page-status-error";
  pageStatusEl.textContent = message || "This regression brick could not be found.";
  brickDetailEl.hidden = true;
}

/** Show a network-failure state, distinct from a 404 / envelope error. */
export function showNetworkError(message) {
  titleEl.textContent = "Could not load brick";
  pageStatusEl.hidden = false;
  pageStatusEl.className = "page-status page-status-error";
  pageStatusEl.textContent = message || "Could not reach the server. Please try again.";
  brickDetailEl.hidden = true;
}

/* -------------------------------------------------------------- render */

/**
 * Renders the full brick detail view from a RegressionBrick object (as
 * returned by GET /api/bricks/{brick_id}, including the attached
 * `review_status`).
 */
export function renderBrick(brick) {
  pageStatusEl.hidden = true;
  brickDetailEl.hidden = false;

  titleEl.textContent = `Brick ${brick.regression_brick_id}`;
  document.title = `Brick ${brick.regression_brick_id} · NanobarAPI`;

  const request = brick.request || {};
  if (request.method || request.path) {
    subtitleEl.textContent = `${request.method || ""} ${request.path || ""}`.trim();
    subtitleEl.hidden = false;
  } else {
    subtitleEl.hidden = true;
  }

  fieldBrickIdEl.textContent = brick.regression_brick_id;
  fieldContentHashEl.textContent = brick.content_hash;
  fieldSchemaVersionEl.textContent = brick.schema_version;
  fieldBrickVersionEl.textContent = brick.brick_version;
  fieldCreatedByEl.textContent = brick.created_by;
  fieldSourceEl.textContent = brick.source;

  renderReviewStatus(brick.review_status);

  requestJsonEl.textContent = JSON.stringify(brick.request, null, 2);
  responseJsonEl.textContent = JSON.stringify(brick.response, null, 2);
}

/**
 * Updates the review-status pill and the "active" button highlight from a
 * review_status object: { regression_brick_id, status, updated_by }.
 */
export function renderReviewStatus(reviewStatus) {
  const status = reviewStatus ? reviewStatus.status : null;
  reviewPillEl.textContent = STATUS_LABELS[status] || status || "Unknown";
  if (status) {
    reviewPillEl.dataset.status = status;
  } else {
    delete reviewPillEl.dataset.status;
  }

  for (const btn of reviewButtonEls) {
    btn.classList.toggle("is-active", btn.dataset.status === status);
  }
}

/* --------------------------------------------------------- review error */

export function showReviewError(message) {
  reviewErrorEl.textContent = message;
  reviewErrorEl.hidden = false;
}

export function clearReviewError() {
  reviewErrorEl.textContent = "";
  reviewErrorEl.hidden = true;
}

/** Disables/enables all review-status buttons (e.g. while a request is in flight). */
export function setReviewButtonsBusy(isBusy) {
  for (const btn of reviewButtonEls) {
    btn.disabled = isBusy;
  }
}

export const elements = {
  reviewButtons: reviewButtonEls,
};
