const $ = (s) => document.querySelector(s);
const PAGE = 60;
const ACTIVE = ["queued", "running", "paused"];  // a job in one of these holds the project

let current = null;      // loaded project
let shown = PAGE;        // how many panel cards are rendered
let selected = new Set();     // selected panels (for re-running script + voice)
let selectedCh = new Set();   // selected chapters (scope for every step); empty = all
let polling = null;
let env = null;

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers = { "Content-Type": "application/json" };
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "class") e.className = v;
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids) if (k != null) e.append(k);
  return e;
}

const fileUrl = (rel, v = current.panels_rev || 0) => `/files/${current.id}/${rel}?v=${v}`;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// In-app replacement for confirm()/alert(). choices: [{value, label, primary?, danger?}];
// resolves to the chosen value, or null for Cancel / Escape. A question asked while another is open
// replaces it (that one answers Cancel), on top of any other dialog.
let asked = 0;
function choose(title, message, choices, { cancel = true } = {}) {
  const d = $("#ask-dialog"), me = ++asked;
  if (d.open) d.close();  // opened again below, so it's the topmost dialog
  $("#ask-title").textContent = title;
  $("#ask-message").textContent = message;
  const buttons = choices.map((c) =>
    el("button", { value: c.value, class: c.danger ? "danger-solid" : c.primary ? "primary" : "" }, c.label));
  const cancelBtn = cancel ? el("button", { value: "cancel", class: "ghost" }, "Cancel") : null;
  $("#ask-actions").replaceChildren(...[cancelBtn, ...buttons].filter(Boolean));
  d.returnValue = "";
  d.showModal();
  // focus a safe choice so Enter never deletes or overwrites by accident
  (buttons.find((b) => !b.classList.contains("danger-solid")) || cancelBtn || buttons[0]).focus();
  return new Promise((resolve) => {
    const done = () => {
      if (me === asked && d.open) return;  // (the close of the question this one replaced)
      d.removeEventListener("close", done);
      resolve(me === asked && d.returnValue && d.returnValue !== "cancel" ? d.returnValue : null);
    };
    d.addEventListener("close", done);
  });
}

async function ask(title, message, { ok = "OK", danger = false, cancel = true } = {}) {
  return (await choose(title, message, [{ value: "ok", label: ok, danger, primary: !danger }], { cancel })) === "ok";
}
const notify = (message, title = "Something went wrong") => ask(title, message, { cancel: false });

// ---- projects ---------------------------------------------------------------

const TRASH_ICON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/></svg>';

async function loadProjects() {
  const list = await api("/api/projects");
  const ul = $("#projects");
  ul.replaceChildren(...list.map((p) => {
    const del = el("button", {
      class: "proj-del", title: `Delete ${p.name}`, "aria-label": `Delete project ${p.name}`,
      onclick: (e) => { e.stopPropagation(); deleteProject(p); },
    });
    del.innerHTML = TRASH_ICON;
    return el("li", { class: current && p.id === current.id ? "active" : "", onclick: () => openProject(p.id) },
      el("div", { class: "proj-text" }, p.name,
        el("small", {}, `${plural(p.chapters, "chapter")} · ${p.pages} pages · ${p.panels} panels`)),
      del);
  }));
}

async function deleteProject(p) {
  const ok = await ask(`Delete "${p.name}"?`,
    `${plural(p.chapters, "chapter")}, ${plural(p.panels, "panel")}. This removes the project from Recap Studio, ` +
    "including its panels, voice audio and rendered videos.\n\nThe folders you imported pages from are not touched.",
    { ok: "Delete project", danger: true });
  if (!ok) return;
  try { await api(`/api/projects/${p.id}`, { method: "DELETE" }); }
  catch (e) { return notify(e.message, "Couldn't delete the project"); }
  localStorage.removeItem(`sel:${p.id}`);
  if (current && current.id === p.id) {
    current = null;
    clearInterval(polling);
    localStorage.removeItem("last");
    render();
  }
  loadProjects();
}

async function openProject(id) {
  if (!current || current.id !== id) {
    shown = PAGE;
    selected.clear();
    selectedCh = new Set(JSON.parse(localStorage.getItem(`sel:${id}`) || "[]"));
  }
  current = await api(`/api/projects/${id}`);
  const ids = new Set(current.chapters.map((c) => c.id));
  selectedCh = new Set([...selectedCh].filter((c) => ids.has(c)));
  localStorage.setItem("last", id);
  render();
  loadProjects();
  if (current.job && ACTIVE.includes(current.job.status)) poll();
}

$("#new-project").addEventListener("submit", async (e) => {
  e.preventDefault();
  const p = await api("/api/projects", { method: "POST", json: { name: $("#new-name").value || "Untitled" } });
  $("#new-name").value = "";
  openProject(p.id);
});

$("#project-name").addEventListener("change", async (e) => {
  await api(`/api/projects/${current.id}`, { method: "PATCH", json: { name: e.target.value } });
  loadProjects();
});

$("#delete-project").addEventListener("click", () => deleteProject({
  id: current.id, name: current.name, chapters: current.chapters.length, panels: current.panels.length,
}));

// ---- environment -----------------------------------------------------------

let globals = null;  // app-wide settings (writer, voice, video), shared by every project

async function loadEnv() {
  const url = globals ? globals.ollama_url : "http://localhost:11434";
  env = await api(`/api/status?ollama_url=${encodeURIComponent(url)}`);
  const line = (ok, good, bad) => el("div", { class: ok ? "" : "bad" }, ok ? good : bad);
  $("#env").replaceChildren(
    line(!env.ollama_error, `Ollama: ${env.ollama_models.length} model(s)`, "Ollama: not reachable"),
    line(env.tts_ready, `Voice: ${env.voices.length} voices`, "Voice model missing (run setup.ps1)"),
    line(env.ffmpeg, "ffmpeg: found", "ffmpeg: missing"),
    line(env.gpu.available, `GPU: ${globals && !globals.use_gpu ? "available (turned off)" : "on"}`, "GPU: not available (using CPU)"),
  );
  const used = [env.gpu.ocr && `text reading on ${env.gpu.ocr}`, env.gpu.voice && `voice on ${env.gpu.voice}`].filter(Boolean);
  $("#gpu-status").textContent = env.gpu.available
    ? `An NVIDIA GPU is available.${used.length ? ` Currently: ${used.join(", ")}.` : ""}`
    : "No usable NVIDIA GPU was found, so everything runs on the CPU.";
  const v = env.video, procs = (n) => `${n} process${n > 1 ? "es" : ""}`;
  $("#video-status").textContent = !v ? ""  // an app started before this setting existed
    : v.nvenc
    ? `Video: ${v.encoder || "the GPU"}'s encoder is available. Automatic draws frames in ${procs(v.auto_gpu)} with it, or ${procs(v.auto_cpu)} with the CPU encoder (this computer has ${v.cores} cores).`
    : `Video: ffmpeg can't use a GPU encoder here (NVIDIA, AMD or Intel), so video is encoded on the CPU. Automatic draws frames in ${procs(v.auto_cpu)} (this computer has ${v.cores} cores).`;
  fillSelects();
  if (current) renderSteps();
}

function fillSelects() {
  if (!env || !globals) return;
  const s = globals;
  const models = env.ollama_models.map((m) => m.name);
  for (const sel of document.querySelectorAll(".ollama-model-select")) {
    sel.replaceChildren(
      el("option", { value: "" }, "First installed model"),
      ...models.map((m) => el("option", { value: m }, m)));
    sel.value = models.includes(s.ollama_model) ? s.ollama_model : "";
  }
  const voices = env.voices.length ? env.voices : [s.voice];
  $("#voice-select").replaceChildren(...voices.map((v) => el("option", { value: v }, v)));
  $("#voice-select").value = s.voice;
  $("#claude-model-select").replaceChildren(...env.claude.models.map((m) => el("option", { value: m.id }, m.name)));
  $("#claude-model-select").value = s.claude_model;
  const model = env.claude.models.find((m) => m.id === s.claude_model);
  $("#claude-model-note").textContent = model ? model.note : "";
  renderKey(env.claude);
}

// ---- settings: app-wide (dialog) and per project (the Video and Project settings tabs) ----

document.querySelectorAll(".open-global").forEach((b) => b.addEventListener("click", () => {
  renderGlobal();
  $("#global-dialog").showModal();
}));

function setInputs(root, values) {
  for (const input of root.querySelectorAll("[data-key]")) {
    if (document.activeElement === input) continue;
    const v = values[input.dataset.key];
    if (input.type === "checkbox") input.checked = !!v; else input.value = v ?? "";
  }
}

function renderSettings() {
  for (const box of document.querySelectorAll(".proj-settings")) setInputs(box, current.settings);
}

function renderGlobal() {
  if (!globals) return;
  setInputs($("#global-dialog"), globals);
  const claudeOn = globals.llm_provider === "claude";
  const codeOn = globals.llm_provider === "claude_code";
  const cloudOn = !!(env && env.writers && env.writers[globals.llm_provider]);
  $("#global-dialog .claude-only").hidden = !claudeOn;
  $("#global-dialog .claudecode-only").hidden = !codeOn;
  $("#global-dialog .cloud-only").hidden = !cloudOn;
  $("#global-dialog .ollama-opts").hidden = claudeOn || codeOn || cloudOn;
  if (codeOn) renderClaudeCode();
  $("#split-chapters-row").hidden = globals.video_split !== "chapters";
  $("#split-hours-row").hidden = globals.video_split !== "hours";
  fillSelects();
  if (cloudOn) renderCloud();
  renderVoice();
  renderAppLine();
  renderMagi();
  renderCharscanSettings();
  renderOcrSettings();
  renderFocusSettings();
  renderBoxSettings();
  renderUpdate();
}

// ---- subject finder for pan and zoom (U2-Net-p): downloaded on request, never shipped ----

