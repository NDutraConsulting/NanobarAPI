// nanobar-ui.js
// Pure DOM rendering/manipulation for the nanobar detail page: the nanobar's own summary/edit
// form/coverage-gaps (unchanged from before this build), and a master-detail bricks section
// (bound bricks on the left, the selected brick's Detail/Run tabs on the right -- the Detail
// tab's rendering logic is what the now-retired brick.html/brick-ui.js used to own, relocated
// here verbatim aside from id/selector changes forced by no longer being its own page).
// No fetch() calls happen in this file.

const titleEl = document.getElementById("nanobar-title");
const subtitleEl = document.getElementById("nanobar-subtitle");

const pageStatusEl = document.getElementById("page-status");

const summarySectionEl = document.getElementById("summary-section");
const summaryGridEl = document.getElementById("summary-grid");
const summaryTargetsEl = document.getElementById("summary-targets");

const bricksStatusEl = document.getElementById("bricks-status");
const bricksLayoutEl = document.getElementById("bricks-layout");
const bricksListEl = document.getElementById("bricks-list");
const bricksEmptyEl = document.getElementById("bricks-empty");

const brickRowTemplate = document.getElementById("brick-row-template");

const editFormEl = document.getElementById("edit-form");
const editLabelEl = document.getElementById("edit-label");
const editScenarioDescriptionEl = document.getElementById("edit-scenario-description");
const editComponentSourceEl = document.getElementById("edit-component-source");
const editDomainEl = document.getElementById("edit-domain");
const editAppBoxEl = document.getElementById("edit-app-box");
const editCriticalityEl = document.getElementById("edit-criticality");
const editSaveBtnEl = document.getElementById("edit-save-btn");
const editErrorEl = document.getElementById("edit-error");
const editSuccessEl = document.getElementById("edit-success");

export const elements = {
  editForm: editFormEl,
};

const REVIEW_PILL_CLASSES = {
  new: "review-pill-new",
  reviewed: "review-pill-reviewed",
  flagged: "review-pill-flagged",
  promoted: "review-pill-promoted",
};

const CONTENT_HASH_PREFIX_LENGTH = 12;

/* ---------------------------------------------------------- page status */

/** Whole-page loading state: shown before either fetch has resolved. */
export function showLoading() {
  titleEl.textContent = "Loading nanobar…";
  subtitleEl.hidden = true;

  pageStatusEl.hidden = false;
  pageStatusEl.className = "page-status";
  pageStatusEl.textContent = "Loading…";

  summarySectionEl.hidden = true;
  hideBricksContent();
  bricksStatusEl.hidden = true;
}

/** The nanobar itself doesn't exist (404 from the bricks endpoint). */
export function showNotFound(message) {
  titleEl.textContent = "Nanobar not found";
  subtitleEl.hidden = true;

  pageStatusEl.hidden = false;
  pageStatusEl.className = "page-status page-status-error";
  pageStatusEl.textContent = message || "This nanobar could not be found.";

  summarySectionEl.hidden = true;
  hideBricksContent();
  bricksStatusEl.hidden = true;
}

/** The network request itself failed (offline, DNS, etc) — distinct from a 404. */
export function showNetworkError(message) {
  titleEl.textContent = "Could not load nanobar";
  subtitleEl.hidden = true;

  pageStatusEl.hidden = false;
  pageStatusEl.className = "page-status page-status-error";
  pageStatusEl.textContent = message || "Could not reach the server. Please refresh the page and try again.";

  summarySectionEl.hidden = true;
  hideBricksContent();
  bricksStatusEl.hidden = true;
}

/** Clears the page-level status banner once real content is ready to show. */
export function clearPageStatus() {
  pageStatusEl.hidden = true;
}

function hideBricksContent() {
  bricksLayoutEl.hidden = true;
  bricksEmptyEl.hidden = true;
}

/* --------------------------------------------------------------- summary */

/**
 * Renders the nanobar's own top-level fields from `GET .../nanobars/{id}`. If `nanobar` is
 * null (the best-effort fetch failed for some reason other than a 404, which is handled by
 * showNotFound above), the summary section stays hidden and only the page title/subtitle fall
 * back to the id.
 */
