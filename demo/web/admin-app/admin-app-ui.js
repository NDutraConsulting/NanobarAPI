// admin-app-ui.js
// Pure DOM rendering/manipulation for the app-admin page.
// No fetch() calls happen in this file.

const notificationsStatusEl = document.getElementById("notifications-status");
const notificationsListEl = document.getElementById("notifications-list");
const notificationsEmptyEl = document.getElementById("notifications-empty");
const notificationItemTemplate = document.getElementById("notification-item-template");

const postsStatusEl = document.getElementById("posts-status");
const postsListEl = document.getElementById("posts-list");
const postsEmptyEl = document.getElementById("posts-empty");
const postItemTemplate = document.getElementById("post-item-template");

const newPostFormEl = document.getElementById("new-post-form");
const newPostTitleEl = document.getElementById("new-post-title");
const newPostBodyEl = document.getElementById("new-post-body");
const newPostScheduledAtEl = document.getElementById("new-post-scheduled-at");
const newPostSubmitBtnEl = document.getElementById("new-post-submit-btn");
const newPostErrorEl = document.getElementById("new-post-error");
const newPostSuccessEl = document.getElementById("new-post-success");

export const elements = {
  newPostForm: newPostFormEl,
};

/* -------------------------------------------------------------- notifications */

export function showNotificationsLoading() {
  notificationsStatusEl.hidden = false;
  notificationsStatusEl.textContent = "Loading…";
  notificationsListEl.hidden = true;
  notificationsEmptyEl.hidden = true;
}

export function showNotificationsError(message) {
  notificationsStatusEl.hidden = false;
  notificationsStatusEl.textContent = message || "Could not load notifications.";
  notificationsListEl.hidden = true;
  notificationsEmptyEl.hidden = true;
}

export function renderNotifications(notifications, onMarkRead) {
  notificationsStatusEl.hidden = true;

  if (!notifications || notifications.length === 0) {
    notificationsListEl.hidden = true;
    notificationsEmptyEl.hidden = false;
    return;
  }

  notificationsEmptyEl.hidden = true;
  notificationsListEl.hidden = false;
  notificationsListEl.textContent = "";

  for (const notification of notifications) {
    const fragment = notificationItemTemplate.content.cloneNode(true);
    fragment.querySelector(".notification-message").textContent = notification.message;
    fragment.querySelector(".notification-date").textContent = new Date(notification.created_at).toLocaleString();
    const readBtn = fragment.querySelector(".notification-read-btn");
    if (notification.read) {
      readBtn.disabled = true;
      readBtn.textContent = "Read";
    } else {
      readBtn.addEventListener("click", () => onMarkRead(notification.id));
    }
    notificationsListEl.appendChild(fragment);
  }
}

/* --------------------------------------------------------------------- posts */

export function showPostsLoading() {
  postsStatusEl.hidden = false;
  postsStatusEl.textContent = "Loading…";
  postsListEl.hidden = true;
  postsEmptyEl.hidden = true;
}

export function showPostsError(message) {
  postsStatusEl.hidden = false;
  postsStatusEl.textContent = message || "Could not load posts.";
  postsListEl.hidden = true;
  postsEmptyEl.hidden = true;
}

/**
 * @param {Array<{id: string, title: string, status: string}>} posts
 */
export function renderPosts(posts) {
  postsStatusEl.hidden = true;

  if (!posts || posts.length === 0) {
    postsListEl.hidden = true;
    postsEmptyEl.hidden = false;
    return;
  }

  postsEmptyEl.hidden = true;
  postsListEl.hidden = false;
  postsListEl.textContent = "";

  for (const post of posts) {
    const fragment = postItemTemplate.content.cloneNode(true);
    fragment.querySelector(".post-title").textContent = post.title;
    fragment.querySelector(".post-status").textContent = post.status;
    fragment.querySelector(".post-item-link").href = `/admin/app/posts/${encodeURIComponent(post.id)}/edit`;
    postsListEl.appendChild(fragment);
  }
}

/* ---------------------------------------------------------------- new-post form */

export function readNewPostFields() {
  const fields = {
    title: newPostTitleEl.value,
    body: newPostBodyEl.value,
  };
  if (newPostScheduledAtEl.value !== "") {
    // datetime-local has no timezone; interpreted as local time by `new Date(...)`, then
    // serialized to a real ISO 8601 (UTC-offset-bearing) string the backend can parse.
    fields.scheduled_at = new Date(newPostScheduledAtEl.value).toISOString();
  }
  return fields;
}

export function resetNewPostForm() {
  newPostFormEl.reset();
}

export function showNewPostError(message) {
  newPostSuccessEl.hidden = true;
  newPostErrorEl.textContent = message;
  newPostErrorEl.hidden = false;
}

export function showNewPostSuccess(message) {
  newPostErrorEl.hidden = true;
  newPostSuccessEl.textContent = message;
  newPostSuccessEl.hidden = false;
}

export function clearNewPostMessages() {
  newPostErrorEl.hidden = true;
  newPostSuccessEl.hidden = true;
}

export function setNewPostFormBusy(isBusy) {
  newPostSubmitBtnEl.disabled = isBusy;
  newPostSubmitBtnEl.textContent = isBusy ? "Saving…" : "Save post";
}
