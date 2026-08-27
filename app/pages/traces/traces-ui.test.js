// Integration tests for traces-ui.js: loads the real traces.html into jsdom and imports the
// real, unmodified module. Only covers what this build added (filter panel + pagination) --
// renderTraces/formatNsTimestamp predate this and aren't re-tested here.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, "traces.html"), "utf8");

const dom = new JSDOM(html, { url: "https://example.test/admin/nanobar/traces" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const ui = await import("./traces-ui.js");

function byId(id) {
  return document.getElementById(id);
}

test("renderPagination hides the control when everything fits on one page", () => {
  ui.renderPagination({ page: 1, pageSize: 100, total: 10 });

  assert.equal(byId("pagination").hidden, true);
});

test("renderPagination shows a summary and enforces first/last page button state", () => {
  ui.renderPagination({ page: 2, pageSize: 100, total: 250 });

  assert.equal(byId("pagination").hidden, false);
  assert.equal(byId("pagination-summary").textContent, "Page 2 of 3 (250 total)");
  assert.equal(byId("pagination-prev-btn").disabled, false);
  assert.equal(byId("pagination-next-btn").disabled, false);
});

test("updateFilterSummaryText reflects show-all, default, since-hours, and explicit-range states", () => {
  ui.updateFilterSummaryText({ showAll: true, createdAfter: "", createdBefore: "", sinceHours: "", isDefault: false });
  assert.equal(byId("filter-summary-text").textContent, "showing all time");

  ui.updateFilterSummaryText({ showAll: false, createdAfter: "", createdBefore: "", sinceHours: "", isDefault: true });
  assert.equal(byId("filter-summary-text").textContent, "showing today");

  ui.updateFilterSummaryText({ showAll: false, createdAfter: "", createdBefore: "", sinceHours: "6", isDefault: false });
  assert.equal(byId("filter-summary-text").textContent, "showing last 6 hour(s)");

  ui.updateFilterSummaryText({
    showAll: false,
    createdAfter: "2026-01-01T00:00:00.000Z",
    createdBefore: "",
    sinceHours: "",
    isDefault: false,
  });
  assert.equal(byId("filter-summary-text").textContent, "showing after 2026-01-01T00:00:00.000Z");
});

test("renderNanobarTypeCheckboxes and renderComponentCheckboxes build checkboxes with prior selections preserved", () => {
  ui.renderNanobarTypeCheckboxes(["controller-request-response", "worker-snapshot"], new Set(["worker-snapshot"]));
  ui.renderComponentCheckboxes(["api:GET /x", "worker:w1"], new Set());

  const checkedTypes = ui.getCheckedValues(ui.nanobarTypesList);
  assert.deepEqual(checkedTypes, ["worker-snapshot"]);

  const checkedComponents = ui.getCheckedValues(ui.componentsList);
  assert.deepEqual(checkedComponents, []);

  ui.componentsList.querySelector('input[value="api:GET /x"]').checked = true;
  assert.deepEqual(ui.getCheckedValues(ui.componentsList), ["api:GET /x"]);
});
