// Integration tests for post-ui.js: loads the real post.html into jsdom and imports the real,
// unmodified module.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, "post.html"), "utf8");

const dom = new JSDOM(html, { url: "https://example.test/posts/post-1" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const ui = await import("./post-ui.js");

function byId(id) {
  return document.getElementById(id);
}

test("showLoading and showError toggle the status banner and hide the article", () => {
  ui.showLoading();
  assert.equal(byId("page-status").hidden, false);
  assert.equal(byId("post-article").hidden, true);

  ui.showError("not found");
  assert.equal(byId("page-status").textContent, "not found");
});

test("renderPost fills in title/date/body and sets document.title", () => {
  ui.renderPost({ title: "Hello World", body: "The body.", published_at: "2026-01-01T00:00:00Z" });

  assert.equal(byId("page-status").hidden, true);
  assert.equal(byId("post-article").hidden, false);
  assert.equal(byId("post-title").textContent, "Hello World");
  assert.equal(byId("post-body").textContent, "The body.");
  assert.equal(document.title, "Hello World · NanobarAPI Demo");
});

test("renderPost treats a body containing markup as inert text, not injected HTML", () => {
  ui.renderPost({ title: "T", body: '<img src=x onerror="window.__pwned=true">', published_at: null });

  assert.equal(byId("post-body").querySelector("img"), null);
  assert.equal(window.__pwned, undefined);
});
