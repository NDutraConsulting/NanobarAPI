// post-ui.js
// Pure DOM rendering/manipulation for the post-detail page.
// No fetch() calls happen in this file.

const pageStatusEl = document.getElementById("page-status");
const articleEl = document.getElementById("post-article");
const titleEl = document.getElementById("post-title");
const dateEl = document.getElementById("post-date");
const bodyEl = document.getElementById("post-body");

export function showLoading() {
  pageStatusEl.hidden = false;
  pageStatusEl.className = "page-status";
  pageStatusEl.textContent = "Loading…";
  articleEl.hidden = true;
}

export function showError(message) {
  pageStatusEl.hidden = false;
  pageStatusEl.className = "page-status page-status-error";
  pageStatusEl.textContent = message || "This post could not be found.";
  articleEl.hidden = true;
}

export function renderPost(post) {
  pageStatusEl.hidden = true;
  articleEl.hidden = false;

  document.title = `${post.title} · NanobarAPI Demo`;
  titleEl.textContent = post.title;
  dateEl.textContent = post.published_at ? new Date(post.published_at).toLocaleDateString() : "";
  bodyEl.textContent = post.body;
}