let focusPoll = null;
async function renderFocusSettings(st = null) {
  clearTimeout(focusPoll);
  try { st = st || await api("/api/addons/focus"); } catch { return; }
  const busy = st.state === "installing";
  $("#focus-off").hidden = st.installed || busy;
  $("#focus-busy").hidden = !busy;
  $("#focus-on").hidden = !st.installed || busy;
  $("#focus-select").querySelector("option[value=subject]").disabled = !st.installed;
  $("#focus-stage").textContent = st.message;
  $("#focus-bar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
  $("#focus-error").textContent = st.state === "error" ? `${st.message}: ${st.error}` : "";
  if (busy) focusPoll = setTimeout(() => renderFocusSettings(), 700);
}
$("#focus-download").addEventListener("click", async () => {
  try { renderFocusSettings(await api("/api/addons/focus/install", { method: "POST" })); }
  catch (e) { $("#focus-error").textContent = e.message; }
});
$("#focus-cancel").addEventListener("click", async () => renderFocusSettings(await api("/api/addons/focus/cancel", { method: "POST" })));
// ---- box finder for black boxes (anime_censor_detection): downloaded on request, never shipped ----

let boxesPoll = null;
async function renderBoxSettings(st = null) {
  clearTimeout(boxesPoll);
  try { st = st || await api("/api/addons/boxes"); } catch { return; }
  const busy = st.state === "installing";
  $("#boxes-off").hidden = st.installed || busy;
  $("#boxes-busy").hidden = !busy;
  $("#boxes-on").hidden = !st.installed || busy;
  $("#boxes-stage").textContent = st.message;
  $("#boxes-bar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
  $("#boxes-error").textContent = st.state === "error" ? `${st.message}: ${st.error}` : "";
  if (busy) boxesPoll = setTimeout(() => renderBoxSettings(), 700);
}
$("#boxes-download").addEventListener("click", async () => {
  try { renderBoxSettings(await api("/api/addons/boxes/install", { method: "POST" })); }
  catch (e) { $("#boxes-error").textContent = e.message; }
});
$("#boxes-cancel").addEventListener("click", async () => renderBoxSettings(await api("/api/addons/boxes/cancel", { method: "POST" })));
$("#boxes-remove").addEventListener("click", async () => {
  if (!await ask("Remove the box finder?", "Boxes it already found stay. New pieces then get boxes only from the script " +
    "writer and from you.", { ok: "Remove", danger: true })) return;
  try { renderBoxSettings(await api("/api/addons/boxes", { method: "DELETE" })); }
  catch (e) { $("#boxes-error").textContent = e.message; }
});

$("#focus-remove").addEventListener("click", async () => {
  if (!await ask("Remove the subject finder?", "Scenes then zoom toward their middle again.", { ok: "Remove", danger: true })) return;
  try {
    renderFocusSettings(await api("/api/addons/focus", { method: "DELETE" }));
    if (globals.motion_focus === "subject") await saveGlobal({ motion_focus: "center" });
    renderGlobal();
  } catch (e) { $("#focus-error").textContent = e.message; }
});

// ---- English text-reading model: downloaded on request, never shipped ----

let ocrPoll = null;
async function renderOcrSettings(st = null) {
  clearTimeout(ocrPoll);
  try { st = st || await api("/api/addons/ocr-en"); } catch { return; }
  const busy = st.state === "installing";
  $("#ocr-off").hidden = st.installed || busy;
  $("#ocr-busy").hidden = !busy;
  $("#ocr-on").hidden = !st.installed || busy;
  $("#ocr-stage").textContent = st.message;
  $("#ocr-bar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
  $("#ocr-error").textContent = st.state === "error" ? `${st.message}: ${st.error}` : "";
  if (busy) ocrPoll = setTimeout(() => renderOcrSettings(), 700);
}
$("#ocr-download").addEventListener("click", async () => {
  try { renderOcrSettings(await api("/api/addons/ocr-en/install", { method: "POST" })); }
  catch (e) { $("#ocr-error").textContent = e.message; }
});
$("#ocr-cancel").addEventListener("click", async () => renderOcrSettings(await api("/api/addons/ocr-en/cancel", { method: "POST" })));
$("#ocr-remove").addEventListener("click", async () => {
  if (!await ask("Remove the English text model?", "Text reading goes back to the built-in model. Text already read is kept.",
    { ok: "Remove", danger: true })) return;
  try { renderOcrSettings(await api("/api/addons/ocr-en", { method: "DELETE" })); }
  catch (e) { $("#ocr-error").textContent = e.message; }
});

// ---- Magi add-on (speaker detection): downloaded on request, never shipped with the app ----

let magiPoll = null;
async function renderMagi(st = null) {
  clearTimeout(magiPoll);
  try { st = st || await api("/api/addons/magi"); } catch { return; }
  const busy = ["installing", "removing"].includes(st.state);
  $("#magi-off").hidden = st.installed || busy;
  $("#magi-busy").hidden = !busy;
  $("#magi-on").hidden = !st.installed || busy;
  $("#magi-license").textContent = st.license;
  $("#magi-install").textContent = `Download Magi (about ${st.download_gb} GB` +
    (st.device === "cuda" ? ", runs on your NVIDIA GPU)" : ", CPU version: slow)");
  $("#magi-install").disabled = !$("#magi-accept").checked;
  $("#magi-stage").textContent = [st.stage, st.message].filter(Boolean).join(" · ");
  $("#magi-bar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
  $("#magi-cancel").hidden = st.state !== "installing";
  $("#magi-info").textContent = st.installed
    ? `Installed: ${st.size_gb} GB in data\\addons\\magi, running on the ${st.device === "cuda" ? "GPU" : "CPU"}. ` +
      "It's a separate download, not part of the app. Run Read text again after adding pictures."
    : "";
  const err = $("#magi-error");
  err.replaceChildren();
  if (st.state === "error") {
    err.append(st.message, el("details", {}, el("summary", {}, "Details"), el("pre", {}, st.error || "")));
  } else if (st.message && !busy && !st.installed) {
    err.append(st.message);
  }
  if (busy) magiPoll = setTimeout(() => renderMagi(), 1000);
}
$("#magi-accept").addEventListener("change", () => { $("#magi-install").disabled = !$("#magi-accept").checked; });
$("#magi-install").addEventListener("click", async () => {
  try {
    renderMagi(await api("/api/addons/magi/install", { method: "POST", json: { accept_license: $("#magi-accept").checked } }));
  } catch (e) { $("#magi-error").textContent = e.message; }
});
$("#magi-cancel").addEventListener("click", async () => renderMagi(await api("/api/addons/magi/cancel", { method: "POST" })));
$("#magi-remove").addEventListener("click", async () => {
  if (!await ask("Remove Magi?", "Its download is deleted. Speaker guesses it already found stay with your panels.",
    { ok: "Remove", danger: true })) return;
  try { renderMagi(await api("/api/addons/magi", { method: "DELETE" })); }
  catch (e) { $("#magi-error").textContent = e.message; }
});

// ---- character scan: find the cast, review the groups, add them to the character list ----

// With the scan models installed, Characters is a step of its own (after Detect panels); without them
// the step bar is as before. Steps are numbered by what is shown.
const scanReady = () => !!(env && env.charscan && env.charscan.installed);
function renderSteps() {
  $("#step-characters").hidden = !scanReady();
  let n = 0;
  for (const b of document.querySelectorAll(".steps button[data-label]")) {
    if (!b.hidden) b.textContent = `${++n} · ${b.dataset.label}`;
  }
}
const openGroups = () => ((current.scan && current.scan.groups) || [])
  .filter((g) => g.status === "open" && g.size >= SCAN_SMALL).length;

$("#step-characters").addEventListener("click", () => {
  if (!current.panels.length) return notify("Run Detect panels first: the scan looks for characters in the panels.", "No panels yet");
  if (openGroups()) return openReview();  // finish naming what the last scan found
  renderScanDialog();
  $("#scan-dialog").showModal();
});

// Before writing: if the scan is installed and the characters aren't done (none named yet, or scanned
// groups left unnamed), offer to do that first. "Write anyway" isn't asked again until that changes.
async function charactersReady() {
  if (!scanReady() || !current.panels.length) return true;
  const named = current.characters.filter((c) => (c.name || "").trim()).length;
  const open = openGroups();
  if (named && !open) return true;
  const key = `chars-reminder:${current.id}`;
  const state = open ? `open:${current.scan.id}:${open}` : "none";
  try { if (localStorage.getItem(key) === state) return true; } catch { /* storage unavailable: ask */ }
  const choice = await choose(
    open ? `${plural(open, "scanned character")} not named yet` : "No characters yet",
    open ? "The character scan found characters you haven't named or ignored yet. Named characters, with a short description and any other looks, help the narrator tell who is who."
      : "The narrator writes better with a character list: names, a short description each, and other looks such as alter egos. Scan for characters finds them for you.",
    [{ value: "write", label: "Write anyway" }, { value: "characters", label: open ? "Review them" : "Scan for characters", primary: true }]);
  if (choice === "write") {
    try { localStorage.setItem(key, state); } catch { /* remembered for this session only */ }
    return true;
  }
  if (choice === "characters") $("#step-characters").click();
  return false;
}

const SCAN_FIRST = 5;      // chapters scanned by default
const SCAN_SMALL = 3;      // groups seen fewer times than this are hidden unless asked for
const SCAN_SECONDS = 0.05; // per panel on a GPU, measured; for the time estimate
let scanPoll = null, reviewSmall = false;

function renderScanButton() {
  const groups = (current.scan && current.scan.groups) || [];
  const open = groups.filter((g) => g.status === "open" && g.size >= SCAN_SMALL).length;
  $("#scan-review").hidden = !groups.length;
  $("#scan-review").textContent = open ? `Review scan (${open} to go)` : "Review scan";
}

$("#scan-open").addEventListener("click", () => { renderScanDialog(); $("#scan-dialog").showModal(); });
$("#scan-review").addEventListener("click", () => openReview());

async function renderScanDialog(st = null) {
  clearTimeout(scanPoll);
  try { st = st || await api("/api/addons/charscan"); } catch { return; }
  const busy = st.state === "installing";
  if (st.installed !== scanReady()) loadEnv();
  $("#scan-get").hidden = st.installed;
  $("#scan-scope").hidden = !st.installed;
  $("#scan-names-label").textContent = `Then suggest names with ${writerLabel()} (you still check each one)`;
  $("#scan-size").textContent = `about ${st.download_mb} MB`;
  $("#scan-credits").textContent = st.credits;
  $("#scan-progress").hidden = !busy;
  $("#scan-stage").textContent = st.message;
  $("#scan-bar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
  $("#scan-error").textContent = st.state === "error" ? `${st.message}: ${st.error}` : "";
  $("#scan-download").hidden = st.installed || busy;
  $("#scan-download").textContent = `Download (${st.download_mb} MB)`;
  $("#scan-cancel-dl").hidden = !busy;
  $("#scan-start").hidden = !st.installed;
  $("#scan-start").disabled = !!(current.job && ACTIVE.includes(current.job.status));
  const minutes = (ids) => {
    const n = current.panels.filter((p) => !ids || ids.includes(p.chapter)).length;
    return n * SCAN_SECONDS < 60 ? "under a minute" : `about ${Math.round(n * SCAN_SECONDS / 60)} min`;
  };
  const first = current.chapters.slice(0, SCAN_FIRST).map((c) => c.id);
  const radio = (v) => document.querySelector(`#scan-dialog input[name=scan-scope][value=${v}]`);
  radio("ticked").disabled = !selectedCh.size;
  $("#scan-ticked").textContent = `Ticked chapters (${selectedCh.size || "none ticked"})` +
    (selectedCh.size ? `, ${minutes([...selectedCh])}` : "");
  $("#scan-first").textContent = `First ${plural(first.length, "chapter")}, ${minutes(first)}`;
  $("#scan-all").textContent = `All ${plural(current.chapters.length, "chapter")}, ${minutes(null)}`;
  if (!document.querySelector("#scan-dialog input[name=scan-scope]:checked:not(:disabled)")) {
    radio(selectedCh.size ? "ticked" : "first").checked = true;
  }
  if (busy) scanPoll = setTimeout(() => renderScanDialog(), 1000);
}
$("#scan-download").addEventListener("click", async () => {
  try { renderScanDialog(await api("/api/addons/charscan/install", { method: "POST" })); }
  catch (e) { $("#scan-error").textContent = e.message; }
});
$("#scan-cancel-dl").addEventListener("click", async () => renderScanDialog(await api("/api/addons/charscan/cancel", { method: "POST" })));
$("#scan-start").addEventListener("click", async () => {
  const scope = document.querySelector("#scan-dialog input[name=scan-scope]:checked").value;
  const chapters = scope === "ticked" ? [...selectedCh]
    : scope === "first" ? current.chapters.slice(0, SCAN_FIRST).map((c) => c.id) : null;
  const steps = $("#scan-names").checked ? ["scan", "names"] : ["scan"];  // Suggest names straight after
  $("#scan-dialog").close();
  try {
    current.job = await api(`/api/projects/${current.id}/run`, { method: "POST", json: { steps, chapters } });
    renderJob();
    poll();
  } catch (e) { notify(e.message); }
});

function openReview() {
  renderReview();
  if (!$("#review-dialog").open) $("#review-dialog").showModal();
}
$("#review-small").addEventListener("change", (e) => { reviewSmall = e.target.checked; renderReview(); });
$("#review-rescan").addEventListener("click", () => {
  $("#review-dialog").close();
  renderScanDialog();
  $("#scan-dialog").showModal();
});
$("#review-clear").addEventListener("click", async () => {
  if (!await ask("Clear the scan results?", "Characters you already added stay in the list.", { ok: "Clear", danger: true })) return;
  const p = await api(`/api/projects/${current.id}/scan`, { method: "DELETE" });
  current.scan = p.scan;
  $("#review-dialog").close();
  renderScanButton();
});

// Suggest names: the script's writer looks at each open group's pictures and what is said around it
// (naming.py); its suggestions fill in the cards, and Add all suggested adds every suggested card that has a name.
$("#review-names").addEventListener("click", async () => {
  const n = openGroups();
  if (!n) return notify("Every group is already added or ignored. Reopen one, or scan again.", "Nothing to name");
  if (!await ask(`Suggest names with ${writerLabel()}?`,
    `It's sent each group's pictures and the lines read around its appearances (${plural(Math.min(n, 30), "group")}, ` +
    "biggest first), in one request, or a few for services that take few pictures. Its suggestions fill in the cards " +
    "for you to check; nothing is added until you say so.", { ok: "Suggest names" })) return;
  try {
    current.job = await api(`/api/projects/${current.id}/run`, { method: "POST", json: { steps: ["names"] } });
    $("#review-dialog").close();
    renderJob();
    poll();
  } catch (e) { notify(e.message); }
});
$("#review-add-all").addEventListener("click", async () => {
  const items = [...reviewFields.values()].map((f) => f()).filter((it) => it.suggested && !it.extra && it.name.trim())
    .sort((a, b) => b.size - a.size);
  if (!items.length) return notify("None of the suggested cards has a name.", "Nothing to add");
  const by = new Map();
  for (const it of items) by.set(it.name.trim(), [...(by.get(it.name.trim()) || []), it.group.slice(1)]);
  const list = [...by].map(([name, ids]) => `${name} (group${ids.length > 1 ? "s" : ""} ${ids.join(", ")})`).join("; ");
  if (!await ask(`Add ${plural(items.length, "group")} as named?`, `${list}. A name already on the list adds the group ` +
    "to that character. Cards without a name, and groups the writer called extras, are left here.", { ok: "Add all" })) return;
  try {
    const p = await api(`/api/projects/${current.id}/scan/add-named`, { method: "POST", json: { items } });
    current.scan = p.scan;
    current.characters = p.characters;
    renderReview();
    renderScanButton();
    renderCharacters();
  } catch (e) { notify(e.message); }
});

let reviewFields = new Map();  // group id -> what its card says now (for Add all suggested)

function renderReview() {
  const scan = current.scan || { groups: [], chapters: [] };
  const groups = scan.groups.filter((g) => g.status !== "merged");
  const open = groups.filter((g) => g.status === "open");
  const small = open.filter((g) => g.size < SCAN_SMALL);
  const shown = open.filter((g) => reviewSmall || g.size >= SCAN_SMALL);
  const suggested = shown.filter((g) => g.suggestion);
  $("#review-summary").textContent = `${plural(open.length - small.length, "character")} to review, ` +
    `from ${plural(scan.chapters.length, "chapter")} (${scan.seen || 0} appearances found).` +
    (suggested.length ? ` Names suggested by ${suggested[0].suggestion.by}: check them before adding.` : "");
  $("#review-small-label").textContent = `Show small groups (${small.length})`;
  $("#review-names").textContent = suggested.length ? "Suggest names again" : "Suggest names";
  $("#review-names").disabled = !!(current.job && ACTIVE.includes(current.job.status));
  $("#review-names").title = `${writerLabel()} (Settings → Script) reads each group's pictures and the lines around it, and suggests who it is.`;
  $("#review-add-all").hidden = !suggested.some((g) => !g.suggestion.extra && (g.suggestion.name || g.suggestion.character));
  reviewFields = new Map();
  $("#review-list").replaceChildren(...(shown.length ? shown.map((g) => reviewCard(g, groups))
    : [el("p", { class: "muted" }, open.length ? "Only small groups are left." : "Nothing left to review.")]));
  const done = groups.filter((g) => g.status !== "open");
  $("#review-done").replaceChildren(...(done.length ? [el("span", { class: "field-head" }, "Done"), ...done.map((g) =>
    el("div", { class: "done-row" },
      g.snapshots.length ? el("img", { src: fileUrl(g.snapshots[0].file) }) : null,
      `Group ${g.id.slice(1)}: ${g.status === "added" ? `added to ${whoLabel(g.character, g.look)}`
        : g.why === "credits" ? "set aside: seen mostly on the scan group's pages (left out of the video)" : "ignored"}`,
      el("button", { type: "button", class: "ghost small", title: "Back to the list above (a character it was added to stays)",
        onclick: () => reviewAct(g, "reopen") }, "Reopen")))] : []));
}

function reviewCard(g, groups) {
  const sug = g.suggestion;  // the writer's suggestion (Suggest names), if asked
  const chips = g.traits.map((t) => ({ ...t, on: true }));
  const name = el("input", { placeholder: "Name (of the character, or of the look)" });
  const aliases = el("input", { class: "aliases", placeholder: "Other names, comma separated (spellings, nicknames, fake names)" });
  const gender = el("select", {}, ...[["", "Gender"], ["female", "Female"], ["male", "Male"]].map(([v, l]) => el("option", { value: v }, l)));
  gender.value = (sug && sug.gender) || g.gender || "";
  const bigger = sug && sug.same_as && groups.find((o) => o.id === sug.same_as);
  const listed = sug && sug.character && current.characters.find((c) => c.id === sug.character);
  if (sug) {
    name.value = sug.name || (listed ? listed.name : "") || (bigger && bigger.suggestion ? bigger.suggestion.name : "");
    aliases.value = sug.aliases;
  }
  const desc = el("textarea", { rows: 2, placeholder: "Short description (appearance, role)" });
  let edited = false;
  const update = () => {
    if (!edited) desc.value = [sug && sug.description, describeTraits(chips.filter((c) => c.on))].filter(Boolean).join(" ");
  };
  desc.addEventListener("input", () => { edited = true; });
  const chipRow = el("div", { class: "chips" }, ...chips.map((c) => {
    const b = el("button", { type: "button", class: "chip on", title: "Click to leave this out of the description" }, c.label);
    b.addEventListener("click", () => { c.on = !c.on; b.classList.toggle("on", c.on); update(); });
    return b;
  }));
  update();
  const named = current.characters.filter((c) => c.id && c.name.trim());
  const toExisting = el("select", {}, el("option", { value: "" }, "Existing character…"),
    ...named.flatMap((c) => [el("option", { value: c.id }, c.name),
      ...(c.looks || []).filter((lk) => lk.id).map((lk) => el("option", { value: `${c.id}:${lk.id}` }, `${c.name}: ${lk.label || "another look"}`))]));
  const target = () => { const [character, look] = toExisting.value.split(":"); return { character, look: look || null }; };
  const others = groups.filter((o) => o.id !== g.id && o.status === "open");
  const sameAs = el("select", {}, el("option", { value: "" }, "Another group…"),
    ...others.map((o) => el("option", { value: o.id }, `Group ${o.id.slice(1)} (${o.size})`)));
  const hints = [];
  // merging keeps the bigger group and folds the smaller one into it, whichever card it's done from
  const mergeWith = (o) => (o.size > g.size ? reviewAct(g, "merge", { into: o.id }) : reviewAct(o, "merge", { into: g.id }));
  if (g.match && named.some((c) => c.id === g.match.character)) {
    hints.push(el("button", { type: "button", class: "small",
      onclick: () => reviewAct(g, "add", { character: g.match.character, look: g.match.look || null, description: desc.value }) },
      `${g.match.sure ? "Looks like" : "Might be"} ${whoLabel(g.match.character, g.match.look)}: add to them`));
  }
  for (const s of g.similar || []) {
    const o = others.find((x) => x.id === s.group);
    if (o) hints.push(el("button", { type: "button", class: "ghost small", onclick: () => mergeWith(o) },
      `Same as group ${o.id.slice(1)}?`));
  }
  if (listed && !(g.match && g.match.character === listed.id)) {
    hints.push(el("button", { type: "button", class: "small",
      onclick: () => reviewAct(g, "add", { character: listed.id, description: desc.value, aliases: aliases.value }) },
      `The writer says ${listed.name}: add to them`));
  }
  if (bigger && bigger.status === "open" && !(g.similar || []).some((x) => x.group === bigger.id)) {
    hints.push(el("button", { type: "button", class: "ghost small", onclick: () => mergeWith(bigger) },
      `Same as group ${bigger.id.slice(1)}?`));
  }
  if (sug && sug.extra) {
    hints.push(el("button", { type: "button", class: "ghost small", onclick: () => reviewAct(g, "ignore") }, "An extra: ignore"));
  }
  reviewFields.set(g.id, () => ({ group: g.id, size: g.size, suggested: !!sug, extra: !!(sug && sug.extra),
    name: name.value, gender: gender.value, aliases: aliases.value, description: desc.value }));
  const add = el("button", { type: "button", class: "primary", onclick: () => {
    if (!name.value.trim()) { name.placeholder = "Name it first"; name.focus(); return; }
    reviewAct(g, "add", { name: name.value, gender: gender.value, description: desc.value, aliases: aliases.value });
  } }, "Add as new character");
  return el("div", { class: "review-card" },
    el("div", { class: "snaps" }, ...g.snapshots.map((s) => el("img", { src: fileUrl(s.file), loading: "lazy", onclick: () => lightbox(fileUrl(s.file)) }))),
    el("div", { class: "review-meta" }, el("b", {}, `Group ${g.id.slice(1)}`),
      ` · ${plural(g.size, "appearance")} in ${plural(g.pieces, "panel")} · ${g.chapters.join(", ")}`, ...hints),
    sug ? el("div", { class: "suggested" }, `${sug.sure ? "Suggested" : "A guess"}: `,
      el("b", {}, sug.name || (listed && listed.name) || (sug.extra ? "an extra" : "no name found")),
      sug.extra && (sug.name || listed) ? " · an extra" : "", bigger ? ` · same as group ${bigger.id.slice(1)}` : "",
      sug.aliases ? ` · also ${sug.aliases}` : "", el("span", { class: "muted" }, ` (${sug.by})`)) : null,
    chipRow,
    el("div", { class: "review-fields" }, name, gender, aliases, desc),
    el("div", { class: "review-actions" }, add,
      el("span", { class: "or" }, "or add to"), toExisting,
      el("button", { type: "button", onclick: () => toExisting.value && reviewAct(g, "add", { ...target(), description: desc.value, aliases: aliases.value }) }, "Add"),
      el("button", { type: "button", title: "An alter ego, disguise, transformation or younger self of that character. The Name box above names the look.",
        onclick: () => {
          if (!toExisting.value) { toExisting.focus(); return; }
          reviewAct(g, "add", { character: target().character, new_look: true, name: name.value, description: desc.value });
        } }, "As another look"),
      el("span", { class: "or" }, "or merge with"), sameAs,
      el("button", { type: "button", onclick: () => sameAs.value && mergeWith(others.find((o) => o.id === sameAs.value)) }, "Merge"),
      el("button", { type: "button", class: "ghost", onclick: () => reviewAct(g, "ignore") }, "Ignore")));
}

async function reviewAct(g, action, body = {}) {
  try {
    const p = await api(`/api/projects/${current.id}/scan/groups/${g.id}/${action}`, { method: "POST", json: body });
    current.scan = p.scan;
    current.characters = p.characters;
    renderReview();
    renderScanButton();
    renderCharacters();
  } catch (e) { notify(e.message); }
}

// A plain description from the suggested traits, e.g. "Short blonde hair in a bun, blue eyes, glasses."
function describeTraits(traits) {
  const of = (kind) => traits.filter((t) => t.kind === kind).map((t) => t.tag);
  const plain = (t) => t.replace(/_/g, " ");
  const STYLE = { hair_bun: "in a bun", single_hair_bun: "in a bun", double_bun: "in two buns", ponytail: "in a ponytail",
    twintails: "in twin tails", braid: "in a braid", bob_cut: "in a bob", messy_hair: "messy" };
  const BUILD = { petite: "petite", tall: "tall", muscular: "muscular", plump: "plump", child: "a child",
    old_man: "an older man", old_woman: "an older woman" };
  const parts = [];
  const color = of("hair_color")[0], length = of("hair_length")[0];
  const style = [...new Set(of("hair_style").map((x) => STYLE[x]).filter(Boolean))];
  if (color || length || style.length) {
    parts.push([length && plain(length).replace(" hair", ""), color && plain(color).replace(" hair", ""), "hair", ...style]
      .filter(Boolean).join(" "));
  }
  parts.push(...of("eyes").slice(0, 1).map(plain), ...of("face").map(plain), ...of("accessory").map(plain),
    ...of("build").map((b) => BUILD[b] || plain(b)));
  let text = parts.join(", ");
  const clothes = of("clothing").map(plain);
  if (clothes.length) text += `${text ? ". " : ""}Often seen in: ${clothes.join(", ")}`;
  return text ? `${text[0].toUpperCase()}${text.slice(1)}.` : "";
}

async function renderCharscanSettings() {
  let st;
  try { st = await api("/api/addons/charscan"); } catch { return; }
  $("#cs-info").textContent = st.installed ? `Installed (${st.download_mb} MB). ${st.credits}`
    : `Not downloaded (${st.download_mb} MB). Scan for characters offers the download.`;
  $("#cs-remove").hidden = !st.installed;
}
$("#cs-remove").addEventListener("click", async () => {
  if (!await ask("Remove the character scan models?", "They can be downloaded again from Scan for characters. Scan results and characters you added stay.",
    { ok: "Remove", danger: true })) return;
  try { await api("/api/addons/charscan", { method: "DELETE" }); } catch (e) { notify(e.message); }
  renderCharscanSettings();
  loadEnv();
});

function writerLabel() {
  const service = env.writers && env.writers[globals.llm_provider];
  if (globals.llm_provider === "claude_code") return `Claude Code (${globals.claude_code_model === "sonnet" ? "Sonnet" : "Opus"})`;
  return globals.llm_provider === "claude"
    ? (env.claude.models.find((m) => m.id === globals.claude_model) || { name: globals.claude_model }).name
    : service ? `${service.name} (${globals[`${globals.llm_provider}_model`] || service.default_model || "no model chosen"})`
    : `local model${globals.ollama_model ? ` (${globals.ollama_model})` : ""}`;
}

function renderAppLine() {
  if (!globals || !env) return;
  const writer = writerLabel();
  const style = { motion: ", pan and zoom", stills: ", stills" }[globals.video_style] || "";
  const fmt = `${globals.resolution || 1080}p ${globals.orientation === "portrait" ? "portrait" : "landscape"} video${style}`;
  const btn = el("button", { type: "button", class: "link-btn", onclick: () => { renderGlobal(); $("#global-dialog").showModal(); } }, "change");
  const voice = { edge: `Edge voice ${globals.edge_voice}`, elevenlabs: "ElevenLabs voice" }[globals.voice_provider] || `voice ${globals.voice}`;
  $("#app-line").replaceChildren(`Script by ${writer} · ${voice} · ${fmt} (`, btn, ")");
}

function readInput(input) {
  let v = input.type === "checkbox" ? input.checked : input.value;
  if (input.type === "number" || input.dataset.type === "number") v = Number(v);
  return v;
}

document.querySelectorAll(".proj-settings [data-key]").forEach((input) => {
  input.addEventListener("change", async () => {
    const p = await api(`/api/projects/${current.id}/settings`, { method: "PUT", json: { [input.dataset.key]: readInput(input) } });
    current.settings = p.settings;
    if (input.dataset.key === "youtube_safe") renderPanels();
  });
});

document.querySelectorAll("#global-dialog [data-key]").forEach((input) => {
  input.addEventListener("change", async () => {
    globals = await api("/api/settings", { method: "PUT", json: { [input.dataset.key]: readInput(input) } });
    if (current) Object.assign(current.settings, globals);
    if (["ollama_url", "use_gpu"].includes(input.dataset.key)) await loadEnv();
    renderGlobal();
  });
});

// Claude Code as the script writer (claude_code.py): found on this PC, and logged in to the user's Claude account?
async function renderClaudeCode(refresh = false) {
  const box = $("#cc-status");
  if (refresh) box.textContent = "Checking…";
  let st;
  try { st = await api(`/api/claude-code${refresh ? "?refresh=true" : ""}`); }
  catch (e) { box.className = "bad"; box.textContent = e.message; return; }
  $("#cc-login").hidden = !st.found || st.logged_in;
  box.className = st.found && st.logged_in && st.skill ? "ok" : "bad";
  box.textContent = !st.found ? "Claude Code wasn't found on this PC. Install it (claude.com/code), or the Claude desktop app, which includes it."
    : !st.logged_in ? `Claude Code ${st.version} is here but not logged in.`
    : !st.skill ? "Claude Code is ready, but the recap-studio-narration skill isn't next to the app (.claude\skills, beside the app's folder): its agents need the brief there."
    : `Claude Code ${st.version}, logged in. Ready.`;
}
$("#cc-check").addEventListener("click", () => renderClaudeCode(true));
$("#cc-login").addEventListener("click", async () => {
  try {
    await api("/api/claude-code/login", { method: "POST" });
    $("#cc-status").className = "muted";
    $("#cc-status").textContent = "Finish signing in in the window and browser that opened, then press Check again.";
  } catch (e) { notify(e.message); }
});

// The key itself never comes back from the server; the page only learns whether one is stored.
function renderKey(st, message = null, ok = null) {
  const has = st.saved || st.env;
  $("#key-entry").hidden = has && !$("#key-entry").dataset.replacing;
  $("#key-actions").hidden = !has;
  $("#remove-key").hidden = !st.saved;
  const status = $("#key-status");
  status.className = ok === true ? "ok" : ok === false ? "bad" : "muted";
  status.textContent = message || (st.saved ? "A key is saved on this PC."
    : st.env ? "Using the ANTHROPIC_API_KEY environment variable." : "No key yet. Paste one from console.anthropic.com.");
}

$("#save-key").addEventListener("click", async () => {
  const key = $("#claude-key").value.trim();
  if (!key) return;
  try {
    const r = await api("/api/claude/key", { method: "PUT", json: { key } });
    $("#claude-key").value = "";
    delete $("#key-entry").dataset.replacing;
    env.claude = { ...env.claude, saved: r.saved, env: r.env };
    renderKey(env.claude, r.ok ? "Key saved and working." : `Key saved, but: ${r.message}`, r.ok);
  } catch (e) { renderKey(env.claude, e.message, false); }
});
$("#check-key").addEventListener("click", async () => {
  renderKey(env.claude, "Checking…");
  const r = await api("/api/claude/check", { method: "POST" });
  renderKey(env.claude, r.message, r.ok);
});
$("#change-key").addEventListener("click", () => {
  $("#key-entry").dataset.replacing = "1";
  renderKey(env.claude);
  $("#claude-key").focus();
});
$("#remove-key").addEventListener("click", async () => {
  if (!await ask("Remove the API key?", "The saved key is deleted from this PC. Claude can't write scripts until you add one again.",
    { ok: "Remove", danger: true })) return;
  const r = await api("/api/claude/key", { method: "DELETE" });
  env.claude = { ...env.claude, ...r };
  renderKey(env.claude);
});

// ---- OpenRouter, Google Gemini, DeepSeek, Groq, Mistral: model and API key ----
// Same idea as Claude's key: the key itself never comes back to the page.

const cloudModels = {};  // provider -> its model list, once loaded: [{id, name, vision, price_in, price_out}]

function money(x) {
  return x < 0.01 ? "under 1¢" : x < 1 ? `${Math.round(x * 100)}¢` : `$${x.toFixed(2)}`;
}

function cloudModelNote(p, id) {
  const list = cloudModels[p];
  if (!id) return "Choose a model.";
  const m = list && list.find((x) => x.id === id);
  if (list && !m) return "This name isn't in the service's model list; check the spelling.";
  const parts = [];
  if ((m && m.vision === true) || (!m && p === "gemini")) parts.push("Reads images.");
  else if (m && m.vision === false) parts.push("Doesn't read images: it will work from the text read from the pages alone, which is much less accurate.");
  else parts.push("If this model can't read images, it works from the text read from the pages alone.");
  // a webtoon chapter here was about 70K tokens in and 5K out with Claude; other models count a little differently
  if (m && m.price_in != null && m.price_out != null) {
    parts.push(`About ${money((m.price_in * 70000 + m.price_out * 5000) / 1e6)} per chapter ($${m.price_in} in / $${m.price_out} out per million tokens).`);
  }
  return parts.join(" ");
}

function renderCloud(message = null, ok = null) {
  const p = globals.llm_provider, w = env.writers[p];
  const model = globals[`${p}_model`] || w.default_model || "";
  $("#cloud-about").textContent = w.about;
  if (document.activeElement !== $("#cloud-model")) $("#cloud-model").value = model;
  $("#cloud-model-list").replaceChildren(...(cloudModels[p] || []).map((m) => el("option", { value: m.id },
    `${m.name}${m.vision === false ? " (no images)" : ""}${m.price_in != null ? `, $${m.price_in} / $${m.price_out} per M tokens` : ""}`)));
  $("#cloud-model-note").textContent = cloudModelNote(p, model);
  const has = w.saved || w.env;
  $("#cloud-key-entry").hidden = has && !$("#cloud-key-entry").dataset.replacing;
  $("#cloud-key-actions").hidden = !has;
  $("#cloud-remove-key").hidden = !w.saved;
  const status = $("#cloud-key-status");
  status.className = ok === true ? "ok" : ok === false ? "bad" : "muted";
  status.textContent = message || (w.saved ? "A key is saved on this PC."
    : w.env ? `Using the ${w.env_name} environment variable.` : `No key yet. Get one at ${w.keys_url.replace("https://", "")}.`);
  $("#cloud-key-hint").textContent = `Stored only on this PC (data\\${p}_key.txt). Billed to your ${w.name} account.`;
  if (has && !cloudModels[p]) loadCloudModels(p);
}

async function loadCloudModels(p) {
  if (cloudModels[`${p}:loading`]) return;
  cloudModels[`${p}:loading`] = true;
  try {
    cloudModels[p] = (await api(`/api/writers/${p}/models`)).models;
  } catch (e) {
    cloudModels[p] = [];
    if (globals.llm_provider === p) $("#cloud-model-note").textContent = e.message;
  } finally {
    delete cloudModels[`${p}:loading`];
  }
  if (globals.llm_provider === p) renderCloud();
}

$("#cloud-model").addEventListener("change", async () => {
  const p = globals.llm_provider;
  globals = await api("/api/settings", { method: "PUT", json: { [`${p}_model`]: $("#cloud-model").value.trim() } });
  if (current) Object.assign(current.settings, globals);
  $("#cloud-test-result").textContent = "";
  renderCloud();
  renderAppLine();
});

// one tiny request: a model that's overloaded, has no allowance on this key or isn't available shows up now
async function testModel(p, model, out) {
  if (!model) return;
  out.className = "hint";
  out.textContent = "Testing…";
  try {
    const r = await api(`/api/writers/${p}/test`, { method: "POST", json: { model } });
    out.className = r.ok ? "ok" : "bad";
    out.textContent = r.message;
  } catch (e) { out.className = "bad"; out.textContent = e.message; }
}
$("#cloud-test-model").addEventListener("click", () =>
  testModel(globals.llm_provider, $("#cloud-model").value.trim(), $("#cloud-test-result")));
$("#cloud-save-key").addEventListener("click", async () => {
  const p = globals.llm_provider, key = $("#cloud-key").value.trim();
  if (!key) return;
  try {
    const r = await api(`/api/writers/${p}/key`, { method: "PUT", json: { key } });
    $("#cloud-key").value = "";
    delete $("#cloud-key-entry").dataset.replacing;
    Object.assign(env.writers[p], { saved: r.saved, env: r.env });
    delete cloudModels[p];
    renderCloud(r.ok ? "Key saved and working." : `Key saved, but: ${r.message}`, r.ok);
  } catch (e) { renderCloud(e.message, false); }
});
$("#cloud-check-key").addEventListener("click", async () => {
  const p = globals.llm_provider;
  renderCloud("Checking…");
  const r = await api(`/api/writers/${p}/check`, { method: "POST" });
  renderCloud(r.message, r.ok);
});
$("#cloud-change-key").addEventListener("click", () => {
  $("#cloud-key-entry").dataset.replacing = "1";
  renderCloud();
  $("#cloud-key").focus();
});
$("#cloud-remove-key").addEventListener("click", async () => {
  const p = globals.llm_provider, w = env.writers[p];
  if (!await ask("Remove the API key?", `The saved key is deleted from this PC. ${w.name} can't write scripts until you add one again.`,
    { ok: "Remove", danger: true })) return;
  Object.assign(w, await api(`/api/writers/${p}/key`, { method: "DELETE" }));
  renderCloud();
});

// ---- voice: Kokoro on this computer, or Microsoft Edge's / ElevenLabs' online voices ----
// Like the writers' keys, the ElevenLabs key never comes back to the page.

const voiceLists = {};  // "edge" -> [{id, name, locale, gender}]; "elevenlabs" -> {voices, models}

async function loadVoiceList(kind) {
  if (voiceLists[`${kind}:loading`]) return;
  voiceLists[`${kind}:loading`] = true;
  try {
    const r = await api(`/api/voices/${kind}`);
    voiceLists[kind] = kind === "edge" ? r.voices : r;
  } catch (e) {
    voiceLists[kind] = kind === "edge" ? [] : { voices: [], models: [] };
    $("#voice-note").textContent = e.message;
  } finally {
    delete voiceLists[`${kind}:loading`];
  }
  renderVoice();
}

function fillVoiceSelect(sel, options, value, empty) {
  const opts = options.map(([id, label]) => el("option", { value: id }, label));
  if (value && !options.some(([id]) => id === value)) opts.unshift(el("option", { value }, value));
  if (!opts.length) opts.push(el("option", { value: "" }, empty));
  sel.replaceChildren(...opts);
  sel.value = value || (options[0] ? options[0][0] : "");
}

function renderVoice(message = null, ok = null) {
  if (!globals || !env) return;
  const p = globals.voice_provider || "kokoro";
  $("#global-dialog .voice-kokoro").hidden = p !== "kokoro";
  $("#global-dialog .voice-edge").hidden = p !== "edge";
  $("#global-dialog .voice-eleven").hidden = p !== "elevenlabs";
  if (!message) $("#voice-note").textContent = p === "elevenlabs" ? "ElevenLabs takes speeds from 0.7 to 1.2." : "";
  if (p === "edge") {
    if (!voiceLists.edge) loadVoiceList("edge");
    fillVoiceSelect($("#edge-voice-select"), (voiceLists.edge || []).map((v) => [v.id, `${v.name} (${v.locale}, ${v.gender.toLowerCase()})`]),
      globals.edge_voice, "Loading voices…");
  }
  if (p === "elevenlabs") {
    const st = env.elevenlabs || {}, has = st.saved || st.env;
    $("#eleven-key-entry").hidden = has && !$("#eleven-key-entry").dataset.replacing;
    $("#eleven-key-actions").hidden = !has;
    $("#eleven-remove-key").hidden = !st.saved;
    const status = $("#eleven-key-status");
    status.className = ok === true ? "ok" : ok === false ? "bad" : "muted";
    status.textContent = message || (st.saved ? "A key is saved on this PC."
      : st.env ? `Using the ${st.env_name} environment variable.` : "No key yet.");
    if (has && !voiceLists.elevenlabs) loadVoiceList("elevenlabs");
    const lists = voiceLists.elevenlabs || { voices: [], models: [] };
    fillVoiceSelect($("#eleven-voice-select"), lists.voices.map((v) => [v.id, `${v.name}${v.category && v.category !== "premade" ? ` (${v.category})` : ""}`]),
      globals.elevenlabs_voice, has ? "Loading voices…" : "Add a key to see the voices");
    fillVoiceSelect($("#eleven-model-select"), lists.models.map((m) => [m.id, `${m.name}${m.cost && m.cost !== 1 ? ` (${m.cost}× credits)` : ""}`]),
      globals.elevenlabs_model, "eleven_multilingual_v2");
  }
}

$("#voice-sample").addEventListener("click", async () => {
  const b = $("#voice-sample"), p = globals.voice_provider || "kokoro";
  const voice = { kokoro: globals.voice, edge: globals.edge_voice, elevenlabs: globals.elevenlabs_voice }[p];
  b.disabled = true;
  b.textContent = "Making a sample…";
  try {
    const r = await fetch("/api/voice/sample", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice, speed: Number(globals.speed), provider: p, model: globals.elevenlabs_model || "" }) });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    new Audio(URL.createObjectURL(await r.blob())).play();
  } catch (e) { notify(e.message); }
  b.disabled = false;
  b.textContent = "▶ Play a sample";
});
$("#eleven-save-key").addEventListener("click", async () => {
  const key = $("#eleven-key").value.trim();
  if (!key) return;
  try {
    const r = await api("/api/voices/elevenlabs/key", { method: "PUT", json: { key } });
    $("#eleven-key").value = "";
    delete $("#eleven-key-entry").dataset.replacing;
    env.elevenlabs = { ...env.elevenlabs, saved: r.saved, env: r.env };
    delete voiceLists.elevenlabs;
    renderVoice(r.ok ? `Key saved and working. ${r.message.replace(/^Key works:? ?/, "")}` : `Key saved, but: ${r.message}`, r.ok);
  } catch (e) { renderVoice(e.message, false); }
});
$("#eleven-check-key").addEventListener("click", async () => {
  renderVoice("Checking…");
  const r = await api("/api/voices/elevenlabs/check", { method: "POST" });
  renderVoice(r.message, r.ok);
});
$("#eleven-change-key").addEventListener("click", () => {
  $("#eleven-key-entry").dataset.replacing = "1";
  renderVoice();
  $("#eleven-key").focus();
});
$("#eleven-remove-key").addEventListener("click", async () => {
  if (!await ask("Remove the API key?", "The saved ElevenLabs key is deleted from this PC. Its voices can't be used until you add one again.",
    { ok: "Remove", danger: true })) return;
  env.elevenlabs = { ...env.elevenlabs, ...await api("/api/voices/elevenlabs/key", { method: "DELETE" }) };
  delete voiceLists.elevenlabs;
  renderVoice();
});

// ---- prompt & characters ---------------------------------------------------

async function saveSettings(changes) {
  const p = await api(`/api/projects/${current.id}/settings`, { method: "PUT", json: changes });
  current.settings = p.settings;
  if (!$("#p-preview-out").hidden) showPreview();
}

function autosize(t) { t.style.height = "auto"; t.style.height = `${t.scrollHeight + 2}px`; }

function listEditor(box, key, numbered) {
  const items = current.settings[key];
  box.replaceChildren(...items.map((text, i) => {
    const t = el("textarea", { rows: 1 });
    t.value = text;
    t.addEventListener("input", () => autosize(t));
    t.addEventListener("change", () => { items[i] = t.value; saveSettings({ [key]: items }); });
    requestAnimationFrame(() => autosize(t));
    return el("div", { class: "list-row" },
      el("span", { class: "num" }, numbered ? `${i + 1}.` : "•"), t,
      el("button", { class: "ghost danger", title: "Remove", onclick: () => {
        items.splice(i, 1);
        saveSettings({ [key]: items }).then(() => renderPrompt(true));
      } }, "✕"));
  }));
}

function addListItem(key, box) {
  current.settings[key].push("");
  renderPrompt(true);
  const inputs = box.querySelectorAll("textarea");
  inputs[inputs.length - 1].focus();
}
$("#p-add-rule").addEventListener("click", () => addListItem("rules", $("#p-rules")));
$("#p-add-style").addEventListener("click", () => addListItem("styles", $("#p-styles")));

$("#p-persona").addEventListener("change", (e) => saveSettings({ persona: e.target.value }));
$("#p-note").addEventListener("change", (e) => saveSettings({ note: e.target.value }));
$("#p-persona").addEventListener("input", (e) => autosize(e.target));

$("#p-reset").addEventListener("click", async () => {
  if (!await ask("Reset the prompt?", "Persona, rules and style go back to the built-in defaults. Your note and characters are kept.",
    { ok: "Reset" })) return;
  const p = await api(`/api/projects/${current.id}/reset-prompt`, { method: "POST" });
  current.settings = p.settings;
  renderPrompt(true);
  if (!$("#p-preview-out").hidden) showPreview();
});

async function showPreview() {
  const r = await api(`/api/projects/${current.id}/prompt-preview`);
  const out = $("#p-preview-out");
  out.textContent = `What the model receives for the first batch (panel images are attached separately):\n\n${r.system}\n\n${r.user}`;
  out.hidden = false;
}
$("#p-preview").addEventListener("click", () => {
  if ($("#p-preview-out").hidden) showPreview(); else $("#p-preview-out").hidden = true;
});

async function saveCharacters() {
  const p = await api(`/api/projects/${current.id}/characters`, { method: "PUT", json: { characters: current.characters } });
  current.characters = p.characters;
  if (!$("#p-preview-out").hidden) showPreview();
}

function renderCharacters() {
  $("#chars").replaceChildren(...current.characters.map((c, i) => {
    const input = (field, placeholder, cls = "") => {
      const e = el("input", { placeholder, class: cls, value: c[field] || "" });
      e.addEventListener("change", () => { c[field] = e.value; saveCharacters(); });
      return e;
    };
    const desc = el("textarea", { rows: 2, class: "wide", placeholder: "Appearance and role, e.g. blonde office worker, the main character" });
    desc.value = c.description || "";
    desc.addEventListener("change", () => { c.description = desc.value; saveCharacters(); });
    const card = el("div", { class: "char-card" },
      input("name", "Name"),
      input("gender", "Gender (optional)"),
      el("button", { class: "ghost danger remove", title: "Remove character", onclick: () => {
        current.characters.splice(i, 1);
        saveCharacters().then(renderCharacters);
      } }, "✕"),
      input("aliases", "Also appears in text as (comma-separated, e.g. how OCR misreads the name)", "wide"),
      desc,
      characterPictures(c),
      characterLooks(c));
    // dropping images here adds pictures (and keeps them away from the page drop zone)
    card.addEventListener("dragover", (e) => { e.preventDefault(); e.stopPropagation(); });
    card.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); addPictures(c, [...e.dataTransfer.files]); });
    return card;
  }));
}

