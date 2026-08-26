// book-appointment-api.js
// Thin fetch() wrapper around POST /book-appointment.
// Resolves to the parsed JSON envelope: {status, msg, result}.
// No DOM access happens in this file.
//
// No CSRF header here -- this route is fully public/unauthenticated (no session cookie), and
// CSRF protection only matters for cookie-authenticated flows (see nanobar_api/admin_auth.py's
// module docstring); there's no ambient credential for a cross-site request to hijack.

export async function bookAppointment(fields) {
  const response = await fetch("/book-appointment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fields),
  });
  return response.json();
}
