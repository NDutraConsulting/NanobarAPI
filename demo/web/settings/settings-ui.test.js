// Integration tests for settings-ui.js: loads the real settings.html into jsdom and imports the
// real, unmodified module.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, "settings.html"), "utf8");

const dom = new JSDOM(html, { url: "https://example.test/admin/nanobar/dashboard/settings" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const ui = await import("./settings-ui.js");

function byId(id) {
  return document.getElementById(id);
}

test("the tracing toggle starts disabled until real data loads", () => {
  assert.equal(byId("tracing-toggle").disabled, true);
});

test("showStatus / showLoadError / hideStatus toggle the status banner and its error class", () => {
  ui.showStatus("Loading settings…");
  assert.equal(byId("settings-status").hidden, false);
  assert.equal(byId("settings-status").textContent, "Loading settings…");
  assert.equal(byId("settings-status").classList.contains("settings-status-error"), false);

  ui.showLoadError("Could not load settings.");
  assert.equal(byId("settings-status").hidden, false);
  assert.equal(byId("settings-status").textContent, "Could not load settings.");
  assert.equal(byId("settings-status").classList.contains("settings-status-error"), true);

  ui.hideStatus();
  assert.equal(byId("settings-status").hidden, true);
  assert.equal(byId("settings-status").classList.contains("settings-status-error"), false);
});

test("renderTracingEnabled reflects the value and enables the toggle", () => {
  ui.renderTracingEnabled(true);
  assert.equal(byId("tracing-toggle").checked, true);
  assert.equal(byId("tracing-toggle").disabled, false);

  ui.renderTracingEnabled(false);
  assert.equal(byId("tracing-toggle").checked, false);
});

test("setTracingToggleBusy disables/enables the toggle", () => {
  ui.setTracingToggleBusy(true);
  assert.equal(byId("tracing-toggle").disabled, true);

  ui.setTracingToggleBusy(false);
  assert.equal(byId("tracing-toggle").disabled, false);
});

test("showTracingToggleError / hideTracingToggleError toggle the inline error message", () => {
  ui.showTracingToggleError("Could not save this setting.");
  assert.equal(byId("tracing-toggle-error").hidden, false);
  assert.equal(byId("tracing-toggle-error").textContent, "Could not save this setting.");

  ui.hideTracingToggleError();
  assert.equal(byId("tracing-toggle-error").hidden, true);
  assert.equal(byId("tracing-toggle-error").textContent, "");
});

for (const kind of ["api-routes", "nanobars", "bricks"]) {
  test(`setRefreshBusy toggles the ${kind} row's button label and disabled state`, () => {
    ui.setRefreshBusy(kind, true);
    assert.equal(byId(`${kind}-refresh-btn`).disabled, true);
    assert.equal(byId(`${kind}-refresh-btn`).textContent, "Refreshing…");

    ui.setRefreshBusy(kind, false);
    assert.equal(byId(`${kind}-refresh-btn`).disabled, false);
    assert.equal(byId(`${kind}-refresh-btn`).textContent, "Refresh");
  });

  test(`showRefreshResult / showRefreshError toggle the ${kind} row's status text and error class`, () => {
    ui.showRefreshResult(kind, "12 done.");
    assert.equal(byId(`${kind}-refresh-status`).hidden, false);
    assert.equal(byId(`${kind}-refresh-status`).textContent, "12 done.");
    assert.equal(byId(`${kind}-refresh-status`).classList.contains("refresh-status-error"), false);

    ui.showRefreshError(kind, "Could not run this refresh.");
    assert.equal(byId(`${kind}-refresh-status`).textContent, "Could not run this refresh.");
    assert.equal(byId(`${kind}-refresh-status`).classList.contains("refresh-status-error"), true);
  });

  test(`renderLastRun shows "Never run yet." for the ${kind} row when info is null`, () => {
    ui.renderLastRun(kind, null);
    assert.equal(byId(`${kind}-refresh-last-run`).textContent, "Never run yet.");
  });

  test(`renderLastRun renders a timestamp and summary for the ${kind} row`, () => {
    ui.renderLastRun(kind, { last_run_at: "2026-01-01T00:00:00+00:00", summary: "3 things happened" });

    const text = byId(`${kind}-refresh-last-run`).textContent;
    assert.match(text, /^Last run .+ -- 3 things happened$/);
  });
}
