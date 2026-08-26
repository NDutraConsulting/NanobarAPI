// blog-api.js
// Thin fetch() wrapper around the public blog API.
// Resolves to the parsed JSON envelope: {status, msg, result}.
// No DOM access happens in this file.

/** GET /api/posts -- published posts only, most-recent first. */
export async function fetchPublishedPosts() {
  const response = await fetch("/api/posts", { method: "GET" });
  return response.json();
}
