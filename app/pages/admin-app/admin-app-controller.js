// admin-app-controller.js
// Orchestrates the app-admin page: loads notifications/posts, wires the new-post form and
// mark-read buttons. This is the only file with top-level "run on page load" logic.

import * as api from "./admin-app-api.js";
import * as ui from "./admin-app-ui.js";

async function loadNotifications() {
  ui.showNotificationsLoading();
  try {
    const envelope = await api.fetchNotifications();
    if (envelope.status !== "success") {
      ui.showNotificationsError(envelope.msg);
      return;
    }
    ui.renderNotifications(envelope.result.data, handleMarkRead);
  } catch (err) {
    ui.showNotificationsError("Could not reach the server. Please refresh the page and try again.");
  }
}

async function loadPosts() {
  ui.showPostsLoading();
  try {
    const envelope = await api.fetchPosts();
    if (envelope.status !== "success") {
      ui.showPostsError(envelope.msg);
      return;
    }
    ui.renderPosts(envelope.result.data);
  } catch (err) {
    ui.showPostsError("Could not reach the server. Please refresh the page and try again.");
  }
}

async function handleMarkRead(notificationId) {
  try {
    await api.markNotificationRead(notificationId);
  } finally {
    loadNotifications();
  }
}

async function handleNewPostSubmit(event) {
  event.preventDefault();
  ui.clearNewPostMessages();
  ui.setNewPostFormBusy(true);
  try {
    const envelope = await api.createPost(ui.readNewPostFields());
    if (envelope.status !== "success") {
      ui.showNewPostError(envelope.msg || "Could not save the post.");
      return;
    }
    ui.resetNewPostForm();
    ui.showNewPostSuccess("Saved.");
    loadPosts();
  } catch (err) {
    ui.showNewPostError("Network error while saving. Please try again.");
  } finally {
    ui.setNewPostFormBusy(false);
  }
}

function init() {
  ui.elements.newPostForm.addEventListener("submit", handleNewPostSubmit);
  loadNotifications();
  loadPosts();
}

init();
