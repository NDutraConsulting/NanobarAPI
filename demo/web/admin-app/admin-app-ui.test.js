// Integration tests for admin-app-ui.js: loads the real admin-app.html into jsdom and imports
// the real, unmodified module.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(__dirname, "admin-app.html"), "utf8");

const dom = new JSDOM(html, { url: "https://example.test/admin/app/dashboard" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const ui = await import("./admin-app-ui.js");

function byId(id) {
  return document.getElementById(id);
}

/* -------------------------------------------------------------- notifications */

test("renderNotifications shows the empty state for zero notifications", () => {
  ui.renderNotifications([], () => {});

  assert.equal(byId("notifications-empty").hidden, false);
  assert.equal(byId("notifications-list").hidden, true);
});

test("renderNotifications builds one item per notification, unread ones get a clickable Mark read button", () => {
  const calls = [];
  ui.renderNotifications(
    [{ id: "notif-1", message: "New appointment", created_at: "2026-01-01T00:00:00Z", read: false }],
    (id) => calls.push(id)
  );

  const item = byId("notifications-list").querySelector(".notification-item");
  assert.equal(item.querySelector(".notification-message").textContent, "New appointment");
  const btn = item.querySelector(".notification-read-btn");
  assert.equal(btn.disabled, false);

  btn.click();
  assert.deepEqual(calls, ["notif-1"]);
});

test("renderNotifications disables the button and relabels it for already-read notifications", () => {
  ui.renderNotifications(
    [{ id: "notif-2", message: "Old", created_at: "2026-01-01T00:00:00Z", read: true }],
    () => {}
  );

  const btn = byId("notifications-list").querySelector(".notification-read-btn");
  assert.equal(btn.disabled, true);
  assert.equal(btn.textContent, "Read");
});

test("renderNotifications treats a message containing markup as inert text", () => {
  ui.renderNotifications(
    [{ id: "notif-3", message: '<img src=x onerror="window.__pwned=true">', created_at: "2026-01-01T00:00:00Z", read: false }],
    () => {}
  );

  const messageEl = byId("notifications-list").querySelector(".notification-message");
  assert.equal(messageEl.querySelector("img"), null);
  assert.equal(window.__pwned, undefined);
});

/* --------------------------------------------------------------------- posts */

test("renderPosts shows the empty state for zero posts", () => {
  ui.renderPosts([]);

  assert.equal(byId("posts-empty").hidden, false);
  assert.equal(byId("posts-list").hidden, true);
});

test("renderPosts builds one item per post with title, status, and an edit-page link", () => {
  ui.renderPosts([{ id: "post-1", title: "Hello", status: "draft" }]);

  const item = byId("posts-list").querySelector(".post-item");
  assert.equal(item.querySelector(".post-title").textContent, "Hello");
  assert.equal(item.querySelector(".post-status").textContent, "draft");
  assert.equal(item.querySelector(".post-item-link").getAttribute("href"), "/admin/app/posts/post-1/edit");
});

/* ---------------------------------------------------------------- new-post form */

test("readNewPostFields omits scheduled_at when the datetime-local input is empty", () => {
  byId("new-post-title").value = "T";
  byId("new-post-body").value = "B";
  byId("new-post-scheduled-at").value = "";

  const fields = ui.readNewPostFields();

  assert.deepEqual(fields, { title: "T", body: "B" });
});

test("readNewPostFields converts a datetime-local value to a real ISO 8601 string", () => {
  byId("new-post-title").value = "T";
  byId("new-post-body").value = "B";
  byId("new-post-scheduled-at").value = "2026-06-01T10:30";

  const fields = ui.readNewPostFields();

  assert.ok("scheduled_at" in fields);
  // Round-trips through Date -- exact offset depends on the test runner's local timezone, but
  // it must always be a real, parseable ISO 8601 instant matching the entered wall-clock time.
  assert.equal(new Date(fields.scheduled_at).getTime(), new Date("2026-06-01T10:30").getTime());
});

test("new-post error/success messages are mutually exclusive, and form-busy toggles the button", () => {
  ui.showNewPostError("bad");
  assert.equal(byId("new-post-error").hidden, false);
  assert.equal(byId("new-post-success").hidden, true);

  ui.showNewPostSuccess("good");
  assert.equal(byId("new-post-success").hidden, false);
  assert.equal(byId("new-post-error").hidden, true);

  ui.clearNewPostMessages();
  assert.equal(byId("new-post-error").hidden, true);
  assert.equal(byId("new-post-success").hidden, true);

  ui.setNewPostFormBusy(true);
  assert.equal(byId("new-post-submit-btn").disabled, true);
  assert.equal(byId("new-post-submit-btn").textContent, "Saving…");

  ui.setNewPostFormBusy(false);
  assert.equal(byId("new-post-submit-btn").disabled, false);
});

test("resetNewPostForm clears the fields", () => {
  byId("new-post-title").value = "T";
  ui.resetNewPostForm();

  assert.equal(byId("new-post-title").value, "");
});
