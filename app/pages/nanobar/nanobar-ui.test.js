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
  byId("edit-app-box").value = "api";
  byId("edit-criticality").value = "0.75";

  const fields = ui.readEditFormFields();

  assert.deepEqual(fields, {
    label: "Get order",
    scenario_description: "desc",
    component_source_description: "checkout.repository",
    domain: "checkout",
    app_box: "api",
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
  byId("edit-app-box").value = "";
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

test("populateEditForm (via renderSummary) pre-fills app_box, same as domain", () => {
  ui.renderSummary("nb-1", { system_name: "checkout", app_box: "admin/app" });
  assert.equal(byId("edit-app-box").value, "admin/app");

  ui.renderSummary("nb-1", { system_name: "checkout", app_box: null });
  assert.equal(byId("edit-app-box").value, "");
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

test("renderCoverageGaps shows the empty state for a classified nanobar with no gaps", () => {
  ui.renderCoverageGaps({ status: "classified", gaps: [] });

  assert.equal(byId("coverage-gaps-empty").hidden, false);
  assert.equal(byId("coverage-gaps-list").hidden, true);
  assert.equal(byId("coverage-needs-classification").hidden, true);
});

test("renderCoverageGaps renders one pill per required scenario type", () => {
  ui.renderCoverageGaps({ status: "classified", gaps: ["unauthorized", "server_error"] });

  const list = byId("coverage-gaps-list");
  assert.equal(list.hidden, false);
  assert.equal(byId("coverage-gaps-empty").hidden, true);
  assert.equal(byId("coverage-needs-classification").hidden, true);
  const pills = [...list.querySelectorAll(".gap-pill")].map((el) => el.textContent);
  assert.deepEqual(pills, ["unauthorized", "server_error"]);
});

test("renderCoverageGaps shows the needs-classification block with a span link when a related span was found", () => {
  ui.renderCoverageGaps({
    status: "needs_classification",
    nanobar_type: "totally-unrecognized-type",
    gaps: [],
    related_span: { trace_id: "tr-1", event_id: "evt-1", name: "GET /checkout", recorded_at_ns: 1 },
  });

  assert.equal(byId("coverage-needs-classification").hidden, false);
  assert.equal(byId("coverage-gaps-list").hidden, true);
  assert.equal(byId("coverage-gaps-empty").hidden, true);
  assert.equal(byId("needs-classification-type").textContent, "totally-unrecognized-type");
  assert.equal(byId("needs-classification-span").hidden, false);
  assert.equal(byId("needs-classification-no-span").hidden, true);
  const link = byId("needs-classification-span-link");
  assert.equal(link.textContent, "GET /checkout");
  assert.equal(link.getAttribute("href"), "/admin/nanobar/traces/tr-1#span-evt-1");
});

test("renderCoverageGaps shows the no-span fallback when needs_classification has no related span", () => {
  ui.renderCoverageGaps({
    status: "needs_classification",
    nanobar_type: "totally-unrecognized-type",
    gaps: [],
    related_span: null,
  });

  assert.equal(byId("coverage-needs-classification").hidden, false);
  assert.equal(byId("needs-classification-span").hidden, true);
  assert.equal(byId("needs-classification-no-span").hidden, false);
});

/* ---------------------------------------------------------------- bricks list (left pane) */

test("renderBricksList shows the empty state for zero bricks", () => {
  ui.renderBricksList([], () => {});

  assert.equal(byId("bricks-empty").hidden, false);
  assert.equal(byId("bricks-layout").hidden, true);
});

test("renderBricksList builds one row per brick with the expected contents", () => {
  ui.renderBricksList(
    [
      {
        regression_brick_id: "rbrick-1",
        request: { method: "GET", path: "/orders/1" },
        response: { status_code: 200 },
        content_hash: "sha256:abcdef0123456789",
        review_status: { status: "reviewed" },
      },
    ],
    () => {}
  );

  const rows = byId("bricks-list").querySelectorAll(".brick-row");
  assert.equal(rows.length, 1);
  const row = rows[0];
  assert.equal(row.querySelector(".brick-method").textContent, "GET");
  assert.equal(row.querySelector(".brick-path").textContent, "/orders/1");
  assert.equal(row.querySelector(".brick-row-btn").dataset.brickId, "rbrick-1");
  const pill = row.querySelector(".review-pill");
  assert.equal(pill.textContent, "reviewed");
  assert.equal(pill.className, "review-pill review-pill-reviewed");
});

test("renderBricksList falls back to the route_key and 'new' status when fields are missing", () => {
  ui.renderBricksList(
    [{ regression_brick_id: "rbrick-2", request: {}, response: {}, source: { route_key: "POST /items" } }],
    () => {}
  );

  const row = byId("bricks-list").querySelector(".brick-row");
  assert.equal(row.querySelector(".brick-method").textContent, "POST");
  assert.equal(row.querySelector(".brick-path").textContent, "/items");
  assert.equal(row.querySelector(".review-pill").textContent, "new");
});

test("renderBricksList treats a path containing markup as inert text, not injected HTML", () => {
  ui.renderBricksList(
    [
      {
        regression_brick_id: "rbrick-3",
        request: { method: "GET", path: '<img src=x onerror="window.__pwned2=true">' },
        response: {},
      },
    ],
    () => {}
  );

  const pathEl = byId("bricks-list").querySelector(".brick-path");
  assert.equal(pathEl.textContent, '<img src=x onerror="window.__pwned2=true">');
  assert.equal(pathEl.querySelector("img"), null);
  assert.equal(window.__pwned2, undefined);
});

test("clicking a brick row calls onSelect with that brick's id", () => {
  const selected = [];
  ui.renderBricksList(
    [
      { regression_brick_id: "rbrick-1", request: { method: "GET", path: "/a" }, response: {} },
      { regression_brick_id: "rbrick-2", request: { method: "GET", path: "/b" }, response: {} },
    ],
    (brickId) => selected.push(brickId)
  );

  byId("bricks-list").querySelectorAll(".brick-row-btn")[1].click();

  assert.deepEqual(selected, ["rbrick-2"]);
});

test("highlightSelectedBrick marks exactly one row selected", () => {
  ui.renderBricksList(
    [
      { regression_brick_id: "rbrick-1", request: { method: "GET", path: "/a" }, response: {} },
      { regression_brick_id: "rbrick-2", request: { method: "GET", path: "/b" }, response: {} },
    ],
    () => {}
  );

  ui.highlightSelectedBrick("rbrick-2");

  const buttons = byId("bricks-list").querySelectorAll(".brick-row-btn");
  assert.equal(buttons[0].classList.contains("brick-row-btn-selected"), false);
  assert.equal(buttons[1].classList.contains("brick-row-btn-selected"), true);
});

/* -------------------------------------------------------- brick detail (right pane, Detail tab) */

test("showBrickDetailEmpty shows the prompt and hides the detail panel", () => {
  ui.renderBrickDetail({
    regression_brick_id: "rbrick-1",
    request: {},
    response: {},
    schema_version: "1.0",
    brick_version: 1,
    created_by: "test",
    content_hash: "sha256:x",
    source: {},
  });
  assert.equal(byId("brick-detail-panel").hidden, false);

  ui.showBrickDetailEmpty();

  assert.equal(byId("brick-detail-empty").hidden, false);
  assert.equal(byId("brick-detail-panel").hidden, true);
});

test("renderBrickDetail fills in identity fields and request/response JSON", () => {
  ui.renderBrickDetail({
    regression_brick_id: "rbrick-1",
    request: { method: "GET", path: "/orders/1" },
    response: { status_code: 200 },
    schema_version: "1.0",
    brick_version: 1,
    created_by: "test",
    content_hash: "sha256:abcdef",
    source: { route_key: "GET /orders/1" },
    review_status: { status: "new" },
    scenario: { regression_scenario_label: null, description: null },
    tags: [],
  });

  assert.equal(byId("brick-detail-heading").textContent, "GET /orders/1");
  assert.equal(byId("field-brick-id").textContent, "rbrick-1");
  assert.equal(byId("field-content-hash").textContent, "sha256:abcdef");
  assert.equal(JSON.parse(byId("request-json").textContent).path, "/orders/1");
  assert.equal(JSON.parse(byId("response-json").textContent).status_code, 200);
});

/* -------------------------------------------------------------- tabs */

test("showDetailTab and showRunTab toggle which panel is visible", () => {
  ui.showRunTab();
  assert.equal(byId("tab-run-panel").hidden, false);
  assert.equal(byId("tab-detail-panel").hidden, true);
  assert.equal(byId("tab-run-btn").getAttribute("aria-selected"), "true");

  ui.showDetailTab();
  assert.equal(byId("tab-detail-panel").hidden, false);
  assert.equal(byId("tab-run-panel").hidden, true);
  assert.equal(byId("tab-detail-btn").getAttribute("aria-selected"), "true");
});

/* -------------------------------------------------------------- Run tab */

test("setRunBusy toggles the Run button's disabled state and label", () => {
  ui.setRunBusy(true);
  assert.equal(byId("run-btn").disabled, true);
  assert.equal(byId("run-btn").textContent, "Running…");

  ui.setRunBusy(false);
  assert.equal(byId("run-btn").disabled, false);
  assert.equal(byId("run-btn").textContent, "Run");
});

test("resetRunTab clears any previous verdict/spans and disables Refresh", () => {
  ui.renderVerdict({ overall_passed: true, diffs: [] });
  assert.equal(byId("refresh-btn").disabled, false);

  ui.resetRunTab();

  assert.equal(byId("run-verdict").hidden, true);
  assert.equal(byId("refresh-btn").disabled, true);
});

test("renderVerdict shows PASS with no diff list when overall_passed is true", () => {
  ui.renderVerdict({ overall_passed: true, diffs: [] });

  assert.equal(byId("run-verdict").hidden, false);
  assert.equal(byId("run-verdict").classList.contains("run-verdict-pass"), true);
  assert.match(byId("run-verdict").textContent, /PASS/);
  assert.equal(byId("run-verdict").querySelector(".run-verdict-diffs"), null);
  assert.equal(byId("refresh-btn").disabled, false);
});

test("renderVerdict shows FAIL and lists each diff line when overall_passed is false", () => {
  ui.renderVerdict({
    overall_passed: false,
    diffs: [
      "response.status_code: brick=200 replayed=404",
      "response.payload.title: brick='Edited' replayed=None",
    ],
  });

  assert.equal(byId("run-verdict").hidden, false);
  assert.equal(byId("run-verdict").classList.contains("run-verdict-fail"), true);
  assert.match(byId("run-verdict").textContent, /FAIL/);
  const items = byId("run-verdict").querySelectorAll(".run-verdict-diff");
  assert.equal(items.length, 2);
  assert.equal(items[0].textContent, "response.status_code: brick=200 replayed=404");
  assert.equal(items[1].textContent, "response.payload.title: brick='Edited' replayed=None");
  assert.equal(byId("refresh-btn").disabled, false);
});

test("renderRunSpans builds one row per span with a nanobar_type badge when tagged", () => {
  ui.renderRunSpans([
    { payload: { name: "controller.POST /x", nanobar_type: "controller-request-response", status_code: 200 } },
    { payload: { name: "GET /x" } },
  ]);

  const rows = byId("run-spans-list").querySelectorAll(".run-span-row");
  assert.equal(rows.length, 2);
  assert.equal(rows[0].querySelector(".run-span-nanobar-type").hidden, false);
  assert.equal(rows[1].querySelector(".run-span-nanobar-type").hidden, true);
});

test("renderRunSpans hides the wrap for an empty span list", () => {
  ui.renderRunSpans([]);

  assert.equal(byId("run-spans-wrap").hidden, true);
});

test("renderRunSpans derives a name/status from a nameless snapshot-channel event instead of falling back to '(unnamed span)'/'—'", () => {
  ui.renderRunSpans([
    {
      payload: {
        request: { method: "POST", path: "/admin/app/api/posts/{post_id}" },
        response: { title: "Edited" },
        nanobar_type: "validator-request-response",
        error: false,
      },
    },
    {
      payload: {
        request: { statement: "SELECT posts.id FROM posts   WHERE posts.id = ?" },
        response: { rowcount: -1 },
        nanobar_type: "orm-request-response",
        error: false,
      },
    },
    { payload: { nanobar_type: "service-request-response", error: true } },
  ]);

  const rows = byId("run-spans-list").querySelectorAll(".run-span-row");
  assert.equal(rows[0].querySelector(".run-span-name").textContent, "POST /admin/app/api/posts/{post_id}");
  assert.equal(rows[0].querySelector(".run-span-status").textContent, "ok");
  assert.equal(rows[1].querySelector(".run-span-name").textContent, "SELECT posts.id FROM posts WHERE posts.id = ?");
  assert.equal(rows[2].querySelector(".run-span-name").textContent, "service-request-response capture");
  assert.equal(rows[2].querySelector(".run-span-status").textContent, "error");
  assert.equal(rows[2].classList.contains("run-span-row-error"), true);
});

test("renderRunSpans truncates a long SQL statement in the name but keeps it in full in the expanded payload", () => {
  const longStatement = `SELECT ${"x".repeat(100)} FROM posts`;
  ui.renderRunSpans([{ payload: { request: { statement: longStatement }, nanobar_type: "orm-request-response" } }]);

  const row = byId("run-spans-list").querySelector(".run-span-row");
  const name = row.querySelector(".run-span-name").textContent;
  assert.equal(name.length <= 71, true);
  assert.equal(name.endsWith("…"), true);
  assert.match(row.querySelector(".run-span-payload").textContent, new RegExp(longStatement));
});

test("renderRunSpans exposes each span's full raw payload as pretty-printed JSON for expansion", () => {
  const payload = { name: "controller.POST /x", nanobar_type: "controller-request-response", status_code: 200 };
  ui.renderRunSpans([{ payload }]);

  const payloadEl = byId("run-spans-list").querySelector(".run-span-payload");
  assert.equal(payloadEl.textContent, JSON.stringify(payload, null, 2));
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
  assert.equal(byId("page-status").textContent, "Could not reach the server. Please refresh the page and try again.");

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
