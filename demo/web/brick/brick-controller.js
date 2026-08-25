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

/* ---------------------------------------------------------------- init */

async function init() {
  wireReviewButtons();

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
