// nanobars-api.js
// Thin fetch() wrappers around the nanobars API contract.
// Each function returns the parsed JSON envelope ({status, msg, result}) or
// throws if the network request itself fails (no response at all).
// No DOM access happens in this file.

const BASE_URL = "/api/nanobars";

/**
 * GET /api/nanobars
 * Fetches every nanobar (no target_type filter) so the page can group them
 * client-side.
 * @returns {Promise<{status: string, msg: string, result: {type: string, data: any}}>}
 */
export async function fetchNanobars() {
  const response = await fetch(BASE_URL, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return response.json();
}
