// traces-api.js
// Thin fetch() wrappers around the traces API contract.
// Each function returns the parsed JSON envelope ({status, msg, result}) or
// throws if the network request itself fails (no response at all).
// No DOM access happens in this file.

const BASE_URL = "/admin/nanobar/api/traces";

/**
 * GET /admin/nanobar/api/traces?channel=trace&page=&created_after=&created_before=&
 * since_hours=&show_all=1&nanobar_types=&components=
 * @param {{
 *   page?: number,
 *   createdAfter?: string,
 *   createdBefore?: string,
 *   sinceHours?: string,
 *   showAll?: boolean,
 *   nanobarTypes?: string[],
 *   components?: string[],
 * }} [options]
 * @returns {Promise<{status: string, msg: string, result: {type: string, data: any}}>}
 */
export async function fetchTraces({
  page = 1,
  createdAfter = "",
  createdBefore = "",
  sinceHours = "",
  showAll = false,
  nanobarTypes = [],
  components = [],
} = {}) {
  const params = new URLSearchParams({ channel: "trace", page: String(page) });
  if (showAll) {
    params.set("show_all", "1");
  } else {
    if (createdAfter) params.set("created_after", createdAfter);
    if (createdBefore) params.set("created_before", createdBefore);
    if (sinceHours) params.set("since_hours", sinceHours);
  }
  for (const value of nanobarTypes) params.append("nanobar_types", value);
  for (const value of components) params.append("components", value);

  const response = await fetch(`${BASE_URL}?${params}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return response.json();
}

/**
 * GET /admin/nanobar/api/traces/facets?channel=trace&... (same date-window params as fetchTraces)
 * @param {{createdAfter?: string, createdBefore?: string, sinceHours?: string, showAll?: boolean}} [options]
 */
export async function fetchTraceFacets({ createdAfter = "", createdBefore = "", sinceHours = "", showAll = false } = {}) {
  const params = new URLSearchParams({ channel: "trace" });
  if (showAll) {
    params.set("show_all", "1");
  } else {
    if (createdAfter) params.set("created_after", createdAfter);
    if (createdBefore) params.set("created_before", createdBefore);
    if (sinceHours) params.set("since_hours", sinceHours);
  }

  const response = await fetch(`/admin/nanobar/api/traces/facets?${params}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return response.json();
}
