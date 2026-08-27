// edit-post-controller.js
// Orchestrates the post-edit page: parses the post id out of the URL, loads it, and wires the
// save form. This is the only file with top-level "run on page load" logic.

import * as api from "./edit-post-api.js";
import * as ui from "./edit-post-ui.js";

function getPostIdFromPath() {
  // .../admin/app/posts/{post_id}/edit -- the id is the segment right before the trailing "edit".
  const segments = window.location.pathname.split("/").filter(Boolean);
  const editIndex = segments.lastIndexOf("edit");
  return editIndex > 0 ? segments[editIndex - 1] : undefined;
}

const postId = getPostIdFromPath();

async function loadPost() {
  ui.showLoading();
  try {
    const envelope = await api.fetchPost(postId);
    if (envelope.status !== "success") {
      ui.showLoadError(envelope.msg || "Post not found.");
      return;
    }
    ui.renderPost(envelope.result.data);
  } catch (err) {
    ui.showLoadError("Could not reach the server. Please refresh the page and try again.");
  }
}

async function handleSubmit(event) {
  event.preventDefault();
  ui.clearSaveError();
  ui.setFormBusy(true);
  try {
    const envelope = await api.updatePost(postId, ui.readFields());
    if (envelope.status !== "success") {
      ui.showSaveError(envelope.msg || "Could not save changes.");
      return;
    }
    window.location.href = "/admin/app/dashboard";
  } catch (err) {
    ui.showSaveError("Network error while saving. Please try again.");
  } finally {
    ui.setFormBusy(false);
  }
}

function init() {
  if (!postId) {
    ui.showLoadError("No post specified.");
    return;
  }
  ui.elements.form.addEventListener("submit", handleSubmit);
  loadPost();
}

init();
