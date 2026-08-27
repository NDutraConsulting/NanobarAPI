// triage-ui.js
// DOM rendering/manipulation for the triage board. Given data (+ a set of
// callback functions supplied by the controller), builds/updates the DOM.
// No fetch() calls happen in this file.

const STATUSES = ["new", "reviewed", "flagged", "promoted"];

const cardTemplate = document.getElementById("card-template");
const boardStatusEl = document.getElementById("board-status");
const boardEl = document.getElementById("board");
const toastEl = document.getElementById("toast");

// One drop zone (.cards) + one count badge per fixed status column. The
// board has exactly these four columns, always, so they're addressed
// directly by id rather than built from a template like the columns
// themselves.
const dropZones = {};
const countEls = {};
for (const status of STATUSES) {
  dropZones[status] = document.getElementById(`cards-${status}`);
  countEls[status] = document.getElementById(`count-${status}`);
}

let toastTimer = null;

// Tracks the card currently being dragged, so drop zones know what to do
// on dragover/drop without relying solely on dataTransfer (which can't be
// read during dragover in most browsers).
let draggingBrickId = null;
let draggingFromStatus = null;

/* ---------------------------------------------------------------- toast */

export function showToast(message) {
  if (!message) return;
  toastEl.textContent = "";

  const text = document.createElement("span");
  text.textContent = message;

  const dismiss = document.createElement("button");
  dismiss.type = "button";
  dismiss.className = "toast-dismiss";
  dismiss.setAttribute("aria-label", "Dismiss message");
  dismiss.textContent = "\u00d7";
  dismiss.addEventListener("click", hideToast);

  toastEl.append(text, dismiss);
  toastEl.hidden = false;

  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, 6000);
}

export function hideToast() {
  toastEl.hidden = true;
  clearTimeout(toastTimer);
}

/* ------------------------------------------------------------- statuses */

export function showLoading() {
  boardStatusEl.hidden = false;
  boardStatusEl.className = "board-status";
  boardStatusEl.textContent = "Loading triage board\u2026";
  boardEl.hidden = true;
}

export function showLoadError(message) {
  boardStatusEl.hidden = false;
  boardStatusEl.className = "board-status board-status-error";
  boardStatusEl.textContent = message || "Could not load the triage board.";
  boardEl.hidden = true;
}

/* -------------------------------------------------------------- render */

/**
 * Renders the full board from a columns object:
 *   { new: [brick, ...], reviewed: [...], flagged: [...], promoted: [...] }
 * Each brick is a RegressionBrick with its `review_status` attached.
 * `callbacks` is an object of handler functions the controller supplies —
 * see attachDropZoneHandlers/buildCard below for the expected names.
 */
export function renderBoard(columns, callbacks) {
  boardStatusEl.hidden = true;
  boardEl.hidden = false;

  for (const status of STATUSES) {
    const zone = dropZones[status];
    zone.textContent = "";

    const bricks = columns[status] || [];
    for (const brick of bricks) {
      zone.appendChild(buildCard(brick, status, callbacks));
    }
    refreshEmptyState(status);
    updateCount(status);
  }
}

function updateCount(status) {
  countEls[status].textContent = String(dropZones[status].querySelectorAll(".card").length);
}

function refreshEmptyState(status) {
  const zone = dropZones[status];
  const hasCards = zone.querySelector(".card") !== null;
  let placeholder = zone.querySelector(".cards-empty");

  if (hasCards) {
    if (placeholder) placeholder.remove();
    return;
  }

  if (!placeholder) {
    placeholder = document.createElement("p");
    placeholder.className = "cards-empty";
    placeholder.textContent = "No bricks here.";
    zone.appendChild(placeholder);
  }
}

