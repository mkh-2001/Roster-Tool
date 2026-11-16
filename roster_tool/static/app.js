"use strict";

// Roster Tool UI. Talks only to the local Roster Tool process (same origin).
// Student data is inserted with textContent / value only, never as HTML.
(() => {
  const TOKEN = document.querySelector('meta[name="roster-token"]').content;
  // Supported extensions come from the server's input-format registry (".pdf,.csv,.xlsx").
  const ACCEPTED = document.querySelector('meta[name="roster-accept"]').content.split(",");
  const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
  const SOURCE_LABELS = { csv: "the CSV file", xlsx: "the Excel workbook" };
  const MAX_ISSUES_SHOWN = 12;
  const $ = (id) => document.getElementById(id);

  const state = {
    roster: null,
    issues: [],
    tab: "help",
    help: null, // { url, filename }
    csv: null, // { filename, csv, header, rows }
    validateTimer: null,
    validateSeq: 0,
    csvSeq: 0,
  };

  // ---------------------------------------------------------------- API ----

  // `fileType` (an extension such as "csv") is sent for uploads; the file's name never leaves the page.
  async function api(path, body, fileType = null) {
    const binary = fileType !== null;
    const headers = { "X-Roster-Token": TOKEN, "Content-Type": binary ? "application/octet-stream" : "application/json" };
    if (binary) headers["X-Roster-File-Type"] = fileType;
    let res;
    try {
      res = await fetch(path, {
        method: "POST",
        headers,
        body: binary ? body : JSON.stringify(body),
      });
    } catch (_) {
      throw new Error("Roster Tool is not responding. Close this window and start Roster Tool again.");
    }
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      /* non-JSON error body */
    }
    if (!res.ok) {
      const err = new Error((data && data.error) || `Request failed (${res.status}).`);
      err.data = data;
      throw err;
    }
    return data;
  }

  function setStatus(el, text, kind) {
    el.textContent = text || "";
    el.className = "status" + (kind ? " " + kind : "");
  }

  const errors = () => state.issues.filter((i) => i.severity === "error");

  // ------------------------------------------------------------- Upload ----

  async function handleFile(file) {
    if (!file) return;
    const status = $("upload-status");
    const dot = file.name.lastIndexOf(".");
    const ext = dot > 0 ? file.name.slice(dot).toLowerCase() : "";
    if (!ACCEPTED.includes(ext)) {
      setStatus(status, `Please choose a roster file of a supported type (${ACCEPTED.join(", ")}).`, "error");
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      setStatus(status, "This file is larger than the 20 MB limit.", "error");
      return;
    }
    clearRoster();
    setStatus(status, "Reading roster…");
    const started = performance.now();
    try {
      const data = await api("/api/roster", file, ext.slice(1));
      loadRoster(data.roster, data.issues, file.name);
      const secs = ((performance.now() - started) / 1000).toFixed(2);
      const n = data.roster.students.length;
      const source = data.page_count !== null
        ? `${data.page_count} page${data.page_count === 1 ? "" : "s"}`
        : SOURCE_LABELS[data.source_format] || "the file";
      setStatus(status, `Read ${n} student${n === 1 ? "" : "s"} from ${source} in ${secs} s.`, "ok");
    } catch (e) {
      setStatus(status, e.message, "error");
    } finally {
      $("file-input").value = ""; // allow choosing the same file again
    }
  }

  function clearRoster() {
    state.roster = null;
    state.issues = [];
    clearHelpOutput(false);
    state.csv = null;
    $("summary").hidden = true;
    $("tools").hidden = true;
  }

  function loadRoster(roster, issues, fileName) {
    state.roster = roster;
    state.issues = issues;
    $("file-name").textContent = fileName;
    $("course-title").value = roster.course_title;
    $("section").value = roster.section;
    $("meetings-location").value = roster.meetings_location || ""; // Help Sheet only; editable
    $("upload").classList.add("compact");
    $("summary").hidden = false;
    $("tools").hidden = false;
    renderStudents();
    $("student-details").open = issues.some((i) => i.row !== null);
    renderAfterValidation();
  }

  // ------------------------------------------------------------ Summary ----

  function renderSummary() {
    const r = state.roster;
    const waitlisted = r.students.filter((s) => s.status === "waitlist").length;
    $("student-count").textContent = String(r.students.length - waitlisted);
    const notes = [];
    if (r.expected_count !== null) {
      notes.push(r.expected_count_source === "row_numbers"
        ? `Roster rows are numbered 1–${r.expected_count}`
        : `Roster lists ${r.expected_count} enrolled`);
    }
    if (waitlisted) notes.push(`+ ${waitlisted} on the wait list`);
    $("expected-note").textContent = notes.join(" · ");
    markField("course-title", "course-hint", "course_title");
    markField("section", "section-hint", "section");
  }

  function markField(inputId, hintId, field) {
    const input = $(inputId);
    const missing = !state.roster[field];
    const uncertain = state.roster.uncertain_fields.includes(field);
    input.classList.toggle("needs-check", missing || uncertain);
    $(hintId).textContent = missing ? "Not found in the roster. Please enter it." : uncertain ? "Please check this." : "";
  }

  function onFieldInput(field, value) {
    state.roster[field] = value;
    state.roster.uncertain_fields = state.roster.uncertain_fields.filter((f) => f !== field);
    rosterChanged();
  }

  // ------------------------------------------------------------- Issues ----

  function renderIssues() {
    const list = $("issue-list");
    list.replaceChildren();
    const sorted = [...errors(), ...state.issues.filter((i) => i.severity !== "error")];
    $("issues").hidden = sorted.length === 0;
    const seen = new Set();
    let shown = 0;
    for (const issue of sorted) {
      const key = issue.code + "|" + issue.message;
      if (seen.has(key)) continue; // duplicate-W issues repeat per row
      seen.add(key);
      if (shown++ >= MAX_ISSUES_SHOWN) continue;
      const li = document.createElement("li");
      li.className = "issue " + issue.severity;
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = issue.severity === "error" ? "Fix" : "Check";
      const msg = document.createElement("span");
      msg.textContent = issue.message;
      li.append(tag, msg);
      if (issue.row !== null) {
        li.append(linkButton("Show row", () => showRow(issue.row)));
      }
      if (issue.code === "count_mismatch") {
        li.append(linkButton("I've checked: this list is correct", () => {
          if (issue.field === "expected_waitlist_count") {
            state.roster.expected_waitlist_count = null;
          } else {
            state.roster.expected_count = null;
            state.roster.expected_count_source = null;
          }
          rosterChanged(0);
        }));
      }
      list.append(li);
    }
    if (shown > MAX_ISSUES_SHOWN) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = `…and ${shown - MAX_ISSUES_SHOWN} more. Open “Review / edit students” to see all marked rows.`;
      list.append(li);
    }
  }

  function linkButton(text, onClick) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "link";
    b.textContent = text;
    b.addEventListener("click", onClick);
    return b;
  }

  function renderBlocked() {
    const n = errors().length;
    const blocked = $("blocked");
    blocked.hidden = n === 0;
    blocked.textContent = n ? `Fix the ${n} problem${n === 1 ? "" : "s"} marked above before creating a help sheet or CSV.` : "";
    for (const id of ["generate-help", "download-csv", "print-help", "download-help"]) {
      $(id).disabled = n > 0;
    }
  }

  // ----------------------------------------------------------- Students ----

  function renderStudents() {
    const body = $("student-rows");
    body.replaceChildren();
    state.roster.students.forEach((s, i) => body.append(studentRow(s, i)));
    updateRowMarks();
  }

  function studentRow(s, i) {
    const tr = document.createElement("tr");
    tr.dataset.index = String(i);
    const num = document.createElement("td");
    num.className = "num";
    num.textContent = String(i + 1);
    tr.append(num);
    for (const field of ["w_number", "last_name", "first_name"]) {
      const td = document.createElement("td");
      if (field === "w_number") td.className = "wcol";
      const input = document.createElement("input");
      input.type = "text";
      input.value = s[field];
      input.dataset.field = field;
      input.maxLength = 300;
      input.setAttribute("aria-label", `Row ${i + 1} ${field.replace("_", " ")}`);
      td.append(input);
      if (field === "last_name") {
        const note = document.createElement("div");
        note.className = "note";
        td.append(note);
      }
      tr.append(td);
    }
    const list = document.createElement("td");
    list.className = "list";
    list.textContent = s.status === "waitlist" ? "Wait List" : "Current";
    tr.append(list);
    const act = document.createElement("td");
    act.className = "act";
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "icon-btn";
    remove.dataset.action = "remove";
    remove.textContent = "Remove";
    remove.setAttribute("aria-label", `Remove row ${i + 1}`);
    act.append(remove);
    tr.append(act);
    return tr;
  }

  function updateRowMarks() {
    const byRow = new Map();
    for (const issue of state.issues) {
      if (issue.row === null) continue;
      const entry = byRow.get(issue.row) || { error: false, fields: new Map() };
      if (issue.severity === "error") entry.error = true;
      if (issue.field) {
        const prev = entry.fields.get(issue.field);
        if (prev !== "error") entry.fields.set(issue.field, issue.severity);
      }
      byRow.set(issue.row, entry);
    }
    for (const tr of $("student-rows").children) {
      const i = Number(tr.dataset.index);
      const s = state.roster.students[i];
      const entry = byRow.get(i);
      tr.classList.toggle("row-error", !!entry && entry.error);
      tr.classList.toggle("row-warning", !!entry && !entry.error);
      for (const input of tr.querySelectorAll("input")) {
        const sev = entry && entry.fields.get(input.dataset.field);
        input.classList.toggle("has-error", sev === "error");
        input.classList.toggle("needs-check", sev === "warning");
      }
      const note = tr.querySelector(".note");
      note.replaceChildren();
      if (s && s.review_note) {
        note.append(document.createTextNode(s.review_note));
        note.append(linkButton("Looks right", () => {
          s.review_note = null;
          rosterChanged(0);
        }));
      }
    }
  }

  function showRow(index) {
    $("student-details").open = true;
    const tr = $("student-rows").querySelector(`tr[data-index="${index}"]`);
    if (!tr) return;
    tr.scrollIntoView({ block: "center" });
    tr.classList.remove("flash");
    void tr.offsetWidth; // restart animation
    tr.classList.add("flash");
    const bad = tr.querySelector("input.has-error, input.needs-check") || tr.querySelector("input");
    bad.focus({ preventScroll: true });
  }

  function onStudentInput(e) {
    const input = e.target;
    if (!input.dataset.field) return;
    const i = Number(input.closest("tr").dataset.index);
    const s = state.roster.students[i];
    s[input.dataset.field] = input.value;
    if (s.review_note) {
      s.review_note = null; // the employee has looked at this row
      updateRowMarks();
    }
    s.display_name = s.last_name && s.first_name ? `${s.last_name}, ${s.first_name}` : s.last_name || s.first_name;
    rosterChanged();
  }

  function onStudentClick(e) {
    const btn = e.target.closest("button[data-action='remove']");
    if (!btn) return;
    const i = Number(btn.closest("tr").dataset.index);
    state.roster.students.splice(i, 1);
    renderStudents();
    rosterChanged(0);
  }

  function addStudent() {
    state.roster.students.push({
      w_number: "", first_name: "", last_name: "", display_name: "", review_note: null, source_page: null,
      status: "current",
    });
    renderStudents();
    rosterChanged(0);
    $("student-details").open = true;
    const last = $("student-rows").lastElementChild;
    last.scrollIntoView({ block: "center" });
    last.querySelector("input").focus({ preventScroll: true });
  }

  // --------------------------------------------------------- Validation ----

  function rosterChanged(delay = 300) {
    clearHelpOutput(true);
    state.csv = null;
    clearTimeout(state.validateTimer);
    state.validateTimer = setTimeout(validateNow, delay);
  }

  async function validateNow() {
    clearTimeout(state.validateTimer);
    const seq = ++state.validateSeq;
    try {
      const data = await api("/api/validate", { roster: state.roster });
      if (seq !== state.validateSeq) return;
      state.issues = data.issues;
      renderAfterValidation();
    } catch (e) {
      setStatus($("upload-status"), e.message, "error");
    }
  }

  function renderAfterValidation() {
    renderSummary();
    renderIssues();
    updateRowMarks();
    renderBlocked();
    if (state.tab === "csv") refreshCsv();
  }

  // ---------------------------------------------------------------- Tabs ----

  function selectTab(tab) {
    state.tab = tab;
    $("tab-help").setAttribute("aria-selected", String(tab === "help"));
    $("tab-csv").setAttribute("aria-selected", String(tab === "csv"));
    $("panel-help").hidden = tab !== "help";
    $("panel-csv").hidden = tab !== "csv";
    if (tab === "csv") refreshCsv();
  }

  // ---------------------------------------------------------- Help sheet ----

  function selectedDays() {
    return [...document.querySelectorAll("input[name='day']:checked")].map((el) => el.value);
  }

  function clearHelpOutput(showStaleNote) {
    if (state.help) {
      URL.revokeObjectURL(state.help.url);
      state.help = null;
      $("help-preview").removeAttribute("src");
      $("help-output").hidden = true;
      if (showStaleNote) setStatus($("help-status"), "Details changed. Generate the help sheet again.");
    }
  }

  async function generateHelpSheet() {
    const status = $("help-status");
    const tutor = $("tutor-name").value.trim();
    const days = selectedDays();
    if (!tutor) {
      setStatus(status, "Enter the tutor's name.", "error");
      $("tutor-name").focus();
      return;
    }
    if (!days.length) {
      setStatus(status, "Select at least one day the course meets.", "error");
      return;
    }
    if (!state.roster.course_title.trim() || !state.roster.section.trim()) {
      setStatus(status, "Enter the course and section above.", "error");
      return;
    }
    setStatus(status, "Generating…");
    await validateNow();
    if (errors().length) {
      setStatus(status, "");
      return;
    }
    try {
      const meetingsLocation = $("meetings-location").value.trim(); // optional; sent with the help sheet only
      const data = await api("/api/help-sheet", { roster: state.roster, tutor_name: tutor, meetings_location: meetingsLocation, days });
      clearHelpOutput(false);
      const bytes = Uint8Array.from(atob(data.pdf_base64), (c) => c.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
      state.help = { url, filename: data.filename };
      $("help-preview").src = url;
      $("help-output").hidden = false;
      $("help-meta").textContent = `${data.page_count} page${data.page_count === 1 ? "" : "s"}, US Letter`;
      setStatus(status, data.warnings.join(" "), data.warnings.length ? "error" : "ok");
    } catch (e) {
      if (e.data && e.data.issues) {
        state.issues = e.data.issues;
        renderAfterValidation();
      }
      setStatus(status, e.message, "error");
    }
  }

  function printHelpSheet() {
    if (!state.help) return;
    try {
      const frame = $("help-preview").contentWindow;
      frame.focus();
      frame.print();
    } catch (_) {
      window.open(state.help.url, "_blank", "noopener");
    }
  }

  function download(url, filename) {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.append(a);
    a.click();
    a.remove();
  }

  // ----------------------------------------------------------------- CSV ----

  async function refreshCsv() {
    const head = $("csv-head");
    const body = $("csv-rows");
    if (errors().length) {
      head.replaceChildren();
      body.replaceChildren();
      $("csv-meta").textContent = "";
      return;
    }
    if (state.csv) return;
    const seq = ++state.csvSeq;
    try {
      const data = await api("/api/csv", { roster: state.roster });
      if (seq !== state.csvSeq) return;
      state.csv = data;
      head.replaceChildren(...data.header.map((h) => cell("th", h)));
      body.replaceChildren(...data.rows.map((row) => {
        const tr = document.createElement("tr");
        tr.append(...row.map((v) => cell("td", v)));
        return tr;
      }));
      $("csv-meta").textContent = `${data.rows.length} student row${data.rows.length === 1 ? "" : "s"} · ${data.filename}`;
    } catch (e) {
      if (e.data && e.data.issues) {
        state.issues = e.data.issues;
        renderAfterValidation();
      }
      $("csv-meta").textContent = e.message;
    }
  }

  function cell(tag, text) {
    const el = document.createElement(tag);
    el.textContent = text;
    return el;
  }

  async function downloadCsv() {
    await validateNow();
    if (errors().length) return;
    await refreshCsv();
    if (!state.csv) return;
    const url = URL.createObjectURL(new Blob([state.csv.csv], { type: "text/csv;charset=utf-8" }));
    download(url, state.csv.filename);
    setTimeout(() => URL.revokeObjectURL(url), 10000);
  }

  // ----------------------------------------------------------- Lifecycle ----
  // The app keeps running while at least one Roster Tool tab is open
  // (roster_tool/lifecycle.py). Closing the last tab, or Quit, stops it.

  const CLIENT_ID = crypto.randomUUID ? crypto.randomUUID() : `${String(Math.random()).slice(2)}-${Date.now()}`;
  const HEARTBEAT_MS = 30000;
  let appClosed = false;

  function showClosed() {
    if (appClosed) return;
    appClosed = true;
    $("closed-banner").hidden = false;
    $("quit-btn").disabled = true;
  }

  async function heartbeat() {
    if (appClosed) return;
    try {
      await api("/api/heartbeat", { client: CLIENT_ID });
    } catch (_) {
      showClosed();
    }
  }

  function sayGoodbye() {
    if (appClosed) return;
    try {
      // keepalive lets the request finish while the tab is closing
      fetch("/api/goodbye", {
        method: "POST",
        keepalive: true,
        headers: { "X-Roster-Token": TOKEN, "Content-Type": "application/json" },
        body: JSON.stringify({ client: CLIENT_ID }),
      });
    } catch (_) {
      /* the app will notice the closed tab by its missing heartbeats */
    }
  }

  async function quitApp() {
    try {
      await api("/api/shutdown", {});
    } catch (_) {
      /* already stopped */
    }
    showClosed();
  }

  // -------------------------------------------------------------- Wiring ----

  function init() {
    $("quit-btn").addEventListener("click", quitApp);
    heartbeat();
    setInterval(heartbeat, HEARTBEAT_MS);
    window.addEventListener("pagehide", sayGoodbye);
    window.addEventListener("pageshow", (e) => {
      if (e.persisted) heartbeat(); // restored from the back/forward cache
    });

    const input = $("file-input");
    input.addEventListener("change", () => handleFile(input.files[0]));
    $("replace-btn").addEventListener("click", () => input.click());

    const zone = $("dropzone");
    // Stop the browser from opening a PDF dropped anywhere on the page.
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", (e) => e.preventDefault());
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.classList.add("dragover");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
      handleFile(e.dataTransfer.files[0]);
    });

    $("course-title").addEventListener("input", (e) => onFieldInput("course_title", e.target.value));
    $("section").addEventListener("input", (e) => onFieldInput("section", e.target.value));
    $("student-rows").addEventListener("input", onStudentInput);
    $("student-rows").addEventListener("click", onStudentClick);
    $("add-student").addEventListener("click", addStudent);

    $("tab-help").addEventListener("click", () => selectTab("help"));
    $("tab-csv").addEventListener("click", () => selectTab("csv"));

    $("generate-help").addEventListener("click", generateHelpSheet);
    $("tutor-name").addEventListener("keydown", (e) => {
      if (e.key === "Enter") generateHelpSheet();
    });
    $("tutor-name").addEventListener("input", () => clearHelpOutput(true));
    $("meetings-location").addEventListener("keydown", (e) => {
      if (e.key === "Enter") generateHelpSheet();
    });
    $("meetings-location").addEventListener("input", () => clearHelpOutput(true));
    for (const box of document.querySelectorAll("input[name='day']")) {
      box.addEventListener("change", () => clearHelpOutput(true));
    }
    $("print-help").addEventListener("click", printHelpSheet);
    $("download-help").addEventListener("click", () => state.help && download(state.help.url, state.help.filename));
    $("download-csv").addEventListener("click", downloadCsv);
  }

  init();
})();
