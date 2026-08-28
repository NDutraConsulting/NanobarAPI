// nanobar-controller.js
// Orchestrates the nanobar detail page: loads the nanobar/its bricks/coverage gaps, wires the
// edit form, and — for whichever brick is currently selected in the left pane — wires the
// Detail tab's review-status/scenario/tags controls (relocated from the now-retired
// brick-controller.js) and the new Run tab (replay + verdict + span monitor). This is the only
// file with top-level "run on page load" logic.

import * as api from "./nanobar-api.js";
import * as ui from "./nanobar-ui.js";

/** Extract the nanobar id from a path like /admin/nanobar/nanobars/{nanobar_id}. */
function getNanobarIdFromPath() {
  const segments = window.location.pathname.split("/").filter(Boolean);
  return segments[segments.length - 1];
}

const nanobarId = getNanobarIdFromPath();

// Which brick is currently shown in the right pane, and the trace id its last "Run" produced
// (if any) — both null until a brick is selected/run.
let selectedBrickId = null;
let lastReplayTraceId = null;

/* ------------------------------------------------------------ nanobar-level handlers */

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

/* ------------------------------------------------------------ brick selection */

function brickIdFromHash() {
  const match = /^#brick-(.+)$/.exec(window.location.hash);
  return match ? decodeURIComponent(match[1]) : null;
}

async function selectBrick(brickId, { updateHash = true } = {}) {
  selectedBrickId = brickId;
  lastReplayTraceId = null;
  ui.highlightSelectedBrick(brickId);
  ui.showDetailTab();
  ui.resetRunTab();
  if (updateHash) {
    window.location.hash = `brick-${encodeURIComponent(brickId)}`;
  }

  try {
    const envelope = await api.fetchBrick(brickId);
    if (envelope.status !== "success") {
      ui.showBrickDetailEmpty();
      return;
    }
    ui.renderBrickDetail(envelope.result.data);
  } catch (err) {
    ui.showBrickDetailEmpty();
  }
}

function handleHashChange() {
  const hashBrickId = brickIdFromHash();
  if (hashBrickId && hashBrickId !== selectedBrickId) {
    selectBrick(hashBrickId, { updateHash: false });
  }
}

/* ------------------------------------------------------------ Detail tab handlers */

