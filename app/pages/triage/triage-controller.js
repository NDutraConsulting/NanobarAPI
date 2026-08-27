// triage-controller.js
// Orchestrates the triage board page: loads bricks, wires up drag-and-drop
// callbacks, calls triage-api.js, hands results to triage-ui.js to render,
// and surfaces errors. This is the only file with top-level "run on page
// load" logic.

import * as api from "./triage-api.js";
import * as ui from "./triage-ui.js";

const STATUSES = ["new", "reviewed", "flagged", "promoted"];

// In-memory copy of the currently rendered board: { new: [brick, ...],
// reviewed: [...], flagged: [...], promoted: [...] }. Kept in sync with the
// DOM so a card's current column can be looked up when it's dragged again.
let boardState = null;

function findBrickColumn(brickId) {
  for (const status of STATUSES) {
    const idx = boardState[status].findIndex((b) => b.regression_brick_id === brickId);
    if (idx !== -1) return { status, idx };
  }
  return null;
}

/* ------------------------------------------------------------ handlers */

async function onCardMove(brickId, fromStatus, toStatus) {
  const located = findBrickColumn(brickId);
  if (!located || located.status !== fromStatus) return;

  ui.setCardPending(brickId, fromStatus, true);

  try {
    const envelope = await api.setBrickReviewStatus(brickId, toStatus);

    if (envelope.status !== "success") {
      ui.setCardPending(brickId, fromStatus, false);
      ui.showCardError(brickId, fromStatus, envelope.msg || "Could not update review status.");
      return;
    }

    const [brick] = boardState[fromStatus].splice(located.idx, 1);
    brick.review_status = envelope.result.data;
    boardState[toStatus].push(brick);

    // Clear the pending state while the card is still addressable at its
    // old status, then move its DOM node into the new column.
    ui.setCardPending(brickId, fromStatus, false);
    ui.moveCardToColumn(brickId, fromStatus, toStatus);
  } catch (err) {
    ui.setCardPending(brickId, fromStatus, false);
    ui.showCardError(brickId, fromStatus, "Could not reach the server. Please refresh the page and try again.");
  }
}

const callbacks = { onCardMove };

/* ---------------------------------------------------------------- init */

/**
 * Loads every brick bound to every nanobar and groups them by review
 * status. There is no single "list all bricks" endpoint, so this fetches
 * GET /api/nanobars and then, for each nanobar, GET
 * /api/nanobars/{id}/bricks (N+1 requests). A brick bound to more than one
 * nanobar is deduped by regression_brick_id. This is a known limitation
 * acceptable at this demo's scale — a real deployment with many nanobars
 * would want a dedicated "all bricks" endpoint instead.
 */
async function loadBoard() {
  const nanobarsEnvelope = await api.fetchNanobars();
  if (nanobarsEnvelope.status !== "success") {
    ui.showLoadError(nanobarsEnvelope.msg || "Could not load nanobars.");
    return;
  }
  const nanobars = nanobarsEnvelope.result.data || [];

  const bricksById = new Map();
  await Promise.all(
    nanobars.map(async (nanobar) => {
      try {
        const bricksEnvelope = await api.fetchNanobarBricks(nanobar.nanobar_id);
        if (bricksEnvelope.status !== "success") return;
        for (const brick of bricksEnvelope.result.data || []) {
          if (!bricksById.has(brick.regression_brick_id)) {
            bricksById.set(brick.regression_brick_id, brick);
          }
        }
      } catch (err) {
        // A single nanobar's bricks failing to load shouldn't take down
        // the whole board — its bricks are just missing from the columns.
      }
    })
  );

  const columns = { new: [], reviewed: [], flagged: [], promoted: [] };
  for (const brick of bricksById.values()) {
    const status = brick.review_status && brick.review_status.status;
    if (status && columns[status]) {
      columns[status].push(brick);
    } else {
      // Unrecognized/missing status: fall back to the "new" bucket rather
      // than silently dropping the brick from the board.
      columns.new.push(brick);
    }
  }

  boardState = columns;
  ui.renderBoard(boardState, callbacks);
}

async function init() {
  ui.setCallbacks(callbacks);
  ui.showLoading();

  try {
    await loadBoard();
  } catch (err) {
    ui.showLoadError("Could not reach the server. Please refresh the page and try again.");
  }
}

init();