export function renderSummary(nanobarId, nanobar) {
  document.title = `${nanobar ? nanobar.system_name : nanobarId} · NanobarAPI`;
  titleEl.textContent = nanobar ? nanobar.system_name : nanobarId;

  subtitleEl.hidden = false;
  subtitleEl.textContent = `Nanobar ${nanobarId}`;

  if (!nanobar) {
    summarySectionEl.hidden = true;
    return;
  }

  summarySectionEl.hidden = false;
  summaryGridEl.textContent = "";

  const fields = [
    ["Type", nanobar.nanobar_type],
    ["Domain", nanobar.domain],
    ["AppBox", nanobar.app_box],
    ["System version", nanobar.system_version],
    ["Regression weight", nanobar.regression_weight],
    ["Scenario frequency", nanobar.endpoint_scenario_frequency],
    ["Source info", nanobar.source_info],
    ["Created by", nanobar.created_by],
    ["Schema version", nanobar.schema_version],
  ];

  for (const [label, value] of fields) {
    if (value === undefined || value === null || value === "") continue;
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    // Object-valued fields (scenario frequency, source info) render as readable JSON,
    // not the useless "[object Object]" that String(value) would otherwise produce.
    dd.textContent = typeof value === "object" ? JSON.stringify(value) : String(value);
    summaryGridEl.append(dt, dd);
  }

  summaryTargetsEl.textContent = "";
  const targets = nanobar.monitor_target_refs || [];
  for (const ref of targets) {
    const chip = document.createElement("span");
    chip.className = "target-chip";
    const strong = document.createElement("strong");
    strong.textContent = ref.target_type;
    chip.append(strong, document.createTextNode(` · ${ref.stable_name}`));
    summaryTargetsEl.appendChild(chip);
  }

  populateEditForm(nanobar);
}

/* ------------------------------------------------------------ edit form */

/** Pre-fills the edit form's inputs with the nanobar's current navigation-field values —
 * the form doubles as this page's display of them, so they aren't duplicated read-only
 * in the summary grid above. */
function populateEditForm(nanobar) {
  editLabelEl.value = nanobar.label || "";
  editScenarioDescriptionEl.value = nanobar.scenario_description || "";
  editComponentSourceEl.value = nanobar.component_source_description || "";
  editDomainEl.value = nanobar.domain || "";
  editAppBoxEl.value = nanobar.app_box || "";
  editCriticalityEl.value = nanobar.criticality ?? 0.5;
}

/** Reads the edit form's current field values as a partial-update payload. An emptied
 * criticality field is omitted (keeps its current stored value) rather than sent as the
 * misleading `Number("") === 0`, which would silently zero it out. */
export function readEditFormFields() {
  const fields = {
    label: editLabelEl.value,
    scenario_description: editScenarioDescriptionEl.value,
    component_source_description: editComponentSourceEl.value,
    domain: editDomainEl.value,
    app_box: editAppBoxEl.value,
  };
  if (editCriticalityEl.value !== "") {
    fields.criticality = Number(editCriticalityEl.value);
  }
  return fields;
}

export function showEditError(message) {
  editSuccessEl.hidden = true;
  editErrorEl.textContent = message;
  editErrorEl.hidden = false;
}

export function showEditSuccess(message) {
  editErrorEl.hidden = true;
  editSuccessEl.textContent = message;
  editSuccessEl.hidden = false;
}

export function clearEditMessages() {
  editErrorEl.hidden = true;
  editSuccessEl.hidden = true;
}

/** Disables the save button (and shows a busy label) while a save request is in flight. */
export function setEditFormBusy(isBusy) {
  editSaveBtnEl.disabled = isBusy;
  editSaveBtnEl.textContent = isBusy ? "Saving…" : "Save";
}

/* -------------------------------------------------------- coverage gaps */

const coverageGapsSectionEl = document.getElementById("coverage-gaps-section");
const coverageGapsStatusEl = document.getElementById("coverage-gaps-status");
const coverageGapsListEl = document.getElementById("coverage-gaps-list");
const coverageGapsEmptyEl = document.getElementById("coverage-gaps-empty");
const needsClassificationEl = document.getElementById("coverage-needs-classification");
const needsClassificationTypeEl = document.getElementById("needs-classification-type");
const needsClassificationSpanEl = document.getElementById("needs-classification-span");
const needsClassificationSpanLinkEl = document.getElementById("needs-classification-span-link");
const needsClassificationNoSpanEl = document.getElementById("needs-classification-no-span");

