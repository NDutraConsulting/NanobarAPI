// admin-login-controller.js
// Orchestrates the admin login page: reads the submitted credential, calls
// admin-login-api.js, hands results to admin-login-ui.js, and redirects on success.
// This is the only file with top-level "run on page load" logic.

import * as api from "./admin-login-api.js";
import * as ui from "./admin-login-ui.js";

async function handleSubmit(event) {
  event.preventDefault();
  ui.clearError();
  ui.setFormBusy(true);
  try {
    const { username, password } = ui.readCredentials();
    const envelope = await api.submitLogin(username, password);
    if (envelope.status !== "success") {
      ui.showError(envelope.msg || "Invalid credential.");
      return;
    }
    window.location.href = (envelope.result && envelope.result.data && envelope.result.data.redirect) || "/";
  } catch (err) {
    ui.showError("Network error while signing in. Please try again.");
  } finally {
    ui.setFormBusy(false);
  }
}

function init() {
  ui.elements.form.addEventListener("submit", handleSubmit);
}

init();
