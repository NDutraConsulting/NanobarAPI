// nanobars-api.js
// Thin fetch() wrappers around the nanobars API contract.
// Each function returns the parsed JSON envelope ({status, msg, result}) or
// throws if the network request itself fails (no response at all).
// No DOM access happens in this file.

import { csrfHeader } from "../shared/csrf.js";

const BASE_URL = "/admin/nanobar/api/nanobars";

/**
 * GET /admin/nanobar/api/nanobars?nanobar_type=&domain=&app_box=&q=&page=&page_size=
 * @param {{nanobarType?: string, domain?: string, appBox?: string, q?: string, page?: number, pageSize?: number}} [options]
 * @returns {Promise<{status: string, msg: string, result: {type: string, data: any}}>}
 */
export async function fetchNanobars({ nanobarType = "", domain, appBox, q = "", page = 1, pageSize } = {}) {
  const params = new URLSearchParams();
  if (nanobarType) params.set("nanobar_type", nanobarType);
  // domain/appBox's own "All ..." value is "" (no filter) same as the others, but a real,
  // meaningful value can *also* be "" (domain: a root-level, un-Mounted route) -- so this can't
  // use nanobarType's truthiness check, only "was a value passed in at all."
  if (domain !== undefined) params.set("domain", domain);
  if (appBox !== undefined) params.set("app_box", appBox);
  if (q) params.set("q", q);
  params.set("page", String(page));
  if (pageSize) params.set("page_size", String(pageSize));

  const response = await fetch(`${BASE_URL}?${params}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return response.json();
}

/** POST /admin/nanobar/api/generate-bricks */
export async function generateBricks() {
  const response = await fetch("/admin/nanobar/api/generate-bricks", {
    method: "POST",
    headers: { ...csrfHeader() },
  });
  return response.json();
}
