// Integration tests for nanobar-ui.js: loads the real nanobar.html into jsdom (not a hand-rolled
// stub DOM) and imports the real, unmodified module, so a test failure here means the actual
// page/script pairing is broken, not a mock's idea of it. Covers the criticality-field fix from
// the 2026-08-25 review pass (.focusari/2026-08-25-bug_findings.md #8) plus the render paths the
// review's fork pass asserted were XSS-safe (textContent-only) without ever executing that claim.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, "nanobar.html"), "utf8");

// One jsdom instance, one module import, for the whole file -- matches how the module is
// actually used (loaded once per real page load, its top-level `document.getElementById(...)`
// calls bind to fixed elements it then repeatedly mutates). Each test sets its own preconditions
// explicitly rather than relying on a fresh DOM per test.
const dom = new JSDOM(html, { url: "https://example.test/nanobars/nb-1" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const ui = await import("./nanobar-ui.js");

function byId(id) {
  return document.getElementById(id);
}

/* ------------------------------------------------------------ edit form */

test("readEditFormFields includes criticality when the field has a value", () => {
  byId("edit-label").value = "Get order";
  byId("edit-scenario-description").value = "desc";
  byId("edit-component-source").value = "checkout.repository";
  byId("edit-domain").value = "checkout";
  byId("edit-criticality").value = "0.75";

  const fields = ui.readEditFormFields();

  assert.deepEqual(fields, {
    label: "Get order",
    scenario_description: "desc",
    component_source_description: "checkout.repository",
    domain: "checkout",
    criticality: 0.75,
  });
});

test("readEditFormFields omits criticality when the field is emptied, not sends 0", () => {
  // The bug this test guards: Number("") === 0, a valid in-range value, so an emptied field
  // used to be silently saved as 0.0 instead of leaving the stored value untouched.
  byId("edit-label").value = "Get order";
  byId("edit-scenario-description").value = "";
  byId("edit-component-source").value = "";
  byId("edit-domain").value = "";
  byId("edit-criticality").value = "";

  const fields = ui.readEditFormFields();

  assert.ok(!("criticality" in fields), "criticality must be omitted, not sent as 0");
  assert.equal(fields.label, "Get order");
});

test("readEditFormFields sends an explicit 0 when the user actually types 0", () => {
  byId("edit-criticality").value = "0";

  const fields = ui.readEditFormFields();

  assert.equal(fields.criticality, 0);
});

test("populateEditForm (via renderSummary) pre-fills criticality, defaulting to 0.5 when nullish", () => {
  ui.renderSummary("nb-1", { system_name: "checkout", criticality: 0.9 });
  assert.equal(byId("edit-criticality").value, "0.9");

  ui.renderSummary("nb-1", { system_name: "checkout", criticality: null });
  assert.equal(byId("edit-criticality").value, "0.5");
});

/* --------------------------------------------------------------- summary */

test("renderSummary hides the summary section and shows the id when nanobar is null", () => {
  ui.renderSummary("nb-missing", null);

  assert.equal(byId("summary-section").hidden, true);
  assert.equal(byId("nanobar-title").textContent, "nb-missing");
});

test("renderSummary renders object-valued fields as JSON via textContent, not innerHTML", () => {
  ui.renderSummary("nb-1", {
    system_name: "checkout",
    nanobar_type: "api-response",
    domain: "checkout",
    system_version: "1.0.0",
    regression_weight: 0.8,
    endpoint_scenario_frequency: { state: "measured" },
    source_info: null,
    created_by: "auto",
    schema_version: "1.0",
  });

  const grid = byId("summary-grid");
  assert.ok(grid.textContent.includes('{"state":"measured"}'), "object field rendered as JSON text");
  // Never set via innerHTML anywhere in the module -- a value containing markup must show up
  // as literal text, never be parsed into real elements.
  assert.equal(grid.querySelector("script"), null);
});

test("renderSummary treats a value containing markup as inert text, not injected HTML", () => {
  ui.renderSummary("nb-1", {
    system_name: '<img src=x onerror="window.__pwned=true">',
    domain: "checkout",
  });

  assert.equal(byId("nanobar-title").textContent, '<img src=x onerror="window.__pwned=true">');
  assert.equal(byId("nanobar-title").querySelector("img"), null);
  assert.equal(window.__pwned, undefined);
});

/* -------------------------------------------------------- coverage gaps */

test("renderCoverageGaps shows the empty state for an empty list", () => {
  ui.renderCoverageGaps([]);

  assert.equal(byId("coverage-gaps-empty").hidden, false);
  assert.equal(byId("coverage-gaps-list").hidden, true);
});

test("renderCoverageGaps renders one pill per required scenario type", () => {
  ui.renderCoverageGaps(["unauthorized", "server_error"]);

  const list = byId("coverage-gaps-list");
  assert.equal(list.hidden, false);
  assert.equal(byId("coverage-gaps-empty").hidden, true);
  const pills = [...list.querySelectorAll(".gap-pill")].map((el) => el.textContent);
  assert.deepEqual(pills, ["unauthorized", "server_error"]);
});

/* ---------------------------------------------------------------- bricks */

test("renderBricks shows the empty state for zero bricks", () => {
  ui.renderBricks([]);

  assert.equal(byId("bricks-empty").hidden, false);
  assert.equal(byId("bricks-table-wrap").hidden, true);
});

test("renderBricks builds one row per brick with the expected cell contents", () => {
  ui.renderBricks([
    {
      regression_brick_id: "rbrick-1",
      request: { method: "GET", path: "/orders/1" },
      response: { status_code: 200 },
      content_hash: "sha256:abcdef0123456789",
      review_status: { status: "reviewed" },
    },
  ]);

  const rows = byId("bricks-table-body").querySelectorAll(".brick-row");
  assert.equal(rows.length, 1);
  const row = rows[0];
  assert.equal(row.querySelector(".brick-method").textContent, "GET");
  assert.equal(row.querySelector(".brick-path").textContent, "/orders/1");
  assert.equal(row.querySelector(".brick-status-code").textContent, "200");
  assert.equal(row.querySelector(".brick-hash").textContent, "sha256:abcde");
  assert.equal(row.querySelector(".brick-link").getAttribute("href"), "/admin/nanobar/bricks/rbrick-1");
  const pill = row.querySelector(".review-pill");
  assert.equal(pill.textContent, "reviewed");
  assert.equal(pill.className, "review-pill review-pill-reviewed");
});

test("renderBricks falls back to placeholders for missing request/response/hash/status fields", () => {
  ui.renderBricks([{ regression_brick_id: "rbrick-2", request: {}, response: {} }]);

  const row = byId("bricks-table-body").querySelector(".brick-row");
  assert.equal(row.querySelector(".brick-method").textContent, "?");
  assert.equal(row.querySelector(".brick-path").textContent, "(no path)");
  assert.equal(row.querySelector(".brick-status-code").textContent, "—");
  assert.equal(row.querySelector(".brick-hash").textContent, "—");
  assert.equal(row.querySelector(".review-pill").textContent, "new");
});

test("renderBricks treats a path containing markup as inert text, not injected HTML", () => {
  ui.renderBricks([
    {
      regression_brick_id: "rbrick-3",
      request: { method: "GET", path: '<img src=x onerror="window.__pwned2=true">' },
      response: {},
    },
  ]);

  const pathEl = byId("bricks-table-body").querySelector(".brick-path");
  assert.equal(pathEl.textContent, '<img src=x onerror="window.__pwned2=true">');
  assert.equal(pathEl.querySelector("img"), null);
  assert.equal(window.__pwned2, undefined);
});

/* ------------------------------------------------------------ page status */

test("showLoading, showNotFound, showNetworkError, and clearPageStatus toggle the status banner", () => {
  ui.showLoading();
  assert.equal(byId("page-status").hidden, false);
  assert.equal(byId("page-status").className, "page-status");

  ui.showNotFound("custom not found message");
  assert.equal(byId("page-status").textContent, "custom not found message");
  assert.equal(byId("page-status").className, "page-status page-status-error");

  ui.showNetworkError();
  assert.equal(byId("page-status").textContent, "Could not reach the server. Please try again.");

  ui.clearPageStatus();
  assert.equal(byId("page-status").hidden, true);
});

/* ---------------------------------------------------------------- editing */

test("setEditFormBusy disables the button and swaps its label", () => {
  ui.setEditFormBusy(true);
  assert.equal(byId("edit-save-btn").disabled, true);
  assert.equal(byId("edit-save-btn").textContent, "Saving…");

  ui.setEditFormBusy(false);
  assert.equal(byId("edit-save-btn").disabled, false);
  assert.equal(byId("edit-save-btn").textContent, "Save");
});

test("showEditError/showEditSuccess/clearEditMessages are mutually exclusive", () => {
  ui.showEditError("bad");
  assert.equal(byId("edit-error").hidden, false);
  assert.equal(byId("edit-success").hidden, true);

  ui.showEditSuccess("good");
  assert.equal(byId("edit-success").hidden, false);
  assert.equal(byId("edit-error").hidden, true);

  ui.clearEditMessages();
  assert.equal(byId("edit-error").hidden, true);
  assert.equal(byId("edit-success").hidden, true);
});
