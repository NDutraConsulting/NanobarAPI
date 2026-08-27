// nanobar-api.js
// Thin fetch() wrappers around this page's API calls -- the nanobar itself, its bound bricks,
// coverage gaps, and (merged in from the now-retired brick.html bundle) one brick's own
// detail/review-status/scenario/tags, plus the Run tab's replay endpoint.
// Every function resolves to the parsed JSON envelope: {status, msg, result}.
// No DOM access happens in this file.

import { csrfHeader } from "../shared/csrf.js";

const BASE_URL = "/admin/nanobar/api";
const JSON_HEADERS = { "Content-Type": "application/json" };

async function request(url, options) {
  const response = await fetch(url, options);
  return response.json();
}

/** GET /admin/nanobar/api/nanobars/{nanobar_id} */
export function fetchNanobar(nanobarId) {
  return request(`${BASE_URL}/nanobars/${encodeURIComponent(nanobarId)}`, { method: "GET" });
}

/** GET /admin/nanobar/api/nanobars/{nanobar_id}/bricks — bricks bound to this nanobar, with
 * review status. */
export function fetchNanobarBricks(nanobarId) {
  return request(`${BASE_URL}/nanobars/${encodeURIComponent(nanobarId)}/bricks`, {
    method: "GET",
  });
}

/** GET /admin/nanobar/api/nanobars/{nanobar_id}/coverage-gaps — required scenario types with no
 * bound brick. */
export function fetchCoverageGaps(nanobarId) {
  return request(`${BASE_URL}/nanobars/${encodeURIComponent(nanobarId)}/coverage-gaps`, {
    method: "GET",
  });
}

/**
 * PATCH /admin/nanobar/api/nanobars/{nanobar_id} — partial update of the human-navigation fields
 * (label, scenario_description, component_source_description, domain, criticality). Any
 * field omitted from `fields` keeps its current stored value.
 * @param {string} nanobarId
 * @param {{label?: string, scenario_description?: string, component_source_description?: string, domain?: string, criticality?: number}} fields
 */
export function updateNanobar(nanobarId, fields) {
  return request(`${BASE_URL}/nanobars/${encodeURIComponent(nanobarId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...csrfHeader() },
    body: JSON.stringify(fields),
  });
}

/** GET /admin/nanobar/api/bricks/{brick_id} */
export function fetchBrick(brickId) {
  return request(`${BASE_URL}/bricks/${encodeURIComponent(brickId)}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
}

/**
 * POST /admin/nanobar/api/bricks/{brick_id}/review-status {status}
 * @param {string} brickId
 * @param {string} status one of "new" | "reviewed" | "flagged" | "promoted"
 */
export function setReviewStatus(brickId, status) {
  return request(`${BASE_URL}/bricks/${encodeURIComponent(brickId)}/review-status`, {
    method: "POST",
    headers: { ...JSON_HEADERS, Accept: "application/json", ...csrfHeader() },
    body: JSON.stringify({ status }),
  });
}

/**
 * PATCH /admin/nanobar/api/bricks/{brick_id}/scenario — partial update of the human-curated
 * scenario label/description. A field omitted from `fields` keeps its current stored value.
 * @param {string} brickId
 * @param {{regression_scenario_label?: string, description?: string}} fields
 */
export function setBrickScenario(brickId, fields) {
  return request(`${BASE_URL}/bricks/${encodeURIComponent(brickId)}/scenario`, {
    method: "PATCH",
    headers: { ...JSON_HEADERS, Accept: "application/json", ...csrfHeader() },
    body: JSON.stringify(fields),
  });
}

/** POST /admin/nanobar/api/bricks/{brick_id}/tags {tag} -> the brick's updated tag list. */
export function addBrickTag(brickId, tag) {
  return request(`${BASE_URL}/bricks/${encodeURIComponent(brickId)}/tags`, {
    method: "POST",
    headers: { ...JSON_HEADERS, Accept: "application/json", ...csrfHeader() },
    body: JSON.stringify({ tag }),
  });
}

/** DELETE /admin/nanobar/api/bricks/{brick_id}/tags/{tag} -> the brick's updated tag list. */
export function removeBrickTag(brickId, tag) {
  return request(`${BASE_URL}/bricks/${encodeURIComponent(brickId)}/tags/${encodeURIComponent(tag)}`, {
    method: "DELETE",
    headers: { Accept: "application/json", ...csrfHeader() },
  });
}

/** POST /admin/nanobar/api/bricks/{brick_id}/replay -> {trace_id, replayed_response, verdict} */
export function replayBrick(brickId) {
  return request(`${BASE_URL}/bricks/${encodeURIComponent(brickId)}/replay`, {
    method: "POST",
    headers: { Accept: "application/json", ...csrfHeader() },
  });
}

/** GET /admin/nanobar/api/traces/{trace_id}/spans — reused by the Run tab's Refresh button. */
export function fetchTraceSpans(traceId) {
  return request(`${BASE_URL}/traces/${encodeURIComponent(traceId)}/spans`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
}
