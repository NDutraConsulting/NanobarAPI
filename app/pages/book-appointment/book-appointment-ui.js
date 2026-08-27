// book-appointment-ui.js
// Pure DOM rendering/manipulation for the appointment-booking page.
// No fetch() calls happen in this file.

const formEl = document.getElementById("booking-form");
const nameEl = document.getElementById("booking-name");
const emailEl = document.getElementById("booking-email");
const noteEl = document.getElementById("booking-note");
const submitBtnEl = document.getElementById("booking-submit-btn");
const errorEl = document.getElementById("booking-error");
const successEl = document.getElementById("booking-success");

export const elements = {
  form: formEl,
};

export function readFormFields() {
  return {
    name: nameEl.value,
    email: emailEl.value,
    note: noteEl.value,
  };
}

export function resetForm() {
  formEl.reset();
}

export function showError(message) {
  successEl.hidden = true;
  errorEl.textContent = message;
  errorEl.hidden = false;
}

export function showSuccess(message) {
  errorEl.hidden = true;
  successEl.textContent = message;
  successEl.hidden = false;
}

export function clearMessages() {
  errorEl.hidden = true;
  successEl.hidden = true;
}

export function setFormBusy(isBusy) {
  submitBtnEl.disabled = isBusy;
  submitBtnEl.textContent = isBusy ? "Sending…" : "Request appointment";
}
