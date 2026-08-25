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
const fieldScenarioTypeRowEl = document.getElementById("field-scenario-type-row");
const fieldScenarioTypeEl = document.getElementById("field-scenario-type");

const reviewPillEl = document.getElementById("review-pill");
const reviewButtonEls = Array.from(document.querySelectorAll(".review-btn"));
const reviewErrorEl = document.getElementById("review-error");

const requestJsonEl = document.getElementById("request-json");
const responseJsonEl = document.getElementById("response-json");

const scenarioFormEl = document.getElementById("scenario-form");
const scenarioLabelEl = document.getElementById("scenario-label");
const scenarioDescriptionEl = document.getElementById("scenario-description");
const scenarioSaveBtnEl = document.getElementById("scenario-save-btn");
const scenarioErrorEl = document.getElementById("scenario-error");
const scenarioSuccessEl = document.getElementById("scenario-success");

const tagsListEl = document.getElementById("tags-list");
const tagChipTemplate = document.getElementById("tag-chip-template");
const tagAddFormEl = document.getElementById("tag-add-form");
const tagAddInputEl = document.getElementById("tag-add-input");
const tagsErrorEl = document.getElementById("tags-error");

export const elements = {
  scenarioForm: scenarioFormEl,
  tagAddForm: tagAddFormEl,
  tagsList: tagsListEl,
};

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

  if (brick.regression_scenario_type) {
    fieldScenarioTypeRowEl.hidden = false;
    fieldScenarioTypeEl.textContent = brick.regression_scenario_type;
  } else {
    fieldScenarioTypeRowEl.hidden = true;
  }

  renderReviewStatus(brick.review_status);
  renderScenario(brick.scenario);
  renderTags(brick.tags);

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

elements.reviewButtons = reviewButtonEls;

/* -------------------------------------------------------------- scenario */

/** Pre-fills the scenario form's inputs from a `BrickScenario` object (as attached to a
 * brick detail response's `scenario` field) — the form doubles as this section's display of
 * the current values, so they aren't shown a second time read-only. */
export function renderScenario(scenario) {
  scenarioLabelEl.value = (scenario && scenario.regression_scenario_label) || "";
  scenarioDescriptionEl.value = (scenario && scenario.description) || "";
}

/** Reads the scenario form's current field values as a partial-update payload. */
export function readScenarioFormFields() {
  return {
    regression_scenario_label: scenarioLabelEl.value,
    description: scenarioDescriptionEl.value,
  };
}

export function showScenarioError(message) {
  scenarioSuccessEl.hidden = true;
  scenarioErrorEl.textContent = message;
  scenarioErrorEl.hidden = false;
}

export function showScenarioSuccess(message) {
  scenarioErrorEl.hidden = true;
  scenarioSuccessEl.textContent = message;
  scenarioSuccessEl.hidden = false;
}

export function clearScenarioMessages() {
  scenarioErrorEl.hidden = true;
  scenarioSuccessEl.hidden = true;
}

export function setScenarioFormBusy(isBusy) {
  scenarioSaveBtnEl.disabled = isBusy;
  scenarioSaveBtnEl.textContent = isBusy ? "Saving…" : "Save";
}

/* ------------------------------------------------------------------ tags */

/** Renders the tag chip list from a plain array of tag strings. Each chip carries the tag
 * as a `data-tag` attribute on its remove button, for the controller's delegated click
 * handler to read. */
export function renderTags(tags) {
  tagsListEl.textContent = "";
  for (const tag of tags || []) {
    const node = tagChipTemplate.content.cloneNode(true);
    node.querySelector(".tag-chip-text").textContent = tag;
    node.querySelector(".tag-chip-remove").dataset.tag = tag;
    tagsListEl.appendChild(node);
  }
}

export function showTagsError(message) {
  tagsErrorEl.textContent = message;
  tagsErrorEl.hidden = false;
}

export function clearTagsError() {
  tagsErrorEl.hidden = true;
}

export function readTagAddInput() {
  return tagAddInputEl.value.trim();
}

export function clearTagAddInput() {
  tagAddInputEl.value = "";
}
