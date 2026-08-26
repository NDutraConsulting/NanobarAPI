// Integration tests for nanobar-api.js's fetch wrappers. `fetch` itself is stubbed (no real
// network hop in a unit test), but everything from the exported function down through URL
// construction, encoding, method, headers, and body serialization is the real module code --
// only the actual HTTP transport is replaced.

import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";

// nanobar-api.js now imports shared/csrf.js, which reads document.cookie -- a jsdom document is
// needed even though this file otherwise never touches the DOM.
const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "https://example.test/" });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const api = await import("./nanobar-api.js");

function stubFetch(responseBody) {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { json: async () => responseBody };
  };
  return calls;
}

test("fetchNanobars issues a GET to /admin/nanobar/api/nanobars", async () => {
  const calls = stubFetch({ status: "success", result: { data: [] } });

  const envelope = await api.fetchNanobars();

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/admin/nanobar/api/nanobars");
  assert.equal(calls[0].options.method, "GET");
  assert.deepEqual(envelope, { status: "success", result: { data: [] } });
});

test("fetchNanobarBricks URL-encodes the nanobar id into the path", async () => {
  const calls = stubFetch({ status: "success", result: { data: [] } });

  await api.fetchNanobarBricks("nb/1 weird?id");

  assert.equal(calls[0].url, "/admin/nanobar/api/nanobars/nb%2F1%20weird%3Fid/bricks");
  assert.equal(calls[0].options.method, "GET");
});

test("fetchCoverageGaps hits the coverage-gaps endpoint for the given nanobar id", async () => {
  const calls = stubFetch({ status: "success", result: { data: [] } });

  await api.fetchCoverageGaps("nb-1");

  assert.equal(calls[0].url, "/admin/nanobar/api/nanobars/nb-1/coverage-gaps");
  assert.equal(calls[0].options.method, "GET");
});

test("updateNanobar PATCHes JSON to the nanobar's own endpoint with only the given fields", async () => {
  const calls = stubFetch({ status: "success", result: { data: {} } });

  await api.updateNanobar("nb-1", { label: "X", domain: "checkout" });

  assert.equal(calls[0].url, "/admin/nanobar/api/nanobars/nb-1");
  assert.equal(calls[0].options.method, "PATCH");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.equal(calls[0].options.body, JSON.stringify({ label: "X", domain: "checkout" }));
});

test("updateNanobar attaches the CSRF header when the cookie is set", async () => {
  document.cookie = "nanobar_csrftoken=tok123";
  const calls = stubFetch({ status: "success", result: { data: {} } });

  await api.updateNanobar("nb-1", { label: "X" });

  assert.equal(calls[0].options.headers["x-nanobar-csrf-token"], "tok123");
});

test("updateNanobar's body omits fields the caller didn't include (partial update)", async () => {
  const calls = stubFetch({ status: "success", result: { data: {} } });

  // Mirrors nanobar-ui.js's readEditFormFields() omitting criticality when its field is empty.
  await api.updateNanobar("nb-1", { label: "X" });

  const sentBody = JSON.parse(calls[0].options.body);
  assert.ok(!("criticality" in sentBody));
  assert.deepEqual(sentBody, { label: "X" });
});

test("every wrapper resolves to the parsed JSON envelope, even an error envelope", async () => {
  stubFetch({ status: "error", msg: "nanobar not found", result: null });

  const envelope = await api.fetchNanobarBricks("nb-missing");

  assert.deepEqual(envelope, { status: "error", msg: "nanobar not found", result: null });
});
