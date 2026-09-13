// Closed Captioning Audit - UI logic.
//
// This page is rendered by pywebview's native web view (WKWebView on macOS,
// WebView2 on Windows, WebKitGTK on Linux) rather than a Python GUI toolkit.
// Python-side actions are called through `pywebview.api.<name>(...)`, which
// returns a Promise; Python reports progress back by calling the global
// `dispatchAppEvent(...)` function defined below (see gui.py's `notify`).

const CAPTION_LABELS = {
  none: "No captions",
  auto_generated: "Auto-generated",
  human_edited: "Edited / human-provided",
  unknown: "Captions (source unknown)",
};

const els = {};

function byId(id) {
  return document.getElementById(id);
}

function cacheElements() {
  [
    "versionBadge", "runCompleteBtn", "runCourseBtn", "viewResultsBtn", "stopBtn",
    "headlessCheckbox", "log", "progressTrack", "statusText", "settingsBtn", "resetBtn",
    "courseDialog", "courseIdInput", "courseIdError", "courseCancelBtn", "courseConfirmBtn",
    "settingsDialog", "canvasTokenInput", "clientIdInput", "clientSecretInput",
    "revealSettingsCheckbox", "settingsError", "settingsCancelBtn", "settingsSaveBtn",
    "loginDialog", "loginTitle", "loginMessage", "loginContinueBtn",
    "confirmDialog", "confirmTitle", "confirmMessage", "confirmCancelBtn", "confirmOkBtn",
    "resultsView", "resultsBackBtn", "openResultsFileBtn", "resultsSummaryLine",
    "resultsBreakdownLine", "resultsPlatformLine", "resultsFilter", "resultsTableBody",
  ].forEach((id) => { els[id] = byId(id); });
}

// ----------------------------------------------------------------------
// small helpers
function appendLog(line) {
  const atBottom = els.log.scrollTop + els.log.clientHeight >= els.log.scrollHeight - 4;
  els.log.textContent += (els.log.textContent ? "\n" : "") + line;
  if (atBottom) {
    els.log.scrollTop = els.log.scrollHeight;
  }
}

function setBusy(isBusy, statusMessage) {
  els.runCompleteBtn.disabled = isBusy;
  els.runCourseBtn.disabled = isBusy;
  els.stopBtn.disabled = !isBusy;
  els.progressTrack.hidden = !isBusy;
  if (statusMessage) {
    els.statusText.textContent = statusMessage;
  }
}

function callApi(name, ...args) {
  if (!window.pywebview || !window.pywebview.api) {
    appendLog(`Error: the Python bridge is not ready yet (tried to call ${name}).`);
    return Promise.reject(new Error("pywebview API not ready"));
  }
  return window.pywebview.api[name](...args);
}

// A single reusable confirm dialog, Promise-based so callers can `await` it.
function showConfirm(title, message, { okLabel = "Confirm", danger = true } = {}) {
  els.confirmTitle.textContent = title;
  els.confirmMessage.textContent = message;
  els.confirmOkBtn.textContent = okLabel;
  els.confirmOkBtn.className = danger ? "btn btn-danger" : "btn btn-accent";
  els.confirmDialog.hidden = false;

  return new Promise((resolve) => {
    function cleanup(result) {
      els.confirmDialog.hidden = true;
      els.confirmOkBtn.removeEventListener("click", onOk);
      els.confirmCancelBtn.removeEventListener("click", onCancel);
      resolve(result);
    }
    function onOk() { cleanup(true); }
    function onCancel() { cleanup(false); }
    els.confirmOkBtn.addEventListener("click", onOk);
    els.confirmCancelBtn.addEventListener("click", onCancel);
  });
}

// ----------------------------------------------------------------------
// events pushed from Python (see gui.py's notify() / gui_api.AuditApi)
function dispatchAppEvent(data) {
  const { event, payload } = data;
  switch (event) {
    case "log":
      appendLog(payload);
      break;
    case "status":
      els.statusText.textContent = payload;
      break;
    case "run_started":
      setBusy(true, `${payload.label} in progress...`);
      appendLog(`--- ${payload.label} ---`);
      break;
    case "run_finished":
      onRunFinished(payload);
      break;
    case "login_prompt":
      els.loginTitle.textContent = payload.title;
      els.loginMessage.textContent = payload.message;
      els.loginDialog.hidden = false;
      break;
    default:
      // Unknown events are ignored so older UI builds don't break on new
      // event types added later.
      break;
  }
}
window.dispatchAppEvent = dispatchAppEvent;

