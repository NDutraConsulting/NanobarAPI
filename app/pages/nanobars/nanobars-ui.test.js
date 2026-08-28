// Integration tests for nanobars-ui.js: loads the real nanobars.html into jsdom and imports the
// real, unmodified module. Only covers what this build added (search/pagination/generate-bricks
// controls) -- renderGroups/populateNanobarTypeFilter predate this and aren't re-tested here.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, "nanobars.html"), "utf8");

const dom = new JSDOM(html, { url: "https://example.test/admin/nanobar/dashboard" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const ui = await import("./nanobars-ui.js");

function byId(id) {
  return document.getElementById(id);
}

test("renderPagination hides the control when everything fits on one page", () => {
  ui.renderPagination({ page: 1, pageSize: 50, total: 10 });

  assert.equal(byId("pagination").hidden, true);
});

test("renderPagination shows a summary and disables Prev on the first page", () => {
  ui.renderPagination({ page: 1, pageSize: 50, total: 120 });

  assert.equal(byId("pagination").hidden, false);
  assert.equal(byId("pagination-summary").textContent, "Page 1 of 3 (120 total)");
  assert.equal(byId("pagination-prev-btn").disabled, true);
  assert.equal(byId("pagination-next-btn").disabled, false);
});

test("renderPagination disables Next on the last page", () => {
  ui.renderPagination({ page: 3, pageSize: 50, total: 120 });

  assert.equal(byId("pagination-prev-btn").disabled, false);
  assert.equal(byId("pagination-next-btn").disabled, true);
});

test("setGenerateBricksBusy toggles the button's disabled state and label", () => {
  ui.setGenerateBricksBusy(true);
  assert.equal(byId("generate-bricks-btn").disabled, true);
  assert.equal(byId("generate-bricks-btn").textContent, "Generating…");

  ui.setGenerateBricksBusy(false);
  assert.equal(byId("generate-bricks-btn").disabled, false);
  assert.equal(byId("generate-bricks-btn").textContent, "Generate bricks");
});

test("renderGroups groups by nanobar_type, one row per nanobar, no per-item type chip", () => {
  ui.renderGroups([
    {
      nanobarType: "validator-request-response",
      nanobars: [
        {
          nanobar_id: "nb-1",
          system_name: "checkout",
          label: "Checkout validator",
          monitor_target_refs: [{ target_type: "route", stable_name: "POST /checkout" }],
        },
        { nanobar_id: "nb-2", system_name: "checkout", label: null, monitor_target_refs: [] },
      ],
    },
    {
      nanobarType: "orm-request-response",
      nanobars: [{ nanobar_id: "nb-3", system_name: "checkout", label: null, monitor_target_refs: [] }],
    },
  ]);

  assert.equal(byId("nanobars-groups").hidden, false);
  assert.equal(byId("nanobars-empty").hidden, true);

  const sections = byId("nanobars-groups").querySelectorAll(".nanobar-type-group");
  assert.equal(sections.length, 2);

  const firstTitle = sections[0].querySelector(".nanobar-type-group-title");
  assert.equal(firstTitle.textContent, "validator-request-response");
  assert.equal(sections[0].querySelector(".nanobar-type-group-count").textContent, "2 nanobars");

  const items = sections[0].querySelectorAll(".nanobar-item");
  assert.equal(items.length, 2);
  assert.equal(items[0].querySelector(".nanobar-id").textContent, "nb-1");
  assert.equal(items[0].querySelector(".nanobar-endpoint").textContent, "POST /checkout");
  assert.equal(items[0].querySelector(".nanobar-label").textContent, "Checkout validator");
  // No route-shaped monitor_target_refs entry -> the endpoint element is removed entirely, not
  // left blank (same pattern .nanobar-label already uses for a missing label).
  assert.equal(items[1].querySelector(".nanobar-endpoint"), null);
  // No label -> the element is removed entirely, not left blank.
  assert.equal(items[1].querySelector(".nanobar-label"), null);
  // The per-item type chip is gone -- the group heading already carries that information.
  assert.equal(items[0].querySelector(".nanobar-track-type"), null);
});

test("renderGroups removes the endpoint element when monitor_target_refs is missing entirely", () => {
  ui.renderGroups([
    { nanobarType: "worker", nanobars: [{ nanobar_id: "nb-4", system_name: "kahnban", label: null }] },
  ]);

  const item = byId("nanobars-groups").querySelector(".nanobar-item");
  assert.equal(item.querySelector(".nanobar-endpoint"), null);
});

test("renderGroups shows the empty state for zero groups", () => {
  ui.renderGroups([]);

  assert.equal(byId("nanobars-groups").hidden, true);
  assert.equal(byId("nanobars-empty").hidden, false);
});

test("populateNanobarTypeFilter fills the select with an 'All' option plus one per type", () => {
  ui.populateNanobarTypeFilter(["orm-request-response", "validator-request-response"]);

  const select = byId("nanobar-type-filter");
  const optionValues = [...select.options].map((o) => o.value);
  assert.deepEqual(optionValues, ["", "orm-request-response", "validator-request-response"]);
  assert.equal(select.options[0].textContent, "All nanobar types");
});

test("populateNanobarTypeFilter preserves the current selection when still valid", () => {
  ui.populateNanobarTypeFilter(["orm-request-response"]);
  byId("nanobar-type-filter").value = "orm-request-response";

  ui.populateNanobarTypeFilter(["orm-request-response", "validator-request-response"]);

  assert.equal(byId("nanobar-type-filter").value, "orm-request-response");
});

test("populateDomainFilter fills the select with an 'All domains' option plus one per domain, rendering '' as (root)", () => {
  ui.populateDomainFilter(["", "admin/app", "admin/nanobar"]);

  const select = byId("domain-filter");
  const optionValues = [...select.options].map((o) => o.value);
  const optionLabels = [...select.options].map((o) => o.textContent);
  assert.deepEqual(optionValues, [ui.ALL_DOMAINS_VALUE, "", "admin/app", "admin/nanobar"]);
  assert.deepEqual(optionLabels, ["All domains", "(root)", "admin/app", "admin/nanobar"]);
});

test("populateDomainFilter renders the (unmapped) sentinel value as-is, not specially", () => {
  ui.populateDomainFilter(["(unmapped)", "admin/app"]);

  const select = byId("domain-filter");
  assert.deepEqual(
    [...select.options].map((o) => o.textContent),
    ["All domains", "(unmapped)", "admin/app"]
  );
});

test("populateDomainFilter preserves the current selection when still valid, including the empty-string root domain", () => {
  ui.populateDomainFilter(["", "admin/app"]);
  byId("domain-filter").value = "";

  ui.populateDomainFilter(["", "admin/app", "admin/nanobar"]);

  assert.equal(byId("domain-filter").value, "");
});

test("populateAppBoxFilter fills the select with an 'All AppBoxes' option plus one per app_box", () => {
  ui.populateAppBoxFilter(["admin/app", "admin/nanobar", "api", "workers"]);

  const select = byId("app-box-filter");
  const optionValues = [...select.options].map((o) => o.value);
  const optionLabels = [...select.options].map((o) => o.textContent);
  assert.deepEqual(optionValues, [ui.ALL_APP_BOXES_VALUE, "admin/app", "admin/nanobar", "api", "workers"]);
  assert.deepEqual(optionLabels, ["All AppBoxes", "admin/app", "admin/nanobar", "api", "workers"]);
});

test("populateAppBoxFilter renders the (unmapped) sentinel value as-is, not specially", () => {
  ui.populateAppBoxFilter(["(unmapped)", "api"]);

  const select = byId("app-box-filter");
  assert.deepEqual(
    [...select.options].map((o) => o.textContent),
    ["All AppBoxes", "(unmapped)", "api"]
  );
});

test("populateAppBoxFilter preserves the current selection when still valid", () => {
  ui.populateAppBoxFilter(["api", "workers"]);
  byId("app-box-filter").value = "workers";

  ui.populateAppBoxFilter(["admin/app", "api", "workers"]);

  assert.equal(byId("app-box-filter").value, "workers");
});

test("showGenerateBricksResult and showGenerateBricksError set the status text and error class", () => {
  ui.showGenerateBricksResult("Processed 3 new brick(s).");
  assert.equal(byId("generate-bricks-status").hidden, false);
  assert.equal(byId("generate-bricks-status").textContent, "Processed 3 new brick(s).");
  assert.equal(byId("generate-bricks-status").classList.contains("generate-bricks-status-error"), false);

  ui.showGenerateBricksError("Could not generate bricks.");
  assert.equal(byId("generate-bricks-status").textContent, "Could not generate bricks.");
  assert.equal(byId("generate-bricks-status").classList.contains("generate-bricks-status-error"), true);
});
