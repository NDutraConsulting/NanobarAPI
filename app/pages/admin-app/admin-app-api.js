// admin-app-api.js
// Thin fetch() wrappers around the /admin/app/* API contract.
// Every function resolves to the parsed JSON envelope: {status, msg, result}.
// No DOM access happens in this file.

import { csrfHeader } from "../shared/csrf.js";

/** GET /admin/app/api/posts */
export function fetchPosts() {
  return fetch("/admin/app/api/posts", { method: "GET" }).then((r) => r.json());
}

/** GET /admin/app/api/notifications */
export function fetchNotifications() {
  return fetch("/admin/app/api/notifications", { method: "GET" }).then((r) => r.json());
}

/**
 * POST /admin/app/api/posts {title, body, scheduled_at?}
 * @param {{title: string, body: string, scheduled_at?: string}} fields
 */
export function createPost(fields) {
  return fetch("/admin/app/api/posts", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeader() },
    body: JSON.stringify(fields),
  }).then((r) => r.json());
}

/** POST /admin/app/api/notifications/{id}/read */
export function markNotificationRead(notificationId) {
  return fetch(`/admin/app/api/notifications/${encodeURIComponent(notificationId)}/read`, {
    method: "POST",
    headers: { ...csrfHeader() },
  }).then((r) => r.json());
}