async function onRunFinished(payload) {
  setBusy(false, "Ready");

  if (payload.error) {
    appendLog(`ERROR: ${payload.error}`);
    els.statusText.textContent = `${payload.label} failed`;
    return;
  }

  const outcome = payload.cancelled ? "stopped early" : "complete";
  appendLog(`--- ${payload.label} ${outcome} ---`);
  els.statusText.textContent = `${payload.label} ${outcome}`;

  const viewNow = await showConfirm(
    payload.label,
    `${payload.label} ${outcome}. View the results now?`,
    { okLabel: "View results", danger: false }
  );
  if (viewNow) {
    openResultsView();
  }
}

// ----------------------------------------------------------------------
// audit controls
async function runCompleteAudit() {
  const result = await callApi("run_complete_audit", els.headlessCheckbox.checked);
  if (!result.started) {
    appendLog(`Could not start: ${result.error}`);
  }
}

function openCourseDialog() {
  els.courseIdInput.value = "";
  els.courseIdError.hidden = true;
  els.courseDialog.hidden = false;
  els.courseIdInput.focus();
}

async function confirmCourseDialog() {
  const courseId = els.courseIdInput.value.trim();
  if (!courseId) {
    els.courseIdError.textContent = "Please enter a course ID.";
    els.courseIdError.hidden = false;
    return;
  }

  els.courseDialog.hidden = true;
  const result = await callApi("run_individual_audit", courseId, els.headlessCheckbox.checked);
  if (!result.started) {
    appendLog(`Could not start: ${result.error}`);
  }
}

async function stopAudit() {
  await callApi("stop_audit");
}

async function resetData() {
  const confirmed = await showConfirm(
    "Reset data",
    "Delete every cached course, module and audit result? This cannot be undone.",
    { okLabel: "Delete", danger: true }
  );
  if (!confirmed) return;

  const result = await callApi("reset_data");
  appendLog(`Reset removed ${result.removed} file(s).`);
}

async function confirmLogin() {
  els.loginDialog.hidden = true;
  await callApi("confirm_login");
}

// ----------------------------------------------------------------------
// settings dialog
async function openSettingsDialog() {
  const settings = await callApi("get_settings");
  els.canvasTokenInput.value = settings.canvasToken || "";
  els.clientIdInput.value = settings.clientId || "";
  els.clientSecretInput.value = settings.clientSecret || "";
  els.revealSettingsCheckbox.checked = false;
  toggleSettingsReveal();
  els.settingsError.hidden = true;
  els.settingsDialog.hidden = false;
}

function toggleSettingsReveal() {
  const type = els.revealSettingsCheckbox.checked ? "text" : "password";
  els.canvasTokenInput.type = type;
  els.clientIdInput.type = type;
  els.clientSecretInput.type = type;
}

async function saveSettings() {
  const result = await callApi(
    "save_settings",
    els.canvasTokenInput.value,
    els.clientIdInput.value,
    els.clientSecretInput.value
  );

  if (!result.saved) {
    els.settingsError.textContent = result.error || "Could not save settings.";
    els.settingsError.hidden = false;
    return;
  }

  els.settingsDialog.hidden = true;
  appendLog("Settings saved.");
}

// ----------------------------------------------------------------------
// results view
let resultsData = { entries: [], summary: {}, captionLabels: CAPTION_LABELS };
let sortColumn = null;
let sortReverse = false;

async function openResultsView() {
  resultsData = await callApi("get_results");
  if (!resultsData.entries.length) {
    appendLog("No audit results yet. Run an audit first.");
    return;
  }
  renderResultsSummary();
  renderResultsTable();
  els.resultsView.hidden = false;
}

function closeResultsView() {
  els.resultsView.hidden = true;
}

