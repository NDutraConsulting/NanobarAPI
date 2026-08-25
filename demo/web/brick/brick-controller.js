// brick-controller.js
// Orchestrates the brick detail page: parses the brick id from the URL,
// calls brick-api.js, hands results to brick-ui.js to render, and wires up
// the review-status buttons. This is the only file with top-level "run on
// page load" logic.

import * as api from "./brick-api.js";
import * as ui from "./brick-ui.js";

/** Extract the brick id from a path like /bricks/{brick_id}. */
function getBrickIdFromPath() {
  const segments = window.location.pathname.split("/").filter(Boolean);
  return segments[segments.length - 1];
}

const brickId = getBrickIdFromPath();

/* ------------------------------------------------------------ handlers */

async function handleReviewStatusClick(status) {
  ui.clearReviewError();
  ui.setReviewButtonsBusy(true);
  try {
    const envelope = await api.setReviewStatus(brickId, status);
    if (envelope.status !== "success") {
      ui.showReviewError(envelope.msg || "Could not update review status.");
      return;
    }
    ui.renderReviewStatus(envelope.result.data);
  } catch (err) {
    ui.showReviewError("Network error while updating review status. Please try again.");
  } finally {
    ui.setReviewButtonsBusy(false);
  }
}

function wireReviewButtons() {
  for (const btn of ui.elements.reviewButtons) {
    btn.addEventListener("click", () => {
      handleReviewStatusClick(btn.dataset.status);
    });
  }
}

async function handleScenarioSubmit(event) {
  event.preventDefault();
  ui.clearScenarioMessages();
  ui.setScenarioFormBusy(true);
  try {
    const envelope = await api.setBrickScenario(brickId, ui.readScenarioFormFields());
    if (envelope.status !== "success") {
      ui.showScenarioError(envelope.msg || "Could not save the scenario.");
      return;
    }
    ui.renderScenario(envelope.result.data);
    ui.showScenarioSuccess("Saved.");
  } catch (err) {
    ui.showScenarioError("Network error while saving. Please try again.");
  } finally {
    ui.setScenarioFormBusy(false);
  }
}

async function handleTagAddSubmit(event) {
  event.preventDefault();
  ui.clearTagsError();
  const tag = ui.readTagAddInput();
  if (!tag) {
    ui.showTagsError("Enter a tag first.");
    return;
  }
  try {
    const envelope = await api.addBrickTag(brickId, tag);
    if (envelope.status !== "success") {
      ui.showTagsError(envelope.msg || "Could not add the tag.");
      return;
    }
    ui.renderTags(envelope.result.data);
    ui.clearTagAddInput();
  } catch (err) {
    ui.showTagsError("Network error while adding the tag. Please try again.");
  }
}

async function handleTagRemoveClick(tag) {
  ui.clearTagsError();
  try {
    const envelope = await api.removeBrickTag(brickId, tag);
    if (envelope.status !== "success") {
      ui.showTagsError(envelope.msg || "Could not remove the tag.");
      return;
    }
    ui.renderTags(envelope.result.data);
  } catch (err) {
    ui.showTagsError("Network error while removing the tag. Please try again.");
  }
}

/** Delegated click handler on the tags list — chips are re-rendered on every change, so a
 * per-chip listener would need re-wiring each time; listening on the stable parent instead
 * avoids that. */
function wireTagRemoveDelegation() {
  ui.elements.tagsList.addEventListener("click", (event) => {
    const btn = event.target.closest(".tag-chip-remove");
    if (btn) {
      handleTagRemoveClick(btn.dataset.tag);
    }
  });
}

/* ---------------------------------------------------------------- init */

async function init() {
  wireReviewButtons();
  ui.elements.scenarioForm.addEventListener("submit", handleScenarioSubmit);
  ui.elements.tagAddForm.addEventListener("submit", handleTagAddSubmit);
  wireTagRemoveDelegation();

  if (!brickId) {
    ui.showNotFound("No brick specified.");
    return;
  }

  ui.showLoading();

  try {
    const envelope = await api.fetchBrick(brickId);
    if (envelope.status !== "success") {
      ui.showNotFound(envelope.msg || "Brick not found.");
      return;
    }
    ui.renderBrick(envelope.result.data);
  } catch (err) {
    ui.showNetworkError("Could not load this brick due to a network error.");
  }
}

init();