function hideCoverageGapsContent() {
  coverageGapsListEl.hidden = true;
  coverageGapsEmptyEl.hidden = true;
  needsClassificationEl.hidden = true;
}

export function showCoverageGapsLoading() {
  coverageGapsSectionEl.hidden = false;
  coverageGapsStatusEl.hidden = false;
  coverageGapsStatusEl.className = "coverage-gaps-status";
  coverageGapsStatusEl.textContent = "Loading…";
  hideCoverageGapsContent();
}

export function showCoverageGapsError(message) {
  coverageGapsSectionEl.hidden = false;
  coverageGapsStatusEl.hidden = false;
  coverageGapsStatusEl.className = "coverage-gaps-status coverage-gaps-status-error";
  coverageGapsStatusEl.textContent = message || "Could not load coverage gaps.";
  hideCoverageGapsContent();
}

/**
 * Renders the coverage-gaps response: `{"status": "classified", "gaps": [...]}` — one pill per
 * required scenario type this nanobar's type expects but has no bound brick for, empty meaning
 * fully covered — or `{"status": "needs_classification", "gaps": [], "related_span": {...} |
 * null}` for a `nanobar_type` this app's taxonomy has no entry (static or dynamic) for at all.
 * The two states are deliberately never conflated: an unresolvable type used to render
 * identically to "fully covered" (both an empty gaps list), which looked reassuring but meant
 * nothing had actually been measured.
 * @param {{status: "classified"|"needs_classification", gaps: string[], nanobar_type?: string, related_span?: {trace_id: string, event_id: string, name: string|null, recorded_at_ns: number} | null}} data
 */
export function renderCoverageGaps(data) {
  coverageGapsSectionEl.hidden = false;
  coverageGapsStatusEl.hidden = true;
  hideCoverageGapsContent();

  if (data.status === "needs_classification") {
    needsClassificationEl.hidden = false;
    needsClassificationTypeEl.textContent = data.nanobar_type;

    if (data.related_span) {
      needsClassificationSpanEl.hidden = false;
      needsClassificationNoSpanEl.hidden = true;
      const { trace_id: traceId, event_id: eventId, name } = data.related_span;
      needsClassificationSpanLinkEl.href = `/admin/nanobar/traces/${encodeURIComponent(traceId)}#span-${encodeURIComponent(eventId)}`;
      needsClassificationSpanLinkEl.textContent = name || eventId;
    } else {
      needsClassificationSpanEl.hidden = true;
      needsClassificationNoSpanEl.hidden = false;
    }
    return;
  }

  const gaps = data.gaps;
  if (!gaps || gaps.length === 0) {
    coverageGapsEmptyEl.hidden = false;
    return;
  }

  coverageGapsListEl.hidden = false;
  coverageGapsListEl.textContent = "";
  for (const scenarioType of gaps) {
    const pill = document.createElement("span");
    pill.className = "gap-pill";
    pill.textContent = scenarioType;
    coverageGapsListEl.appendChild(pill);
  }
}

/* ---------------------------------------------------------------- bricks list (left pane) */

export function showBricksLoading() {
  bricksStatusEl.hidden = false;
  bricksStatusEl.className = "bricks-status";
  bricksStatusEl.textContent = "Loading bricks…";
  hideBricksContent();
}

export function showBricksError(message) {
  bricksStatusEl.hidden = false;
  bricksStatusEl.className = "bricks-status bricks-status-error";
  bricksStatusEl.textContent = message || "Could not load bricks.";
  hideBricksContent();
}

/**
 * Renders the bricks list (left pane) from an array of RegressionBrick objects (each already
 * carrying a `review_status` object, per the bricks-for-nanobar endpoint). Each row is a button
 * carrying the brick's id as `data-brick-id`; `onSelect` is called with that id on click.
 */
export function renderBricksList(bricks, onSelect) {
  bricksStatusEl.hidden = true;

  if (!bricks || bricks.length === 0) {
    bricksLayoutEl.hidden = true;
    bricksEmptyEl.hidden = false;
    return;
  }

  bricksEmptyEl.hidden = true;
  bricksLayoutEl.hidden = false;
  bricksListEl.textContent = "";

  for (const brick of bricks) {
    bricksListEl.appendChild(buildBrickRow(brick, onSelect));
  }
}

