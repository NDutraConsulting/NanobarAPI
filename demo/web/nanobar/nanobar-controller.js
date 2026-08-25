// nanobar-controller.js
// Orchestrates the nanobar detail page: parses the nanobar id out of the URL,
// calls nanobar-api.js, hands results to nanobar-ui.js to render, and
// surfaces errors. This is the only file with top-level "run on page load"
// logic.

import * as api from "./nanobar-api.js";
import * as ui from "./nanobar-ui.js";

/** Extract the nanobar id from a path like /nanobars/{nanobar_id}. */
function getNanobarIdFromPath() {
  const segments = window.location.pathname.split("/").filter(Boolean);
  return segments[segments.length - 1];
}

const nanobarId = getNanobarIdFromPath();

/* ------------------------------------------------------------ handlers */

async function handleEditSubmit(event) {
  event.preventDefault();
  ui.clearEditMessages();
  ui.setEditFormBusy(true);
  try {
    const envelope = await api.updateNanobar(nanobarId, ui.readEditFormFields());
    if (envelope.status !== "success") {
      ui.showEditError(envelope.msg || "Could not save changes.");
      return;
    }
    ui.renderSummary(nanobarId, envelope.result.data);
    ui.showEditSuccess("Saved.");
  } catch (err) {
    ui.showEditError("Network error while saving. Please try again.");
  } finally {
    ui.setEditFormBusy(false);
  }
}

/**
 * The bricks-for-nanobar endpoint is the only one that 404s when the
 * nanobar itself doesn't exist, so it drives the not-found state. The
 * nanobar's own summary fields come from the full /api/nanobars list
 * (there is no GET /api/nanobars/{id} endpoint) — fetched in parallel and
 * simply left out of the page if that lookup fails for some reason.
 */
async function loadNanobar() {
  let bricksEnvelope;
  try {
    bricksEnvelope = await api.fetchNanobarBricks(nanobarId);
  } catch (err) {
    ui.showNetworkError("Could not reach the server. Please check your connection and try again.");
    return;
  }

  if (bricksEnvelope.status !== "success") {
    ui.showNotFound(bricksEnvelope.msg || `Nanobar ${nanobarId} not found.`);
    return;
  }

  ui.clearPageStatus();

  const bricks = bricksEnvelope.result.data;
  ui.showBricksLoading();
  ui.renderBricks(bricks);

  // Best-effort: the nanobar's own summary fields aren't fatal to the page.
  try {
    const nanobarsEnvelope = await api.fetchNanobars();
    if (nanobarsEnvelope.status === "success") {
      const nanobar = nanobarsEnvelope.result.data.find(
        (candidate) => String(candidate.nanobar_id) === String(nanobarId)
      );
      ui.renderSummary(nanobarId, nanobar || null);
    } else {
      ui.renderSummary(nanobarId, null);
    }
  } catch (err) {
    ui.renderSummary(nanobarId, null);
  }
}

async function init() {
  ui.elements.editForm.addEventListener("submit", handleEditSubmit);

  if (!nanobarId) {
    ui.showNotFound("No nanobar specified.");
    return;
  }

  ui.showLoading();
  await loadNanobar();
}

init();
