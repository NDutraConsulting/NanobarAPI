// nanobars-controller.js
// Orchestrates the Nanobar Dashboard main list page: calls nanobars-api.js,
// groups the results client-side by monitor_target_refs[].target_type, and
// calls nanobars-ui.js to render them (or an error state). This is the only
// file with top-level "run on page load" logic.

import { fetchNanobars } from "./nanobars-api.js";
import { showStatus, showLoadError, hideStatus, renderGroups } from "./nanobars-ui.js";

const UNTARGETED_LABEL = "(untargeted)";

/**
 * Groups nanobars by each distinct `monitor_target_refs[].target_type` they
 * carry. A nanobar referencing more than one target type appears in each of
 * those groups. A nanobar with no target refs at all still shows up, under
 * an "(untargeted)" bucket, rather than being silently dropped.
 * @param {Array<{monitor_target_refs: Array<{target_type: string}>}>} nanobars
 * @returns {Array<{targetType: string, nanobars: Array}>} groups sorted by target type
 */
function groupByTargetType(nanobars) {
  const groups = new Map();

  for (const nanobar of nanobars) {
    const refs = nanobar.monitor_target_refs || [];
    const targetTypes = refs.length > 0
      ? [...new Set(refs.map((ref) => ref.target_type))].sort()
      : [UNTARGETED_LABEL];

    for (const targetType of targetTypes) {
      if (!groups.has(targetType)) {
        groups.set(targetType, []);
      }
      groups.get(targetType).push(nanobar);
    }
  }

  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([targetType, groupNanobars]) => ({ targetType, nanobars: groupNanobars }));
}

/** Load every nanobar from the API, group them, and render them. */
async function loadNanobars() {
  showStatus("Loading nanobars…");
  try {
    const envelope = await fetchNanobars();
    if (envelope.status !== "success") {
      showLoadError(envelope.msg || "Could not load nanobars.");
      return;
    }
    hideStatus();
    renderGroups(groupByTargetType(envelope.result.data));
  } catch (err) {
    showLoadError("Could not reach the server. Please try again.");
  }
}

function init() {
  loadNanobars();
}

init();