function buildBrickRow(brick, onSelect) {
  const fragment = brickRowTemplate.content.cloneNode(true);
  const btn = fragment.querySelector(".brick-row-btn");
  const methodEl = fragment.querySelector(".brick-method");
  const pathEl = fragment.querySelector(".brick-path");
  const pillEl = fragment.querySelector(".review-pill");

  const request = brick.request || {};
  const routeKey = (brick.source && brick.source.route_key) || "";
  const [routeKeyMethod, routeKeyPath] = [routeKey.split(" ")[0] || "", routeKey.split(" ").slice(1).join(" ")];
  const reviewStatus = (brick.review_status && brick.review_status.status) || "new";

  methodEl.textContent = request.method || routeKeyMethod || "?";
  pathEl.textContent = request.path || routeKeyPath || "(no path)";

  pillEl.textContent = reviewStatus;
  pillEl.className = `review-pill ${REVIEW_PILL_CLASSES[reviewStatus] || "review-pill-new"}`;

  btn.dataset.brickId = brick.regression_brick_id;
  btn.addEventListener("click", () => onSelect(brick.regression_brick_id));

  return fragment;
}

/** Marks exactly one brick row as selected (visual highlight), clearing any previous selection. */
export function highlightSelectedBrick(brickId) {
  for (const btn of bricksListEl.querySelectorAll(".brick-row-btn")) {
    btn.classList.toggle("brick-row-btn-selected", btn.dataset.brickId === brickId);
  }
}

/* ------------------------------------------------------- brick detail (right pane, Detail tab) */

const brickDetailEmptyEl = document.getElementById("brick-detail-empty");
const brickDetailPanelEl = document.getElementById("brick-detail-panel");

const brickDetailHeadingEl = document.getElementById("brick-detail-heading");

const fieldBrickIdEl = document.getElementById("field-brick-id");
const fieldContentHashEl = document.getElementById("field-content-hash");
const fieldSchemaVersionEl = document.getElementById("field-schema-version");
const fieldBrickVersionEl = document.getElementById("field-brick-version");
const fieldCreatedByEl = document.getElementById("field-created-by");
const fieldSourceEl = document.getElementById("field-source");
const fieldScenarioTypeRowEl = document.getElementById("field-scenario-type-row");
const fieldScenarioTypeEl = document.getElementById("field-scenario-type");

const reviewPillEl = document.getElementById("review-pill");
const reviewButtonEls = Array.from(document.querySelectorAll(".review-btn"));
const reviewErrorEl = document.getElementById("review-error");

const requestJsonEl = document.getElementById("request-json");
const responseJsonEl = document.getElementById("response-json");

const scenarioFormEl = document.getElementById("scenario-form");
const scenarioLabelEl = document.getElementById("scenario-label");
const scenarioDescriptionEl = document.getElementById("scenario-description");
const scenarioSaveBtnEl = document.getElementById("scenario-save-btn");
const scenarioErrorEl = document.getElementById("scenario-error");
const scenarioSuccessEl = document.getElementById("scenario-success");

const tagsListEl = document.getElementById("tags-list");
const tagChipTemplate = document.getElementById("tag-chip-template");
const tagAddFormEl = document.getElementById("tag-add-form");
const tagAddInputEl = document.getElementById("tag-add-input");
const tagsErrorEl = document.getElementById("tags-error");

elements.scenarioForm = scenarioFormEl;
elements.tagAddForm = tagAddFormEl;
elements.tagsList = tagsListEl;
elements.reviewButtons = reviewButtonEls;

/** Shows the right pane's "nothing selected yet" prompt, hiding any previously selected
 * brick's detail panel. */
export function showBrickDetailEmpty() {
  brickDetailEmptyEl.hidden = false;
  brickDetailPanelEl.hidden = true;
}

/**
 * Renders the full brick detail view (Detail tab) from a RegressionBrick object (as returned
 * by GET /api/bricks/{brick_id}, including the attached `review_status`).
 */
