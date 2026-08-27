// edit-post-ui.js
// Pure DOM rendering/manipulation for the post-edit page.
// No fetch() calls happen in this file.

const pageStatusEl = document.getElementById("page-status");
const formEl = document.getElementById("edit-post-form");
const titleEl = document.getElementById("edit-post-title");
const bodyEl = document.getElementById("edit-post-body");
const saveBtnEl = document.getElementById("edit-post-save-btn");
const errorEl = document.getElementById("edit-post-error");

export const elements = {
  form: formEl,
};

export function showLoading() {
  pageStatusEl.hidden = false;
  pageStatusEl.textContent = "Loading…";
  formEl.hidden = true;
}

export function showLoadError(message) {
  pageStatusEl.hidden = false;
  pageStatusEl.textContent = message || "Could not load this post.";
  formEl.hidden = true;
}

export function renderPost(post) {
  pageStatusEl.hidden = true;
  formEl.hidden = false;
  titleEl.value = post.title;
  bodyEl.value = post.body;
}

export function readFields() {
  return { title: titleEl.value, body: bodyEl.value };
}

export function showSaveError(message) {
  errorEl.textContent = message;
  errorEl.hidden = false;
}

export function clearSaveError() {
  errorEl.hidden = true;
}

export function setFormBusy(isBusy) {
  saveBtnEl.disabled = isBusy;
  saveBtnEl.textContent = isBusy ? "Saving…" : "Save";
}
