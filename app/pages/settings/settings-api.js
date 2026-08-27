// settings-api.js
// Thin fetch() wrappers around the settings API contract.
// Each function returns the parsed JSON envelope ({status, msg, result}) or
// throws if the network request itself fails (no response at all).
// No DOM access happens in this file.

import { csrfHeader } from "../shared/csrf.js";

const BASE_URL = "/admin/nanobar/api/settings";

/** GET /admin/nanobar/api/settings -> {status, msg, result: {type, data: {tracing_enabled}}} */
export async function fetchSettings() {
  const response = await fetch(BASE_URL, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return response.json();
}

/**
 * POST /admin/nanobar/api/settings with {"tracing_enabled": bool}.
 * @param {{tracingEnabled: boolean}} options
 */
export async function updateSettings({ tracingEnabled }) {
  const response = await fetch(BASE_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeader() },
    body: JSON.stringify({ tracing_enabled: tracingEnabled }),
  });
  return response.json();
}

const REFRESH_BASE_URL = "/admin/nanobar/api/refresh";

/** GET /admin/nanobar/api/refresh/status -> {status, msg, result: {type, data: {api, nanobars, bricks}}} */
export async function fetchRefreshStatus() {
  const response = await fetch(`${REFRESH_BASE_URL}/status`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return response.json();
}

/** POST /admin/nanobar/api/refresh/api-routes */
export async function refreshApiRoutes() {
  const response = await fetch(`${REFRESH_BASE_URL}/api-routes`, { method: "POST", headers: { ...csrfHeader() } });
  return response.json();
}

/** POST /admin/nanobar/api/refresh/nanobars */
export async function refreshNanobars() {
  const response = await fetch(`${REFRESH_BASE_URL}/nanobars`, { method: "POST", headers: { ...csrfHeader() } });
  return response.json();
}

/** POST /admin/nanobar/api/generate-bricks -- the "Regression bricks" refresh row calls the
 * same existing endpoint the Nanobars page's "Generate bricks" button already does. */
export async function refreshBricks() {
  const response = await fetch("/admin/nanobar/api/generate-bricks", { method: "POST", headers: { ...csrfHeader() } });
  return response.json();
}