// Other looks of a character: an alter ego, disguise, transformation or younger self. Each has its own
// pictures (an alter ego looks like someone else), and the narrator is told it is still this character.
function characterLooks(c) {
  return el("div", { class: "char-looks wide" },
    ...(c.looks || []).map((lk, k) => {
      const field = (key, placeholder) => {
        const e = el("input", { placeholder, value: lk[key] || "" });
        e.addEventListener("change", () => { lk[key] = e.value; saveCharacters(); });
        return e;
      };
      const box = el("div", { class: "look" },
        el("div", { class: "look-row" },
          field("label", "Look, e.g. pink-haired princess with a crown"),
          field("note", "What it is, e.g. her fantasy version of herself"),
          el("button", { class: "ghost danger remove", title: "Remove this look", onclick: () => {
            c.looks.splice(k, 1);
            saveCharacters().then(renderCharacters);
          } }, "✕")),
        characterPictures(c, lk));
      box.addEventListener("drop", (e) => { e.preventDefault(); e.stopPropagation(); addPictures(c, [...e.dataTransfer.files], lk); });
      return box;
    }),
    el("button", { class: "ghost small", title: "An alter ego, disguise, transformation or younger self: still this character",
      onclick: () => { (c.looks = c.looks || []).push({ label: "", note: "", pictures: [] }); renderCharacters(); } }, "+ Another look"));
}

