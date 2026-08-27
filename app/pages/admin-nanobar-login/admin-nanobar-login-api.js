// admin-nanobar-login-api.js
// Thin fetch() wrapper around POST /admin/nanobar/login.
// Resolves to the parsed JSON envelope: {status, msg, result}.
// No DOM access happens in this file.

import { csrfHeader } from "../shared/csrf.js";

/** POST /admin/nanobar/login {username, password} -> on success, result.data.redirect names where to
 * send the user. */
export async function submitLogin(username, password) {
  const response = await fetch("/admin/nanobar/login", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...csrfHeader() },
    body: JSON.stringify({ username, password }),
  });
  return response.json();
}
