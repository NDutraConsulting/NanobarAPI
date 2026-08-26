// landing.js
// Fetches /openapi.json (already carries info.title/info.version -- no new server-side data
// endpoint needed) and renders the app's own title/version. The only file on this page; kept as
// one file rather than the usual api/ui/controller split since there's no state to separate.

async function init() {
  const titleEl = document.getElementById("app-title");
  const versionEl = document.getElementById("app-version");

  try {
    const response = await fetch("/openapi.json");
    const schema = await response.json();
    if (schema.info && schema.info.title) {
      titleEl.textContent = schema.info.title;
      document.title = schema.info.title;
    }
    if (schema.info && schema.info.version) {
      versionEl.textContent = `v${schema.info.version}`;
      versionEl.hidden = false;
    }
  } catch (err) {
    // Best-effort: the static title/nav still work with no JS data at all.
  }
}

init();
