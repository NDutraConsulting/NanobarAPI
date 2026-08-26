// book-appointment-controller.js
// Orchestrates the appointment-booking page: reads the submitted form, calls
// book-appointment-api.js, hands results to book-appointment-ui.js.
// This is the only file with top-level "run on page load" logic.

import * as api from "./book-appointment-api.js";
import * as ui from "./book-appointment-ui.js";

async function handleSubmit(event) {
  event.preventDefault();
  ui.clearMessages();
  ui.setFormBusy(true);
  try {
    const envelope = await api.bookAppointment(ui.readFormFields());
    if (envelope.status !== "success") {
      ui.showError(envelope.msg || "Could not book the appointment.");
      return;
    }
    ui.resetForm();
    ui.showSuccess("Thanks -- we've received your request and will be in touch.");
  } catch (err) {
    ui.showError("Network error while sending your request. Please try again.");
  } finally {
    ui.setFormBusy(false);
  }
}

function init() {
  ui.elements.form.addEventListener("submit", handleSubmit);
}

init();
