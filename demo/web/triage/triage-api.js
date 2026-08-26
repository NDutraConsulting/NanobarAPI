// triage-api.js
// Thin fetch() wrappers around the triage board's API calls.
// Every function resolves to the parsed JSON envelope: {status, msg, result}.
// No DOM access happens in this file.

import { csrfHeader } from "../shared/csrf.js";

const BASE_URL = "/admin/nanobar/api";

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

/** GET /admin/nanobar/api/nanobars */
export function fetchNanobars() {
  return request(`${BASE_URL}/nanobars`, { method: "GET" });
}

/** GET /admin/nanobar/api/nanobars/{nanobar_id}/bricks */
export function fetchNanobarBricks(nanobarId) {
  return request(`${BASE_URL}/nanobars/${encodeURIComponent(nanobarId)}/bricks`, {
    method: "GET",
  });
}

/** POST /admin/nanobar/api/bricks/{brick_id}/review-status {status} */
export function setBrickReviewStatus(brickId, status) {
  return request(`${BASE_URL}/bricks/${encodeURIComponent(brickId)}/review-status`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeader() },
    body: JSON.stringify({ status }),
  });
}
