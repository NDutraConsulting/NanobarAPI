// Integration tests for admin-login-ui.js: loads the real admin-login.html into jsdom and
// imports the real, unmodified module.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, "admin-login.html"), "utf8");

const dom = new JSDOM(html, { url: "https://example.test/admin/login" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const ui = await import("./admin-login-ui.js");

function byId(id) {
  return document.getElementById(id);
}

test("readCredentials reads the current username and password values", () => {
  byId("login-username").value = "admin";
  byId("login-password").value = "changeme123";

  assert.deepEqual(ui.readCredentials(), { username: "admin", password: "changeme123" });
});

test("showError displays the message and clearError hides it", () => {
  ui.showError("invalid credential");
  assert.equal(byId("login-error").hidden, false);
  assert.equal(byId("login-error").textContent, "invalid credential");

  ui.clearError();
  assert.equal(byId("login-error").hidden, true);
});

test("setFormBusy disables the button and swaps its label", () => {
  ui.setFormBusy(true);
  assert.equal(byId("login-submit-btn").disabled, true);
  assert.equal(byId("login-submit-btn").textContent, "Signing in…");

  ui.setFormBusy(false);
  assert.equal(byId("login-submit-btn").disabled, false);
  assert.equal(byId("login-submit-btn").textContent, "Sign in");
});
