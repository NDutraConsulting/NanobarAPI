// post-api.js
// Thin fetch() wrapper around the public post-detail API.
// Resolves to the parsed JSON envelope: {status, msg, result}.
// No DOM access happens in this file.

/** GET /api/posts/{post_id} */
export async function fetchPost(postId) {
  const response = await fetch(`/api/posts/${encodeURIComponent(postId)}`, { method: "GET" });
  return response.json();
}
