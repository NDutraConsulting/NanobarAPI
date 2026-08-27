// Integration test for admin-app-login-api.js's fetch wrapper. `fetch` is stubbed (no real network
// hop), but URL, method, headers, and body construction are the real module code.

import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "https://example.test/" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const api = await import("./admin-app-login-api.js");

function stubFetch(responseBody) {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { json: async () => responseBody };
  };
  return calls;
}

test("submitLogin POSTs the username/password to /admin/app/login with a JSON body", async () => {
  const calls = stubFetch({ status: "success", result: { data: { redirect: "/admin/app/dashboard" } } });

  const envelope = await api.submitLogin("admin", "changeme123");

  assert.equal(calls[0].url, "/admin/app/login");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.equal(calls[0].options.body, JSON.stringify({ username: "admin", password: "changeme123" }));
  assert.equal(envelope.result.data.redirect, "/admin/app/dashboard");
});

test("submitLogin attaches the CSRF header when the cookie is set", async () => {
  document.cookie = "nanobar_csrftoken=tok456";
  const calls = stubFetch({ status: "error", msg: "invalid username or password" });

  await api.submitLogin("admin", "wrong");

  assert.equal(calls[0].options.headers["x-nanobar-csrf-token"], "tok456");
});
