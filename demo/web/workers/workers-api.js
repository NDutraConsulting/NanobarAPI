// workers-api.js
// Thin fetch() wrappers around the workers API contract.
// Each function resolves to the parsed JSON envelope ({status, msg, result}) or throws only
// when the network request itself fails. No DOM access happens in this file.

const BASE_URL = "/admin/nanobar/api/workers";

/** GET /admin/nanobar/api/workers?stale_seconds= */
export async function fetchWorkers({ staleSeconds } = {}) {
  const params = new URLSearchParams();
  if (staleSeconds) params.set("stale_seconds", String(staleSeconds));
  const response = await fetch(`${BASE_URL}?${params}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return response.json();
}

/** GET /admin/nanobar/api/workers/{worker_id}/log?limit= */
export async function fetchWorkerLog(workerId, { limit } = {}) {
  const params = new URLSearchParams();
  if (limit) params.set("limit", String(limit));
  const response = await fetch(`${BASE_URL}/${encodeURIComponent(workerId)}/log?${params}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return response.json();
}
