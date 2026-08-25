// nanobar-api.js
// Thin fetch() wrappers around this page's API calls.
// Every function resolves to the parsed JSON envelope: {status, msg, result}.
// No DOM access happens in this file.

async function request(url, options) {
  const response = await fetch(url, options);
  return response.json();
}

/** GET /api/nanobars — the full nanobar list (used to find this page's own summary fields,
 * since there is no GET /api/nanobars/{id} endpoint). */
export function fetchNanobars() {
  return request("/api/nanobars", { method: "GET" });
}

/** GET /api/nanobars/{nanobar_id}/bricks — bricks bound to this nanobar, with review status. */
export function fetchNanobarBricks(nanobarId) {
  return request(`/api/nanobars/${encodeURIComponent(nanobarId)}/bricks`, {
    method: "GET",
  });
}

/**
 * PATCH /api/nanobars/{nanobar_id} — partial update of the human-navigation fields
 * (label, scenario_description, component_source_description). Any field omitted from
 * `fields` keeps its current stored value.
 * @param {string} nanobarId
 * @param {{label?: string, scenario_description?: string, component_source_description?: string}} fields
 */
export function updateNanobar(nanobarId, fields) {
  return request(`/api/nanobars/${encodeURIComponent(nanobarId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
}