export function renderBrickDetail(brick) {
  brickDetailEmptyEl.hidden = true;
  brickDetailPanelEl.hidden = false;

  const request = brick.request || {};
  const routeKey = (brick.source && brick.source.route_key) || "";
  brickDetailHeadingEl.textContent = request.method || request.path ? `${request.method || ""} ${request.path || ""}`.trim() : routeKey || brick.regression_brick_id;

  fieldBrickIdEl.textContent = brick.regression_brick_id;
  fieldContentHashEl.textContent = brick.content_hash;
  fieldSchemaVersionEl.textContent = brick.schema_version;
  fieldBrickVersionEl.textContent = brick.brick_version;
  fieldCreatedByEl.textContent = brick.created_by;
  fieldSourceEl.textContent = JSON.stringify(brick.source);

  if (brick.regression_scenario_type) {
    fieldScenarioTypeRowEl.hidden = false;
    fieldScenarioTypeEl.textContent = brick.regression_scenario_type;
  } else {
    fieldScenarioTypeRowEl.hidden = true;
  }

  renderReviewStatus(brick.review_status);
  renderScenario(brick.scenario);
  renderTags(brick.tags);

  requestJsonEl.textContent = JSON.stringify(brick.request, null, 2);
  responseJsonEl.textContent = JSON.stringify(brick.response, null, 2);
}

/**
 * Updates the review-status pill and the "active" button highlight from a
 * review_status object: { regression_brick_id, status, updated_by }.
 */
export function renderReviewStatus(reviewStatus) {
  const status = reviewStatus ? reviewStatus.status : null;
  reviewPillEl.textContent = status || "Unknown";
  if (status) {
    reviewPillEl.dataset.status = status;
  } else {
    delete reviewPillEl.dataset.status;
  }

  for (const btn of reviewButtonEls) {
    btn.classList.toggle("is-active", btn.dataset.status === status);
  }
}

export function showReviewError(message) {
  reviewErrorEl.textContent = message;
  reviewErrorEl.hidden = false;
}

export function clearReviewError() {
  reviewErrorEl.textContent = "";
  reviewErrorEl.hidden = true;
}

/** Disables/enables all review-status buttons (e.g. while a request is in flight). */
export function setReviewButtonsBusy(isBusy) {
  for (const btn of reviewButtonEls) {
    btn.disabled = isBusy;
  }
}

/** Pre-fills the scenario form's inputs from a `BrickScenario` object (as attached to a
 * brick detail response's `scenario` field) — the form doubles as this section's display of
 * the current values, so they aren't shown a second time read-only. */
export function renderScenario(scenario) {
  scenarioLabelEl.value = (scenario && scenario.regression_scenario_label) || "";
  scenarioDescriptionEl.value = (scenario && scenario.description) || "";
}

/** Reads the scenario form's current field values as a partial-update payload. */
export function readScenarioFormFields() {
  return {
    regression_scenario_label: scenarioLabelEl.value,
    description: scenarioDescriptionEl.value,
  };
}

export function showScenarioError(message) {
  scenarioSuccessEl.hidden = true;
  scenarioErrorEl.textContent = message;
  scenarioErrorEl.hidden = false;
}

export function showScenarioSuccess(message) {
  scenarioErrorEl.hidden = true;
  scenarioSuccessEl.textContent = message;
  scenarioSuccessEl.hidden = false;
}

export function clearScenarioMessages() {
  scenarioErrorEl.hidden = true;
  scenarioSuccessEl.hidden = true;
}

export function setScenarioFormBusy(isBusy) {
  scenarioSaveBtnEl.disabled = isBusy;
  scenarioSaveBtnEl.textContent = isBusy ? "Saving…" : "Save";
}

/** Renders the tag chip list from a plain array of tag strings. Each chip carries the tag
 * as a `data-tag` attribute on its remove button, for the controller's delegated click
 * handler to read. */
export function renderTags(tags) {
  tagsListEl.textContent = "";
  for (const tag of tags || []) {
    const node = tagChipTemplate.content.cloneNode(true);
    node.querySelector(".tag-chip-text").textContent = tag;
    node.querySelector(".tag-chip-remove").dataset.tag = tag;
    tagsListEl.appendChild(node);
  }
}

export function showTagsError(message) {
  tagsErrorEl.textContent = message;
  tagsErrorEl.hidden = false;
}

