// blog-controller.js
// Orchestrates the blog index page: fetches published posts and hands them to blog-ui.js.
// This is the only file with top-level "run on page load" logic.

import * as api from "./blog-api.js";
import * as ui from "./blog-ui.js";

async function init() {
  ui.showLoading();
  try {
    const envelope = await api.fetchPublishedPosts();
    if (envelope.status !== "success") {
      ui.showError(envelope.msg);
      return;
    }
    ui.renderPosts(envelope.result.data);
  } catch (err) {
    ui.showError("Could not reach the server. Please refresh the page and try again.");
  }
}

init();