function buildCard(brick, status, callbacks) {
  const fragment = cardTemplate.content.cloneNode(true);
  const el = fragment.querySelector(".card");
  const methodEl = fragment.querySelector(".card-method");
  const pathEl = fragment.querySelector(".card-path");
  const idEl = fragment.querySelector(".card-id");
  const hashEl = fragment.querySelector(".card-hash");

  el.dataset.brickId = brick.regression_brick_id;
  el.dataset.status = status;

  const request = brick.request || {};
  methodEl.textContent = request.method || "?";
  pathEl.textContent = request.path || "(no path)";
  pathEl.title = request.path || "";

  idEl.textContent = brick.regression_brick_id;
  idEl.title = brick.regression_brick_id;

  const hash = brick.content_hash || "";
  hashEl.textContent = hash ? hash.slice(0, 12) + "\u2026" : "(no hash)";

  el.addEventListener("dragstart", (event) => {
    draggingBrickId = brick.regression_brick_id;
    draggingFromStatus = status;
    el.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", String(brick.regression_brick_id));
  });

  el.addEventListener("dragend", () => {
    el.classList.remove("dragging");
    draggingBrickId = null;
    draggingFromStatus = null;
  });

  return el;
}

/* ---------------------------------------------------------- drag/drop */

// Drop zones are static (one per fixed column, present in the HTML from
// the start), so their listeners are attached once at module load rather
// than rebuilt on every render.
for (const status of STATUSES) {
  attachDropZoneHandlers(dropZones[status], status);
}

let dropCallbacks = null;

function attachDropZoneHandlers(zone, targetStatus) {
  zone.addEventListener("dragover", (event) => {
    if (draggingBrickId === null) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    zone.classList.add("drag-over");
  });

  zone.addEventListener("dragleave", (event) => {
    if (event.target === zone) zone.classList.remove("drag-over");
  });

  zone.addEventListener("drop", (event) => {
    if (draggingBrickId === null) return;
    event.preventDefault();
    zone.classList.remove("drag-over");

    const brickId = draggingBrickId;
    const fromStatus = draggingFromStatus;

    if (dropCallbacks && fromStatus !== targetStatus) {
      dropCallbacks.onCardMove(brickId, fromStatus, targetStatus);
    }
  });
}

/**
 * The controller registers its callbacks once, up front (renderBoard is
 * called repeatedly on re-render, but the drop zones themselves — and
 * therefore their listeners — are only ever attached once).
 */
export function setCallbacks(callbacks) {
  dropCallbacks = callbacks;
}

/* ---------------------------------------------------- post-move updates */

/** Moves an existing card's DOM node from one column to another. */
export function moveCardToColumn(brickId, fromStatus, toStatus) {
  const card = findCardEl(brickId, fromStatus);
  if (!card) return;

  card.dataset.status = toStatus;
  clearCardError(card);
  dropZones[toStatus].appendChild(card);

  refreshEmptyState(fromStatus);
  refreshEmptyState(toStatus);
  updateCount(fromStatus);
  updateCount(toStatus);
}

/** Shows an inline error on a card, leaving it wherever it currently is. */
export function showCardError(brickId, currentStatus, message) {
  const card = findCardEl(brickId, currentStatus);
  if (!card) return;
  const errorEl = card.querySelector(".card-error");
  errorEl.textContent = message;
  errorEl.hidden = false;
}

function clearCardError(card) {
  const errorEl = card.querySelector(".card-error");
  errorEl.hidden = true;
  errorEl.textContent = "";
}

/** Marks a card as mid-request: dims it and blocks further drag attempts. */
export function setCardPending(brickId, status, isPending) {
  const card = findCardEl(brickId, status);
  if (!card) return;
  card.classList.toggle("card-pending", isPending);
  card.draggable = !isPending;
}

function findCardEl(brickId, status) {
  const zone = dropZones[status];
  if (!zone) return null;
  return zone.querySelector(`.card[data-brick-id="${cssEscape(brickId)}"]`);
}

function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === "function") {
    return window.CSS.escape(value);
  }
  return String(value).replace(/["\\]/g, "\\$&");
}