// Pictures of a character (or of one of its looks), used by Magi and the character scan to tell who is who.
function characterPictures(c, look = null) {
  const target = look || c;
  const file = el("input", { type: "file", accept: "image/*", multiple: true, hidden: true });
  file.addEventListener("change", () => { addPictures(c, [...file.files], look); file.value = ""; });
  return el("div", { class: "char-pics wide adv" },
    ...(target.pictures || []).map((n) => el("span", { class: "pic" },
      el("img", { src: fileUrl(`characters/${n}`), alt: c.name, onclick: () => lightbox(fileUrl(`characters/${n}`)) }),
      el("button", { class: "ghost danger", title: "Remove this picture", onclick: () => {
        target.pictures = target.pictures.filter((x) => x !== n);
        saveCharacters().then(renderCharacters);
      } }, "✕"))),
    el("button", { class: "ghost small", title: "Pictures help Magi (Settings → Speaker detection) tell who says what",
      onclick: () => file.click() }, "+ Pictures…"),
    file);
}

async function addPictures(c, files, look = null) {
  files = files.filter((f) => f.type.startsWith("image/"));
  if (!files.length) return;
  const i = current.characters.indexOf(c);
  const k = look ? c.looks.indexOf(look) : -1;
  if (!c.id || (look && !look.id)) await saveCharacters();  // new ones get their ids when saved (and fresh objects)
  const saved = current.characters[i];
  const lid = look ? saved.looks[k].id : null;
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  try {
    const p = await api(`/api/projects/${current.id}/characters/${saved.id}/pictures${lid ? `?look=${lid}` : ""}`,
      { method: "POST", body: fd });
    current.characters = p.characters;
  } catch (e) { notify(e.message); }
  renderCharacters();
}

// "name" or "name (look)" for a character id and optional look id
function whoLabel(cid, lid = null) {
  const c = current.characters.find((x) => x.id === cid);
  if (!c) return "a character";
  const lk = lid && (c.looks || []).find((x) => x.id === lid);
  return lk ? `${c.name} (${lk.label || "another look"})` : c.name;
}
$("#add-char").addEventListener("click", () => {
  current.characters.push({ name: "", gender: "", aliases: "", description: "", looks: [] });
  renderCharacters();
  const names = $("#chars").querySelectorAll(".char-card input");
  names[names.length - 3]?.focus();
});

function renderPrompt(force = false) {
  // don't replace fields while the user is typing in them (polling re-renders every 1.5s)
  if (!force && ($("#prompt").contains(document.activeElement) || $("#characters-page").contains(document.activeElement))) return;
  const s = current.settings;
  $("#p-persona").value = s.persona || "";
  $("#p-note").value = s.note || "";
  requestAnimationFrame(() => autosize($("#p-persona")));
  listEditor($("#p-rules"), "rules", true);
  listEditor($("#p-styles"), "styles", false);
  renderCharacters();
}

// ---- adding pages ----------------------------------------------------------

async function upload(files) {
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  $("#page-count").textContent = `Uploading ${files.length} file(s)…`;
  try {
    const r = await api(`/api/projects/${current.id}/pages`, { method: "POST", body: fd });
    if (!r.added) notify("No images were found in those files.", "Nothing added");
    else noteDuplicates(r);
  } catch (e) { notify(e.message); }
  openProject(current.id);
}

// A chapter imported twice (the same pages as another one, as "34-5" and "34.5"): said once it's in
function noteDuplicates(r) {
  if (!r.duplicates || !r.duplicates.length) return;
  notify(r.duplicates.map((d) => `"${d.chapter}" has the same pages as "${d.same_as}".`).join(" ") +
    " Delete one of them in the chapter list, unless you want it in the video twice.", "Imported twice?");
}

const dz = $("#dropzone"), dropArea = $("#project");
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
dropArea.addEventListener("dragover", (e) => { if (!hasFiles(e)) return; e.preventDefault(); dz.classList.add("over"); });
dropArea.addEventListener("dragleave", (e) => { if (!dropArea.contains(e.relatedTarget)) dz.classList.remove("over"); });
dropArea.addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dz.classList.remove("over");
  showTab("chapters");
  upload([...e.dataTransfer.files]);
});
$("#file-input").addEventListener("change", (e) => { upload([...e.target.files]); e.target.value = ""; });
$("#clear-pages").addEventListener("click", async () => {
  if (!await ask("Remove all chapters?", "Every chapter, panel and voice line in this project is removed. " +
    "The folders you imported from are not touched.", { ok: "Remove all", danger: true })) return;
  try { await api(`/api/projects/${current.id}/pages`, { method: "DELETE" }); } catch (e) { return notify(e.message); }
  openProject(current.id);
});

// ---- folder import ---------------------------------------------------------

let scanned = [];
const dlg = $("#import-dialog");

// Where imported chapters go: by their number among the chapters there (the default), first, last, or after one
function fillPlaces() {
  $("#import-place").replaceChildren(
    el("option", { value: "number" }, "In order, by chapter number"),
    el("option", { value: "end" }, "At the end"),
    el("option", { value: "start" }, "At the start"),
    ...current.chapters.map((c) => el("option", { value: c.id }, `After ${c.name}`)));
  $("#import-place-row").hidden = !current.chapters.length;
}

$("#import-folder").addEventListener("click", async () => {
  fillPlaces();
  $("#import-list").replaceChildren();
  $("#import-tools").hidden = true;
  $("#import-status").textContent = "";
  $("#import-go").disabled = true;
  scanned = [];
  dlg.showModal();
  if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder) {
    const path = await window.pywebview.api.pick_folder();
    if (!path) { if (!$("#import-path").value) dlg.close(); return; }
    $("#import-path").value = path;
    scan();
  } else {
    $("#import-path").focus();
  }
});

$("#import-scan").addEventListener("click", scan);
$("#import-path").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); scan(); } });

async function scan() {
  const path = $("#import-path").value.trim().replace(/^"|"$/g, "");
  if (!path) return;
  $("#import-status").textContent = "Scanning…";
  $("#import-list").replaceChildren();
  $("#import-tools").hidden = true;
  try {
    const r = await api(`/api/projects/${current.id}/scan-folder`, { method: "POST", json: { path } });
    scanned = r.chapters.map((c) => ({ ...c, on: !c.exists }));
  } catch (e) {
    $("#import-status").textContent = e.message;
    return;
  }
  if (!scanned.length) {
    $("#import-status").textContent = "No images or .cbz/.zip files found in that folder or its subfolders.";
    return;
  }
  $("#import-status").textContent = "";
  $("#import-tools").hidden = false;
  renderScan();
}

function renderScan() {
  $("#import-list").replaceChildren(...scanned.map((c) => el("label", { class: "imp-row" },
    el("input", { type: "checkbox", checked: c.on, onchange: (e) => { c.on = e.target.checked; summarize(); } }),
    el("span", {}, c.name,
      c.kind === "archive" ? el("span", { class: "tag" }, "archive") : null,
      c.exists ? el("span", { class: "tag" }, "already imported") : null),
    el("span", { class: "muted" }, `${c.pages} pages`))));
  summarize();
}

function summarize() {
  const on = scanned.filter((c) => c.on);
  const pages = on.reduce((n, c) => n + c.pages, 0);
  $("#import-summary").textContent =
    `${plural(scanned.length, "chapter")} found · importing ${plural(on.length, "chapter")}, ${pages} pages`;
  $("#import-go").disabled = on.length === 0;
}

$("#imp-all").addEventListener("click", () => { scanned.forEach((c) => (c.on = true)); renderScan(); });
$("#imp-none").addEventListener("click", () => { scanned.forEach((c) => (c.on = false)); renderScan(); });

$("#import-go").addEventListener("click", async () => {
  const keys = scanned.filter((c) => c.on).map((c) => c.key);
  $("#import-go").disabled = true;
  $("#import-status").textContent = `Importing ${plural(keys.length, "chapter")}…`;
  try {
    const r = await api(`/api/projects/${current.id}/import-folder`, {
      method: "POST", json: { path: $("#import-path").value.trim().replace(/^"|"$/g, ""), keys, place: $("#import-place").value || "number" },
    });
    dlg.close();
    await openProject(current.id);
    if (!r.chapters) notify("None of the chosen chapters had readable images.", "Nothing imported");
    else noteDuplicates(r);
  } catch (e) {
    $("#import-status").textContent = e.message;
    $("#import-go").disabled = false;
  }
});

// ---- chapters --------------------------------------------------------------

function saveChapterSelection() {
  localStorage.setItem(`sel:${current.id}`, JSON.stringify([...selectedCh]));
}

const chosenIds = () => (isAdvanced() ? [...selectedCh].filter((id) => current.chapters.some((c) => c.id === id)) : []);
const inScope = (chapterId) => !isAdvanced() || selectedCh.size === 0 || selectedCh.has(chapterId);

// Scenes: runs of enabled pieces where each piece after the first is "joined" (mirrors scenes.py).
function sceneGroups(panels) {
  const out = [];
  for (const p of panels) {
    if (!p.enabled) continue;
    const last = out[out.length - 1];
    if (last && p.joined && last[0].chapter === p.chapter) last.push(p); else out.push([p]);
  }
  return out;
}

function chapterStats() {
  const stats = {};
  for (const c of current.chapters) stats[c.id] = { pages: 0, panels: 0, text: 0, scenes: 0, script: 0, lines: 0, voice: 0 };
  for (const pg of current.pages) if (stats[pg.chapter]) stats[pg.chapter].pages++;
  for (const p of current.panels) {
    const s = stats[p.chapter];
    if (!s || !p.enabled) continue;
    s.panels++;
    if (p.ocr_done || p.text) s.text++;
  }
  for (const g of sceneGroups(current.panels)) {
    const s = stats[g[0].chapter];
    if (!s) continue;
    s.scenes++;
    if (g[0].script_done || g[0].narration) s.script++;
    if ((g[0].narration || "").trim()) { s.lines++; if (g[0].audio) s.voice++; }
  }
  return stats;
}

function renderChapters() {
  const box = $("#chapters");
  $("#chapters-box").hidden = current.chapters.length === 0;
  $("#chapter-count").textContent = current.chapters.length ? `${current.chapters.length}` : "";
  const busy = current.job && ACTIVE.includes(current.job.status);
  if (!box.contains(document.activeElement)) {
    const stats = chapterStats();
    box.replaceChildren(...current.chapters.map((c, i) => {
      const s = stats[c.id];
      const stat = (label, n, of) => el("span", { class: of && n === of ? "done" : "" }, `${label} ${n}/${of}`);
      const name = el("input", { class: "ch-name", value: c.name, title: c.source || "" });
      name.addEventListener("change", async () => {
        await api(`/api/projects/${current.id}/chapters/${c.id}`, { method: "PATCH", json: { name: name.value } });
        c.name = name.value;
        renderPanels();
      });
      const status = chapterStatus(s);
      const flags = wordingFlags(c);
      return el("div", { class: "ch-row" + (selectedCh.has(c.id) && isAdvanced() ? " sel" : "") },
        el("input", {
          type: "checkbox", class: "adv", checked: selectedCh.has(c.id), title: "Include in the next run",
          onchange: (e) => {
            e.target.checked ? selectedCh.add(c.id) : selectedCh.delete(c.id);
            saveChapterSelection();
            shown = PAGE;
            renderChapters(); renderScope(); renderPanels();
          },
        }),
        name,
        el("span", { class: `pill sim ${status.kind}`, title: c.narrator ? `Narrated by ${c.narrator}` : "" }, status.text),
        flags.length ? el("button", { class: "pill warn", disabled: busy && !waitingForWording(), onclick: () => checkWording(c, flags),
          title: `${plural(flags.length, "line")} may go further than YouTube-safe allows` }, "Check wording") : null,
        el("span", { class: "ch-stats adv", title: c.narrator ? `Narrated by ${c.narrator}` : "" }, `${s.pages} pages · `,
          s.panels ? el("span", {}, `${s.scenes} scenes · `, stat("text", s.text, s.panels), " · ",
            stat("script", s.script, s.scenes), " · ", stat("voice", s.voice, s.lines),
            c.narrator && c.narrator.startsWith("local model (fallback)") ? el("span", { class: "narrator" }, " · local fallback") : null)
                   : "not detected yet"),
        el("span", { class: "ch-actions" },
          el("button", { class: "ghost adv" + (c.voiceover ? " on" : ""), title: c.voiceover
            ? `Your own voice-over: ${c.voiceover.name}` : "Use your own recorded voice-over for this chapter", disabled: busy,
          onclick: () => openVoiceover(c) }, "🎙"),
          el("button", { class: "ghost adv", title: "Move up", disabled: i === 0 || busy, onclick: () => moveChapter(i, -1) }, "↑"),
          el("button", { class: "ghost adv", title: "Move down", disabled: i === current.chapters.length - 1 || busy, onclick: () => moveChapter(i, 1) }, "↓"),
          el("button", { class: "ghost danger", title: "Remove chapter", disabled: busy, onclick: () => deleteChapter(c) }, "✕")));
    }));
  }
}

// A YouTube-safe chapter's lines that the wording check flagged and that still read the same (pipeline.wording_flags)
function wordingFlags(c) {
  if (!c.wording || !c.wording.length) return [];
  const lines = new Set(sceneGroups(current.panels).filter((g) => g[0].chapter === c.id).map((g) => g[0].narration));
  return c.wording.filter((w) => lines.has(w.line));
}

// The job waiting at Voice for flagged lines to be checked (pipeline._wording_hold)
const waitingForWording = () => !!(current.job && current.job.status === "paused" && current.job.waiting_for === "wording");

async function checkWording(c, flags) {
  // Waiting at Voice in Make the recap: writing the chapter again restarts the job with that chapter rewritten
  const job = waitingForWording() ? current.job : null;
  const carryOn = job && job.mode === "missing" && !job.only;
  const pick = await choose(`Check the wording of ${c.name}`,
    `A word check found ${plural(flags.length, "line")} that may go further than a YouTube-safe recap can ` +
    "(they're marked in the Script tab). It's only a word search and can be wrong, so read them:\n\n" +
    flags.map((w) => `• ${w.why}, "${w.word}": ${w.line}`).join("\n\n") +
    "\n\nEdit a line and it's no longer flagged. Writing the chapter again replaces all of its narration and voice" +
    (carryOn ? ", then the recap carries on." : job ? " (this job stops first)." : "."),
    [{ value: "keep", label: "Keep it as it is" }, { value: "redo", label: "Write the chapter again", primary: true }]);
  try {
    if (pick === "keep") {
      await api(`/api/projects/${current.id}/chapters/${c.id}`, { method: "PATCH", json: { wording: [] } });
      openProject(current.id);  // a job waiting at Voice carries on by itself once nothing is left to check
    } else if (pick === "redo") {
      if (job) {
        await api(`/api/jobs/${job.id}/cancel`, { method: "POST" });
        for (let i = 0; i < 40 && ACTIVE.includes((await api(`/api/projects/${current.id}`)).job.status); i++) {
          await new Promise((r) => setTimeout(r, 250));
        }
      }
      current.job = await api(`/api/projects/${current.id}/run`, { method: "POST", json: carryOn
        ? { steps: [...new Set(["script", ...job.steps])], only: null, mode: "missing", chapters: job.chapters, rewrite: [c.id] }
        : { steps: ["script"], only: null, mode: "redo", chapters: [c.id] } });
      renderJob();
      poll();
    }
  } catch (e) { notify(e.message); }
}

// The job box's Check wording: the first chapter (in the job's chapters) still to check
$("#job-wording").addEventListener("click", () => {
  const scope = current.job && current.job.chapters;
  const c = current.chapters.find((x) => (!scope || scope.includes(x.id)) && wordingFlags(x).length);
  if (c) checkWording(c, wordingFlags(c));
  else showTab("chapters");
});

// One word on how far a chapter is (simple mode's chapter list)
function chapterStatus(s) {
  if (!s.panels) return { kind: "new", text: "Not started" };
  if (s.script < s.scenes) return { kind: "todo", text: s.script ? `Script ${s.script} of ${s.scenes}` : "Needs script" };
  if (s.voice < s.lines) return { kind: "ready", text: s.voice ? `Voice ${s.voice} of ${s.lines}` : "Script ready" };
  return { kind: "done", text: "Voiced" };
}

