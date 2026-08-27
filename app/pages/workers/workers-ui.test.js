// Integration tests for workers-ui.js: loads the real workers.html into jsdom and imports the
// real, unmodified module.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, "workers.html"), "utf8");

const dom = new JSDOM(html, { url: "https://example.test/admin/nanobar/workers" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const ui = await import("./workers-ui.js");

function byId(id) {
  return document.getElementById(id);
}

const WORKERS = [
  {
    worker_id: "worker-a",
    channels: ["domain.appointments"],
    mode: "listening",
    schedule: null,
    poll_interval_s: 1.0,
    claim_limit: 10,
    lease_seconds: 30.0,
    started_at: "2026-01-01 00:00:00",
    last_heartbeat_at: "2026-01-01 00:00:05",
    is_stale: false,
  },
  {
    worker_id: "worker-b",
    channels: ["integration-tests"],
    mode: "cron",
    schedule: "0 * * * *",
    poll_interval_s: null,
    claim_limit: 10,
    lease_seconds: 30.0,
    started_at: "2026-01-01 00:00:00",
    last_heartbeat_at: "2026-01-01 00:00:00",
    is_stale: true,
  },
];

test("renderWorkersList shows the empty state for zero workers", () => {
  ui.renderWorkersList([], () => {});

  assert.equal(byId("workers-empty").hidden, false);
  assert.equal(byId("workers-layout").hidden, true);
});

test("renderWorkersList builds one row per worker with a status dot reflecting is_stale", () => {
  ui.renderWorkersList(WORKERS, () => {});

  const rows = byId("workers-list").querySelectorAll(".worker-row");
  assert.equal(rows.length, 2);
  assert.equal(rows[0].querySelector(".worker-id").textContent, "worker-a");
  assert.equal(rows[0].querySelector(".worker-status-dot").classList.contains("worker-status-dot-healthy"), true);
  assert.equal(rows[1].querySelector(".worker-status-dot").classList.contains("worker-status-dot-stale"), true);
});

test("clicking a worker row calls onSelect with that worker's id", () => {
  const selected = [];
  ui.renderWorkersList(WORKERS, (workerId) => selected.push(workerId));

  byId("workers-list").querySelectorAll(".worker-row-btn")[1].click();

  assert.deepEqual(selected, ["worker-b"]);
});

test("highlightSelectedWorker marks exactly one row selected", () => {
  ui.renderWorkersList(WORKERS, () => {});

  ui.highlightSelectedWorker("worker-b");

  const buttons = byId("workers-list").querySelectorAll(".worker-row-btn");
  assert.equal(buttons[0].classList.contains("worker-row-btn-selected"), false);
  assert.equal(buttons[1].classList.contains("worker-row-btn-selected"), true);
});

test("showWorkerDetailEmpty shows the prompt and hides the detail panel", () => {
  ui.renderWorkerDetail(WORKERS[0]);
  assert.equal(byId("worker-detail").hidden, false);

  ui.showWorkerDetailEmpty();

  assert.equal(byId("worker-detail-empty").hidden, false);
  assert.equal(byId("worker-detail").hidden, true);
});

test("renderWorkerDetail shows the status pill and known config fields, omitting null ones", () => {
  ui.renderWorkerDetail(WORKERS[1]);

  assert.equal(byId("worker-detail-heading").textContent, "worker-b");
  assert.equal(byId("worker-status-pill").textContent, "Stale");
  assert.equal(byId("worker-status-pill").classList.contains("worker-status-pill-stale"), true);

  const labels = [...byId("worker-config-grid").querySelectorAll(".worker-config-field-label")].map(
    (el) => el.textContent
  );
  assert.ok(labels.includes("Schedule"));
  assert.ok(labels.includes("Mode"));
  assert.ok(!labels.includes("Poll interval (s)")); // null on worker-b -- omitted, not shown blank
});

test("renderWorkerDetail marks a healthy worker's pill accordingly", () => {
  ui.renderWorkerDetail(WORKERS[0]);

  assert.equal(byId("worker-status-pill").textContent, "Healthy");
  assert.equal(byId("worker-status-pill").classList.contains("worker-status-pill-healthy"), true);
});

test("renderWorkerLog shows the empty state for zero entries", () => {
  ui.renderWorkerLog([]);

  assert.equal(byId("worker-log-empty").hidden, false);
  assert.equal(byId("worker-log-list").hidden, true);
});

test("renderWorkerLog builds one row per entry, falling back to a dash for a missing event_id", () => {
  ui.renderWorkerLog([
    { worker_id: "worker-a", event_id: "evt-1", error: "boom", logged_at: "2026-01-01 00:00:00" },
    { worker_id: "worker-a", event_id: null, error: "boom2", logged_at: "2026-01-01 00:00:01" },
  ]);

  const rows = byId("worker-log-list").querySelectorAll(".worker-log-row");
  assert.equal(rows.length, 2);
  assert.equal(rows[0].querySelector(".worker-log-event-id").textContent, "evt-1");
  assert.equal(rows[1].querySelector(".worker-log-event-id").textContent, "—");
  assert.equal(rows[1].querySelector(".worker-log-error").textContent, "boom2");
});

test("showLoading and showLoadError toggle the status banner and hide the layout", () => {
  ui.renderWorkersList(WORKERS, () => {});

  ui.showLoading();
  assert.equal(byId("workers-status").hidden, false);
  assert.equal(byId("workers-layout").hidden, true);

  ui.showLoadError("custom error");
  assert.equal(byId("workers-status").textContent, "custom error");
  assert.equal(byId("workers-status").classList.contains("workers-status-error"), true);
});
