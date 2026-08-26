// Integration tests for book-appointment-ui.js: loads the real book-appointment.html into
// jsdom and imports the real, unmodified module.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, "book-appointment.html"), "utf8");

const dom = new JSDOM(html, { url: "https://example.test/book-appointment" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const ui = await import("./book-appointment-ui.js");

function byId(id) {
  return document.getElementById(id);
}

test("readFormFields reads the current input values", () => {
  byId("booking-name").value = "Ada Lovelace";
  byId("booking-email").value = "ada@example.com";
  byId("booking-note").value = "a note";

  assert.deepEqual(ui.readFormFields(), { name: "Ada Lovelace", email: "ada@example.com", note: "a note" });
});

test("showError/showSuccess are mutually exclusive, clearMessages hides both", () => {
  ui.showError("bad");
  assert.equal(byId("booking-error").hidden, false);
  assert.equal(byId("booking-success").hidden, true);

  ui.showSuccess("good");
  assert.equal(byId("booking-success").hidden, false);
  assert.equal(byId("booking-error").hidden, true);

  ui.clearMessages();
  assert.equal(byId("booking-error").hidden, true);
  assert.equal(byId("booking-success").hidden, true);
});

test("setFormBusy disables the button and swaps its label", () => {
  ui.setFormBusy(true);
  assert.equal(byId("booking-submit-btn").disabled, true);
  assert.equal(byId("booking-submit-btn").textContent, "Sending…");

  ui.setFormBusy(false);
  assert.equal(byId("booking-submit-btn").disabled, false);
  assert.equal(byId("booking-submit-btn").textContent, "Request appointment");
});

test("resetForm clears the fields", () => {
  byId("booking-name").value = "Ada";
  ui.resetForm();

  assert.equal(byId("booking-name").value, "");
});
