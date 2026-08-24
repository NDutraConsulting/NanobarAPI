// triage.js — drag-and-drop for the brick review-status triage board.
//
// Reuses the INTERACTION PATTERN from focusari_kahnban's board-ui.js (native HTML5
// drag-and-drop: dragstart/dragover/drop + event.dataTransfer, no drag-and-drop library),
// applied to this app's own brick/review-status data model instead of kahnban's
// boards/lists/cards. Cards move between columns only (no within-column ordering, since a
// review-status column has no meaningful card order); a successful drop calls the
// review-status API via fetch() and moves the card in the DOM in place, rather than
// reloading the page.

(function () {
  "use strict";

  let draggingCardEl = null;

  function init() {
    document.querySelectorAll(".triage-card").forEach(attachCardHandlers);
    document.querySelectorAll(".triage-column-dropzone").forEach(attachDropZoneHandlers);
  }

  function attachCardHandlers(card) {
    card.addEventListener("dragstart", (event) => {
      draggingCardEl = card;
      card.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", card.dataset.brickId);
    });

    card.addEventListener("dragend", () => {
      card.classList.remove("dragging");
      draggingCardEl = null;
    });
  }

  function attachDropZoneHandlers(zone) {
    zone.addEventListener("dragover", (event) => {
      if (!draggingCardEl) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      zone.classList.add("drag-over");
    });

    zone.addEventListener("dragleave", (event) => {
      if (event.target === zone) zone.classList.remove("drag-over");
    });

    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      zone.classList.remove("drag-over");
      if (!draggingCardEl) return;

      const card = draggingCardEl;
      const targetStatus = zone.dataset.status;

      if (card.parentElement === zone) return;

      moveCard(card, zone, targetStatus);
    });
  }

  function moveCard(card, zone, targetStatus) {
    const brickId = card.dataset.brickId;
    setCardBusy(card, true);

    fetch(`/api/bricks/${encodeURIComponent(brickId)}/review-status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: targetStatus }),
    })
      .then((response) => response.json().then((body) => ({ ok: response.ok, body: body })))
      .then(({ ok, body }) => {
        setCardBusy(card, false);
        if (!ok || body.status !== "success") {
          showCardError(card, (body && body.msg) || "Failed to update review status.");
          return;
        }
        zone.appendChild(card);
        updateColumnCounts();
      })
      .catch(() => {
        setCardBusy(card, false);
        showCardError(card, "Network error updating review status.");
      });
  }

  function setCardBusy(card, busy) {
    card.classList.toggle("busy", busy);
    card.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function showCardError(card, message) {
    card.title = message;
    card.classList.add("error");
    window.setTimeout(() => card.classList.remove("error"), 4000);
  }

  function updateColumnCounts() {
    document.querySelectorAll(".triage-column").forEach((column) => {
      const zone = column.querySelector(".triage-column-dropzone");
      const countEl = column.querySelector(".triage-column-count");
      if (zone && countEl) {
        countEl.textContent = `(${zone.querySelectorAll(".triage-card").length})`;
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
