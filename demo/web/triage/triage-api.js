// triage-api.js
// Thin fetch() wrappers around the triage board's API calls.
// Every function resolves to the parsed JSON envelope: {status, msg, result}.
// No DOM access happens in this file.

const JSON_HEADERS = { "Content-Type": "application/json" };

/**
 * Shared request helper. Resolves to the parsed envelope for any response
 * the server actually sent back (success, error, or timeout are all valid
 * JSON envelopes). Only throws when the network request itself fails
 * (offline, DNS, aborted, non-JSON body, etc).
 */
async function request(url, options) {
  const response = await fetch(url, options);
  return response.json();
}

/** GET /api/nanobars */
export function fetchNanobars() {
  return request("/api/nanobars", { method: "GET" });
}

/** GET /api/nanobars/{nanobar_id}/bricks */
export function fetchNanobarBricks(nanobarId) {
  return request(`/api/nanobars/${encodeURIComponent(nanobarId)}/bricks`, {
    method: "GET",
  });
}

/** POST /api/bricks/{brick_id}/review-status {status} */
export function setBrickReviewStatus(brickId, status) {
  return request(`/api/bricks/${encodeURIComponent(brickId)}/review-status`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ status }),
  });
}