function renderResultsSummary() {
  const s = resultsData.summary;
  const total = s.total || 0;
  const pct = total ? Math.round((s.withCaptions / total) * 100) : 0;
  els.resultsSummaryLine.textContent =
    `${total} videos audited - ${s.withCaptions || 0} captioned (${pct}%), ` +
    `${s.withoutCaptions || 0} missing captions`;

  const labels = resultsData.captionLabels || CAPTION_LABELS;
  const byKind = s.byCaptionKind || {};
  els.resultsBreakdownLine.textContent = Object.keys(byKind)
    .sort()
    .map((kind) => `${labels[kind] || kind}: ${byKind[kind]}`)
    .join(" | ");

  const byType = s.byType || {};
  els.resultsPlatformLine.textContent = Object.keys(byType)
    .sort()
    .map((platform) => {
      const bucket = byType[platform];
      return `${platform}: ${bucket.withCaptions}/${bucket.total} captioned`;
    })
    .join(" | ");
}

function rowsForDisplay() {
  const filter = els.resultsFilter.value;
  const labels = resultsData.captionLabels || CAPTION_LABELS;

  let rows = resultsData.entries.map((entry) => {
    const hasCaptions = Boolean(entry.has_captions);
    const kind = entry.caption_kind || (hasCaptions ? "unknown" : "none");
    return {
      type: String(entry.type || ""),
      captions: hasCaptions ? "Yes" : "No",
      kind: labels[kind] || kind,
      kindKey: kind,
      confidence: String(entry.caption_confidence || ""),
      course: String(entry.course_id || ""),
      url: String(entry.url || ""),
      hasCaptions,
    };
  });

  if (filter === "missing") {
    rows = rows.filter((row) => !row.hasCaptions);
  } else if (filter !== "all") {
    rows = rows.filter((row) => row.kindKey === filter);
  }

  if (sortColumn) {
    rows.sort((a, b) => {
      const cmp = String(a[sortColumn]).localeCompare(String(b[sortColumn]));
      return sortReverse ? -cmp : cmp;
    });
  }

  return rows;
}

function renderResultsTable() {
  const rows = rowsForDisplay();
  els.resultsTableBody.innerHTML = "";

  for (const row of rows) {
    const tr = document.createElement("tr");
    if (!row.hasCaptions) {
      tr.className = "row-missing";
    } else if (row.kindKey === "auto_generated") {
      tr.className = "row-auto";
    }

    [row.type, row.captions, row.kind, row.confidence, row.course, row.url].forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    });

    tr.title = "Click to open this video";
    tr.addEventListener("click", () => callApi("open_external", row.url));
    els.resultsTableBody.appendChild(tr);
  }
}

function sortBy(column) {
  sortReverse = sortColumn === column ? !sortReverse : false;
  sortColumn = column;
  renderResultsTable();
}

// ----------------------------------------------------------------------
function wireEvents() {
  els.runCompleteBtn.addEventListener("click", runCompleteAudit);
  els.runCourseBtn.addEventListener("click", openCourseDialog);
  els.viewResultsBtn.addEventListener("click", openResultsView);
  els.stopBtn.addEventListener("click", stopAudit);
  els.resetBtn.addEventListener("click", resetData);
  els.settingsBtn.addEventListener("click", openSettingsDialog);

  els.courseCancelBtn.addEventListener("click", () => { els.courseDialog.hidden = true; });
  els.courseConfirmBtn.addEventListener("click", confirmCourseDialog);
  els.courseIdInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") confirmCourseDialog();
  });

  els.settingsCancelBtn.addEventListener("click", () => { els.settingsDialog.hidden = true; });
  els.settingsSaveBtn.addEventListener("click", saveSettings);
  els.revealSettingsCheckbox.addEventListener("change", toggleSettingsReveal);

  els.loginContinueBtn.addEventListener("click", confirmLogin);

  els.resultsBackBtn.addEventListener("click", closeResultsView);
  els.openResultsFileBtn.addEventListener("click", () => callApi("open_results_file"));
  els.resultsFilter.addEventListener("change", renderResultsTable);

  document.querySelectorAll("#resultsTable thead th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => sortBy(th.dataset.sort));
  });
}

async function init() {
  cacheElements();
  wireEvents();
  try {
    els.versionBadge.textContent = `v${await callApi("get_version")}`;
  } catch (err) {
    els.versionBadge.textContent = "";
  }
}

if (window.pywebview) {
  init();
} else {
  window.addEventListener("pywebviewready", init);
}