async function moveChapter(i, delta) {
  const ids = current.chapters.map((c) => c.id);
  [ids[i], ids[i + delta]] = [ids[i + delta], ids[i]];
  try { await api(`/api/projects/${current.id}/chapters/order`, { method: "PUT", json: { ids } }); }
  catch (e) { return notify(e.message); }
  openProject(current.id);
}

async function deleteChapter(c) {
  if (!await ask(`Remove "${c.name}"?`, "The chapter and its panels and voice lines are removed from this project. " +
    "The source folder is not touched.", { ok: "Remove chapter", danger: true })) return;
  try { await api(`/api/projects/${current.id}/chapters/${c.id}`, { method: "DELETE" }); }
  catch (e) { return notify(e.message); }
  selectedCh.delete(c.id);
  saveChapterSelection();
  openProject(current.id);
}

$("#ch-all").addEventListener("click", () => {
  selectedCh = new Set(current.chapters.map((c) => c.id));
  saveChapterSelection(); shown = PAGE; render();
});
$("#ch-none").addEventListener("click", () => {
  selectedCh.clear();
  saveChapterSelection(); shown = PAGE; render();
});

// ---- time estimates ------------------------------------------------------------
// From the server: the work each step would do in the chapters in scope, times how fast this
// computer has done that work before (measured speeds, refined after every finished step).

let estimate = null, estimateFor = "";

function aboutTime(s) {
  if (s == null) return "";
  if (s < 45) return "under a minute";
  const m = Math.round(s / 60);
  if (m < 60) return `about ${m} min`;
  const h = Math.floor(m / 60), r = m % 60;
  return `about ${h} h${r ? ` ${r} min` : ""}`;
}

async function loadEstimate(force = false) {
  if (!current || !current.chapters.length) {
    $("#estimate").textContent = "";
    if (current) $("#go-note").textContent = "Import a folder of chapters, or drop images, .zip or .cbz files on this page.";
    return;
  }
  const chosen = chosenIds();
  const key = `${current.id}:${chosen.join(",")}`;
  if (!force && key === estimateFor && estimate) return renderEstimate();
  estimateFor = key;
  try {
    const e = await api(`/api/projects/${current.id}/estimate?chapters=${encodeURIComponent(chosen.join(","))}`);
    if (estimateFor === key) { estimate = e; renderEstimate(); }
  } catch { $("#estimate").textContent = ""; }
}

function renderEstimate() {
  if (!estimate) return;
  const names = { panels: "Detect panels", ocr: "Read text", script: "Write script", audio: "Voice", video: "Render video" };
  const all = estimate.run_all;
  const parts = Object.entries(names).filter(([s]) => all[s] && all[s].seconds > 0)
    .map(([s, name]) => `${name} ${aboutTime(all[s].seconds).replace("about ", "")}`);
  $("#estimate").textContent = parts.length
    ? `All of it: ${aboutTime(all.total)} (${parts.join(" · ")}). Hover a step for its own estimate.`
    : "Everything in these chapters is done. Render video makes the video again.";
  const todo = { panels: "find the panels", ocr: "read the text", script: "write the script", audio: "voice it", video: "render the video" };
  const left = Object.keys(todo).filter((s) => all[s] && all[s].seconds > 0);
  const what = left.map((s) => todo[s]);
  $("#go-note").textContent = left.some((s) => s !== "video")
    ? `${aboutTime(all.total).replace(/^about/, "About")}: ${what.join(", ")}.`
    : current.output ? "Everything is done. Making the video again picks up any changes." : `${aboutTime(all.total).replace(/^about/, "About")} to render the video.`;
  for (const b of document.querySelectorAll(".steps button[data-steps]")) {
    const steps = b.dataset.steps.split(",");
    if (steps.length > 1) { b.title = `About ${aboutTime(all.total).replace("about ", "")} for what's left`; continue; }
    const left = estimate.missing[steps[0]], redo = estimate.redo[steps[0]];
    if (!left) continue;
    b.title = steps[0] === "video" ? `${aboutTime(redo.seconds)} to render`
      : `${left.amount ? `${aboutTime(left.seconds)} for what's left (${plural(left.amount, left.unit)})` : "All done"}; ${aboutTime(redo.seconds)} to redo all`;
  }
}

// time left in the running step: its progress so far, blended in from the estimate while there's little to go on
function timeLeft(j) {
  if (j.status !== "running" || !j.step_started) return null;
  const active = Date.now() / 1000 - j.step_started - (j.paused_for || 0);
  const p = j.progress || 0;
  const byEstimate = j.step_estimate != null ? Math.max(0, j.step_estimate - active) : null;
  const byProgress = p >= 0.01 && active >= 15 ? (active * (1 - p)) / p : null;
  if (byProgress == null) return byEstimate;
  if (byEstimate == null) return byProgress;
  const w = Math.min(1, p / 0.15);
  return w * byProgress + (1 - w) * byEstimate;
}

function renderScope() {
  loadEstimate();
  const n = current.chapters.length;
  if (!n) { $("#scope").textContent = ""; return; }
  const chosen = current.chapters.filter((c) => selectedCh.has(c.id));
  const tail = " The main button picks up where you left off.";
  if (!chosen.length || chosen.length === n) {
    $("#scope").textContent = `Steps run on all ${plural(n, "chapter")}. Tick chapters above to work on just those.` + tail;
  } else {
    const names = chosen.slice(0, 3).map((c) => c.name).join(", ") + (chosen.length > 3 ? ", …" : "");
    $("#scope").textContent = `Steps run on ${plural(chosen.length, "selected chapter")}: ${names}.` + tail;
  }
}

// ---- jobs ------------------------------------------------------------------

async function runSteps(steps, only = null, mode = "missing") {
  try {
    current.job = await api(`/api/projects/${current.id}/run`, {
      method: "POST", json: { steps, only, mode, chapters: chosenIds().length ? chosenIds() : null },
    });
    renderJob();
    poll();
  } catch (e) { notify(e.message); }
}

// How much of a step is already done in the ticked chapters (mirrors the checks in pipeline.py).
const STEP_INFO = {
  panels: { name: "Detect panels", unit: "chapter", redo: "replaces their panels, along with the text, narration and voice of those panels" },
  ocr: { name: "Read text", unit: "panel", redo: "reads the text of every panel again" },
  script: { name: "Write script", unit: "scene", redo: "rewrites all narration, including lines you edited by hand, and clears their voice audio. With Claude it also regroups the pieces into scenes" },
  audio: { name: "Voice", unit: "narration line", redo: "regenerates the voice for every line" },
};

function stepProgress(step) {
  const panels = current.panels.filter((p) => p.enabled && inScope(p.chapter));
  if (step === "panels") {
    const chapters = current.chapters.filter((c) => inScope(c.id));
    const has = new Set(current.panels.map((p) => p.chapter));
    return { total: chapters.length, done: chapters.filter((c) => has.has(c.id)).length };
  }
  if (step === "ocr") return { total: panels.length, done: panels.filter((p) => p.ocr_done).length };
  const heads = sceneGroups(current.panels).filter((g) => inScope(g[0].chapter)).map((g) => g[0]);
  if (step === "script") return { total: heads.length, done: heads.filter((p) => p.script_done || p.narration).length };
  const lines = heads.filter((p) => (p.narration || "").trim());
  return { total: lines.length, done: lines.filter((p) => p.audio).length };
}

async function runStep(step) {
  const info = STEP_INFO[step];
  if (!info) return runSteps([step]);  // rendering always re-renders
  const { total, done } = stepProgress(step);
  if (!total) {
    const before = { ocr: "Detect panels", script: "Detect panels", audio: "Write script" }[step];
    return notify(`Nothing to do yet. Run ${before} first.`, info.name);
  }
  if (!done) return runSteps([step]);
  const all = `${plural(total, info.unit)}`;
  const est = (mode) => (estimate && estimate[mode][step] ? `, ${aboutTime(estimate[mode][step].seconds)}` : "");
  if (done === total) {
    const go = await choose(`${info.name} again?`,
      `All ${all} are already done. Redoing ${info.redo}.`,
      [{ value: "redo", label: `Redo all ${total}${est("redo")}`, danger: true }]);
    if (go) runSteps([step], null, "redo");
    return;
  }
  const go = await choose(info.name,
    `${done} of ${all} are already done.\n\nContinue does the remaining ${total - done}. Redo all ${info.redo}.`,
    [{ value: "redo", label: `Redo all ${total}${est("redo")}`, danger: true },
     { value: "missing", label: `Continue (${total - done} left${est("missing")})`, primary: true }]);
  if (go) runSteps([step], null, go);
}

// The main button: Run all (it picks up where things left off and never redoes finished work), or Add chapters
$("#go").addEventListener("click", async () => {
  if (!current.chapters.length) return $("#import-folder").click();
  if (!await charactersReady()) return;
  runSteps(["panels", "ocr", "script", "audio", "video"], null, "missing");
});

document.querySelectorAll(".steps button[data-steps]").forEach((b) =>
  b.addEventListener("click", async () => {
    if (!current.chapters.length) return notify("Import a folder or add pages first.", "No pages yet");
    const steps = b.dataset.steps.split(",");
    if (steps.includes("script") && !await charactersReady()) return;
    // Run all picks up where things left off and never redoes finished work
    if (steps.length > 1) return runSteps(steps, null, "missing");
    runStep(steps[0]);
  }));

$("#redo-selected").addEventListener("click", () => runSteps(["script", "audio"], [...selected]));
$("#job-cancel").addEventListener("click", () => api(`/api/jobs/${current.job.id}/cancel`, { method: "POST" }));
$("#job-pause").addEventListener("click", async () => {
  const j = current.job;
  current.job = await api(`/api/jobs/${j.id}/${j.pause ? "resume" : "pause"}`, { method: "POST" });
  renderJob();
});

function poll() {
  clearInterval(polling);
  let lastStep = current.job && current.job.step, lastStatus = current.job && current.job.status;
  polling = setInterval(async () => {
    if (!current || !current.job) return clearInterval(polling);
    const p = await api(`/api/projects/${current.id}`);
    if (p.id !== current.id) return;
    const job = p.job;
    const finished = !ACTIVE.includes(job.status);
    // refresh the lists when a step finishes, and while panels/text/script are streaming in
    const streaming = job.status === "running" && ["panels", "ocr", "script"].includes(job.step);
    // (and when it pauses or resumes: Check wording works while Voice waits for it)
    if (finished || job.step !== lastStep || job.status !== lastStatus || streaming) {
      current = p;
      render();
    } else {
      current.job = job;
      renderJob();
    }
    lastStep = job.step;
    lastStatus = job.status;
    if (finished) {
      clearInterval(polling);
      loadProjects();
      loadEstimate(true);  // what's left has changed, and the speed this computer managed was saved
      if (["scan", "names"].includes(job.step) && job.status === "done" && current.scan && current.scan.groups.length) openReview();
      if (job.step === "voiceover" && $("#voiceover-dialog").open) renderVoiceover();
      if (job.step === "boxes" && job.status === "done") openBoxes();
    }
  }, 1500);
}

function renderJob() {
  const j = current.job;
  const box = $("#job");
  box.hidden = !j || j.status === "done" && Date.now() / 1000 - j.created > 600;
  const running = !!j && ACTIVE.includes(j.status);
  // (Import folder and dropping files stay open: a job goes on with the chapters it started with)
  document.querySelectorAll(".steps button, #go, #redo-selected, #clear-pages").forEach((b) => (b.disabled = running));
  $(".go-row").hidden = running;
  if (!running) $("#redo-selected").disabled = selected.size === 0;
  if (!j) return;
  const left = timeLeft(j);
  const more = j.steps && j.step ? j.steps.length - j.steps.indexOf(j.step) - 1 : 0;
  $("#job-msg").textContent = j.status === "queued" ? "Waiting…"
    : j.message + (left != null ? ` · ${aboutTime(left)} left${more ? ` in this step (${plural(more, "more step")} after)` : ""}` : "");
  $("#job-bar").style.width = `${Math.round((j.progress || 0) * 100)}%`;
  $("#job-error").textContent = j.error || "";
  $("#job-cancel").hidden = !running;
  // Pause takes effect after the item being worked on (for Claude, the chapter), so it can say "Pausing…" for a bit
  const pause = $("#job-pause");
  pause.hidden = !running;
  pause.textContent = j.status === "paused" ? "Resume" : j.pause ? "Pausing… (resume)" : "Pause";
  $("#job-wording").hidden = !waitingForWording();
  box.classList.toggle("paused", j.status === "paused");
}

// ---- panels ----------------------------------------------------------------

async function editPanel(id, changes) {
  const updated = await api(`/api/projects/${current.id}/panels/${id}`, { method: "PATCH", json: changes });
  const i = current.panels.findIndex((p) => p.id === id);
  current.panels[i] = updated;
  return updated;
}

// After you put back or leave out a piece: the same picture at the start or end of other chapters (the scan
// group's page, or the series' card, repeated) can follow. Asks first; true when the copies changed too.
async function followCopies(pieces, enabled) {
  const ids = new Set();
  try {
    for (const p of pieces) for (const id of (await api(`/api/projects/${current.id}/panels/${p.id}/same`)).ids) ids.add(id);
  } catch { return false; }
  const byId = new Map(current.panels.map((p) => [p.id, p]));
  const todo = [...ids].filter((id) => byId.has(id) && byId.get(id).enabled !== enabled);
  if (!todo.length) return false;
  const chapters = new Set(todo.map((id) => byId.get(id).chapter)).size;
  const go = await choose(enabled ? "Put back the same picture elsewhere?" : "Leave out the same picture elsewhere?",
    `The same picture is at the start or end of ${plural(chapters, "other chapter")} (${plural(todo.length, "piece")}). ` +
    `${enabled ? "Use" : "Leave out"} those too? Either way, chapters added later follow what you choose here.`,
    [{ value: "one", label: "Just this one" }, { value: "all", label: enabled ? "Use them too" : "Leave them out too", primary: true }],
    { cancel: false });
  // what you chose for these pieces themselves is remembered too, as for a piece found as the group's page
  const r = await api(`/api/projects/${current.id}/panels/choose`, { method: "POST",
    json: { ids: go === "all" ? [...pieces.map((p) => p.id), ...todo] : pieces.map((p) => p.id), enabled } });
  for (const u of r.panels) current.panels[current.panels.findIndex((p) => p.id === u.id)] = u;
  return go === "all";
}

async function setJoined(panelId, joined) {
  try { await api(`/api/projects/${current.id}/panels/${panelId}/join`, { method: "POST", json: { joined } }); }
  catch (e) { return notify(e.message); }
  openProject(current.id);
}

// For display, disabled pieces stay in their scene (sceneGroups leaves them out of the story).
function displayGroups(panels) {
  const out = [];
  for (const p of panels) {
    const last = out[out.length - 1];
    if (last && p.joined && last[0].chapter === p.chapter) last.push(p); else out.push([p]);
  }
  return out;
}

function sceneCard(g, n, firstInChapter) {
  const head = g[0];
  const on = g.some((p) => p.enabled);
  // a scene used in the video with some of its pieces left out (the scan group's notice after the last lines, say)
  const left = on ? g.filter((p) => !p.enabled) : [];
  const c = el("div", { class: "card" + (on ? "" : " off") });
  const sel = el("input", {
    type: "checkbox", class: "adv", title: "Select", checked: selected.has(head.id),
    onchange: (e) => { e.target.checked ? selected.add(head.id) : selected.delete(head.id); renderJob(); },
  });
  const pieces = el("div", { class: "pieces" });
  g.forEach((p, i) => {
    if (i > 0) pieces.append(el("button", { class: "split adv", title: "Make this piece start its own scene", onclick: () => setJoined(p.id, false) }, "✂ split here"));
    const src = fileUrl(`panels/${p.file}`);
    const img = el("img", { loading: "lazy", src, onclick: () => lightbox(src, p.id) });
    if (!left.includes(p)) return pieces.append(youtube() ? boxed(img, pieceBoxes(p)) : img);
    pieces.append(el("div", { class: "piece-off", title: "Left out of the video (the rest of this scene is used)" }, img,
      el("span", { class: "piece-tag" }, p.credits ? "left out: credits" : "left out"),
      el("button", { type: "button", class: "piece-use small", title: "Use this piece in the video too",
        onclick: async () => { await editPanel(p.id, { enabled: true }); renderPanels(); await followCopies([p], true); renderPanels(); } },
      "Use it")));
  });
  const narration = el("textarea", { rows: 3, placeholder: "Narration (empty = no voice for this scene)" });
  narration.value = head.narration || "";
  narration.addEventListener("change", async () => { await editPanel(head.id, { narration: narration.value }); });
  const enabled = el("label", {}, el("input", {
    type: "checkbox", checked: on,
    onchange: async (e) => {
      // ticked back in, the scene's pieces left out as the scan group's pages stay out ("Use it" on one keeps it),
      // unless they're all there is to the scene
      const want = e.target.checked;
      const keep = want && g.some((p) => !p.credits) ? g.filter((p) => p.credits && !p.enabled) : [];
      const changed = g.filter((p) => !keep.includes(p) && p.enabled !== want);
      for (const p of g) if (!keep.includes(p)) await editPanel(p.id, { enabled: want });
      renderPanels();
      if (changed.length && await followCopies(changed, want)) renderPanels();
    },
  }), " Use in video");
  const isCredits = g.some((p) => p.credits) && !on;
  const chapter = current.chapters.find((x) => x.id === head.chapter);
  const flag = youtube() && chapter && (chapter.wording || []).find((w) => w.line === head.narration);
  const meta = el("div", { class: "meta" },
    el("span", {}, `#${n + 1} · page ${head.page + 1}${g.length > 1 ? ` · ${g.length} pieces` : ""}`), enabled,
    isCredits ? el("span", { class: "credits", title: "Left out automatically: one of the scan group's pages at the start or end of the chapter. Tick Use in video to keep it." }, "left out: credits") : null,
    youtube() && on && g.some((p) => pieceBoxes(p).length) ? el("span", { class: "box-tag", title: "Black boxes cover part of this scene in the video. Click a piece to change them." },
      `■ ${boxCount(g.reduce((k, p) => k + pieceBoxes(p).length, 0))}`) : null,
    flag ? el("span", { class: "word-tag", title: `The wording check flagged this line: ${flag.why}, "${flag.word}". ` +
      "Edit it, or keep it with Check wording in the Chapters tab." }, "⚠ check wording") : null,
    left.length ? el("span", { class: "credits", title: "Those pieces are dimmed: they're left out of the video, the rest of the scene is used" },
      `${left.length} of ${g.length} pieces left out`) : null,
    head.audio ? el("audio", { controls: true, preload: "none", src: fileUrl(`audio/${head.audio}`, head.audio_rev) }) : null,
    head.audio ? el("span", {}, `${head.duration.toFixed(1)}s`) : null,
    head.audio && head.audio_source === "recording" ? el("span", {
      class: head.voice_match < 0.6 ? "rec warn" : "rec",
      title: "From your recording: the share of this line's words the speech recognizer heard in it" },
    `🎙 ${Math.round((head.voice_match || 0) * 100)}%`) : null,
    firstInChapter ? null : el("button", { class: "ghost join-up adv", title: "Merge this scene into the one above (their narration is combined)",
      onclick: () => setJoined(head.id, true) }, "⤒ Join with scene above"));
  const text = g.map((p) => p.text).filter(Boolean).join("\n");
  const ocr = text ? el("div", { class: "ocr adv", title: "Text found in this scene" }, text) : null;
  const guesses = g.flatMap((p) => p.speakers || []);
  const who = guesses.length ? el("div", { class: "speakers adv", title: "Magi's guesses at who says each line (often wrong)" },
    ...guesses.map((s) => el("div", {}, el("b", {}, `${s.who}: `), s.text))) : null;
  c.append(sel, pieces, el("div", { class: "fields" }, meta, narration, ocr, who));
  return c;
}

