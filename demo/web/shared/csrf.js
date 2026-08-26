// shared/csrf.js
// Reads the double-submit CSRF cookie (issued by nanobar_api.admin_auth.CSRFMiddleware, part of
// session_protected() -- every /admin/* route) and returns the header pair to attach to any
// non-GET fetch under /admin/*. Deliberately not HttpOnly on the server side -- this file's
// whole job is to read it and echo it back as a header, proving the request came from a page
// that could read the cookie (same-origin), not a cross-site form/script forging it blindly.

const CSRF_COOKIE_NAME = "nanobar_csrftoken";
const CSRF_HEADER_NAME = "x-nanobar-csrf-token";

function readCookie(name) {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

/**
 * Returns `{ [headerName]: token }` for the current CSRF cookie, or `{}` if none is set yet
 * (e.g. before any GET request under session_protected() has run in this browser session).
 * Spread into a fetch() call's headers: `{ ...JSON_HEADERS, ...csrfHeader() }`.
 */
export function csrfHeader() {
  const token = readCookie(CSRF_COOKIE_NAME);
  return token ? { [CSRF_HEADER_NAME]: token } : {};
}