async function handleReviewStatusClick(status) {
  if (!selectedBrickId) return;
  ui.clearReviewError();
  ui.setReviewButtonsBusy(true);
  try {
    const envelope = await api.setReviewStatus(selectedBrickId, status);
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
  if (!selectedBrickId) return;
  ui.clearScenarioMessages();
  ui.setScenarioFormBusy(true);
  try {
    const envelope = await api.setBrickScenario(selectedBrickId, ui.readScenarioFormFields());
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
  if (!selectedBrickId) return;
  ui.clearTagsError();
  const tag = ui.readTagAddInput();
  if (!tag) {
    ui.showTagsError("Enter a tag first.");
    return;
  }
  try {
    const envelope = await api.addBrickTag(selectedBrickId, tag);
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
  if (!selectedBrickId) return;
  ui.clearTagsError();
  try {
    const envelope = await api.removeBrickTag(selectedBrickId, tag);
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

/* ------------------------------------------------------------ Run tab handlers */

async function handleRunClick() {
  if (!selectedBrickId) return;
  ui.setRunBusy(true);
  ui.setRunProgressVisible(true);
  ui.showRunStatus("Running…");
  try {
    const envelope = await api.replayBrick(selectedBrickId);
    if (envelope.status !== "success") {
      ui.showRunError(envelope.msg || "Could not run this brick.");
      return;
    }
    lastReplayTraceId = envelope.result.data.trace_id;
    ui.renderVerdict(envelope.result.data.verdict);
    await handleRefreshClick();
  } catch (err) {
    ui.showRunError("Network error while running this brick. Please try again.");
  } finally {
    ui.setRunBusy(false);
    ui.setRunProgressVisible(false);
  }
}

// A replay's own spans reach the telemetry db via the background TelemetryDrainWorker, which
// batches on a timer (nanobar_api/telemetry/telemetry_drain_worker.py, up to ~batch_window_s)
// rather than synchronously with the replay POST that produced them -- GET .../traces/{trace_id}
// /spans can 404 for up to that long right after a run, even though the replay itself already
// succeeded. Same class of race the backend's own event-to-subscriber replay dispatch already
// handles with a bounded poll loop (regression_brick_analysis_service.py's
// _REPLAY_SPAN_POLL_ATTEMPTS/_REPLAY_SPAN_POLL_INTERVAL_S) -- mirrored here with the same total
// ~3s budget, since this is the same underlying drain delay, just observed from the client side
// instead of from another server-side call.
const SPAN_POLL_ATTEMPTS = 10;
const SPAN_POLL_INTERVAL_MS = 300;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function handleRefreshClick() {
  if (!lastReplayTraceId) return;
  const traceId = lastReplayTraceId;
  ui.setRefreshBusy(true);
  ui.setRunProgressVisible(true);
  try {
    for (let attempt = 0; attempt < SPAN_POLL_ATTEMPTS; attempt++) {
      // The user may have selected a different brick (changing lastReplayTraceId) or started a
      // fresh run while this loop was waiting -- stop rather than clobber newer state with a
      // stale trace's spans.
      if (lastReplayTraceId !== traceId) return;

      let envelope;
      try {
        envelope = await api.fetchTraceSpans(traceId);
      } catch (err) {
        // Best-effort: the verdict already rendered is the primary result; a failed span
        // refresh just leaves the span list as it was.
        return;
      }
      if (envelope.status === "success") {
        ui.renderRunSpans(envelope.result.data);
        return;
      }
      if (attempt === 0) {
        ui.showRunStatus("Waiting for spans…");
      }
      await sleep(SPAN_POLL_INTERVAL_MS);
    }
    // Never resolved within budget -- leave the span list as it was rather than showing a
    // misleading "not found" for what's most likely still-draining data, not a real error.
  } finally {
    ui.setRefreshBusy(false);
    ui.setRunProgressVisible(false);
  }
}

/* ------------------------------------------------------------ tabs */

function wireTabs() {
  ui.elements.tabDetailBtn.addEventListener("click", ui.showDetailTab);
  ui.elements.tabRunBtn.addEventListener("click", ui.showRunTab);
}

/* ---------------------------------------------------------------- init */

/**
 * The bricks-for-nanobar endpoint is the only one that 404s when the nanobar itself doesn't
 * exist, so it drives the not-found state. The nanobar's own summary fields come from the
 * dedicated GET .../nanobars/{id} endpoint — fetched in parallel and simply left out of the
 * page if that lookup fails for some reason.
 */
async function loadNanobar() {
  let bricksEnvelope;
  try {
    bricksEnvelope = await api.fetchNanobarBricks(nanobarId);
  } catch (err) {
    ui.showNetworkError("Could not reach the server. Please refresh the page and try again.");
    return;
  }

  if (bricksEnvelope.status !== "success") {
    ui.showNotFound(bricksEnvelope.msg || `Nanobar ${nanobarId} not found.`);
    return;
  }

  ui.clearPageStatus();

  const bricks = bricksEnvelope.result.data;
  ui.showBricksLoading();
  ui.renderBricksList(bricks, (brickId) => selectBrick(brickId));

  const hashBrickId = brickIdFromHash();
  if (hashBrickId && bricks.some((b) => b.regression_brick_id === hashBrickId)) {
    selectBrick(hashBrickId, { updateHash: false });
  }

  // Best-effort: missing coverage isn't fatal to the page either.
  ui.showCoverageGapsLoading();
  try {
    const gapsEnvelope = await api.fetchCoverageGaps(nanobarId);
    if (gapsEnvelope.status === "success") {
      ui.renderCoverageGaps(gapsEnvelope.result.data);
    } else {
      ui.showCoverageGapsError(gapsEnvelope.msg);
    }
  } catch (err) {
    ui.showCoverageGapsError("Could not reach the server. Please refresh the page and try again.");
  }

  // Best-effort: the nanobar's own summary fields aren't fatal to the page.
  try {
    const nanobarEnvelope = await api.fetchNanobar(nanobarId);
    ui.renderSummary(nanobarId, nanobarEnvelope.status === "success" ? nanobarEnvelope.result.data : null);
  } catch (err) {
    ui.renderSummary(nanobarId, null);
  }
}

async function init() {
  ui.elements.editForm.addEventListener("submit", handleEditSubmit);
  wireReviewButtons();
  ui.elements.scenarioForm.addEventListener("submit", handleScenarioSubmit);
  ui.elements.tagAddForm.addEventListener("submit", handleTagAddSubmit);
  wireTagRemoveDelegation();
  wireTabs();
  ui.elements.runBtn.addEventListener("click", handleRunClick);
  ui.elements.refreshBtn.addEventListener("click", handleRefreshClick);
  window.addEventListener("hashchange", handleHashChange);

  if (!nanobarId) {
    ui.showNotFound("No nanobar specified.");
    return;
  }

  ui.showLoading();
  await loadNanobar();
}

init();
