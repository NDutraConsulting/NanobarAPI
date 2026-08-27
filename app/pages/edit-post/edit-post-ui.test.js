// Integration tests for edit-post-ui.js: loads the real edit-post.html into jsdom and imports
// the real, unmodified module.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, "edit-post.html"), "utf8");

const dom = new JSDOM(html, { url: "https://example.test/admin/app/posts/post-1/edit" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const ui = await import("./edit-post-ui.js");

function byId(id) {
  return document.getElementById(id);
}

test("showLoading and showLoadError toggle the status banner and hide the form", () => {
  ui.showLoading();
  assert.equal(byId("page-status").hidden, false);
  assert.equal(byId("edit-post-form").hidden, true);

  ui.showLoadError("not found");
  assert.equal(byId("page-status").textContent, "not found");
  assert.equal(byId("edit-post-form").hidden, true);
});

test("renderPost fills in the form fields and reveals the form", () => {
  ui.renderPost({ title: "Hello", body: "World" });

  assert.equal(byId("page-status").hidden, true);
  assert.equal(byId("edit-post-form").hidden, false);
  assert.equal(byId("edit-post-title").value, "Hello");
  assert.equal(byId("edit-post-body").value, "World");
});

test("readFields reads the current form values", () => {
  byId("edit-post-title").value = "Edited title";
  byId("edit-post-body").value = "Edited body";

  assert.deepEqual(ui.readFields(), { title: "Edited title", body: "Edited body" });
});

test("save error/busy state toggles the button and error message", () => {
  ui.showSaveError("could not save");
  assert.equal(byId("edit-post-error").hidden, false);
  assert.equal(byId("edit-post-error").textContent, "could not save");

  ui.clearSaveError();
  assert.equal(byId("edit-post-error").hidden, true);

  ui.setFormBusy(true);
  assert.equal(byId("edit-post-save-btn").disabled, true);
  assert.equal(byId("edit-post-save-btn").textContent, "Saving…");

  ui.setFormBusy(false);
  assert.equal(byId("edit-post-save-btn").disabled, false);
  assert.equal(byId("edit-post-save-btn").textContent, "Save");
});
