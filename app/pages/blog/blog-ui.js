// blog-ui.js
// Pure DOM rendering/manipulation for the blog index page.
// No fetch() calls happen in this file.

const statusEl = document.getElementById("posts-status");
const listEl = document.getElementById("posts-list");
const emptyEl = document.getElementById("posts-empty");
const itemTemplate = document.getElementById("post-item-template");

export function showLoading() {
  statusEl.hidden = false;
  statusEl.className = "posts-status";
  statusEl.textContent = "Loading…";
  listEl.hidden = true;
  emptyEl.hidden = true;
}

export function showError(message) {
  statusEl.hidden = false;
  statusEl.className = "posts-status posts-status-error";
  statusEl.textContent = message || "Could not load posts.";
  listEl.hidden = true;
  emptyEl.hidden = true;
}

export function renderPosts(posts) {
  statusEl.hidden = true;

  if (!posts || posts.length === 0) {
    listEl.hidden = true;
    emptyEl.hidden = false;
    return;
  }

  emptyEl.hidden = true;
  listEl.hidden = false;
  listEl.textContent = "";

  for (const post of posts) {
    const fragment = itemTemplate.content.cloneNode(true);
    const link = fragment.querySelector(".post-link");
    link.href = `/posts/${encodeURIComponent(post.id)}`;
    fragment.querySelector(".post-title").textContent = post.title;
    fragment.querySelector(".post-date").textContent = post.published_at
      ? new Date(post.published_at).toLocaleDateString()
      : "";
    listEl.appendChild(fragment);
  }
}
