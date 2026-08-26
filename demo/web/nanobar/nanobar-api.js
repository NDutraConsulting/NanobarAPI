// nanobar-api.js
// Thin fetch() wrappers around this page's API calls.
// Every function resolves to the parsed JSON envelope: {status, msg, result}.
// No DOM access happens in this file.

import { csrfHeader } from "../shared/csrf.js";

const BASE_URL = "/admin/nanobar/api";

async function request(url, options) {
  const response = await fetch(url, options);
  return response.json();
}

/** GET /admin/nanobar/api/nanobars — the full nanobar list (used to find this page's own summary
 * fields, since there is no GET .../nanobars/{id} endpoint). */
export function fetchNanobars() {
  return request(`${BASE_URL}/nanobars`, { method: "GET" });
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
