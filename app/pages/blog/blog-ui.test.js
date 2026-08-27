// Integration tests for blog-ui.js: loads the real blog.html into jsdom and imports the real,
// unmodified module.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, "blog.html"), "utf8");

const dom = new JSDOM(html, { url: "https://example.test/" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const ui = await import("./blog-ui.js");

function byId(id) {
  return document.getElementById(id);
}

test("showLoading and showError toggle the status banner", () => {
  ui.showLoading();
  assert.equal(byId("posts-status").hidden, false);
  assert.equal(byId("posts-status").textContent, "Loading…");

  ui.showError("boom");
  assert.equal(byId("posts-status").textContent, "boom");
});

test("renderPosts shows the empty state for zero posts", () => {
  ui.renderPosts([]);

  assert.equal(byId("posts-empty").hidden, false);
  assert.equal(byId("posts-list").hidden, true);
});

test("renderPosts builds one item per post with a link to /posts/{id}", () => {
  ui.renderPosts([{ id: "post-1", title: "Hello", published_at: "2026-01-01T00:00:00Z" }]);

  const items = byId("posts-list").querySelectorAll(".post-item");
  assert.equal(items.length, 1);
  assert.equal(items[0].querySelector(".post-link").getAttribute("href"), "/posts/post-1");
  assert.equal(items[0].querySelector(".post-title").textContent, "Hello");
});

test("renderPosts treats a title containing markup as inert text", () => {
  ui.renderPosts([{ id: "post-2", title: '<img src=x onerror="window.__pwned=true">', published_at: null }]);

  const titleEl = byId("posts-list").querySelector(".post-title");
  assert.equal(titleEl.querySelector("img"), null);
  assert.equal(window.__pwned, undefined);
});