// Show a full-screen overlay above everything, including an open dialog: a modal dialog sits in the
// browser's top layer, above any z-index, so the overlay goes there too, as a newer modal dialog.
// Returns a function that closes it (Escape closes it too).
function overlay(node) {
  const host = el("dialog", { class: "overlay-host" }, node);
  host.addEventListener("close", () => host.remove());
  document.body.append(host);
  host.showModal();
  return () => host.close();
}

// ---- black boxes (YouTube-safe projects, see cover.py) ----

const youtube = () => !!(current && current.settings.youtube_safe);
const boxCount = (n) => (n === 1 ? "1 box" : `${n} boxes`);
const BOX_BY = { auto: "Found by the box finder", claude: "Marked by the script writer", you: "Drawn by you" };

// A piece's boxes as the video draws them (mirrors cover.boxes): yours and the finder's, and the script
// writer's where the finder didn't box the same spot. [{box: [left, top, right, bottom] as fractions, by}]
function pieceBoxes(p) {
  const cover = p.cover || [];
  const touch = (a, b) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
  const auto = cover.filter((c) => c.by === "auto").map((c) => c.box);
  return cover.filter((c) => !(c.by === "claude" && auto.some((a) => touch(c.box, a))));
}

function boxStyle(b) {
  return `left:${b[0] * 100}%;top:${b[1] * 100}%;width:${(b[2] - b[0]) * 100}%;height:${(b[3] - b[1]) * 100}%`;
}

// img with its boxes drawn over it
function boxed(img, boxes) {
  if (!boxes.length) return img;
  return el("span", { class: "boxed" }, img,
    ...boxes.map((c) => el("span", { class: `bbox by-${c.by}`, style: boxStyle(c.box), title: BOX_BY[c.by] || "" })));
}

function boxedPieces() {
  return current.panels.filter((p) => inScope(p.chapter) && p.enabled && pieceBoxes(p).length);
}

function renderBoxesCount() {
  if (!youtube()) return ($("#boxes-count").textContent = "");
  const n = boxedPieces().length;
  $("#boxes-count").textContent = `${plural(n, "piece")} with boxes${selectedCh.size ? " in the selected chapters" : ""}`;
}

function renderBoxes() {
  const pieces = boxedPieces();
  const sections = current.chapters.map((c) => {
    const mine = pieces.filter((p) => p.chapter === c.id);
    if (!mine.length) return null;
    return el("div", { class: "credits-chapter" },
      el("div", { class: "chapter-divider" }, `${c.name} · ${mine.length}`),
      el("div", { class: "credits-grid" }, ...mine.map((p) => {
        const src = fileUrl(`panels/${p.file}`);
        const n = pieceBoxes(p).length;
        return el("div", { class: "credit-piece boxes-piece" },
          boxed(el("img", { loading: "lazy", src, title: `Page ${p.page + 1}`, onclick: () => lightbox(src, p.id) }), pieceBoxes(p)),
          el("span", { class: "piece-tag" }, boxCount(n)));
      })));
  }).filter(Boolean);
  $("#boxes-list").replaceChildren(...sections);
  const chapters = new Set(pieces.map((p) => p.chapter)).size;
  $("#boxes-summary").textContent = pieces.length
    ? `${plural(pieces.length, "piece")} with boxes in ${plural(chapters, "chapter")}` + (selectedCh.size ? " (selected chapters)" : "")
    : "No boxes yet" + (selectedCh.size ? " in the selected chapters" : "") + ". Find boxes now (the Video tab, in advanced mode) runs the box finder.";
  renderBoxesCount();
}

function openBoxes() {
  renderBoxes();
  if (!$("#boxes-dialog").open) $("#boxes-dialog").showModal();
}
for (const b of document.querySelectorAll(".boxes-open")) b.addEventListener("click", openBoxes);

async function findBoxes(mode) {
  if (current.job && ACTIVE.includes(current.job.status)) return notify("Wait for the running job to finish first.");
  if (!youtube()) return notify("Turn on YouTube-safe in the Video tab first: the boxes are only drawn in YouTube-safe projects.", "Not YouTube-safe");
  try {
    if (!(await api("/api/addons/boxes")).installed) {
      return notify("Download the box finder first (Settings → Video → Black boxes). You can also draw boxes yourself: " +
        "click a piece and drag over what to cover.", "No box finder");
    }
    current.job = await api(`/api/projects/${current.id}/run`, { method: "POST",
      json: { steps: ["boxes"], mode, chapters: selectedCh.size ? [...selectedCh] : null } });
    if ($("#boxes-dialog").open) $("#boxes-dialog").close();
    renderJob();
    poll();
  } catch (e) { notify(e.message); }
}
$("#find-boxes").addEventListener("click", () => findBoxes("missing"));
$("#boxes-again").addEventListener("click", async () => {
  if (!await ask("Find the boxes again?", `The box finder looks at every piece used in ${selectedCh.size ? "the selected chapters" : "this project"} again. ` +
    "The boxes it found before are replaced; the ones you drew and the script writer's stay.", { ok: "Find again" })) return;
  findBoxes("redo");
});

// Full-size view of an image. For a panel (panelId given) it doubles as the place to cut out
// character pictures for Magi: drag a box around the character, pick who it is, add. In a YouTube-safe
// project, dragging draws a black box there instead (or still a character picture, when chosen).
function lightbox(src, panelId = null) {
  const img = el("img", { src, draggable: "false" });
  const named = current ? current.characters.filter((c) => c.id && c.name.trim()) : [];
  const boxing = !!panelId && youtube();
  if (!panelId || (!named.length && !boxing)) {
    const close = overlay(el("div", { class: "lightbox", onclick: () => close() }, img));
    return;
  }
  const rect = el("div", { class: "crop-rect", hidden: true });
  const frame = el("div", { class: "crop-frame" }, img, rect);
  let mode = boxing ? "box" : "picture";
  let list = boxing ? pieceBoxes(current.panels.find((x) => x.id === panelId) || {}).map((c) => ({ ...c })) : [];
  const boxStatus = el("span", { class: "crop-status" });
  const drawBoxes = () => {
    frame.querySelectorAll(".bbox").forEach((b) => b.remove());
    list.forEach((c, i) => frame.append(el("span", { class: `bbox by-${c.by}`, style: boxStyle(c.box), title: BOX_BY[c.by] || "" },
      el("span", { class: "bbox-x", title: "Remove this box", onpointerdown: (e) => e.stopPropagation(),
        onclick: () => { list.splice(i, 1); drawBoxes(); saveBoxes(); } }, "✕"))));
    boxStatus.textContent = list.length ? boxCount(list.length) : "No boxes on this piece";
  };
  const saveBoxes = async () => {
    try {
      const u = await api(`/api/projects/${current.id}/panels/${panelId}/cover`, { method: "PUT", json: { boxes: list } });
      current.panels[current.panels.findIndex((x) => x.id === panelId)] = u;
    } catch (e) { boxStatus.textContent = e.message; }
  };
  let start = null, box = null;
  const at = (e) => {
    const r = img.getBoundingClientRect();
    return [Math.min(Math.max(e.clientX - r.left, 0), r.width), Math.min(Math.max(e.clientY - r.top, 0), r.height)];
  };
  frame.addEventListener("pointerdown", (e) => { start = at(e); box = null; rect.hidden = true; frame.setPointerCapture(e.pointerId); });
  frame.addEventListener("pointermove", (e) => {
    if (!start) return;
    const [x, y] = at(e);
    box = [Math.min(start[0], x), Math.min(start[1], y), Math.max(start[0], x), Math.max(start[1], y)];
    Object.assign(rect.style, { left: `${box[0]}px`, top: `${box[1]}px`, width: `${box[2] - box[0]}px`, height: `${box[3] - box[1]}px` });
    rect.hidden = false;
  });
  frame.addEventListener("pointerup", () => {
    start = null;
    if (box && (box[2] - box[0] < 12 || box[3] - box[1] < 12)) { box = null; rect.hidden = true; }  // a click, not a drag
    if (box && mode === "box") {  // a new black box, as fractions of the picture
      const w = img.clientWidth, h = img.clientHeight;
      list.push({ box: [box[0] / w, box[1] / h, box[2] / w, box[3] / h], by: "you" });
      box = null; rect.hidden = true;
      drawBoxes();
      saveBoxes();
    }
  });
  // a character, or one of its saved other looks ("character id:look id")
  const who = el("select", {}, ...named.flatMap((c) => [el("option", { value: c.id }, c.name),
    ...(c.looks || []).filter((lk) => lk.id).map((lk) => el("option", { value: `${c.id}:${lk.id}` }, `${c.name}: ${lk.label || "another look"}`))]));
  const status = el("span", { class: "crop-status" });
  const add = el("button", { class: "primary", onclick: async () => {
    const s = img.naturalWidth / img.clientWidth;
    const [cid, look] = who.value.split(":");
    try {
      const p = await api(`/api/projects/${current.id}/characters/${cid}/pictures/from-panel`, {
        method: "POST", json: { panel: panelId, box: box ? box.map((v) => Math.round(v * s)) : null, look: look || null } });
      current.characters = p.characters;
      if (!$("#prompt").hidden) renderCharacters();
      status.textContent = `Added to ${who.selectedOptions[0].textContent}${box ? "" : " (whole panel)"}`;
      box = null; rect.hidden = true;
    } catch (e) { status.textContent = e.message; }
  } }, "Add picture");
  const pictureBar = el("div", { class: "crop-bar" }, el("span", {}, "Drag around a character to use as a picture of"), who, add,
    el("button", { class: "ghost", onclick: () => close() }, "Close"), status);
  const switchTo = (m) => () => { mode = m; pictureBar.hidden = m !== "picture"; boxBar.hidden = m !== "box"; drawBoxes(); };
  const boxBar = el("div", { class: "crop-bar" }, el("span", {}, "Drag over what the video should cover. ✕ removes a box."),
    named.length ? el("button", { class: "ghost", onclick: switchTo("picture") }, "Cut out a character picture instead") : null,
    el("button", { class: "ghost", onclick: () => close() }, "Close"), boxStatus);
  if (boxing) {
    pictureBar.prepend(el("button", { class: "ghost", onclick: switchTo("box") }, "Back to black boxes"));
    pictureBar.hidden = true;
  } else {
    boxBar.hidden = true;
  }
  const lb = el("div", { class: "lightbox cropping" }, frame, boxBar, pictureBar);
  lb.addEventListener("click", (e) => { if (e.target === lb) close(); });
  const close = overlay(lb);
  if (boxing) {
    drawBoxes();
    lb.parentElement.addEventListener("close", () => {  // the scene list and the boxes dialog show the new boxes
      renderPanels();
      if ($("#boxes-dialog").open) renderBoxes();
    });
  }
}

function renderPanels() {
  const box = $("#panels");
  // keep a textarea the user is typing in
  if (box.contains(document.activeElement) && document.activeElement.tagName === "TEXTAREA") return;
  const names = Object.fromEntries(current.chapters.map((c) => [c.id, c.name]));
  const groups = displayGroups(current.panels.filter((p) => inScope(p.chapter)));
  const nodes = [];
  let lastChapter = null, n = 0;
  for (const g of groups.slice(0, shown)) {
    const first = g[0].chapter !== lastChapter;
    if (first) {
      nodes.push(el("div", { class: "chapter-divider" }, names[g[0].chapter] || "Chapter"));
      lastChapter = g[0].chapter;
      n = 0;
    }
    nodes.push(sceneCard(g, n++, first));
  }
  box.replaceChildren(...nodes);
  $("#more").hidden = groups.length <= shown;
  const on = groups.filter((g) => g.some((p) => p.enabled)).length;
  const pieces = groups.reduce((k, g) => k + g.length, 0);
  $("#panel-count").textContent = groups.length
    ? `${on} of ${plural(groups.length, "scene")} used (${pieces} pieces)${chosenIds().length ? " in selected chapters" : ""}`
    : current.chapters.length ? "— none yet: Make the recap finds them" : "— add chapters to start";
  renderCreditsCount();
  renderBoxesCount();
}
$("#more").addEventListener("click", () => { shown += PAGE; renderPanels(); });

// ---- layout: tabs, simple and advanced mode, the project menu, long help text ----
// Simple mode shows what most projects need; Advanced mode (bottom of the sidebar) shows every step, setting and
// tool: anything marked .adv. Chapter selection only counts in advanced mode (chosenIds).

const ADV_TABS = ["prompt", "project"];
const isAdvanced = () => document.body.classList.contains("advanced");
const stored = (key) => { try { return localStorage.getItem(key); } catch { return null; } };
const store = (key, value) => { try { localStorage.setItem(key, value); } catch { /* not remembered */ } };
let tab = stored("tab") || "chapters";

