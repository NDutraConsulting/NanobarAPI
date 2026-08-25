// brick-api.js
// Thin fetch() wrappers around the brick-detail API contract.
// Each function resolves to the parsed JSON envelope ({status, msg, result})
// or throws only when the network request itself fails (no response at
// all — offline, DNS, aborted, non-JSON body, etc). No DOM access happens
// in this file.

const JSON_HEADERS = { "Content-Type": "application/json" };

/**
 * GET /api/bricks/{brick_id}
 * @param {string} brickId
 * @returns {Promise<{status: string, msg: string, result: {type: string, data: any}}>}
 */
export async function fetchBrick(brickId) {
  const response = await fetch(`/api/bricks/${encodeURIComponent(brickId)}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return response.json();
}

/**
 * POST /api/bricks/{brick_id}/review-status {status}
 * @param {string} brickId
 * @param {string} status one of "new" | "reviewed" | "flagged" | "promoted"
 * @returns {Promise<{status: string, msg: string, result: {type: string, data: any}}>}
 */
export async function setReviewStatus(brickId, status) {
  const response = await fetch(`/api/bricks/${encodeURIComponent(brickId)}/review-status`, {
    method: "POST",
    headers: { ...JSON_HEADERS, Accept: "application/json" },
    body: JSON.stringify({ status }),
  });
  return response.json();
}
