// nanobars-ui.js
// Pure DOM rendering/manipulation functions for the Nanobar Dashboard main
// list page. Given data, update the DOM. No fetch() calls happen in this file.

const statusEl = document.getElementById("nanobars-status");
const groupsEl = document.getElementById("nanobars-groups");
const emptyEl = document.getElementById("nanobars-empty");
const groupTemplate = document.getElementById("target-group-template");
const itemTemplate = document.getElementById("nanobar-item-template");
const trackTypeFilterEl = document.getElementById("track-type-filter");

export const elements = {
  trackTypeFilter: trackTypeFilterEl,
};

/**
 * Fills the track-type filter <select> with one option per distinct track
 * type present in the data, plus the "All track types" default. Preserves
 * the currently selected value if it's still one of the options.
 * @param {string[]} trackTypes sorted, deduplicated track types
 */
export function populateTrackTypeFilter(trackTypes) {
  const previousValue = trackTypeFilterEl.value;
  trackTypeFilterEl.textContent = "";

  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = "All track types";
  trackTypeFilterEl.appendChild(allOption);

  for (const trackType of trackTypes) {
    const option = document.createElement("option");
    option.value = trackType;
    option.textContent = trackType;
    trackTypeFilterEl.appendChild(option);
  }

  if (trackTypes.includes(previousValue)) {
    trackTypeFilterEl.value = previousValue;
  }
}

/** Show a transient status message (e.g. "Loading..."). */
export function showStatus(message) {
  statusEl.textContent = message;
  statusEl.hidden = false;
  statusEl.classList.remove("nanobars-status-error");
  groupsEl.hidden = true;
  emptyEl.hidden = true;
}

/** Show an error status message (e.g. load failure or envelope error). */
export function showLoadError(message) {
  statusEl.textContent = message;
  statusEl.hidden = false;
  statusEl.classList.add("nanobars-status-error");
  groupsEl.hidden = true;
  emptyEl.hidden = true;
}

/** Hide the status message entirely. */
export function hideStatus() {
  statusEl.hidden = true;
  statusEl.textContent = "";
  statusEl.classList.remove("nanobars-status-error");
}

/**
 * Render the nanobars-by-target-type groups. Each group becomes its own
 * section with a heading, a count, and a list of nanobar links. Shows the
 * empty state when there are no groups at all.
 * @param {Array<{targetType: string, nanobars: Array<{nanobar_id: string, system_name: string, nanobar_type: string, label: string | null}>}>} groups
 */
export function renderGroups(groups) {
  groupsEl.innerHTML = "";

  if (!groups || groups.length === 0) {
    groupsEl.hidden = true;
    emptyEl.hidden = false;
    return;
  }

  emptyEl.hidden = true;

  for (const group of groups) {
    const groupNode = groupTemplate.content.cloneNode(true);

    const titleEl = groupNode.querySelector(".target-group-title");
    titleEl.textContent = group.targetType;

    const countEl = groupNode.querySelector(".target-group-count");
    const count = group.nanobars.length;
    countEl.textContent = `${count} nanobar${count === 1 ? "" : "s"}`;

    const listEl = groupNode.querySelector(".nanobar-list");
    for (const nanobar of group.nanobars) {
      const itemNode = itemTemplate.content.cloneNode(true);

      const link = itemNode.querySelector(".nanobar-link");
      link.href = `/admin/nanobar/nanobars/${nanobar.nanobar_id}`;

      itemNode.querySelector(".nanobar-id").textContent = nanobar.nanobar_id;
      itemNode.querySelector(".nanobar-system").textContent = nanobar.system_name;
      itemNode.querySelector(".nanobar-track-type").textContent = nanobar.nanobar_type;
      const labelEl = itemNode.querySelector(".nanobar-label");
      if (nanobar.label) {
        labelEl.textContent = nanobar.label;
      } else {
        labelEl.remove();
      }

      listEl.appendChild(itemNode);
    }

    groupsEl.appendChild(groupNode);
  }

  groupsEl.hidden = false;
}