function showTab(name) {
  if (!isAdvanced() && ADV_TABS.includes(name)) name = "chapters";
  tab = name;
  store("tab", name);
  for (const b of document.querySelectorAll(".tabs [data-tab]")) {
    b.classList.toggle("on", b.dataset.tab === name);
    b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false");
  }
  for (const page of document.querySelectorAll(".tab-page")) page.hidden = page.dataset.page !== name;
  // text boxes sized while their tab was hidden measured nothing: size them now
  requestAnimationFrame(() => document.querySelectorAll(`.tab-page[data-page="${name}"] textarea`).forEach(autosize));
}
document.querySelectorAll(".tabs [data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
document.querySelectorAll(".tile[data-goto]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.goto)));

function applyMode(on) {
  document.body.classList.toggle("advanced", on);
  $("#advanced-mode").checked = on;
  store("advanced", on ? "1" : "");
  showTab(tab);
  if (current) { shown = PAGE; render(); }
}
$("#advanced-mode").addEventListener("change", (e) => applyMode(e.target.checked));
applyMode(stored("advanced") === "1");

const projectMenu = $("#project-menu-list");
function setMenu(open) {
  projectMenu.hidden = !open;
  $("#project-menu").setAttribute("aria-expanded", open ? "true" : "false");
}
$("#project-menu").addEventListener("click", (e) => { e.stopPropagation(); setMenu(projectMenu.hidden); });
document.addEventListener("click", () => setMenu(false));  // (a menu item's own click runs first)
document.addEventListener("keydown", (e) => { if (e.key === "Escape") setMenu(false); });

// In simple mode a long help text shows its first two lines; hover it for the rest, or click it to open it.
// Warnings always show in full.
let hintsQueued = false;
function markLongHints() {
  hintsQueued = false;
  for (const h of document.querySelectorAll(".hint")) {
    const long = h.textContent.trim().length > 150 && !h.classList.contains("warn") && !h.closest(".field-head");
    h.classList.toggle("long", long);
    if (long) h.title = h.textContent.trim(); else if (h.classList.contains("was-long")) h.removeAttribute("title");
    h.classList.toggle("was-long", long);
  }
}
new MutationObserver(() => {
  if (!hintsQueued) { hintsQueued = true; requestAnimationFrame(markLongHints); }
}).observe(document.body, { childList: true, subtree: true, characterData: true });
markLongHints();
document.addEventListener("click", (e) => {
  const h = e.target.closest(".hint.long");
  if (h && !e.target.closest("a, button, input, select")) h.classList.toggle("open");
});

// ---- render ----------------------------------------------------------------

function render() {
  $("#empty").hidden = !!current;
  $("#project").hidden = !current;
  if (!current) return;
  if (document.activeElement !== $("#project-name")) $("#project-name").value = current.name;
  $("#page-count").textContent = current.chapters.length
    ? `${plural(current.chapters.length, "chapter")}, ${current.pages.length} pages`
    : "";
  $("#clear-pages").hidden = current.chapters.length === 0;
  renderSettings();
  renderOverview();
  renderAppLine();
  renderPrompt();
  renderSteps();
  renderScanButton();
  renderChapters();
  renderScope();
  renderJob();
  renderPanels();
  renderOutput();
}

// The overview above the tabs: how far each stage is, and what the main button does next.
function renderOverview() {
  const stats = chapterStats();
  const all = current.chapters.map((c) => stats[c.id]);
  const n = all.length;
  const found = all.filter((s) => s.panels).length;
  const written = all.filter((s) => s.scenes && s.script === s.scenes).length;
  const voiced = all.filter((s) => s.scenes && s.script === s.scenes && s.voice === s.lines).length;
  const out = current.output;
  const seconds = out ? (out.parts || []).reduce((k, x) => k + (x.seconds || 0), 0) : 0;
  const tile = (id, text, done) => {
    $(id).textContent = text;
    $(id).parentElement.classList.toggle("done", !!done);
  };
  tile("#tile-chapters", n ? `${plural(n, "chapter")} · ${current.pages.length} pages` : "None yet", n > 0);
  const toCheck = current.chapters.filter((c) => wordingFlags(c).length).length;
  tile("#tile-script", !found ? "Not started" : `${written} of ${n} written` + (toCheck ? ` · ${toCheck} to check` : ""),
    n && written === n && !toCheck);
  tile("#tile-voice", !written ? "Not started" : `${voiced} of ${n} voiced`, n && voiced === n);
  tile("#tile-video", out ? (seconds ? `Made · ${lengthText(seconds)}` : "Made") : "Not made yet", !!out);
  $("#go").textContent = !n ? "Add chapters" : !found ? "Make the recap" : voiced === n && out ? "Make the video again" : "Continue the recap";
}

// The rendered video, or its parts (Settings → Video → Split into parts), with each one's subtitles
// and YouTube chapter list; and the last export for video editors.
let shownPart = 0;

const lengthText = (s) => (s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min` : `${Math.max(1, Math.round(s / 60))} min`);
const sizeText = (bytes) => (bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`);

function renderOutput() {
  const out = current.output;
  $("#output").hidden = !out && !current.editors && !current.package;
  renderEditorsOut();
  const pk = current.package;
  $("#package-out").hidden = !pk;
  if (pk) $("#package-out").replaceChildren(el("span", {}, "Project file: ", el("b", {}, pk.file.split("/").pop()),
    ` · ${sizeText(pk.size)} · saved ${new Date(pk.time * 1000).toLocaleString()}`));
  for (const x of ["#player", "#srt-link", "#chapters-link"]) $(x).hidden = !out;
  $("#video-empty").hidden = !!out;
  if (!out) {
    $("#player").removeAttribute("src");
    $("#output-parts").replaceChildren();
    return;
  }
  const parts = out.parts && out.parts.length ? out.parts : [{ video: out.video, srt: out.srt }];
  if (shownPart >= parts.length) shownPart = 0;
  const part = parts[shownPart];
  const src = `/files/${current.id}/${encodeURI(part.video)}?t=${out.time}`;
  if (!$("#player").src.endsWith(src)) $("#player").src = src;
  $("#srt-link").href = fileUrl(encodeURI(part.srt), out.time);
  $("#chapters-link").hidden = !part.chapters;
  if (part.chapters) $("#chapters-link").href = fileUrl(encodeURI(part.chapters), out.time);
  $("#output-parts").replaceChildren(...(parts.length > 1 ? parts.map((x, k) => el("button", {
    type: "button", class: k === shownPart ? "ghost small on" : "ghost small", "aria-pressed": k === shownPart ? "true" : "false",
    onclick: () => { shownPart = k; renderOutput(); },
  }, `Part ${k + 1}: ${x.first}${x.last !== x.first ? ` to ${x.last}` : ""} (${lengthText(x.seconds)})`)) : []));
}
$("#open-folder").addEventListener("click", () => api(`/api/projects/${current.id}/open-folder`, { method: "POST" }));

function renderEditorsOut() {
  const ed = current.editors;
  $("#editors-out").hidden = !ed;
  if (!ed) return;
  const span = ed.first === ed.last ? ed.first : `${ed.first} to ${ed.last}`;
  $("#editors-out").replaceChildren(
    el("span", {}, "For video editors: ", el("b", {}, ed.file.split("/").pop()),
      ` · ${span} · ${lengthText(ed.seconds)} · ${plural(ed.clips, "clip")} · ${sizeText(ed.size)} · `),
    el("a", { class: "link", target: "_blank", href: fileUrl(encodeURI(ed.srt), ed.time) }, "Subtitles (.srt)"),
    el("span", { class: "hint" }, ed.kind === "resolve" ? " In DaVinci Resolve: File → Import → Timeline." : ""));
}

// ---- export for video editors -------------------------------------------------
// The recap as an OpenTimelineIO timeline (.otioz, see editors.py): for DaVinci Resolve, with the
// motion as keyframes, or rendered clips that any editor shows as they are.

function renderEditorsDialog() {
  const kind = current.settings.editor_export || "resolve";
  document.querySelector(`#editors-dialog input[name=editor-kind][value=${kind}]`).checked = true;
  $("#editors-transition").value = current.settings.editor_transition || "dissolve";
  $("#editors-transition-row").hidden = kind !== "resolve";
  const chosen = current.chapters.filter((c) => selectedCh.has(c.id));
  const names = chosen.slice(0, 3).map((c) => c.name).join(", ") + (chosen.length > 3 ? ", …" : "");
  $("#editors-scope").textContent = !chosen.length || chosen.length === current.chapters.length
    ? `Exports all ${plural(current.chapters.length, "chapter")}. Tick chapters to export just those.`
    : `Exports the ${plural(chosen.length, "ticked chapter")}: ${names}.`;
  const s = current.settings;
  const [w, h] = { 1080: [1920, 1080], 720: [1280, 720], 480: [854, 480], 360: [640, 360] }[s.resolution || 1080] || [1920, 1080];
  $("#editors-format").textContent = s.orientation === "landscape" ? `${w} × ${h}, ${s.fps} fps` : `${h} × ${w}, ${s.fps} fps`;
  $("#editors-start").disabled = !!(current.job && ACTIVE.includes(current.job.status));
  for (const k of ["resolve", "baked"]) {
    const e = estimate && estimate.editors && estimate.editors[k];
    $(`#editors-time-${k}`).textContent = e ? `${aboutTime(e.seconds).replace(/^./, (c) => c.toUpperCase())}.` : "";
  }
}
$("#editors-open").addEventListener("click", async () => {
  if (!current.panels.length) return notify("Detect panels first: the timeline is made from the scenes.", "Nothing to export yet");
  renderEditorsDialog();
  $("#editors-dialog").showModal();
  await loadEstimate();
  renderEditorsDialog();
});
document.querySelectorAll("#editors-dialog input[name=editor-kind]").forEach((r) => r.addEventListener("change", () => {
  $("#editors-transition-row").hidden = r.value !== "resolve";
}));
$("#editors-start").addEventListener("click", async () => {
  const kind = document.querySelector("#editors-dialog input[name=editor-kind]:checked").value;
  $("#editors-dialog").close();
  try {
    await saveGlobal({ editor_export: kind, editor_transition: $("#editors-transition").value });
    current.job = await api(`/api/projects/${current.id}/run`, {
      method: "POST", json: { steps: ["editors"], chapters: selectedCh.size ? [...selectedCh] : null },
    });
    renderJob();
    poll();
  } catch (e) { notify(e.message); }
});

// ---- your own voice-over, per chapter (voiceover.py) ---------------------------
// The chapter's script to read, the speech recognizer's download, the recording's upload, and how
// well each line was found in it.

let voChapter = null, voPoll = null;

function voiceoverLines(cid) {
  return sceneGroups(current.panels).filter((g) => g[0].chapter === cid && (g[0].narration || "").trim()).map((g) => g[0]);
}

async function openVoiceover(c) {
  voChapter = c.id;
  $("#vo-file").value = "";
  $("#vo-error").textContent = "";
  $("#voiceover-dialog").showModal();
  renderVoiceover();
}

const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

async function renderVoiceover(st = null) {
  clearTimeout(voPoll);
  const c = current.chapters.find((x) => x.id === voChapter);
  if (!c) return $("#voiceover-dialog").close();
  const lines = voiceoverLines(c.id);
  $("#vo-chapter").textContent = c.name;
  $("#vo-lines").textContent = lines.length ? `${plural(lines.length, "line")}` : "";
  $("#vo-script").textContent = lines.length ? lines.map((x) => x.narration.trim()).join("\n\n")
    : "This chapter has no narration yet: write its script first, then record it.";
  $("#vo-copy").disabled = !lines.length;
  try { st = st || await api("/api/addons/vosk"); } catch { return; }
  const busy = st.state === "installing";
  $("#vo-get").hidden = st.installed;
  $("#vo-progress").hidden = !busy;
  $("#vo-download").hidden = busy;
  $("#vo-stage").textContent = st.message;
  $("#vo-bar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
  if (st.state === "error") $("#vo-error").textContent = `${st.message}: ${st.error}`;
  if (busy) voPoll = setTimeout(() => renderVoiceover(), 700);
  const running = current.job && ACTIVE.includes(current.job.status);
  $("#vo-upload").hidden = !st.installed;
  $("#vo-start").hidden = !st.installed;
  $("#vo-start").disabled = !st.installed || !lines.length || running || !$("#vo-file").files.length;
  $("#vo-remove").hidden = !c.voiceover;
  $("#vo-remove").disabled = running;
  const v = c.voiceover, box = $("#vo-result");
  if (running && current.job.step === "voiceover") return box.replaceChildren(el("p", { class: "muted" }, "Splitting the recording… (progress above)"));
  if (!v) return box.replaceChildren();
  if (!v.split) return box.replaceChildren(el("p", { class: "muted" }, `${v.name} hasn't been split yet.`));
  const byId = Object.fromEntries(current.panels.map((p) => [p.id, p]));
  const check = (v.check || []).filter((x) => byId[x.id]);
  box.replaceChildren(
    el("p", {}, el("b", {}, v.name), ` (${lengthText(v.seconds)}): ${v.found} of ${plural(v.lines, "line")} found, `,
      `${Math.round(v.heard * 100)}% of the script's words heard.`),
    check.length ? el("div", {}, el("span", { class: "field-head" }, "Lines to check"),
      el("ul", { class: "vo-list" }, ...check.map((x) => el("li", {},
        el("span", { class: x.found ? "warn" : "bad" }, x.found ? `${Math.round(x.heard * 100)}% heard: ` : "not found: "),
        (byId[x.id].narration || "").trim())))) : el("p", { class: "ok" }, "Every line was found."),
    (v.extras || []).length ? el("div", {}, el("span", { class: "field-head" }, "Said, but not in the script"),
      el("ul", { class: "vo-list" }, ...v.extras.map((x) => el("li", {}, `${clock(x.start)} “${x.text}”`)))) : null,
    el("p", { class: "hint" }, "Names and unusual words aren't in the recognizer's vocabulary, so a line with them shows fewer words heard even when it was read right. Lines not found keep the voice they had; record again, or run Voice for them."));
}

$("#vo-file").addEventListener("change", () => renderVoiceover());
$("#vo-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#vo-script").textContent); $("#vo-copy").textContent = "Copied"; }
  catch { notify("Couldn't copy; select the script and copy it instead."); }
  setTimeout(() => { $("#vo-copy").textContent = "Copy"; }, 1500);
});
$("#vo-download").addEventListener("click", async () => {
  $("#vo-error").textContent = "";
  try { renderVoiceover(await api("/api/addons/vosk/install", { method: "POST" })); }
  catch (e) { $("#vo-error").textContent = e.message; }
});
$("#vo-start").addEventListener("click", async () => {
  const file = $("#vo-file").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  $("#vo-start").disabled = true;
  $("#vo-error").textContent = "";
  try {
    const r = await fetch(`/api/projects/${current.id}/chapters/${voChapter}/voiceover`, { method: "POST", body: form });
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    current.job = await r.json();
    $("#vo-file").value = "";
    renderJob();
    poll();
    renderVoiceover();
  } catch (e) { $("#vo-error").textContent = e.message; renderVoiceover(); }
});
$("#vo-remove").addEventListener("click", async () => {
  if (!await ask("Remove the recording?", "Its lines lose their voice (run Voice to have them read again). The recording file is deleted from the project.",
    { ok: "Remove", danger: true })) return;
  try {
    await api(`/api/projects/${current.id}/chapters/${voChapter}/voiceover`, { method: "DELETE" });
    await openProject(current.id);
    renderVoiceover();
  } catch (e) { $("#vo-error").textContent = e.message; }
});

// ---- the scan group's pages at the start and end of chapters (credits.py) ----

$("#find-credits").addEventListener("click", async () => {
  if (current.job && ACTIVE.includes(current.job.status)) return notify("Wait for the running job to finish first.");
  try {
    current.job = await api(`/api/projects/${current.id}/run`, { method: "POST",
      json: { steps: ["credits"], chapters: selectedCh.size ? [...selectedCh] : null } });
    renderJob();
    poll();
  } catch (e) { notify(e.message); }
});

// Every piece found as the scan group's pages, chapter by chapter in one dialog, to check what was left out
// and put back what shouldn't be (or leave out again one you put back).
function creditPieces(withKept) {
  return current.panels.filter((p) => p.credits && inScope(p.chapter) && (withKept || !p.enabled));
}

function renderCreditsCount() {
  const all = creditPieces(true), off = all.filter((p) => !p.enabled).length;
  $("#credits-count").hidden = !all.length;
  $("#credits-count").textContent = `Left out: ${plural(off, "piece")}…`;
}

function creditTile(p) {
  const tile = el("div", {});
  const fill = () => {
    const src = fileUrl(`panels/${p.file}`);
    tile.className = "credit-piece" + (p.enabled ? " kept" : "");
    tile.replaceChildren(
      el("img", { loading: "lazy", src, title: `Page ${p.page + 1}`, onclick: () => lightbox(src, p.id) }),
      el("span", { class: "piece-tag" }, p.enabled ? "put back" : "left out"),
      el("button", { type: "button", class: "small", title: p.enabled ? "Leave this piece out of the video again" : "Use this piece in the video",
        onclick: async (e) => {
          e.target.disabled = true;
          try { p = await editPanel(p.id, { enabled: !p.enabled }); } catch (err) { e.target.disabled = false; return notify(err.message); }
          fill();
          renderCreditsSummary();
          renderPanels();  // the scene list behind, and its count
          if (await followCopies([p], p.enabled)) renderCredits();
          p = current.panels.find((x) => x.id === p.id);
        } }, p.enabled ? "Leave out" : "Use it"));
  };
  fill();
  return tile;
}

function renderCreditsSummary() {
  const all = creditPieces(true), off = all.filter((p) => !p.enabled);
  const chapters = new Set(off.map((p) => p.chapter)).size;
  $("#credits-summary").textContent = all.length
    ? `${plural(off.length, "piece")} left out in ${plural(chapters, "chapter")}` +
      (all.length > off.length ? `, ${all.length - off.length} put back` : "") + (selectedCh.size ? " (selected chapters)" : "")
    : "Nothing is left out as the scan group's pages" + (selectedCh.size ? " in the selected chapters" : "") +
      ". Find them now (Settings for this project) looks for them.";
}

function renderCredits() {
  const withKept = $("#credits-kept").checked;
  const pieces = creditPieces(withKept);
  const sections = current.chapters.map((c) => {
    const mine = pieces.filter((p) => p.chapter === c.id);
    if (!mine.length) return null;
    return el("div", { class: "credits-chapter" },
      el("div", { class: "chapter-divider" }, `${c.name} · ${mine.length}`),
      el("div", { class: "credits-grid" }, ...mine.map(creditTile)));
  }).filter(Boolean);
  $("#credits-list").replaceChildren(...sections);
  renderCreditsSummary();
}

for (const b of document.querySelectorAll(".credits-open")) b.addEventListener("click", () => {
  renderCredits();
  $("#credits-dialog").showModal();
  // the first look for a piece's copies reads every chapter's ends (a few seconds); start it now
  const first = creditPieces(true)[0];
  if (first) api(`/api/projects/${current.id}/panels/${first.id}/same`).catch(() => {});
});
$("#credits-kept").addEventListener("change", renderCredits);

// ---- the project as one file (.recap): Save as file, and Open project file (package.py) ----

$("#save-file").addEventListener("click", async () => {
  if (current.job && ACTIVE.includes(current.job.status)) return notify("Wait for the running job to finish first.");
  if (!await ask("Save the project as a file?", "Everything in the project goes into one .recap file in its output folder: " +
    "pages (also ones imported from a folder), scenes, script, voice lines, characters and recordings. The rendered video " +
    "isn't included (it's made again from the rest). Open it here or on another computer with Open project file.",
  { ok: "Save" })) return;
  try {
    current.job = await api(`/api/projects/${current.id}/run`, { method: "POST", json: { steps: ["package"] } });
    renderJob();
    poll();
  } catch (e) { notify(e.message); }
});
$("#open-file").addEventListener("click", async () => {
  $("#open-file-status").textContent = "";
  $("#open-file-dialog").showModal();
  if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_project_file) {
    const path = await window.pywebview.api.pick_project_file();
    if (!path) { if (!$("#open-file-path").value) $("#open-file-dialog").close(); return; }
    $("#open-file-path").value = path;
    $("#open-file-go").click();
  } else {
    $("#open-file-path").focus();
  }
});
$("#open-file-path").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#open-file-go").click(); } });
$("#open-file-go").addEventListener("click", async () => {
  const path = $("#open-file-path").value.trim();
  if (!path) return;
  $("#open-file-go").disabled = true;
  $("#open-file-status").textContent = "Opening… (a big project takes a minute)";
  try {
    const r = await api("/api/projects/open-file", { method: "POST", json: { path } });
    $("#open-file-dialog").close();
    await loadProjects();
    await openProject(r.id);
  } catch (e) { $("#open-file-status").textContent = e.message; }
  $("#open-file-go").disabled = false;
});

// ---- first-run setup --------------------------------------------------------
// Walks a new user through what they'd otherwise have to hunt for in Settings: what this computer
// has (and the voice download if it's missing), the script writer with its key and model, the
// voice, the video, and the optional downloads. Each choice is saved as it's made, so leaving
// early keeps what was chosen; Settings → Run setup again reopens it.

const SETUP_PAGES = ["computer", "writer", "voice", "video", "extras", "done"];
const SETUP_TITLES = { computer: "Welcome to Recap Studio", writer: "Script writer", voice: "Voice", video: "Video",
  extras: "Optional downloads", done: "All set" };
const WRITERS = [
  ["claude", "Claude (Anthropic)", "Read the story best in testing: kept track of who was narrating, who said what and what was only imagined. From about 1¢ (Haiku) to 40¢ (Opus) per chapter."],
  ["openrouter", "OpenRouter", null],
  ["gemini", "Google Gemini", null],
  ["deepseek", "DeepSeek", null],
  ["groq", "Groq", null],
  ["mistral", "Mistral", null],
  ["ollama", "Local model (Ollama)", "Free and private: runs on this computer with Ollama. Much less accurate at following the story, and slower."],
];
const VOICE_LANGS = { a: "American English", b: "British English", e: "Spanish", f: "French", h: "Hindi", i: "Italian",
  j: "Japanese", p: "Brazilian Portuguese", z: "Mandarin" };
let setupPage = 0, setupPoll = null, setupKeyNote = null;
const setupWants = { ocr: true, scan: false, magi: false, magiLicense: false };

function openSetup(page = 0) {
  setupPage = page;
  renderSetup();
  if (!$("#setup-dialog").open) $("#setup-dialog").showModal();
}

async function saveGlobal(changes) {
  globals = await api("/api/settings", { method: "PUT", json: changes });
  if (current) Object.assign(current.settings, globals);
  renderAppLine();
}

function setupLine(ok, text, ...more) {
  return el("div", { class: `setup-line ${ok ? "ok" : "warn"}` }, el("span", { class: "mark" }, ok ? "✓" : "!"),
    el("div", {}, text, ...more));
}

async function renderSetup() {
  clearTimeout(setupPoll);
  const page = SETUP_PAGES[setupPage];
  $("#setup-title").textContent = SETUP_TITLES[page];
  $("#setup-stepno").textContent = `Step ${setupPage + 1} of ${SETUP_PAGES.length}`;
  $("#setup-back").hidden = setupPage === 0;
  $("#setup-skip").hidden = page === "done";
  $("#setup-next").textContent = page === "done" ? "Finish" : page === "extras" ? "Next (start the downloads)" : "Next";
  const body = el("div", { class: "setup-page" });
  $("#setup-body").replaceChildren(body);
  const pages = { computer: setupComputer, writer: setupWriter, voice: setupVoice, video: setupVideo,
    extras: setupExtras, done: setupDone };
  try { await pages[page](body); } catch (e) { body.append(el("p", { class: "error" }, e.message)); }
}

