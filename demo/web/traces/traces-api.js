// traces-api.js
// Thin fetch() wrappers around the traces API contract.
// Each function returns the parsed JSON envelope ({status, msg, result}) or
// throws if the network request itself fails (no response at all).
// No DOM access happens in this file.

const BASE_URL = "/api/traces";

/**
 * GET /api/traces?channel=trace&limit=100
 * @returns {Promise<{status: string, msg: string, result: {type: string, data: any}}>}
 */
export async function fetchTraces() {
  const response = await fetch(`${BASE_URL}?channel=trace&limit=100`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return response.json();
}