export function clearTagsError() {
  tagsErrorEl.hidden = true;
}

export function readTagAddInput() {
  return tagAddInputEl.value.trim();
}

export function clearTagAddInput() {
  tagAddInputEl.value = "";
}

/* -------------------------------------------------------------- tabs (Detail / Run) */

const tabDetailBtnEl = document.getElementById("tab-detail-btn");
const tabRunBtnEl = document.getElementById("tab-run-btn");
const tabDetailPanelEl = document.getElementById("tab-detail-panel");
const tabRunPanelEl = document.getElementById("tab-run-panel");

elements.tabDetailBtn = tabDetailBtnEl;
elements.tabRunBtn = tabRunBtnEl;

export function showDetailTab() {
  tabDetailBtnEl.classList.add("brick-tab-btn-active");
  tabDetailBtnEl.setAttribute("aria-selected", "true");
  tabRunBtnEl.classList.remove("brick-tab-btn-active");
  tabRunBtnEl.setAttribute("aria-selected", "false");
  tabDetailPanelEl.hidden = false;
  tabRunPanelEl.hidden = true;
}

export function showRunTab() {
  tabRunBtnEl.classList.add("brick-tab-btn-active");
  tabRunBtnEl.setAttribute("aria-selected", "true");
  tabDetailBtnEl.classList.remove("brick-tab-btn-active");
  tabDetailBtnEl.setAttribute("aria-selected", "false");
  tabRunPanelEl.hidden = false;
  tabDetailPanelEl.hidden = true;
}

/** Resets the Run tab back to its just-selected-this-brick state — no prior run's verdict/spans
 * left showing from a previously selected brick. */
export function resetRunTab() {
  runStatusEl.hidden = true;
  runVerdictEl.hidden = true;
  runVerdictEl.textContent = "";
  runSpansWrapEl.hidden = true;
  runSpansListEl.textContent = "";
  refreshBtnEl.disabled = true;
}

/* -------------------------------------------------------------- Run tab */

const runBtnEl = document.getElementById("run-btn");
const refreshBtnEl = document.getElementById("refresh-btn");
const runProgressEl = document.getElementById("run-progress");
const runStatusEl = document.getElementById("run-status");
const runVerdictEl = document.getElementById("run-verdict");
const runSpansWrapEl = document.getElementById("run-spans-wrap");
const runSpansListEl = document.getElementById("run-spans-list");
const runSpanRowTemplate = document.getElementById("run-span-row-template");

elements.runBtn = runBtnEl;
elements.refreshBtn = refreshBtnEl;

export function setRunBusy(isBusy) {
  runBtnEl.disabled = isBusy;
  runBtnEl.textContent = isBusy ? "Running…" : "Run";
}

export function setRefreshBusy(isBusy) {
  refreshBtnEl.disabled = isBusy;
}

/** Shows/hides the indeterminate loading bar -- there's no real progress fraction to report (a
 * replay's own duration and the drain-worker delay while polling for its spans are both
 * open-ended), just "something is happening, please wait." */
export function setRunProgressVisible(isVisible) {
  runProgressEl.hidden = !isVisible;
}

export function showRunStatus(message) {
  runStatusEl.hidden = false;
  runStatusEl.className = "run-status";
  runStatusEl.textContent = message;
}

export function showRunError(message) {
  runStatusEl.hidden = false;
  runStatusEl.className = "run-status run-status-error";
  runStatusEl.textContent = message;
}

/**
 * Renders a `Verdict` object (`{overall_passed, diffs}` -- `nanobar_api/bricks/verdict.py`'s
 * current, simplified shape: "run it, then diff it. If they match, show a pass. If they don't,
 * show the diff. That is all." -- one flat list of human-readable diff lines, no per-layer
 * structure) as a pass/fail summary plus, on failure, the actual diffs, and enables the Refresh
 * button now that there's a trace to watch.
 */
