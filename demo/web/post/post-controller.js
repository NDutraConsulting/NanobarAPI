// post-controller.js
// Orchestrates the post-detail page: parses the post id out of the URL, fetches it, and hands
// results to post-ui.js. This is the only file with top-level "run on page load" logic.

import * as api from "./post-api.js";
import * as ui from "./post-ui.js";

function getPostIdFromPath() {
  const segments = window.location.pathname.split("/").filter(Boolean);
  return segments[segments.length - 1];
}

async function init() {
  const postId = getPostIdFromPath();
  if (!postId) {
    ui.showError("No post specified.");
    return;
  }

  ui.showLoading();
  try {
    const envelope = await api.fetchPost(postId);
    if (envelope.status !== "success") {
      ui.showError(envelope.msg || `Post ${postId} not found.`);
      return;
    }
    ui.renderPost(envelope.result.data);
  } catch (err) {
    ui.showError("Could not reach the server. Please check your connection and try again.");
  }
}

init();
