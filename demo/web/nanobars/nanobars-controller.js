// nanobars-controller.js
// Orchestrates the Nanobar Dashboard main list page: calls nanobars-api.js,
// groups the results client-side by monitor_target_refs[].target_type, and
// calls nanobars-ui.js to render them (or an error state). This is the only
// file with top-level "run on page load" logic.

import { fetchNanobars } from "./nanobars-api.js";
import { showStatus, showLoadError, hideStatus, renderGroups, populateTrackTypeFilter, elements } from "./nanobars-ui.js";

const UNTARGETED_LABEL = "(untargeted)";

// The full, unfiltered list from the last successful fetch — kept so the track-type filter
// can re-render client-side without a network round trip.
let allNanobars = [];

/**
 * Distinct `nanobar_type` values present in the data, sorted.
 * @param {Array<{nanobar_type: string}>} nanobars
 * @returns {string[]}
 */
function distinctTrackTypes(nanobars) {
  return [...new Set(nanobars.map((n) => n.nanobar_type).filter(Boolean))].sort();
}

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

/**
 * Renders `allNanobars` filtered by the currently selected track type (supplementing, not
 * replacing, the target-type grouping — a nanobar still appears once per target type it
 * carries, just narrowed down to whichever track type is selected).
 */
function renderFiltered() {
  const selected = elements.trackTypeFilter.value;
  const filtered = selected ? allNanobars.filter((n) => n.nanobar_type === selected) : allNanobars;
  renderGroups(groupByTargetType(filtered));
}

/** Load every nanobar from the API, populate the track-type filter, group them, and render them. */
async function loadNanobars() {
  showStatus("Loading nanobars…");
  try {
    const envelope = await fetchNanobars();
    if (envelope.status !== "success") {
      showLoadError(envelope.msg || "Could not load nanobars.");
      return;
    }
    hideStatus();
    allNanobars = envelope.result.data;
    populateTrackTypeFilter(distinctTrackTypes(allNanobars));
    renderFiltered();
  } catch (err) {
    showLoadError("Could not reach the server. Please try again.");
  }
}

function init() {
  elements.trackTypeFilter.addEventListener("change", renderFiltered);
  loadNanobars();
}

init();