async function setupComputer(body) {
  const v = await api("/api/addons/voice");
  body.append(
    el("p", {}, "Recap Studio turns comic and webtoon chapters into narrated recap videos. A few choices first; you can change any of them later in Settings."),
    el("span", { class: "field-head" }, "This computer"),
    v.installed ? setupLine(true, `Voice model ready (${plural(env.voices.length, "voice")}).`)
      : setupLine(false, "The voice model isn't downloaded yet. It reads the narration aloud.", voiceDownload(v)),
    env.ffmpeg ? setupLine(true, "ffmpeg found: it puts the videos together.")
      : setupLine(false, "ffmpeg is missing, so videos can't be made. Run setup.ps1 again, or install ffmpeg and restart the app."),
    env.gpu.available ? setupLine(true, "NVIDIA GPU found: text reading and the voice run on it.")
      : setupLine(false, "No NVIDIA GPU found: text reading and the voice run on the CPU. That works, just more slowly."),
    env.video && env.video.nvenc ? setupLine(true, `Videos are encoded on the ${env.video.encoder || ""} GPU.`.replace("  ", " "))
      : setupLine(false, "Videos are encoded on the CPU (about half the speed of a GPU encoder)."));
}

function voiceDownload(st) {
  const box = el("div", { class: "setup-dl" });
  const draw = (s) => {
    clearTimeout(setupPoll);
    if (s.installed) { box.replaceChildren(el("span", { class: "ok" }, "Downloaded.")); loadEnv(); return; }
    const busy = s.state === "installing";
    box.replaceChildren(
      busy ? el("div", { class: "bar" }, el("div", { style: `width: ${Math.round(s.progress * 100)}%` })) : "",
      el("span", { class: s.error ? "error" : "muted" }, busy ? s.message : s.error ? `The download failed: ${s.error}`
        : `${s.download_mb} MB, from the kokoro-onnx project on GitHub.`),
      busy ? el("button", { type: "button", class: "ghost small", onclick: async () => draw(await api("/api/addons/voice/cancel", { method: "POST" })) }, "Cancel")
        : el("button", { type: "button", class: "small", onclick: async () => draw(await api("/api/addons/voice/install", { method: "POST" })) }, "Download the voice"));
    if (busy) setupPoll = setTimeout(async () => draw(await api("/api/addons/voice")), 800);
  };
  draw(st);
  return box;
}

function setupWriter(body) {
  const p = globals.llm_provider;
  body.append(
    el("p", {}, "Who writes the narration from your pages. The online services read each whole chapter, pictures included, and charge your own account per use."),
    el("div", { class: "setup-choices" }, ...WRITERS.map(([id, name, about]) =>
      el("label", { class: `setup-choice${id === p ? " on" : ""}` },
        el("input", { type: "radio", name: "setup-writer", value: id, checked: id === p,
          onchange: async () => { setupKeyNote = null; await saveGlobal({ llm_provider: id }); renderSetup(); } }),
        el("div", {}, el("b", {}, name), el("div", { class: "muted" }, about || env.writers[id].about))))),
    writerDetails(p));
}

function writerDetails(p) {
  const box = el("div", { class: "setup-details" });
  if (p === "ollama") {
    const models = env.ollama_models.map((m) => m.name);
    if (env.ollama_error) {
      box.append(el("p", {}, "Ollama isn't running. Install it from ollama.com, run ", el("code", {}, "ollama pull gemma3"),
        " (a model that can also look at the pages), then come back to this page."));
    } else if (!models.length) {
      box.append(el("p", {}, "Ollama is running but has no models yet. Run ", el("code", {}, "ollama pull gemma3"), ", then come back to this page."));
    } else {
      const sel = el("select", { onchange: () => saveGlobal({ ollama_model: sel.value }) },
        el("option", { value: "" }, "First installed model"), ...models.map((m) => el("option", { value: m }, m)));
      sel.value = models.includes(globals.ollama_model) ? globals.ollama_model : "";
      box.append(el("label", {}, "Model ", sel));
    }
    return box;
  }
  const claudeOn = p === "claude";
  const st = claudeOn ? env.claude : env.writers[p];
  const has = st.saved || st.env;
  if (claudeOn) {
    const note = el("span", { class: "hint" });
    const sel = el("select", {}, ...env.claude.models.map((m) => el("option", { value: m.id }, m.name)));
    const showNote = () => { note.textContent = (env.claude.models.find((m) => m.id === sel.value) || {}).note || ""; };
    sel.addEventListener("change", async () => { await saveGlobal({ claude_model: sel.value }); showNote(); });
    sel.value = globals.claude_model;
    showNote();
    box.append(el("label", {}, "Model ", sel), note);
  } else {
    const note = el("span", { class: "hint" });
    const input = el("input", { list: "setup-models", spellcheck: "false", autocomplete: "off", placeholder: "Type or pick a model" });
    input.value = globals[`${p}_model`] || st.default_model || "";
    input.addEventListener("change", async () => {
      await saveGlobal({ [`${p}_model`]: input.value.trim() });
      note.textContent = cloudModelNote(p, input.value.trim());
    });
    note.textContent = cloudModelNote(p, input.value);
    const result = el("span", { class: "hint" });
    const test = el("button", { type: "button", class: "ghost small", onclick: () => testModel(p, input.value.trim(), result) },
      "Test this model");
    box.append(el("label", {}, "Model ", input,
      el("datalist", { id: "setup-models" }, ...(cloudModels[p] || []).map((m) => el("option", { value: m.id }, m.name)))), note,
    ...(has ? [el("div", { class: "key-row" }, test), result] : []));
    if (has && !cloudModels[p]) {
      loadCloudModels(p).then(() => { if ($("#setup-dialog").open && SETUP_PAGES[setupPage] === "writer") renderSetup(); });
    }
  }
  const status = el("div", { class: setupKeyNote ? (setupKeyNote.ok ? "ok" : "bad") : has ? "ok" : "muted" },
    setupKeyNote ? setupKeyNote.text : has ? (st.saved ? "✓ A key is saved on this computer."
      : `✓ Using the ${claudeOn ? "ANTHROPIC_API_KEY" : st.env_name} environment variable.`)
      : `No key yet. Get one at ${claudeOn ? "console.anthropic.com" : st.keys_url.replace("https://", "")}, then paste it here.`);
  const input = el("input", { type: "password", placeholder: claudeOn ? "sk-ant-…" : "Paste your key", autocomplete: "off", spellcheck: "false" });
  const save = el("button", { type: "button" }, has ? "Replace the key" : "Save the key");
  save.addEventListener("click", async () => {
    const key = input.value.trim();
    if (!key) return;
    try {
      const r = await api(claudeOn ? "/api/claude/key" : `/api/writers/${p}/key`, { method: "PUT", json: { key } });
      Object.assign(st, { saved: r.saved, env: r.env });
      if (!claudeOn) delete cloudModels[p];
      setupKeyNote = { ok: r.ok, text: r.ok ? "✓ Key saved and working." : `Key saved, but: ${r.message}` };
    } catch (e) { setupKeyNote = { ok: false, text: e.message }; }
    renderSetup();
  });
  box.append(el("span", { class: "field-head" }, "API key"), status, el("div", { class: "key-row" }, input, save),
    el("span", { class: "hint" }, `Stored only on this computer. Billed to your ${claudeOn ? "Anthropic account, separate from a Claude subscription" : `${st.name} account`}.`));
  return box;
}

function setupVoice(body) {
  if (!env.tts_ready) {
    body.append(el("p", {}, "The voice model isn't downloaded yet. Go back to the first step to download it; you can carry on with setup meanwhile."));
    return;
  }
  const name = (v) => `${v}${VOICE_LANGS[v[0]] ? ` (${VOICE_LANGS[v[0]]}, ${v[1] === "m" ? "male" : "female"})` : ""}`;
  const sel = el("select", {}, ...env.voices.map((v) => el("option", { value: v }, name(v))));
  sel.value = globals.voice;
  sel.addEventListener("change", () => saveGlobal({ voice: sel.value }));
  const speed = el("input", { type: "number", step: "0.05", min: "0.5", max: "2" });
  speed.value = globals.speed;
  speed.addEventListener("change", () => saveGlobal({ speed: Number(speed.value) }));
  const play = el("button", { type: "button" }, "▶ Play a sample");
  play.addEventListener("click", async () => {
    play.disabled = true;
    play.textContent = "Making a sample…";
    try {
      const r = await fetch("/api/voice/sample", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice: sel.value, speed: Number(speed.value) }) });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      new Audio(URL.createObjectURL(await r.blob())).play();
    } catch (e) { notify(e.message); }
    play.disabled = false;
    play.textContent = "▶ Play a sample";
  });
  body.append(el("p", {}, "The voice that reads the narration. It runs on this computer (Kokoro), so there's nothing to pay. " +
    "Settings → Voice also has Microsoft Edge's online voices (free) and ElevenLabs (paid), and each chapter's 🎙 button takes your own recording."),
    el("label", {}, "Voice ", sel), el("label", {}, "Speed ", speed), el("div", {}, play));
}

function setupVideo(body) {
  const line = el("p", { class: "muted" });
  const showSpeed = async () => {
    const q = new URLSearchParams({ resolution: globals.resolution, fps: globals.fps, video_style: globals.video_style,
      video_encoder: globals.video_encoder });
    try {
      const r = await api(`/api/video-speed?${q}`);
      line.textContent = `With these choices, an hour of video takes ${aboutTime(r.seconds_per_hour)} to make on this computer.`;
    } catch { line.textContent = ""; }
  };
  const field = (key, label, options, numeric = false) => {
    const sel = el("select", {}, ...options.map(([v, t]) => el("option", { value: v }, t)));
    sel.value = String(globals[key]);
    sel.addEventListener("change", async () => { await saveGlobal({ [key]: numeric ? Number(sel.value) : sel.value }); showSpeed(); });
    return el("label", {}, `${label} `, sel);
  };
  body.append(el("p", {}, "How the videos look. Smaller sizes and lower frame rates are quicker to make."),
    field("orientation", "Format", [["landscape", "Landscape (YouTube)"], ["portrait", "Portrait (Shorts, TikTok, Reels)"]]),
    field("resolution", "Resolution", [[1080, "1080p"], [720, "720p"], [480, "480p"], [360, "360p"]], true),
    field("fps", "Frame rate", [[60, "60 fps"], [50, "50 fps"], [30, "30 fps"], [25, "25 fps"], [24, "24 fps"], [20, "20 fps"],
      [15, "15 fps"], [12, "12 fps"], [10, "10 fps"], [5, "5 fps (stills)"]], true),
    field("video_style", "Style", [["auto", "Automatic (scroll webtoons)"], ["motion", "Pan and zoom, scene by scene"],
      ["stills", "Stills, scene by scene"]]),
    ...(env.video && env.video.nvenc ? [field("video_encoder", "Encoder", [["auto", `${env.video.encoder || "GPU"} GPU (fast)`], ["cpu", "CPU only"]])] : []),
    line);
  showSpeed();
}

async function setupExtras(body) {
  const [o, c, m] = await Promise.all([api("/api/addons/ocr-en"), api("/api/addons/charscan"), api("/api/addons/magi")]);
  const row = (key, st, title, about) => (st.installed ? setupLine(true, `${title}: installed.`)
    : st.state === "installing" ? setupLine(true, `${title}: downloading (see Settings).`)
    : el("label", { class: "setup-choice" },
      el("input", { type: "checkbox", checked: setupWants[key], onchange: (e) => { setupWants[key] = e.target.checked; } }),
      el("div", {}, el("b", {}, title), el("div", { class: "muted" }, about))));
  const magiOpen = !m.installed && m.state !== "installing";
  body.append(el("p", {}, "Optional extras that improve the results. Tick what you want; it downloads in the background after you press Next, and you can add or remove these later in Settings."),
    row("ocr", o, `English text reading (${o.download_mb} MB)`, "Reads English comic lettering far more accurately than the built-in model. Recommended."),
    row("scan", c, `Character scan (${c.download_mb} MB)`, "Finds the characters in your chapters so you can name them. Named characters help the narrator tell who is who."),
    row("magi", m, `Speaker detection, Magi v2 (about ${m.download_gb} GB)`, `Guesses who says each line. ${m.license}`),
    magiOpen ? el("label", { class: "check setup-license" },
      el("input", { type: "checkbox", checked: setupWants.magiLicense, onchange: (e) => { setupWants.magiLicense = e.target.checked; } }),
      "For Magi: I'll use it only for personal, non-commercial purposes") : "");
}

async function startSetupDownloads() {
  const tries = [];
  if (setupWants.ocr) tries.push(api("/api/addons/ocr-en/install", { method: "POST" }));
  if (setupWants.scan) tries.push(api("/api/addons/charscan/install", { method: "POST" }));
  if (setupWants.magi) {
    if (!setupWants.magiLicense) return notify("Tick the box agreeing to Magi's license, or untick Magi.", "Magi's license");
    tries.push(api("/api/addons/magi/install", { method: "POST", json: { accept_license: true } }));
  }
  const failed = (await Promise.allSettled(tries)).filter((r) => r.status === "rejected" && !/already/i.test(r.reason.message));
  if (failed.length) notify(failed.map((r) => r.reason.message).join("\n"), "A download couldn't start");
  return true;
}

function setupDone(body) {
  const style = { auto: "", motion: ", pan and zoom", stills: ", stills" }[globals.video_style] || "";
  body.append(el("p", {}, "You're set. Everything chosen here is also in Settings, and Settings → Run setup again brings this back."),
    el("ul", { class: "setup-summary" },
      el("li", {}, `Script: ${writerLabel()}`),
      el("li", {}, `Voice: ${globals.voice}, speed ${globals.speed}`),
      el("li", {}, `Video: ${globals.resolution}p ${globals.orientation}, ${globals.fps} fps${style}`)),
    el("p", {}, $("#projects").children.length ? "Open a project from the list to carry on."
      : "To start, import a folder of chapter images: the steps across the top then turn it into a video."));
}

async function finishSetup() {
  clearTimeout(setupPoll);
  await saveGlobal({ setup_done: true });
  $("#setup-dialog").close();
  renderGlobal();
}

$("#setup-next").addEventListener("click", async () => {
  const page = SETUP_PAGES[setupPage];
  if (page === "done") return finishSetup();
  if (page === "extras" && !(await startSetupDownloads())) return;
  setupPage += 1;
  renderSetup();
});
$("#setup-back").addEventListener("click", () => { setupPage = Math.max(0, setupPage - 1); renderSetup(); });
$("#setup-skip").addEventListener("click", finishSetup);
$("#rerun-setup").addEventListener("click", () => { $("#global-dialog").close(); openSetup(0); });

// ---- updates from GitHub (updates.py): a button in the sidebar when a newer release is out, Settings → Updates ----
let appUpdate = null, updatePoll = null;
const updating = () => appUpdate && ["downloading", "installing"].includes(appUpdate.state);

async function loadUpdate() {
  try { appUpdate = await api("/api/update"); } catch { return; }
  renderUpdate();
  // the check runs in the background as the app starts: look again once GitHub has answered
  if (appUpdate.kind !== "source" && appUpdate.check_updates && !appUpdate.checked) setTimeout(loadUpdate, 3000);
  if (updating()) watchUpdate();
}

function renderUpdate() {
  if (!appUpdate) return;
  const u = appUpdate, latest = u.latest;
  const btn = $("#update-btn");
  btn.hidden = !(updating() || (u.available && !u.skipped));
  btn.disabled = updating();
  btn.textContent = u.state === "installing" ? "Installing the update…"
    : u.state === "downloading" ? `Downloading the update… ${Math.round(u.progress * 100)}%`
    : u.available ? `⬆ Update to ${latest.version}` : "";
  btn.title = u.message || "";
  $("#update-info").textContent = u.kind === "source" ? "Running from source: git pull updates it."
    : `Version ${u.version} (${u.kind === "installed" ? "installed" : "portable"}). ` +
      (u.state === "checking" ? "Checking GitHub…" : u.state === "error" ? `The last update failed: ${u.error}`
        : u.error ? u.error : !u.checked ? "" : u.available ? `${latest.version} is out.` : "This is the latest version.");
  $("#update-check").hidden = u.kind === "source";
  $("#update-check").disabled = updating() || u.state === "checking";
  $("#update-box [data-key=check_updates]").closest("label").hidden = u.kind === "source";
}

async function offerUpdate() {
  const u = appUpdate, latest = u.latest;
  const canInstall = !!(latest.asset && latest.asset.sha256);
  // the release notes as plain text: GitHub's Markdown without its heading and bold marks
  const notes = (latest.notes || "").replace(/^#+\s*/gm, "").replace(/\*\*(.+?)\*\*/g, "$1").replace(/^\* /gm, "- ").trim();
  const pick = await choose(`Recap Studio ${latest.version} is out`,
    `You have ${u.version}.` + (notes ? `\n\n${notes}` : "") + "\n\n" +
    (canInstall ? `Update now downloads it from GitHub (${Math.round(latest.asset.size / 1e6)} MB), checks it, closes ` +
      "Recap Studio, installs it and opens it again. Your projects, settings and downloads stay."
      : "This release can't be installed from here: download it from its page on GitHub."),
    [{ value: "skip", label: "Skip this version" },
      canInstall ? { value: "update", label: "Update now", primary: true } : { value: "page", label: "Open on GitHub", primary: true }]);
  try {
    if (pick === "skip") appUpdate = await api("/api/update/skip", { method: "POST", json: { version: latest.version } });
    else if (pick === "page") await api("/api/update/page", { method: "POST" });
    else if (pick === "update") {
      appUpdate = await api("/api/update/install", { method: "POST" });
      watchUpdate();
    }
  } catch (e) { notify(e.message, "Can't update now"); }
  renderUpdate();
}

function watchUpdate() {
  clearInterval(updatePoll);
  updatePoll = setInterval(async () => {
    try { appUpdate = await api("/api/update"); } catch { return; }  // (the app is closing for the update)
    renderUpdate();
    if (appUpdate.state === "error") {
      clearInterval(updatePoll);
      notify(`${appUpdate.error}\n\nNothing was changed. You can try again, or download it from its page on GitHub.`,
        "The update failed");
    } else if (!updating()) clearInterval(updatePoll);
  }, 700);
}

$("#update-btn").addEventListener("click", offerUpdate);
$("#update-check").addEventListener("click", async () => {
  $("#update-check").disabled = true;
  try { appUpdate = await api("/api/update/check", { method: "POST" }); } catch (e) { return notify(e.message); }
  renderUpdate();
  if (appUpdate.available) offerUpdate();
});
$("#update-notes").addEventListener("click", () => api("/api/update/page", { method: "POST" }));

(async () => {
  globals = await api("/api/settings");
  await loadProjects();
  const last = localStorage.getItem("last");
  if (last) { try { await openProject(last); } catch { localStorage.removeItem("last"); } }
  render();
  await loadEnv();
  renderGlobal();
  if (!globals.setup_done) openSetup();
  loadUpdate();
})();
