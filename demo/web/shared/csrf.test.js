// Integration test for shared/csrf.js -- real jsdom `document.cookie`, real module, not a mock.

import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "https://example.test/" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { csrfHeader } = await import("./csrf.js");

test("csrfHeader returns {} when no CSRF cookie is set", () => {
  assert.deepEqual(csrfHeader(), {});
});

test("csrfHeader returns the header pair once the CSRF cookie is set", () => {
  document.cookie = "nanobar_csrftoken=abc123";

  assert.deepEqual(csrfHeader(), { "x-nanobar-csrf-token": "abc123" });
});

test("csrfHeader ignores unrelated cookies and decodes the token value", () => {
  document.cookie = "other=1";
  document.cookie = "nanobar_csrftoken=" + encodeURIComponent("weird value/with+chars");

  assert.deepEqual(csrfHeader(), { "x-nanobar-csrf-token": "weird value/with+chars" });
});