export function renderVerdict(verdict) {
  runStatusEl.hidden = true;
  runVerdictEl.hidden = false;
  runVerdictEl.textContent = "";
  runVerdictEl.className = `run-verdict ${verdict.overall_passed ? "run-verdict-pass" : "run-verdict-fail"}`;

  const overall = document.createElement("p");
  overall.className = "run-verdict-overall";
  overall.textContent = verdict.overall_passed
    ? "PASS — replay matches the original capture."
    : "FAIL — replay differs from the original capture.";
  runVerdictEl.appendChild(overall);

  if (verdict.diffs && verdict.diffs.length > 0) {
    const list = document.createElement("ul");
    list.className = "run-verdict-diffs";
    for (const diff of verdict.diffs) {
      const item = document.createElement("li");
      item.className = "run-verdict-diff";
      item.textContent = diff;
      list.appendChild(item);
    }
    runVerdictEl.appendChild(list);
  }

  refreshBtnEl.disabled = false;
}

/** A replay's trace carries two different event shapes per boundary on the same channel set --
 * a "trace"-channel event (`{name, status, code.function.name, ...}`, from `NanobarTelemetry`/
 * `EventBusTraceMiddleware`) and a "snapshot"-channel event (`{request, response, content_hash,
 * nanobar_type, ...}`, from `capture_layer()`) -- and only the first carries a `name` field. A
 * snapshot event has no name of its own but *does* carry the actual request/response content,
 * so falling back to a literal "(unnamed span)" for it was actively hiding the more useful of
 * the two shapes behind the least informative label. Derives something real from whatever the
 * payload actually has instead: the request's own method+path (a raw `SnapshotMiddleware`
 * capture), a truncated SQL statement (an `orm-request-response` capture), or -- if neither
 * shape matches -- the nanobar_type itself (already shown as a badge, but still better than
 * nothing in the name slot). */
function summarizeSpanName(payload) {
  if (payload.name) return payload.name;

  const request = payload.request;
  if (request && typeof request === "object") {
    if (typeof request.method === "string" && typeof request.path === "string") {
      return `${request.method} ${request.path}`;
    }
    if (typeof request.statement === "string") {
      const statement = request.statement.trim().replace(/\s+/g, " ");
      return statement.length > 70 ? `${statement.slice(0, 70)}…` : statement;
    }
  }

  return payload.nanobar_type ? `${payload.nanobar_type} capture` : "(unnamed span)";
}

/** Same reasoning as `summarizeSpanName()` -- a snapshot-channel event has no top-level
 * `status_code`/`status` field (those only exist on trace-channel events), so this used to
 * always render "—" for exactly the events most worth knowing the outcome of. Falls back to
 * `payload.error` (every event shape carries this) when neither of the trace-channel fields is
 * present. */
function summarizeSpanStatus(payload) {
  if (payload.status_code != null) return String(payload.status_code);
  if (payload.status) return payload.status;
  if (payload.error === true) return "error";
  if (payload.error === false) return "ok";
  return "—";
}

/** Renders the replay's own trace's spans (name, nanobar_type badge if tagged, status/error) as
 * a flat, read-only, expandable list — a lighter-weight rendering than the full trace-detail
 * page's master-detail view, since this is just "what happened during this one replay," not a
 * general trace browser. Each row is a native `<details>` disclosure; expanding one reveals its
 * full raw payload (the same `.json-block` rendering the Detail tab's own Request/Response
 * panels already use) -- the actual request/response content, SQL statement, or code-location
 * attributes behind whatever `summarizeSpanName()`/`summarizeSpanStatus()` show by default. */
export function renderRunSpans(events) {
  runSpansListEl.textContent = "";

  if (!events || events.length === 0) {
    runSpansWrapEl.hidden = true;
    return;
  }

  runSpansWrapEl.hidden = false;
  for (const event of events) {
    const payload = event.payload || {};
    const fragment = runSpanRowTemplate.content.cloneNode(true);
    const row = fragment.querySelector(".run-span-row");
    fragment.querySelector(".run-span-name").textContent = summarizeSpanName(payload);

    const badgeEl = fragment.querySelector(".run-span-nanobar-type");
    if (payload.nanobar_type) {
      badgeEl.textContent = payload.nanobar_type;
      badgeEl.hidden = false;
    }

    fragment.querySelector(".run-span-status").textContent = summarizeSpanStatus(payload);
    fragment.querySelector(".run-span-payload").textContent = JSON.stringify(payload, null, 2);

    if (payload.error) {
      row.classList.add("run-span-row-error");
    }

    runSpansListEl.appendChild(fragment);
  }
}
