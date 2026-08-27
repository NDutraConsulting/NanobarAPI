// Integration tests for trace-ui.js: loads the real trace.html into jsdom and imports the
// real, unmodified module.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, "trace.html"), "utf8");

const dom = new JSDOM(html, { url: "https://example.test/admin/nanobar/traces/tr-1" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const ui = await import("./trace-ui.js");

function byId(id) {
  return document.getElementById(id);
}

const EVENTS = [
  {
    event_id: "evt-1",
    span_id: "span-1",
    trace_id: "tr-1",
    monotonic_ns: 1_000_000,
    recorded_at_ns: 1_700_000_000_000_000_000,
    payload: { name: "GET /checkout", "http.request.method": "GET", status_code: 200 },
  },
  {
    event_id: "evt-2",
    span_id: "span-2",
    trace_id: "tr-1",
    monotonic_ns: 2_000_000,
    recorded_at_ns: 1_700_000_000_100_000_000,
    payload: { name: "controller.GET /checkout", nanobar_type: "controller-request-response", error: true },
  },
];

test("renderSpans builds one row per event and shows the empty state for zero events", () => {
  ui.renderSpans([], () => {});
  assert.equal(byId("trace-empty").hidden, false);
  assert.equal(byId("trace-layout").hidden, true);

  ui.renderSpans(EVENTS, () => {});
  assert.equal(byId("trace-empty").hidden, true);
  assert.equal(byId("trace-layout").hidden, false);
  assert.equal(byId("spans-list").querySelectorAll(".span-row").length, 2);
});

test("a row with a nanobar_type shows the badge; one without does not", () => {
  ui.renderSpans(EVENTS, () => {});
  const rows = byId("spans-list").querySelectorAll(".span-row");

  assert.equal(rows[0].querySelector(".span-nanobar-type-badge").hidden, true);
  assert.equal(rows[1].querySelector(".span-nanobar-type-badge").hidden, false);
  assert.equal(rows[1].querySelector(".span-nanobar-type-badge").textContent, "controller-request-response");
});

test("an errored span's row gets the error class", () => {
  ui.renderSpans(EVENTS, () => {});
  const rows = byId("spans-list").querySelectorAll(".span-row");

  assert.equal(rows[0].classList.contains("span-row-error"), false);
  assert.equal(rows[1].classList.contains("span-row-error"), true);
});

test("clicking a span row calls onSelect with that event's id", () => {
  const selected = [];
  ui.renderSpans(EVENTS, (eventId) => selected.push(eventId));

  byId("spans-list").querySelectorAll(".span-row-btn")[1].click();

  assert.deepEqual(selected, ["evt-2"]);
});

test("highlightSelectedSpan marks exactly one row selected", () => {
  ui.renderSpans(EVENTS, () => {});

  ui.highlightSelectedSpan("evt-2");

  const buttons = byId("spans-list").querySelectorAll(".span-row-btn");
  assert.equal(buttons[0].classList.contains("span-row-btn-selected"), false);
  assert.equal(buttons[1].classList.contains("span-row-btn-selected"), true);
});

test("showEmptyDetail shows the prompt and hides the detail pane", () => {
  ui.renderSpanDetail(EVENTS[0]);
  assert.equal(byId("span-detail").hidden, false);

  ui.showEmptyDetail();

  assert.equal(byId("span-detail-empty").hidden, false);
  assert.equal(byId("span-detail").hidden, true);
});

test("renderSpanDetail shows identity fields, known payload fields, and the raw JSON", () => {
  ui.renderSpanDetail(EVENTS[1]);

  const text = byId("span-detail").textContent;
  assert.match(text, /evt-2/);
  assert.match(text, /span-2/);
  assert.match(text, /controller-request-response/);
  assert.ok(byId("span-detail").querySelector(".span-detail-raw"));
  assert.equal(
    JSON.parse(byId("span-detail").querySelector(".span-detail-raw").textContent).nanobar_type,
    "controller-request-response"
  );
});

test("renderSpanDetail omits a known field the payload doesn't have", () => {
  ui.renderSpanDetail(EVENTS[0]);

  const labels = [...byId("span-detail").querySelectorAll(".span-detail-field-label")].map((el) => el.textContent);
  assert.ok(!labels.includes("Nanobar type"));
});
