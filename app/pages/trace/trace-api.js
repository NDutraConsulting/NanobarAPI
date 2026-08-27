// trace-api.js
// Thin fetch() wrapper around the trace-detail API contract.
// Returns the parsed JSON envelope ({status, msg, result}) or throws if the
// network request itself fails (no response at all).
// No DOM access happens in this file.

/**
 * GET /admin/nanobar/api/traces/{trace_id}/spans
 * @param {string} traceId
 * @returns {Promise<{status: string, msg: string, result: {type: string, data: any}}>}
 */
export async function fetchTraceSpans(traceId) {
  const response = await fetch(`/admin/nanobar/api/traces/${encodeURIComponent(traceId)}/spans`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return response.json();
}
