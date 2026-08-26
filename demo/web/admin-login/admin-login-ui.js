// admin-login-ui.js
// Pure DOM rendering/manipulation for the admin login page.
// No fetch() calls happen in this file.

const formEl = document.getElementById("login-form");
const usernameEl = document.getElementById("login-username");
const passwordEl = document.getElementById("login-password");
const submitBtnEl = document.getElementById("login-submit-btn");
const errorEl = document.getElementById("login-error");

export const elements = {
  form: formEl,
};

export function readCredentials() {
  return { username: usernameEl.value, password: passwordEl.value };
}

export function showError(message) {
  errorEl.textContent = message;
  errorEl.hidden = false;
}

export function clearError() {
  errorEl.hidden = true;
}

export function setFormBusy(isBusy) {
  submitBtnEl.disabled = isBusy;
  submitBtnEl.textContent = isBusy ? "Signing in…" : "Sign in";
}
