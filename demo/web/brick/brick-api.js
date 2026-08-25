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

/**
 * PATCH /api/bricks/{brick_id}/scenario — partial update of the human-curated scenario
 * label/description. A field omitted from `fields` keeps its current stored value.
 * @param {string} brickId
 * @param {{regression_scenario_label?: string, description?: string}} fields
 * @returns {Promise<{status: string, msg: string, result: {type: string, data: any}}>}
 */
export async function setBrickScenario(brickId, fields) {
  const response = await fetch(`/api/bricks/${encodeURIComponent(brickId)}/scenario`, {
    method: "PATCH",
    headers: { ...JSON_HEADERS, Accept: "application/json" },
    body: JSON.stringify(fields),
  });
  return response.json();
}

/**
 * POST /api/bricks/{brick_id}/tags {tag} -> the brick's updated tag list.
 * @param {string} brickId
 * @param {string} tag
 * @returns {Promise<{status: string, msg: string, result: {type: string, data: any}}>}
 */
export async function addBrickTag(brickId, tag) {
  const response = await fetch(`/api/bricks/${encodeURIComponent(brickId)}/tags`, {
    method: "POST",
    headers: { ...JSON_HEADERS, Accept: "application/json" },
    body: JSON.stringify({ tag }),
  });
  return response.json();
}

/**
 * DELETE /api/bricks/{brick_id}/tags/{tag} -> the brick's updated tag list.
 * @param {string} brickId
 * @param {string} tag
 * @returns {Promise<{status: string, msg: string, result: {type: string, data: any}}>}
 */
export async function removeBrickTag(brickId, tag) {
  const response = await fetch(
    `/api/bricks/${encodeURIComponent(brickId)}/tags/${encodeURIComponent(tag)}`,
    { method: "DELETE", headers: { Accept: "application/json" } }
  );
  return response.json();
}
