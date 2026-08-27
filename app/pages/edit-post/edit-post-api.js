// edit-post-api.js
// Thin fetch() wrappers around the /admin/app/api/posts/{post_id} contract.
// No DOM access happens in this file.

import { csrfHeader } from "../shared/csrf.js";

/** GET /admin/app/api/posts/{post_id} */
export function fetchPost(postId) {
  return fetch(`/admin/app/api/posts/${encodeURIComponent(postId)}`, { method: "GET" }).then((r) => r.json());
}

/**
 * POST /admin/app/api/posts/{post_id} {title, body}
 * @param {string} postId
 * @param {{title: string, body: string}} fields
 */
export function updatePost(postId, fields) {
  return fetch(`/admin/app/api/posts/${encodeURIComponent(postId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeader() },
    body: JSON.stringify(fields),
  }).then((r) => r.json());
}
