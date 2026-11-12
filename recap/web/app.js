const $ = (s) => document.querySelector(s);
const PAGE = 60;
const ACTIVE = ["queued", "running", "paused"];  // a job in one of these holds the project

let current = null;      // loaded project
let shown = PAGE;        // how many panel cards are rendered
let selectedCh = new Set();   // selected chapters (scope for every step); empty = all
let polling = null;
let env = null;
// (an app that doesn't say which system it runs on is on Windows: it was the only one)
const onWindows = () => !env || !env.os || env.os === "windows";
let newCounts = {};  // project id: new chapters at its source (sources.py), from the check when the app started

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers = { "Content-Type": "application/json" };
  }
  const r = await fetch(path, init);
  if (r.status === 401 && path !== "/api/auth/login") location.href = "/login";  // (a server's login ran out)
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    const err = new Error(msg);
    try { err.check = JSON.parse(r.headers.get("X-Site-Check") || "null"); } catch {}
    if (err.check) siteCheck(err.check, msg);
    throw err;
  }
  return r.json();
}

// A project, with its ETag kept: asked for again with it, the server answers 304 and nothing when the project hasn't
// changed (null here), so polling a big project costs neither it nor the page anything. fresh: the whole thing anyway.
const projectTags = new Map();  // project id -> the ETag of the copy this page holds
async function fetchProject(id, { fresh = false } = {}) {
  const tag = fresh ? null : projectTags.get(id);
  const r = await fetch(`/api/projects/${id}`, tag ? { headers: { "If-None-Match": tag } } : {});
  if (r.status === 304) return null;
  if (r.status === 401) location.href = "/login";
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  projectTags.set(id, r.headers.get("ETag"));
  return r.json();
}

// One search in several sources (their keys), got() called with each one's answer as soon as it comes: {key, items,
// more}, or {key, error} (with check, offered as api() does, when a site wants its check for a person). It's one
// request for them all, so the page's other requests (the results' covers) aren't kept waiting behind the searches.
// Done once every source has answered; signal (an AbortController's) stops it.
async function searchSources(keys, query, got, { signal } = {}) {
  const r = await fetch("/api/sources/search-many", { method: "POST", signal, headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keys, query, page: 1 }) });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  const reader = r.body.getReader(), text = new TextDecoder();
  let rest = "";
  for (;;) {
    const { value, done } = await reader.read();
    rest += text.decode(value || new Uint8Array(), { stream: !done });
    let end;
    while ((end = rest.indexOf("\n")) >= 0) {
      const line = rest.slice(0, end).trim();
      rest = rest.slice(end + 1);
      if (!line) continue;
      const one = JSON.parse(line);
      if (one.check) siteCheck(one.check, one.error);
      try { got(one); } catch (e) { console.error(e); }
    }
    if (done) return;
  }
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
// Whether a field in box is being typed in or chosen from, which redrawing it would undo (so the redraw waits). A
// focused button or tick box doesn't count: a dialog gives focus back to the button that opened it (Remove chapter).
const editingIn = (box) => box.contains(document.activeElement)
  && document.activeElement.matches("textarea, select, input:not([type=checkbox]):not([type=radio])");

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

// Where a project is, in a line, and its colour: "ask" (its job stopped to ask), "on" (working), "done" (video made)
function projectState(p) {
  const j = p.job || {};
  if (j.status === "paused" && j.pause_reason) return ["ask", "Stopped to ask"];
  if (ACTIVE.includes(j.status)) return ["on", j.message || "Working"];
  if (!p.chapters) return ["", "No chapters yet"];
  if (p.video) {
    // where it has gone up (the posting plugins record it), else that Publish has a title and thumbnail waiting
    const then = (p.posted || []).length ? ` · posted to ${p.posted.join(" and ")}` : p.youtube ? " · title and thumbnail ready" : "";
    return ["done", `Video made${p.seconds ? ` · ${lengthText(p.seconds)}` : ""}${then}`];
  }
  if (p.written === p.chapters) return ["", p.voiced === p.chapters ? "Voiced · no video yet" : `${p.voiced} of ${p.chapters} voiced`];
  if (p.written) return ["", `${p.written} of ${p.chapters} written`];
  if (p.found) return ["", `${p.found} of ${p.chapters} chapters read`];
  return ["", `${plural(p.chapters, "chapter")} · ${p.pages} pages`];
}

let projectList = [];  // the library, as /api/projects gave it
let libSort = null;    // how the cards are ordered: updated (last worked on) or name; remembered

async function loadProjects() {
  projectList = await api("/api/projects");
  try { newCounts = await api("/api/sources/new"); } catch {}
  drawLibrary();
  // the plugins' panels under the cards draw again with the projects (not on every keystroke of the search)
  for (const panel of libraryPanels) { try { panel.render(panel.box); } catch (e) { console.error(e); } }
}

// The rail's list, and the library's cards (the stages as a bar: chapters, characters, script and voice, video,
// publish): the ones the search finds, in the order picked. What needs you first above them.
function drawLibrary() {
  if (libSort == null) libSort = stored("lib-sort") || "updated";
  const list = projectList;
  const newTag = (p) => (newCounts[p.id] ? el("span", { class: "new-tag", title: "New chapters at its source" }, ` · ${newCounts[p.id]} new`) : null);
  $("#projects").replaceChildren(...list.map((p) => {
    const [cls, line] = projectState(p);
    const del = el("button", {
      class: "proj-del", title: `Delete ${p.name}`, "aria-label": `Delete project ${p.name}`,
      onclick: (e) => { e.stopPropagation(); deleteProject(p); },
    });
    del.innerHTML = TRASH_ICON;
    return el("li", { class: current && p.id === current.id ? "active" : "", onclick: () => openProject(p.id) },
      el("span", { class: `proj-dot ${cls}`.trim(), "aria-hidden": "true" }),
      el("div", { class: "proj-text" }, p.name, el("small", {}, line, newTag(p))),
      del);
  }));
  const words = $("#lib-find").value.trim().toLowerCase();
  const shown = list.filter((p) => !words || p.name.toLowerCase().includes(words))
    .sort((x, y) => (libSort === "name" ? x.name.localeCompare(y.name) : (y.updated || 0) - (x.updated || 0)));
  $("#lib-sort").textContent = libSort === "name" ? "Sort: by name" : "Sort: last worked on";
  $("#library-cards").replaceChildren(...shown.map((p) => {
    const [cls, line] = projectState(p);
    const done = [p.chapters > 0, p.characters > 0, p.chapters > 0 && p.voiced === p.chapters, p.video, p.youtube];
    const at = done.indexOf(false);
    const bar = el("div", { class: "stage-bar", "aria-hidden": "true" },
      ...done.map((d, i) => el("span", { class: d ? "done" : i === at && (cls === "on" || cls === "ask") ? cls : "" })));
    const cover = el("div", { class: `cover${p.cover ? " has-pic" : ""}` },
      el("span", {}, !p.chapters ? "No pages yet" : p.youtube ? "The video's thumbnail" : "Cover from the first page"));
    if (p.cover) cover.style.backgroundImage = `url("/files/${p.id}/${encodeURI(p.cover)}?t=${Math.round(p.updated || 0)}")`;
    return el("button", { type: "button", class: "proj-card", onclick: () => openProject(p.id) }, cover,
      el("div", { class: "card-body" },
        el("div", { class: "card-title" }, el("b", {}, p.name), p.kind ? el("span", { class: "tag" }, p.kind) : null),
        bar, el("div", { class: `card-line ${cls}`.trim() }, line, newTag(p))));
  }), words ? null : el("button", { type: "button", class: "proj-card new-card", onclick: () => openNew("") },
    el("b", {}, "＋ New project"), el("span", { class: "muted" }, "or drop a folder, .zip, .cbz or .recap here")));
  $("#empty-pick").hidden = list.length > 0 && (!words || shown.length > 0);
  $("#empty-pick").textContent = words && list.length ? "No recap is called that."
    : "No projects yet. Make one, open a project file, or drop a folder, .zip or .cbz file here.";
  // what needs you first: a recap that stopped to ask something
  const asks = list.filter((p) => p.job && p.job.status === "paused" && p.job.pause_reason);
  $("#library-attention").hidden = !asks.length;
  $("#library-attention").replaceChildren(...asks.map((p) => el("div", { class: "attention-item ask" },
    el("div", { class: "att-text" }, el("b", {}, `${p.name} stopped to ask`), el("span", {}, p.job.pause_reason)),
    el("button", { type: "button", onclick: () => openProject(p.id) }, "Answer"))));
}
$("#lib-find").addEventListener("input", drawLibrary);
$("#lib-sort").addEventListener("click", () => { libSort = libSort === "name" ? "updated" : "name"; store("lib-sort", libSort); drawLibrary(); });

// Settings covers the main area while the rail stays in reach: picking a project or the library there closes it,
// so what was picked shows (not the Settings page, with the project drawn behind it)
function closeSettings() {
  if ($("#global-dialog").open) $("#global-dialog").close();
}

// the brand at the top of the rail, and the Library link under it while a project is open: back to the library
function goLibrary() {
  closeSettings();
  if (!current) return;
  clearInterval(polling);
  current = null;
  localStorage.removeItem("last");
  render();
  loadProjects();
}
$("#brand").addEventListener("click", (e) => { e.preventDefault(); goLibrary(); });
$("#rail-back").addEventListener("click", (e) => { e.preventDefault(); goLibrary(); });

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
  closeSettings();
  if (!current || current.id !== id) {
    shown = PAGE;
    selectedCh = new Set(JSON.parse(localStorage.getItem(`sel:${id}`) || "[]"));
  }
  current = await fetchProject(id, { fresh: true });
  const ids = new Set(current.chapters.map((c) => c.id));
  selectedCh = new Set([...selectedCh].filter((c) => ids.has(c)));
  localStorage.setItem("last", id);
  render();
  loadProjects();
  if (current.job && ACTIVE.includes(current.job.status)) poll();
  emitPlugins("project-opened", { project: current });
}

// ---- New project: a few questions first (the kind of comic, its characters, how its video looks, YouTube); the
// video settings are the project's own from then on (its Video tab), the rest in its Project settings ----
const NEW_PAGES = ["comic", "characters", "narrator", "video", "content"];
const KINDS = [
  ["webtoon", "Webtoon", "One long vertical strip per chapter, read top to bottom.", { layout: "webtoon", reading_direction: "ltr" }],
  ["manga", "Manga", "Pages, read right to left.", { layout: "pages", reading_direction: "rtl" }],
  ["comic", "Comic", "Pages, read left to right.", { layout: "pages", reading_direction: "ltr" }],
  ["auto", "Not sure", "Recap Studio tells from the pages (reading left to right).", { layout: "auto", reading_direction: "ltr" }],
];
const STYLES = { auto: "Automatic", motion: "Pan and zoom", stills: "Stills" };
let newPage = 0, newName = "", newSettings = null;

const kindOf = (s) => (s.layout === "webtoon" ? "webtoon" : s.layout === "pages" ? (s.reading_direction === "rtl" ? "manga" : "comic") : "auto");

const CAPTION_NAMES = { classic: "CapCut classic", box: "highlight box", minimal: "minimal" };

function choiceList(name, options, value, onpick) {
  return el("div", { class: "setup-choices" }, ...options.map(([id, title, about]) =>
    el("label", { class: `setup-choice${id === value ? " on" : ""}` },
      el("input", { type: "radio", name, value: id, checked: id === value, onchange: () => onpick(id) }),
      el("div", {}, el("b", {}, title), about ? el("div", { class: "muted" }, about) : ""))));
}

// A pill per choice (New project's rows of round options), its description as the tooltip
function pillRow(name, options, value, onpick, small = false) {
  return el("div", { class: `np-pills${small ? " small" : ""}`, role: "radiogroup" }, ...options.map(([id, title, about]) =>
    el("label", { class: id === value ? "on" : "", title: about || null },
      el("input", { type: "radio", name, value: id, checked: id === value, onchange: () => onpick(id) }), title)));
}

// How long an hour of the video takes on this computer (/api/video-speed), and that it all can change later
function speedLine(s) {
  const line = el("span", { class: "np-line" }, "Any of this can change later in the project.");
  const q = new URLSearchParams({ resolution: s.resolution, fps: s.fps, video_style: s.video_style, video_encoder: globals.video_encoder });
  api(`/api/video-speed?${q}`).then((r) => {
    line.textContent = `An hour of this video takes ${aboutTime(r.seconds_per_hour)} to make on this computer. Any of this can change later in the project.`;
  }).catch(() => {});
  return line;
}

// New project, named (from the sidebar's box, or a plugin's RecapStudio.newProject), with the kind of comic and the
// series a plugin found it as, if it knows them
async function openNew(name, { kind = "", series = null } = {}) {
  try { newSettings = await api("/api/projects/new-settings"); } catch (err) { return notify(err.message); }
  newName = name;
  newFocus = true;
  const k = KINDS.find((x) => x[0] === kind);
  if (k) Object.assign(newSettings, k[3]);
  emitPlugins("new-project-open", { name: newName, series });
  renderNew();
  $("#new-dialog").showModal();
}

$("#new-project").addEventListener("submit", (e) => {
  e.preventDefault();
  openNew($("#new-name").value.trim());
});

// What a feature plugin is told about the New project dialog (RecapStudio.on("new-project-page")): it can name the
// project and say what kind of comic it is.
const newState = () => ({
  get name() { return newName; },
  get kind() { return kindOf(newSettings); },
  settings: newSettings,
  setName(v) { newName = String(v || ""); renderNew(); },
  redraw: () => renderNew(),
  setKind(id) {
    const k = KINDS.find((x) => x[0] === id);
    if (k) { Object.assign(newSettings, k[3]); renderNew(); }
  },
});

// The dialog's pages: its own, then the ones plugins add (RecapStudio.addNewProjectPage), before Create project.
const pluginPages = [];
const newPages = () => [...NEW_PAGES, ...pluginPages.map((p) => p.id)];

let newFocus = false;  // (the name box takes the focus when the dialog opens)

// One section's contents (the dialog's own pages, and a plugin's). The dialog's own are laid out as the mockup:
// the series, what it is, the characters in a sentence, the video as rows of choices and one row of checks.
function newPageBody(page, body) {
  const s = newSettings;
  const extra = pluginPages.find((p) => p.id === page);
  if (extra) {
    try { extra.render({ body, state: newState() }); } catch (e) { console.error(e); body.append(el("span", { class: "error" }, e.message)); }
  }
  const check = (key, text, attrs = {}) => el("label", { class: "check", ...attrs },
    el("input", { type: "checkbox", checked: !!s[key], onchange: (e) => { s[key] = e.target.checked; renderNew(); } }), ` ${text}`);
  const field = (label, key, options, number = false) => {
    const sel = el("select", { onchange: () => { s[key] = number ? Number(sel.value) : sel.value; renderNew(); } },
      ...options.map(([v, t]) => el("option", { value: v }, t)));
    sel.value = String(s[key] ?? options[0][0]);
    return el("label", {}, label, sel);
  };
  const add = (...kids) => body.append(...kids.filter((k) => k != null));  // (append would write "null")
  if (page === "comic") {
    const name = el("input", { value: newName, placeholder: "Its name", "aria-label": "The series' name",
      oninput: (e) => { newName = e.target.value; },
      onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); $("#new-next").click(); } } });
    const icon = el("span", { class: "np-search-icon", "aria-hidden": "true" });
    icon.innerHTML = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6">' +
      '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/></svg>';
    const lang = el("select", { onchange: (e) => { s.language = e.target.value; renderNew(); } },
      ...Object.entries(LANGUAGES).map(([v, t]) => el("option", { value: v }, v === "en" ? t : `${t}, translated into English`)));
    lang.value = s.language || "en";
    add(el("div", { class: "np-search" }, icon, name),
      el("h4", { class: "np-sub" }, "What it is"),
      pillRow("new-kind", KINDS.map(([id, title, about]) => [id, id === "auto" ? "Not sure, tell from the pages" : title, about]), kindOf(s),
        (id) => { Object.assign(s, KINDS.find((k) => k[0] === id)[3]); renderNew(); }),
      el("div", { class: "np-row" }, el("label", {}, "Its text is in", lang),
        isRaw(s) ? check("typeset", "Letter the English into the pictures", { title: `The ${LANGUAGES[s.language]} in speech bubbles ` +
          "and captions is painted out and the translation lettered in its place; signs get a small English caption, and sound " +
          "effects stay as drawn. Off, the pictures keep their own text and the narration tells the story." }) : null),
      isRaw(s) ? el("span", { class: "np-line" }, `Read in ${LANGUAGES[s.language]} and translated into English a chapter at a time, ` +
        "before the narration is written" + (["ko", "zh", "ja"].includes(s.language)
          ? "; the first time, a text reader for Korean, Chinese and Japanese is downloaded (about 35 MB)." : ".")) : null);
    if (newFocus) { newFocus = false; setTimeout(() => name.focus(), 0); }
  } else if (page === "characters") {
    const scope = el("select", { class: "np-inline", "aria-label": "Which chapters to scan",
      onchange: (e) => { s.characters_chapters = e.target.value; renderNew(); } },
      el("option", { value: "first" }, "in the first 5 chapters"), el("option", { value: "all" }, "in every chapter"));
    scope.value = s.characters_chapters === "all" ? "all" : "first";
    add(el("div", { class: "np-check-row" }, check("find_characters", "Find the characters"), s.find_characters ? scope : null,
      el("span", {}, s.find_characters ? "and suggest their names, so the narration can say who is who" : "once the text is read, and suggest their names")),
      s.find_characters ? el("div", { class: "np-indent" }, pillRow("new-review", [
        ["review", "Let me check the names", "Make the recap waits with the names filled in; add or change them, and it writes the script."],
        ["trust", "Trust them and go on", "Added as suggested and the script written straight away; look them over at Characters afterwards."]],
      s.characters_review === "trust" ? "trust" : "review", (v) => { s.characters_review = v; renderNew(); }, true)) : null,
      s.find_characters && !scanReady() ? el("span", { class: "np-line warn" }, `Needs the character scan (about ${(env && env.charscan && env.charscan.download_mb) || 586} MB), ` +
        "not downloaded yet: the Characters stage gets it. Until then the recap goes on without it.") : null);
  } else if (page === "narrator") {  // the persona; its rules and style keep theirs (Script and voice → The narrator)
    const builtIn = () => !!s.default_persona && (s.persona || "").trim() === s.default_persona.trim();
    const reset = el("button", { type: "button", class: "ghost small", hidden: builtIn(),
      onclick: () => { s.persona = s.default_persona; renderNew(); } }, "Use the built-in persona");
    const box = el("textarea", { class: "np-persona", rows: 4, "aria-label": "The narrator's persona",
      oninput: (e) => {  // (no redraw while typing: it would take the focus away)
        s.persona = e.target.value;
        autosize(e.target);
        reset.hidden = builtIn();
        $("#new-summary").replaceChildren(...newSummary());
      } });
    box.value = s.persona || "";
    add(el("span", { class: "np-line" }, "Who the narrator is and how they sound. Its rules and style can be changed later, " +
      "at Script and voice."), box, reset);
    requestAnimationFrame(() => autosize(box));
  } else if (page === "video") {
    const on = (s.captions || "off") !== "off";
    add(
      el("div", { class: "np-row" },
        field("Format", "orientation", [["landscape", "Landscape, for YouTube"], ["portrait", "Portrait, for Shorts and TikTok"]]),
        field("Size", "resolution", [[1080, "1080p"], [720, "720p"], [480, "480p"], [360, "360p"]], true),
        field("Style", "video_style", [["auto", "Automatic"], ["motion", "Pan and zoom"], ["stills", "Stills"]])),
      el("div", { class: "np-row" },
        field("Frame rate", "fps", [[60, "60 fps"], [50, "50 fps"], [30, "30 fps"], [25, "25 fps"], [24, "24 fps"], [20, "20 fps"],
          [15, "15 fps"], [12, "12 fps"], [10, "10 fps"], [5, "5 fps (stills)"]], true),
        field("Captions", "captions", [["off", "None"], ["classic", "CapCut classic"], ["box", "Highlight box"], ["minimal", "Minimal"]]),
        on ? field("Colour of the word being said", "captions_color",
          [["yellow", "Yellow"], ["green", "Green"], ["red", "Red"], ["purple", "Purple"], ["white", "White (not picked out)"]]) : null,
        Number(s.shorts) ? field("Shorts of up to", "shorts", [[60, "1 minute"], [90, "90 seconds"], [180, "3 minutes"]], true) : null),
      on ? el("img", { class: "caption-preview np-caption", alt: "How the captions look", src: captionsPreview(s) }) : null,
      el("div", { class: "np-checks" },
        check("background_music", "Background music", { title: "Your own tracks, added at the project's Video stage: they play in " +
          "turn until the video ends, evened out in loudness. Use music you're allowed to put on YouTube." }),
        el("label", { class: "check", title: "Cut where a line ends, with \"Part 1\", \"Part 2\"… at the top. Portrait suits Shorts, TikTok and Reels best." },
          el("input", { type: "checkbox", checked: !!Number(s.shorts), onchange: (e) => { s.shorts = e.target.checked ? 60 : 0; renderNew(); } }),
          " Also cut it into Shorts"),
        check("suggest_title", "Suggest a title and thumbnail", { title: "Your script writer picks a clickbait title (and a few others), " +
          "one or two pictures for the thumbnail and its labels; Recap Studio draws it. Both are at Publish, to change or suggest again." }),
        s.suggest_title ? check("redraw_thumbnail", "Then redraw the thumbnail with AI",
          { title: "An image model repaints it as one glossy illustration, a few cents each time." }) : null,
        check("youtube_safe", "YouTube-safe", { title: "For adult comics going on YouTube: sex scenes are written the way an R-rated " +
          "film shows them, and black boxes cover nudity in the video. It can make mistakes: read the script and look over the boxes " +
          "before you upload. Leave it off for everything else." })));
  } else if (page === "content") {  // (the lines under the video's checks: joins the video's section)
    add(
      s.suggest_title && s.redraw_thumbnail && redrawProblem() ? el("span", { class: "np-line warn" }, `Before the thumbnail can be redrawn, ${redrawProblem()}.`) : null,
      speedLine(s));
  }
  emitPlugins("new-project-page", { page, body, state: newState() });
}

// What the project will be, from its choices (beside the form)
function newSummary() {
  const s = newSettings, kind = KINDS.find((k) => k[0] === kindOf(s))[1];
  return [
    `${newName.trim() || "Untitled"}: ${kind === "Not sure" ? "the kind of comic told from the pages" : `a ${kind.toLowerCase()}`}` +
      (isRaw(s) ? ` in ${LANGUAGES[s.language]}, translated into English${s.typeset ? " and lettered into the pictures" : ""}` : ""),
    !s.find_characters ? "Characters: not looked for" : `Characters: found in ${s.characters_chapters === "all" ? "every chapter" : "the first 5 chapters"}, ` +
      (s.characters_review === "trust" ? "names added as suggested" : "names checked by you"),
    !(s.persona || "").trim() ? "Narrator: no persona, only its rules and style"
      : s.default_persona && s.persona.trim() === s.default_persona.trim() ? "Narrator: the built-in persona" : "Narrator: your own persona",
    `${s.resolution}p ${s.orientation}, ${s.fps} fps, ${STYLES[s.video_style] || s.video_style}` +
      (s.background_music ? ", with background music" : "") +
      ((s.captions || "off") !== "off" ? `, with ${CAPTION_NAMES[s.captions]} captions` : "") +
      (Number(s.shorts) ? `, cut into Shorts of up to ${Number(s.shorts) >= 120 ? `${Number(s.shorts) / 60} minutes` : `${s.shorts} seconds`}` : ""),
    s.suggest_title ? `A title and thumbnail suggested after the script${s.redraw_thumbnail ? ", then redrawn with AI" : ""}` : "No title or thumbnail suggested",
  ].map((x) => el("li", {}, x));
}

// The whole dialog on one page: its sections in order (the dialog's own, then the plugins'), the summary beside
function renderNew() {
  const titles = { comic: "The series", characters: "Characters", narrator: "The narrator", video: "The video",
    content: null };  // (content joins the video's)
  const scroll = $("#new-body").scrollTop;
  const sections = [];
  for (const page of newPages()) {
    const extra = pluginPages.find((p) => p.id === page);
    const body = el("div", { class: "setup-page" });
    newPageBody(page, body);
    const title = extra ? extra.title : titles[page];
    if (title == null && sections.length) { sections[sections.length - 1].append(body); continue; }
    sections.push(el("section", { class: "np-section", "data-page": page }, el("h3", {}, title), body));
  }
  $("#new-body").replaceChildren(...sections);
  $("#new-body").scrollTop = scroll;
  $("#new-summary").replaceChildren(...newSummary());
}

async function createProject(run) {
  const buttons = [$("#new-next"), $("#new-create-run")];
  buttons.forEach((b) => { b.disabled = true; });
  try {
    const p = await api("/api/projects", { method: "POST", json: { name: newName.trim() || "Untitled", settings: newSettings } });
    await emitPlugins("project-created", { project: p });
    $("#new-dialog").close();
    $("#new-name").value = "";
    await openProject(p.id);
    showTab("chapters");
    if (run) $("#go").click();
  } catch (e) { notify(e.message); }
  buttons.forEach((b) => { b.disabled = false; });
}
$("#new-next").addEventListener("click", () => createProject(false));
$("#new-create-run").addEventListener("click", () => createProject(true));

$("#project-name").addEventListener("change", async (e) => {
  await api(`/api/projects/${current.id}`, { method: "PATCH", json: { name: e.target.value } });
  loadProjects();
});

$("#delete-project").addEventListener("click", () => deleteProject({
  id: current.id, name: current.name, chapters: current.chapters.length, panels: current.panels.length,
}));

// ---- environment -----------------------------------------------------------

let globals = null;  // app-wide settings (writer, voice, video), shared by every project

// Buttons and hints that name Windows' own things, said Linux's way there
let toldWhyBrowser = false;
function systemWording() {
  if (env.server) {  // (on the network behind a login: no folders to show, no phone page, Settings → Server)
    for (const x of ["#show-folder", "#projects-show", "#open-folder", "#phone-box"]) $(x).hidden = true;
    $("#server-box").hidden = false;
    renderServer();
  }
  if (onWindows()) return;
  if (env.why_browser === "sandbox" && !toldWhyBrowser) {
    toldWhyBrowser = true;
    notify("Recap Studio opened in your browser: this Linux keeps Chromium's sandbox from apps it doesn't know, so " +
      "its own window can't start. The Linux page of Recap Studio's docs says how to get the window back.",
      "Opened in your browser");
  }
  const fm = env.file_manager || "your file manager";
  $("#show-folder").title = `Open this project's folder in ${fm}`;
  $("#projects-show").textContent = `Show in ${fm}`;
  $("#phone-firewall").textContent = "If a firewall runs on this computer (ufw or firewalld, say), it has to let other " +
    "devices on your network reach Recap Studio, or your phone can't connect.";
}

// Settings → Server (auth.py): the login, the API key, where it listens
async function renderServer(message = "") {
  let s;
  try { s = await api("/api/server"); } catch (e) { $("#server-error").textContent = e.message; return; }
  $("#server-who").textContent = `Signed in as ${s.user}. Listening on ${s.listening}.`;
  if (!$("#server-user").value) $("#server-user").value = s.user;
  $("#server-key").textContent = s.api_key;
  if (document.activeElement !== $("#server-host")) $("#server-host").value = s.host;
  if (document.activeElement !== $("#server-port")) $("#server-port").value = s.port;
  $("#server-address-note").textContent = message || (s.command_line
    ? "It was started with --host or --port, which win over these. Change them where it's started (its service)."
    : `${s.host}:${s.port}` !== s.listening ? `From the next start: ${s.host}:${s.port}.`
    : "0.0.0.0 listens on every network this server is on.");
  $("#server-error").textContent = "";
}
$("#server-save-login").addEventListener("click", async () => {
  try {
    await api("/api/auth/password", { method: "POST", json: { current: $("#server-current").value,
      user: $("#server-user").value.trim(), password: $("#server-new").value } });
    $("#server-current").value = $("#server-new").value = "";
    renderServer("The login is changed.");
  } catch (e) { $("#server-error").textContent = e.message; }
});
$("#server-logout").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  location.href = "/login";
});
$("#server-key-new").addEventListener("click", async () => {
  if (!await ask("Make a new API key?", "Programs using the old one (Claude Code's MCP server, scripts) stop until they have the new one.")) return;
  try { await api("/api/server/api-key", { method: "POST" }); renderServer(); }
  catch (e) { $("#server-error").textContent = e.message; }
});
$("#server-address-save").addEventListener("click", async () => {
  try {
    await api("/api/server", { method: "PUT", json: { host: $("#server-host").value.trim() || "0.0.0.0",
      port: Number($("#server-port").value) } });
    renderServer();
  } catch (e) { $("#server-error").textContent = e.message; }
});

// Linux has none of Windows' fonts (fonts.py): which ones stand in, and the open look-alikes to download
const FONT_LOOKS = { "Arial Black": "arial-black", "Segoe UI Black": "segoe-black", "Impact": "impact", "Comic Sans": "comic-bold" };
let fontsInfo = null;

async function loadFonts(refresh = false) {
  if (onWindows()) return null;
  if (!fontsInfo || refresh) fontsInfo = await api("/api/fonts");
  return fontsInfo;
}

function fontsDownload(after) {
  const button = el("button", { type: "button", class: "small" }, `Download them (${fontsInfo.size_mb} MB)`);
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Downloading…";
    try {
      fontsInfo = await api("/api/fonts/download", { method: "POST" });
      if ($("#captions-preview").dataset.src) $("#captions-preview").dataset.src = "";  // (drawn again in the new font)
      if (current) renderCaptionsBox();
      after();
    } catch (e) {
      notify(e.message);
      button.disabled = false;
      button.textContent = `Download them (${fontsInfo.size_mb} MB)`;
    }
  });
  return button;
}

// For setup's "This computer": nothing on Windows
async function fontsLine() {
  const f = await loadFonts();
  if (!f) return null;
  const shown = f.looks.filter((x) => Object.values(FONT_LOOKS).includes(x.id));
  const instead = shown.filter((x) => !x.exact).map((x) => `${x.name} with ${x.font}`).join(", ");
  if (f.downloaded) return setupLine(true, `Fonts: the open look-alikes are downloaded.${instead ? ` Linux draws ${instead}.` : ""}`);
  if (!f.offered) return setupLine(true, "Fonts: Microsoft's fonts are installed, so videos look as they do on Windows.");
  return setupLine(false, `Linux doesn't have Windows' fonts, so captions and thumbnails draw ${instead}. Free look-alikes ` +
    "(Anton, Archivo Black, Comic Neue and Noto Sans, from Google Fonts) look closer. ", fontsDownload(() => renderSetup()));
}

// Linux's AppImage in the applications menu (system.py): for setup's "This computer"; nothing elsewhere
async function menuLine() {
  if (onWindows()) return null;
  const m = await api("/api/menu");
  if (!m.offered) return null;
  if (m.added) return setupLine(true, "Recap Studio is in your applications menu.");
  const add = el("button", { type: "button", class: "small" }, "Add it");
  add.addEventListener("click", async () => {
    add.disabled = true;
    try { await api("/api/menu", { method: "POST" }); renderSetup(); }
    catch (e) { notify(e.message); add.disabled = false; }
  });
  return setupLine(false, m.elsewhere ? "Your applications menu starts another copy of Recap Studio. Put this one there instead? "
    : "Recap Studio isn't in your applications menu yet, so it starts from this file only. ", add);
}

async function loadEnv() {
  const url = globals ? globals.ollama_url : "http://localhost:11434";
  env = await api(`/api/status?ollama_url=${encodeURIComponent(url)}`);
  systemWording();
  const line = (ok, good, bad) => el("div", { class: ok ? "" : "bad" }, ok ? good : bad);
  const addon = env.gpu.addon || {};  // GPU support, the download
  // Ollama's reachability matters only when it's the writer: with another writer, the writer's name instead
  const ollama = globals && !["claude", "claude_code"].includes(globals.llm_provider) && !(env.writers && env.writers[globals.llm_provider]);
  $("#env").replaceChildren(
    ollama ? line(!env.ollama_error, `Ollama: ${env.ollama_models.length} model(s)`, "Ollama: not reachable")
      : el("div", {}, `Writer: ${globals ? writerLabel() : "…"}`),
    line(env.tts_ready, `Voice: ${env.voices.length} voices`, "Voice model missing (Settings → Run setup again)"),
    line(env.ffmpeg, "ffmpeg: found", "ffmpeg: missing"),
    line(env.gpu.available, `GPU: ${globals && !globals.use_gpu ? "available (turned off)" : "on"}`,
      addon.installed ? "GPU: restart to use GPU support" : env.gpu.card ? "GPU: text and voice on the CPU (CPU build)"
        : "GPU: not available (using CPU)"),
  );
  // the rail's foot: who writes and who reads (the mockup's one line)
  const voice = { edge: `Edge's ${globals && globals.edge_voice} voice`, elevenlabs: "an ElevenLabs voice" }[globals && globals.voice_provider]
    || `the ${globals ? voiceName(globals.voice, true) : "…"} voice`;
  $("#rail-line").textContent = globals ? `${writerLabel()} writes · ${voice}` : "";
  const used = [env.gpu.ocr && `text reading on ${env.gpu.ocr}`, env.gpu.voice && `voice on ${env.gpu.voice}`].filter(Boolean);
  $("#gpu-status").textContent = env.gpu.available
    ? `An NVIDIA GPU is available.${used.length ? ` Currently: ${used.join(", ")}.` : ""}`
    : env.gpu.card ? `Your ${env.gpu.card} encodes the videos, but text reading and the voice run on the CPU: this copy ` +
      "carries the CPU build of ONNX Runtime, the engine they run on." +
      (addon.installed ? " GPU support is downloaded: close Recap Studio and open it again to use it."
        : addon.offered ? " GPU support (below) runs them on the card." : "")
    : "No usable NVIDIA GPU was found, so everything runs on the CPU.";
  renderGpuSettings(addon);
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
  $("#voice-select").replaceChildren(...voices.map((v) => el("option", { value: v }, voiceName(v))));
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
  loadStorage();
  $("#global-dialog").show();
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
  $("#split-chapters-row").hidden = current.settings.video_split !== "chapters";
  $("#split-hours-row").hidden = current.settings.video_split !== "hours";
  $("#focus-select").querySelector("option[value=subject]").disabled = !focusInstalled;
  $("#typeset-row").hidden = $("#typeset-hint").hidden = !isRaw(current.settings);
  renderMusic();
  renderCaptionsBox();
}

// ---- captions in the video and Shorts (captions.py, video.make_shorts) ----

// a picture of the chosen caption style, over one of the project's pictures (or a plain one, for a new project)
function captionsPreview(s, pid) {
  const q = new URLSearchParams({ style: s.captions, color: s.captions_color || "yellow", font: s.captions_font || "Arial Black",
    orientation: s.orientation || "landscape", pid: pid || "" });
  return `/api/captions/preview.jpg?${q}`;
}

function renderCaptionsBox() {
  const s = current.settings, on = (s.captions || "off") !== "off";
  $("#captions-color-row").hidden = $("#captions-font-row").hidden = $("#captions-preview").hidden = !on;
  renderCaptionsFont(on ? s.captions_font || "Arial Black" : null);
  const src = on ? captionsPreview(s, current.id) : "";
  if (on && $("#captions-preview").dataset.src !== src) $("#captions-preview").src = $("#captions-preview").dataset.src = src;
  $("#shorts-label-row").hidden = !Number(s.shorts);
}

// On Linux, the font that stands in for the chosen one (fonts.py)
async function renderCaptionsFont(name) {
  const hint = $("#captions-font-hint");
  const f = name && await loadFonts().catch(() => null);
  const look = f && f.looks.find((x) => x.id === FONT_LOOKS[name]);
  hint.hidden = !look || look.exact;
  if (hint.hidden) return;
  hint.replaceChildren(`Linux doesn't have ${name}, so the captions use ${look.font}. `,
    ...(f.offered ? [`A free look-alike looks closer: `, fontsDownload(() => {})] : []));
}

// ---- background music (music.py): the project's own tracks, in the order they play ----

function renderMusic() {
  const tracks = current.music || [], on = !!current.settings.background_music;
  $("#music-volume-out").textContent = `${Math.round((current.settings.music_volume ?? 0.25) * 100)}%`;
  $("#music-warn").hidden = !on || tracks.length > 0;
  const move = (i, by) => {
    const order = tracks.map((t) => t.file);
    order.splice(i + by, 0, ...order.splice(i, 1));
    musicChange(api(`/api/projects/${current.id}/music/order`, { method: "PUT", json: { files: order } }));
  };
  $("#music-list").replaceChildren(...tracks.map((t, i) => el("div", { class: "music-track" },
    el("span", { class: "music-name", title: t.name }, `${i + 1}. ${t.name} `, el("span", { class: "muted" }, clock(t.seconds))),
    el("button", { type: "button", class: "ghost small", title: "Play it earlier", disabled: i === 0, onclick: () => move(i, -1) }, "↑"),
    el("button", { type: "button", class: "ghost small", title: "Play it later", disabled: i === tracks.length - 1,
      onclick: () => move(i, 1) }, "↓"),
    el("button", { type: "button", class: "ghost small danger", title: "Remove this track", onclick: () => musicChange(
      api(`/api/projects/${current.id}/music/${encodeURIComponent(t.file.slice("music/".length))}`, { method: "DELETE" })) },
    "✕"),
    el("audio", { controls: true, preload: "none", src: fileUrl(t.file.split("/").map(encodeURIComponent).join("/")) }))));
}

async function musicChange(request) {
  try {
    const p = await request;
    if (current && current.id === p.id) {
      current.music = p.music;
      renderMusic();
    }
  } catch (e) { notify(e.message); }
}

$("#music-volume").addEventListener("input", (e) => {
  $("#music-volume-out").textContent = `${Math.round(Number(e.target.value) * 100)}%`;
});
$("#music-add").addEventListener("click", () => $("#music-file").click());
$("#music-file").addEventListener("change", async (e) => {
  const files = [...e.target.files];
  e.target.value = "";
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  $("#music-status").textContent = `Adding ${plural(files.length, "track")}…`;
  await musicChange(api(`/api/projects/${current.id}/music`, { method: "POST", body: fd }));
  $("#music-status").textContent = "";
});

// The window's look (Settings → Look): the system's light or dark, or one of them (html[data-theme], style.css).
// Kept in localStorage too, so the page paints in it before the settings arrive (index.html's head applies it).
function applyTheme(theme) {
  const t = theme === "light" || theme === "dark" ? theme : "";
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
  store("theme", t);
}

function renderGlobal() {
  if (!globals) return;
  applyTheme(globals.theme);
  setInputs($("#global-dialog"), globals);
  const claudeOn = globals.llm_provider === "claude";
  const codeOn = globals.llm_provider === "claude_code";
  const cloudOn = !!(env && env.writers && env.writers[globals.llm_provider]);
  $("#global-dialog .claude-only").hidden = !claudeOn;
  $("#global-dialog .claudecode-only").hidden = !codeOn;
  $("#global-dialog .cloud-only").hidden = !cloudOn;
  $("#global-dialog .ollama-opts").hidden = claudeOn || codeOn || cloudOn;
  renderWriterCards();
  if (codeOn) renderClaudeCode();
  fillSelects();
  if (cloudOn) renderCloud();
  renderVoice();
  renderMagi();
  renderCharscanSettings();
  renderOcrSettings();
  renderCjkSettings();
  loadPhone();
  renderFocusSettings();
  renderBoxSettings();
  renderBubbleSettings();
  renderSourceSettings();
  renderPluginSettings();
  renderGpuSettings();
  renderUpdate();
  renderRedraw();
  renderSettingsNav();
}

// ---- GPU support (ONNX Runtime's GPU build, with CUDA and cuDNN): downloaded on request, never shipped ----

let gpuPoll = null, gpuBusy = false;
const gpuGB = (st) => (st.download_mb / 1000).toFixed(1);
const gpuDriver = (st) => `Your NVIDIA driver (${st.driver}) is older than the ${st.driver_needed} these libraries need: ` +
  "update it (nvidia.com/drivers) first, or text reading and the voice stay on the CPU.";
const gpuNote = (st) => st.installed && st.active ? `On: text reading and the voice run on your ${st.card}.`
  : st.installed ? "Downloaded. Close Recap Studio and open it again to use it."
  : "Removed. Text reading and the voice go back to the CPU when Recap Studio is next started.";

async function renderGpuSettings(st = null) {
  clearTimeout(gpuPoll);
  try { st = st && "state" in st ? st : await api("/api/addons/gpu"); } catch { return; }
  const busy = st.state === "installing", here = st.installed || st.active;
  $("#gpu-box").hidden = !st.offered;
  $("#gpu-about").textContent = "The GPU build of ONNX Runtime, the engine text reading and the voice run on, with NVIDIA's " +
    `CUDA and cuDNN libraries: a ${gpuGB(st)} GB download from PyPI (checked against its SHA-256s) into ${st.folder}, ` +
    `${st.unpacked_gb} GB once unpacked, not part of the app. It's used from the next start.`;
  $("#gpu-driver").hidden = !st.driver_old || here;
  $("#gpu-driver").textContent = st.driver_old ? gpuDriver(st) : "";
  $("#gpu-off").hidden = here || busy;
  $("#gpu-download").textContent = `Download GPU support (${gpuGB(st)} GB)`;
  $("#gpu-busy").hidden = !busy;
  $("#gpu-on").hidden = !here || busy;
  $("#gpu-note").textContent = gpuNote(st);
  $("#gpu-remove").hidden = !st.installed;
  $("#gpu-stage").textContent = st.message;
  $("#gpu-bar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
  $("#gpu-error").textContent = st.state === "error" ? `${st.message}: ${st.error}` : "";
  const finished = gpuBusy && !busy;
  gpuBusy = busy;
  if (busy) gpuPoll = setTimeout(() => renderGpuSettings(), 700);
  else if (finished) loadEnv();  // the GPU lines say it's downloaded
}
$("#gpu-download").addEventListener("click", async () => {
  try { renderGpuSettings(await api("/api/addons/gpu/install", { method: "POST" })); }
  catch (e) { $("#gpu-error").textContent = e.message; }
});
$("#gpu-cancel").addEventListener("click", async () => renderGpuSettings(await api("/api/addons/gpu/cancel", { method: "POST" })));
$("#gpu-remove").addEventListener("click", async () => {
  const st = await api("/api/addons/gpu");
  if (!await ask("Remove GPU support?", st.active ? "Text reading and the voice then run on the CPU again, from the next " +
    "start. Your NVIDIA card still encodes the videos." : `Its ${st.unpacked_gb} GB of files are deleted. Text reading and ` +
    "the voice stay on the CPU.", { ok: "Remove", danger: true })) return;
  try {
    renderGpuSettings(await api("/api/addons/gpu", { method: "DELETE" }));
    loadEnv();
  } catch (e) { $("#gpu-error").textContent = e.message; }
});

// ---- subject finder for pan and zoom (U2-Net-p): downloaded on request, never shipped ----

let focusPoll = null;
let focusInstalled = false;  // the subject finder, for a project's "Pan and zoom toward" (its Video tab)
async function renderFocusSettings(st = null) {
  clearTimeout(focusPoll);
  try { st = st || await api("/api/addons/focus"); } catch { return; }
  const busy = st.state === "installing";
  $("#focus-off").hidden = st.installed || busy;
  $("#focus-busy").hidden = !busy;
  $("#focus-on").hidden = !st.installed || busy;
  focusInstalled = st.installed;
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

// ---- bubble finder for cutting webtoons (comic-text-and-bubble-detector): downloaded on request, never shipped ----

let bubblesPoll = null;
async function renderBubbleSettings(st = null) {
  clearTimeout(bubblesPoll);
  try { st = st || await api("/api/addons/bubbles"); } catch { return; }
  const busy = st.state === "installing";
  $("#bubbles-off").hidden = st.installed || busy;
  $("#bubbles-busy").hidden = !busy;
  $("#bubbles-on").hidden = !st.installed || busy;
  $("#bubbles-stage").textContent = st.message;
  $("#bubbles-bar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
  $("#bubbles-error").textContent = st.state === "error" ? `${st.message}: ${st.error}` : "";
  if (busy) bubblesPoll = setTimeout(() => renderBubbleSettings(), 700);
}
$("#bubbles-download").addEventListener("click", async () => {
  try { renderBubbleSettings(await api("/api/addons/bubbles/install", { method: "POST" })); }
  catch (e) { $("#bubbles-error").textContent = e.message; }
});
$("#bubbles-cancel").addEventListener("click", async () => renderBubbleSettings(await api("/api/addons/bubbles/cancel", { method: "POST" })));
$("#bubbles-remove").addEventListener("click", async () => {
  if (!await ask("Remove the bubble finder?", "Chapters already cut keep their scenes. Webtoon chapters cut from now on " +
    "can be cut through speech bubbles again.", { ok: "Remove", danger: true })) return;
  try { renderBubbleSettings(await api("/api/addons/bubbles", { method: "DELETE" })); }
  catch (e) { $("#bubbles-error").textContent = e.message; }
});

$("#focus-remove").addEventListener("click", async () => {
  if (!await ask("Remove the subject finder?", "Scenes then zoom toward their middle again.", { ok: "Remove", danger: true })) return;
  try {
    renderFocusSettings(await api("/api/addons/focus", { method: "DELETE" }));  // (projects set to it zoom to the middle)
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

// ---- Phone access (phone.py): the phone page on the home network, linked by a QR code ----

function renderPhone(st) {
  $("#phone-on").checked = !!st.enabled;
  $("#phone-pair").hidden = !st.running;
  $("#phone-url").textContent = st.address || "";
  if (st.running) $("#phone-qr").src = `/api/phone/qr.png?t=${Date.now()}`;
  $("#phone-error").textContent = st.error || "";
}
async function loadPhone() {
  try { renderPhone(await api("/api/phone")); } catch { /* (an older server) */ }
}
$("#phone-on").addEventListener("change", async (e) => {
  try { renderPhone(await api("/api/phone", { method: "PUT", json: { enabled: e.target.checked } })); }
  catch (err) { $("#phone-error").textContent = err.message; }
});
$("#phone-forget").addEventListener("click", async () => {
  if (!await ask("Forget phones?", "A new code is made: phones that scanned the old one can't open Recap Studio until they scan the new one.",
    { ok: "Forget them" })) return;
  renderPhone(await api("/api/phone/key", { method: "POST" }));
});

// ---- the Korean, Chinese and Japanese text reader (ocr.CJK): downloaded on request or by Read text, never shipped ----

let cjkPoll = null;
async function renderCjkSettings(st = null) {
  clearTimeout(cjkPoll);
  try { st = st || await api("/api/addons/ocr-cjk"); } catch { return; }
  const busy = st.state === "installing";
  $("#cjk-off").hidden = st.installed || busy;
  $("#cjk-busy").hidden = !busy;
  $("#cjk-on").hidden = !st.installed || busy;
  $("#cjk-stage").textContent = st.message;
  $("#cjk-bar").style.width = `${Math.round((st.progress || 0) * 100)}%`;
  $("#cjk-error").textContent = st.state === "error" ? `${st.message}: ${st.error}` : "";
  if (busy) cjkPoll = setTimeout(() => renderCjkSettings(), 700);
}
$("#cjk-download").addEventListener("click", async () => {
  try { renderCjkSettings(await api("/api/addons/ocr-cjk/install", { method: "POST" })); }
  catch (e) { $("#cjk-error").textContent = e.message; }
});
$("#cjk-cancel").addEventListener("click", async () => renderCjkSettings(await api("/api/addons/ocr-cjk/cancel", { method: "POST" })));
$("#cjk-remove").addEventListener("click", async () => {
  if (!await ask("Remove the Korean, Chinese and Japanese reader?", "Text already read and translated is kept. Reading a comic " +
    "in one of those languages downloads it again.", { ok: "Remove", danger: true })) return;
  try { renderCjkSettings(await api("/api/addons/ocr-cjk", { method: "DELETE" })); }
  catch (e) { $("#cjk-error").textContent = e.message; }
});

// ---- Magi add-on (speaker detection): downloaded on request, never shipped with the app ----

let magiPoll = null, magiInstalled = false;
const showPictures = () => document.body.classList.toggle("has-pics", magiInstalled || scanReady());
async function renderMagi(st = null) {
  clearTimeout(magiPoll);
  try { st = st || await api("/api/addons/magi"); } catch { return; }
  magiInstalled = !!st.installed;
  showPictures();
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

// With the scan models installed, Characters is a step of its own (after Read text, where Make the recap finds
// them); without them the step bar is as before. Steps are numbered by what is shown.
const scanReady = () => !!(env && env.charscan && env.charscan.installed);
// a comic in Korean, Chinese or Japanese, translated by the script writer (translate.py)
const LANGUAGES = { en: "English", ko: "Korean", zh: "Chinese", ja: "Japanese" };
const isRaw = (s) => !!s && (s.language || "en") !== "en";
// a panel's text boxes all translated (as pipeline.translate_done: Read text again drops the English of boxes
// that read differently)
const translated = (p) => (p.bubbles || []).every((b) => "english" in b);

function renderSteps() {
  $("#step-characters").hidden = !scanReady();
  $("#step-translate").hidden = !current || !isRaw(current.settings);
  let n = 0;
  for (const b of document.querySelectorAll(".steps button[data-label]")) {
    if (!b.hidden) b.querySelector("span").textContent = `${++n} · ${b.dataset.label}`;
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
  $("#review-wait").hidden = !waitingForCharacters();
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
    (suggested.length ? ` Names suggested${suggested[0].suggestion.by ? ` by ${suggested[0].suggestion.by}` : ""}: check them before adding.` : "");
  $("#review-small-label").textContent = `Show small groups (${small.length})`;
  $("#review-names").textContent = suggested.length ? "Suggest names again" : "Suggest names";
  $("#review-names").disabled = $("#review-rescan").disabled = !!(current.job && ACTIVE.includes(current.job.status));
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
  const chips = (g.traits || []).map((t) => ({ ...t, on: true }));
  const name = el("input", { placeholder: "Name (of the character, or of the look)" });
  const aliases = el("input", { class: "aliases", placeholder: "Other names, comma separated (spellings, nicknames, fake names)" });
  const gender = el("select", {}, ...[["", "Gender"], ["female", "Female"], ["male", "Male"]].map(([v, l]) => el("option", { value: v }, l)));
  gender.value = (sug && sug.gender) || g.gender || "";
  const bigger = sug && sug.same_as && groups.find((o) => o.id === sug.same_as);
  const listed = sug && sug.character && current.characters.find((c) => c.id === sug.character);
  if (sug) {
    name.value = sug.name || (listed ? listed.name : "") || (bigger && bigger.suggestion ? bigger.suggestion.name : "");
    aliases.value = sug.aliases || "";
  }
  const desc = el("textarea", { rows: 2, placeholder: "Short description (appearance, role)" });
  // The story may not have given their name yet: they can be added under their role ("the Director"), which the
  // narrator uses as the story does; when a later scan's suggestion names them, the card offers to rename them.
  const roleOf = (words) => { const first = (words || "").split(",")[0].trim(); return first ? (/^(the|a|an) /i.test(first) ? first : `the ${first}`) : ""; };
  const unnamedBox = el("input", { type: "checkbox" });
  const unnamedRow = el("label", { class: "check unnamed-check", title: "The story hasn't said their name: add them under their role, and the narrator calls them that, as the story does. When a later chapter names them, the scan's suggestion offers to rename them." },
    unnamedBox, "Name not known yet: add them under their role");
  unnamedBox.addEventListener("change", () => { if (unnamedBox.checked && !name.value.trim()) name.value = roleOf(aliases.value); });
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
    const m = named.find((c) => c.id === g.match.character);
    const renames = !!(m.unnamed && sug && sug.name && !sug.extra);  // (the story has named them now)
    hints.push(el("button", { type: "button", class: "small",
      onclick: () => reviewAct(g, "add", { character: g.match.character, look: g.match.look || null, description: desc.value,
        ...(renames ? { name: sug.name, rename: true } : {}) }) },
      renames ? `${g.match.sure ? "Looks like" : "Might be"} ${m.name}, named ${sug.name} now: name them and add`
        : `${g.match.sure ? "Looks like" : "Might be"} ${whoLabel(g.match.character, g.match.look)}: add to them`));
  }
  for (const s of g.similar || []) {
    const o = others.find((x) => x.id === s.group);
    if (o) hints.push(el("button", { type: "button", class: "ghost small", onclick: () => mergeWith(o) },
      `Same as group ${o.id.slice(1)}?`));
  }
  if (listed && !(g.match && g.match.character === listed.id)) {
    hints.push(listed.unnamed && sug.name && !sug.extra
      ? el("button", { type: "button", class: "small",
        onclick: () => reviewAct(g, "add", { character: listed.id, name: sug.name, rename: true, description: desc.value, aliases: aliases.value }) },
        `The writer says ${listed.name} is ${sug.name}: name them and add`)
      : el("button", { type: "button", class: "small",
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
  // the other names, less the role when that is the name now
  const otherNames = (chosen) => aliases.value.split(",").map((a) => a.trim())
    .filter((a) => a && roleOf(a).toLowerCase() !== chosen.trim().toLowerCase() && a.toLowerCase() !== chosen.trim().toLowerCase()).join(", ");
  reviewFields.set(g.id, () => {
    const chosen = name.value.trim() || (unnamedBox.checked ? roleOf(aliases.value) : "");
    return { group: g.id, size: g.size, suggested: !!sug, extra: !!(sug && sug.extra), name: chosen, gender: gender.value,
      aliases: unnamedBox.checked ? otherNames(chosen) : aliases.value, description: desc.value, unnamed: unnamedBox.checked };
  });
  const add = el("button", { type: "button", class: "primary", onclick: () => {
    if (!name.value.trim() && unnamedBox.checked) name.value = roleOf(aliases.value);
    if (!name.value.trim()) { name.placeholder = unnamedBox.checked ? "Their role, e.g. the Director" : "Name it first"; name.focus(); return; }
    reviewAct(g, "add", { name: name.value, gender: gender.value, description: desc.value,
      aliases: unnamedBox.checked ? otherNames(name.value) : aliases.value, unnamed: unnamedBox.checked });
  } }, "Add as new character");
  return el("div", { class: "review-card" },
    el("div", { class: "snaps" }, ...g.snapshots.map((s) => el("img", { src: fileUrl(s.file), loading: "lazy", onclick: () => lightbox(fileUrl(s.file)) }))),
    el("div", { class: "review-body" },
    el("div", { class: "review-meta" }, el("b", {}, `Group ${g.id.slice(1)}`),
      ` · ${plural(g.size, "appearance")} in ${plural(g.pieces, "panel")} · ${g.chapters.join(", ")}`, ...hints),
    sug ? el("div", { class: "suggested" }, `${sug.sure ? "Suggested" : "A guess"}: `,
      el("b", {}, sug.name || (listed && listed.name) || (sug.extra ? "an extra" : "no name found")),
      sug.extra && (sug.name || listed) ? " · an extra" : "", bigger ? ` · same as group ${bigger.id.slice(1)}` : "",
      sug.aliases ? ` · also ${sug.aliases}` : "", sug.by ? el("span", { class: "muted" }, ` (${sug.by})`) : null) : null,
    chipRow,
    el("div", { class: "review-fields" }, name, gender, aliases, desc),
    unnamedRow,
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
      el("button", { type: "button", class: "ghost", onclick: () => reviewAct(g, "ignore") }, "Ignore"))));
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
  const service = env && env.writers && env.writers[globals.llm_provider];  // (env comes a few seconds after the page)
  if (globals.llm_provider === "claude_code") return `Claude Code (${globals.claude_code_model === "sonnet" ? "Sonnet" : "Opus"})`;
  return globals.llm_provider === "claude"
    ? ((env && env.claude ? env.claude.models.find((m) => m.id === globals.claude_model) : null) || { name: globals.claude_model }).name
    : service ? `${service.name} (${globals[`${globals.llm_provider}_model`] || service.default_model || "no model chosen"})`
    : `local model${globals.ollama_model ? ` (${globals.ollama_model})` : ""}`;
}

function readInput(input) {
  let v = input.type === "checkbox" ? input.checked : input.value;
  if (input.type === "number" || input.dataset.type === "number") v = Number(v);
  return v;
}

// A Project settings field is saved as soon as it changes (fields plugins add too: RecapStudio.addProjectSetting).
function bindProjectInput(input) {
  input.addEventListener("change", async () => {
    const p = await api(`/api/projects/${current.id}/settings`, { method: "PUT", json: { [input.dataset.key]: readInput(input) } });
    current.settings = p.settings;
    if (input.dataset.key === "youtube_safe") renderPanels();
    if (input.dataset.key === "language") render();  // (the Translate step, the translation in the Script tab)
    if (input.closest("#music-box")) renderMusic();
    if (input.closest("#captions-box") || input.dataset.key === "orientation") renderCaptionsBox();
    if (input.closest("#video-settings")) {  // (the split rows, the line above the steps, the time left)
      renderSettings();
      loadEstimate();
    }
  });
}
document.querySelectorAll(".proj-settings [data-key]").forEach(bindProjectInput);

// The writer as four cards (Settings → Script writer, as the mockup): Claude Code, Claude API, a cloud service (which
// one in the box below) and Ollama; picking one sets the Writer dropdown behind them, which saves as any setting.
const WRITER_CARDS = [
  ["claude_code", "Claude Code", "Your Claude subscription, through the Claude Code app on this PC. Nothing per recap."],
  ["claude", "Claude API", "Anthropic's API with your own key. A few cents a chapter."],
  ["cloud", "A cloud service", "Gemini, OpenRouter, DeepSeek, Groq or Mistral, with a key."],
  ["ollama", "Ollama, on this PC", "A local model. Free and private; the writing is plainer."],
];
const CLOUD_WRITERS = ["openrouter", "gemini", "deepseek", "groq", "mistral"];
function renderWriterCards() {
  const sel = document.querySelector('#global-dialog [data-key="llm_provider"]');
  if (!sel || !globals) return;
  const now = globals.llm_provider, kind = CLOUD_WRITERS.includes(now) ? "cloud" : now;
  const status = {
    claude_code: $("#cc-status").textContent, claude: $("#key-status").textContent, cloud: $("#cloud-key-status").textContent,
    ollama: env ? (env.ollama_error ? "Not reachable" : `${plural((env.ollama_models || []).length, "model")} installed`) : "",
  };
  const pick = (id) => {
    sel.value = id === "cloud" ? (CLOUD_WRITERS.includes(now) ? now : $("#cloud-service").value || "openrouter") : id;
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  };
  $("#writer-cards").replaceChildren(...WRITER_CARDS.map(([id, title, about]) => el("label", { class: `gs-card${id === kind ? " on" : ""}` },
    el("b", {}, el("input", { type: "radio", name: "writer-card", value: id, checked: id === kind, onchange: () => pick(id) }), title),
    el("span", {}, about), status[id] ? el("small", { class: id === kind ? "on" : "" }, status[id].trim()) : null)));
  if (CLOUD_WRITERS.includes(now)) $("#cloud-service").value = now;
}
$("#cloud-service").addEventListener("change", (e) => {
  const sel = document.querySelector('#global-dialog [data-key="llm_provider"]');
  sel.value = e.target.value;
  sel.dispatchEvent(new Event("change", { bubbles: true }));
});
// the cards' status lines follow the checks (Claude Code found, a key tested) as they answer
const writerWatch = new MutationObserver(() => { if ($("#global-dialog").open) renderWriterCards(); });
for (const id of ["cc-status", "key-status", "cloud-key-status"]) writerWatch.observe($(`#${id}`), { childList: true, characterData: true, subtree: true });

document.querySelectorAll("#global-dialog [data-key]").forEach((input) => {
  input.addEventListener("change", async () => {
    globals = await api("/api/settings", { method: "PUT", json: { [input.dataset.key]: readInput(input) } });
    if (current) Object.assign(current.settings, globals);
    if (["ollama_url", "use_gpu"].includes(input.dataset.key)) await loadEnv();
    if (input.closest("#sources-box")) await renderSourceSettings();
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
  box.textContent = !st.found ? `Claude Code wasn't found on this computer. Install it (claude.com/code)${onWindows() ? ", or the Claude desktop app, which includes it" : ""}.`
    : !st.logged_in ? `Claude Code ${st.version} is here but not logged in.`
    : !st.skill ? "Claude Code is ready, but the recap-studio-narration skill isn't next to the app (.claude\skills, beside the app's folder): its agents need the brief there."
    : `Claude Code ${st.version}, logged in. Ready.`;
}
$("#cc-check").addEventListener("click", () => renderClaudeCode(true));
// what to say once the sign-in was started: in a console window (a terminal on Linux), or the command to run
const claudeLoginSaid = (r) => r.started === false
  ? env && env.server ? `Sign in on the server itself: in its terminal (over SSH, as the user it runs as), run ${r.command}, then press Check again.`
    : `No terminal was found to sign in with. Open one, run ${r.command}, then press Check again.`
  : `Finish signing in in the ${onWindows() ? "window" : "terminal"} and browser that opened, then press Check again.`;

$("#cc-login").addEventListener("click", async () => {
  try {
    const r = await api("/api/claude-code/login", { method: "POST" });
    $("#cc-status").className = "muted";
    $("#cc-status").textContent = claudeLoginSaid(r);
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

// ---- Thumbnail redraw (youtube.REDRAW): an image model on the user's own key, the same key file as that
// service's script writer ----
const redrawModels = {};  // "gemini" -> [{id, name}]

// what's missing before Redraw with AI can work, or null
function redrawProblem() {
  const p = globals && globals.redraw_provider;
  if (!p || !env || !env.writers || !env.writers[p]) return "choose a service in Settings → Thumbnail redraw";
  const w = env.writers[p];
  return w.saved || w.env ? null : `add your ${w.name} key in Settings → Thumbnail redraw`;
}

function renderRedraw(message = null, ok = null) {
  const p = globals.redraw_provider;
  $("#redraw-opts").hidden = !p || !env.writers || !env.writers[p];
  if ($("#redraw-opts").hidden) return;
  const w = env.writers[p], r = env.redraw[p];
  $("#redraw-model").placeholder = r.default_model || "Pick a model";
  $("#redraw-model-list").replaceChildren(...(redrawModels[p] || []).map((m) => el("option", { value: m.id }, m.name)));
  const has = w.saved || w.env;
  $("#redraw-key-entry").hidden = has && !$("#redraw-key-entry").dataset.replacing;
  $("#redraw-key-actions").hidden = !has;
  $("#redraw-remove-key").hidden = !w.saved;
  const status = $("#redraw-key-status");
  status.className = ok === true ? "ok" : ok === false ? "bad" : "muted";
  status.textContent = message || (w.saved ? "A key is saved on this PC."
    : w.env ? `Using the ${w.env_name} environment variable.` : `No key yet. Get one at ${w.keys_url.replace("https://", "")}.`);
  $("#redraw-key-hint").textContent = `Stored only on this PC (data\\${p}_key.txt), the same key ${w.name} writes scripts with. ` +
    `Billed to your ${w.name} account.`;
  if (has && !redrawModels[p]) loadRedrawModels(p);
}

async function loadRedrawModels(p) {
  if (redrawModels[`${p}:loading`]) return;
  redrawModels[`${p}:loading`] = true;
  try {
    redrawModels[p] = (await api(`/api/redraw/${p}/models`)).models;
    $("#redraw-model-note").textContent = "";
  } catch (e) {
    redrawModels[p] = [];
    $("#redraw-model-note").textContent = e.message;
  } finally {
    delete redrawModels[`${p}:loading`];
  }
  if (globals.redraw_provider === p) renderRedraw();
}

$("#redraw-save-key").addEventListener("click", async () => {
  const p = globals.redraw_provider, key = $("#redraw-key").value.trim();
  if (!p || !key) return;
  try {
    const r = await api(`/api/writers/${p}/key`, { method: "PUT", json: { key } });
    $("#redraw-key").value = "";
    delete $("#redraw-key-entry").dataset.replacing;
    Object.assign(env.writers[p], { saved: r.saved, env: r.env });
    delete redrawModels[p];
    delete cloudModels[p];
    renderRedraw(r.ok ? "Key saved and working." : `Key saved, but: ${r.message}`, r.ok);
  } catch (e) { renderRedraw(e.message, false); }
});
$("#redraw-check-key").addEventListener("click", async () => {
  const p = globals.redraw_provider;
  renderRedraw("Checking…");
  const r = await api(`/api/writers/${p}/check`, { method: "POST" });
  renderRedraw(r.message, r.ok);
});
$("#redraw-change-key").addEventListener("click", () => {
  $("#redraw-key-entry").dataset.replacing = "1";
  renderRedraw();
  $("#redraw-key").focus();
});
$("#redraw-remove-key").addEventListener("click", async () => {
  const p = globals.redraw_provider, w = env.writers[p];
  if (!await ask("Remove the API key?", `The saved key is deleted from this PC. ${w.name} can't redraw thumbnails` +
    `${globals.llm_provider === p ? " or write scripts" : ""} until you add one again.`, { ok: "Remove", danger: true })) return;
  Object.assign(w, await api(`/api/writers/${p}/key`, { method: "DELETE" }));
  renderRedraw();
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

const GENDERS = [["", "Not said"], ["female", "Female"], ["male", "Male"]];

function renderCharacters() {
  $("#chars").replaceChildren(...current.characters.map((c, i) => {
    const field = (label, e, cls = "") => el("label", { class: `cfield ${cls}`.trim() }, label, e);
    const input = (key, placeholder, cls = "") => {
      const e = el("input", { placeholder, class: cls, value: c[key] || "" });
      e.addEventListener("change", () => { c[key] = e.value; saveCharacters(); });
      return e;
    };
    const gender = el("select", {}, ...GENDERS.map(([v, t]) => el("option", { value: v }, t)));
    if (c.gender && !GENDERS.some(([v]) => v === c.gender)) gender.append(el("option", { value: c.gender }, c.gender));  // (a word of its own, kept)
    gender.value = c.gender || "";
    gender.addEventListener("change", () => { c.gender = gender.value; saveCharacters(); });
    const desc = el("textarea", { rows: 2, placeholder: "Looks and role, e.g. blonde office worker, the main character" });
    desc.value = c.description || "";
    desc.addEventListener("change", () => { c.description = desc.value; saveCharacters(); });
    // a character the story hasn't named yet goes by their role; typing the name once it's given takes the mark off
    // and keeps the role among the other names (the saving listener runs first, so c.name is the new value here)
    const was = c.name;
    const nameInput = input("name", c.unnamed ? "Their role, until the story names them" : "As the narrator should say it", "char-name");
    if (c.unnamed) nameInput.addEventListener("change", () => {
      const now = (c.name || "").trim();
      if (!now || now.toLowerCase() === (was || "").trim().toLowerCase()) return;
      const have = (c.aliases || "").split(",").map((a) => a.trim()).filter(Boolean);
      if (was && !have.some((a) => a.toLowerCase() === was.trim().toLowerCase())) c.aliases = [...have, was.trim()].join(", ");
      c.unnamed = false;
      saveCharacters().then(renderCharacters);
    });
    const card = el("div", { class: "char-card" },
      field("Name", nameInput),
      field("Gender", gender),
      c.unnamed ? el("span", { class: "hint wide unnamed-note" }, "The story hasn't given their name: the narrator calls them this, as the story does. Type the name here once it's given; this role then goes under Other names.") : null,
      el("button", { class: "ghost danger remove", title: "Remove character", onclick: () => {
        current.characters.splice(i, 1);
        saveCharacters().then(renderCharacters);
      } }, "✕"),
      field("Other names", input("aliases", "Nicknames, other spellings, how the text reader misreads it: comma-separated"), "wide"),
      field("Description", desc, "wide"),
      c.source ? el("span", { class: "hint wide" }, `From ${c.source}`) : null,
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
  return el("div", { class: "char-pics wide magi-only" },
    ...(target.pictures || []).map((n) => el("span", { class: "pic" },
      el("img", { src: fileUrl(`characters/${n}`), alt: c.name, onclick: () => lightbox(fileUrl(`characters/${n}`)) }),
      el("button", { class: "ghost danger", title: "Remove this picture", onclick: () => {
        target.pictures = target.pictures.filter((x) => x !== n);
        saveCharacters().then(renderCharacters);
      } }, "✕"))),
    el("button", { class: "ghost small", title: "Pictures help Magi (Settings → Reading comics) and the character scan tell who is who",
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
  $("#chars .char-card:last-child .char-name")?.focus();
});

function renderPrompt(force = false) {
  // don't replace fields while the user is typing in them (polling re-renders every 1.5s)
  if (!force && (editingIn($("#prompt")) || editingIn($("#characters-page")))) return;
  const s = current.settings;
  $("#p-persona").value = s.persona || "";
  $("#p-note").value = s.note || "";
  requestAnimationFrame(() => autosize($("#p-persona")));
  listEditor($("#p-rules"), "rules", true);
  listEditor($("#p-styles"), "styles", false);
  renderCharacters();
}

// ---- adding pages ----------------------------------------------------------

let fileTypes = [];  // the kinds of file plugins import chapters from ({ext, name}: .epub), besides .zip and .cbz
const fileKinds = () => [".zip", ".cbz", ...fileTypes.map((t) => t.ext)];
const orList = (xs) => xs.length > 1 ? `${xs.slice(0, -1).join(", ")} or ${xs[xs.length - 1]}` : xs[0];
// The files a plugin's type couldn't read, and why
const skippedNote = (skipped) => (skipped || []).map((x) => `${x.file} is left out: ${x.reason.replace(/\.$/, "")}.`).join(" ");

function showFileTypes(types) {
  fileTypes = types || [];
  $("#drop-words").textContent = `Drop images, folders, ${orList(fileKinds())} files anywhere here to add chapters, or`;
  $("#file-input").accept = ["image/*", ...fileKinds()].join(",");
}

async function upload(files) {
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f, f.name);
  $("#page-count").textContent = `Uploading ${files.length} file(s)…`;
  try {
    const r = await api(`/api/projects/${current.id}/pages`, { method: "POST", body: fd });
    if (r.skipped && r.skipped.length) await notify(skippedNote(r.skipped), r.added ? "Not all of it was added" : "Nothing added");
    else if (!r.added) notify("No images were found in those files.", "Nothing added");
    noteDuplicates(r);
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
  let left = "";
  const path = $("#import-path").value.trim().replace(/^"|"$/g, "");
  if (!path) return;
  $("#import-status").textContent = "Scanning…";
  $("#import-list").replaceChildren();
  $("#import-tools").hidden = true;
  try {
    const r = await api(`/api/projects/${current.id}/scan-folder`, { method: "POST", json: { path } });
    scanned = r.chapters.map((c) => ({ ...c, on: !c.exists }));
    left = skippedNote(r.skipped);
  } catch (e) {
    $("#import-status").textContent = e.message;
    return;
  }
  if (!scanned.length) {
    $("#import-status").textContent = left || `No images or ${orList(fileKinds())} files found in that folder or its subfolders.`;
    return;
  }
  $("#import-status").textContent = left;
  $("#import-tools").hidden = false;
  renderScan();
}

function renderScan() {
  $("#import-list").replaceChildren(...scanned.map((c) => el("label", { class: "imp-row" },
    el("input", { type: "checkbox", checked: c.on, onchange: (e) => { c.on = e.target.checked; summarize(); } }),
    el("span", {}, c.name,
      c.kind === "archive" ? el("span", { class: "tag" }, "archive") : null,
      c.kind === "file" ? el("span", { class: "tag" }, c.type) : null,
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

// ---- getting chapters from a site (sources.py): a member's plugin, gallery-dl or Suwayomi ----

let srcAll = [];          // the sources that can be used now
let srcMode = "link";     // how a series is found: link, search, lists
let srcMore = null;       // loads the next page of results, when there is one
let srcSeries = null;     // {key, series} picked, with its chapters
let srcHave = new Set();  // the project's chapters from that series (source_ref)
const srcDlg = $("#sources-dialog");
const srcName = (key) => (srcAll.find((s) => s.key === key) || {}).name || key;

// find (RecapStudio.getChapters): a series to search every source for straight away, {titles, auto}
async function openSources(newOnly = false, find = null) {
  $("#src-status").textContent = "";
  $("#src-results").replaceChildren();
  $("#src-more").hidden = true;
  $("#src-go").disabled = true;
  srcDlg.showModal();
  if (newOnly) return openNewChapters();
  showSourceStep("find");
  srcDlg.classList.add("loading");  // (its dropdowns stay out of sight until there are sources to fill them)
  $("#src-status").textContent = "Looking for sources…";
  try { srcAll = (await api("/api/sources")).sources.filter((s) => s.chapters !== false); }  // (not series details only)
  catch (e) { $("#src-status").textContent = e.message; return; }
  srcDlg.classList.remove("loading");
  $("#src-status").textContent = "";
  $("#src-none").hidden = srcAll.length > 0;
  $("#src-find").hidden = !srcAll.length;
  const searchable = srcAll.filter((s) => s.search), listed = srcAll.filter((s) => s.lists.length);
  const opts = (list) => list.map((s) => el("option", { value: s.key }, s.name + (s.language ? ` (${s.language})` : "")));
  $("#src-search-source").replaceChildren(...[searchable.length > 1 ? el("option", { value: "*" }, "All your sources") : null,
    ...opts(searchable)].filter(Boolean));
  $("#src-list-source").replaceChildren(...opts(listed));
  fillSourceLists();
  document.querySelector('.src-modes [data-mode="search"]').disabled = !searchable.length;
  document.querySelector('.src-modes [data-mode="lists"]').disabled = !listed.length;
  setSourceMode(find && searchable.length ? "search"
    : srcMode === "search" && !searchable.length || srcMode === "lists" && !listed.length ? "link" : srcMode);
  if (find && searchable.length) {
    $("#src-search-source").value = $("#src-search-source").options[0].value;
    $("#src-query").value = find.titles[0] || "";
    return searchEvery(find);
  }
}

// The sources one search asks: the one picked, or all of them. Suwayomi's only in the project's language (or in
// every language), since a server can have dozens.
function searchTargets() {
  const key = $("#src-search-source").value, lang = (current && current.settings.language) || "en";
  return key !== "*" ? srcAll.filter((s) => s.key === key)
    : srcAll.filter((s) => s.search && (s.kind !== "suwayomi" || !s.language || s.language === "all" || s.language === lang));
}

const sameTitle = (t) => String(t || "").toLowerCase().normalize("NFKC").replace(/[^\p{L}\p{N}]+/gu, "");

// A series searched for in every source that can search (all at once), by its first titles in turn (up to three)
// until one finds something, each source's results shown as they come. Once all have answered, one series called
// exactly one of its titles is opened (auto: and all its chapters are got), unless one was picked meanwhile.
let srcSearch = null;  // the search running: a new one (or closing the dialog) stops it
async function searchEvery({ titles = [], auto = false }) {
  if (srcSearch) srcSearch.abort();
  const stop = srcSearch = new AbortController();
  const targets = searchTargets(), queries = [...new Set(titles.map((t) => String(t).trim()).filter(Boolean))].slice(0, 3);
  let found = [], searched = "";
  $("#src-more").hidden = true;
  for (const query of queries) {
    $("#src-query").value = query;
    $("#src-results").replaceChildren();
    $("#src-status").textContent = `Asking ${plural(targets.length, "source")} for "${query}"…`;
    found = [];
    try {
      await searchSources(targets.map((s) => s.key), query, (one) => {
        const source = targets.find((s) => s.key === one.key);
        if (!source) return;
        const r = { source, items: one.items || [], error: one.error };
        found.push(r);
        if (found.length < targets.length) {
          $("#src-status").textContent = `Asked ${found.length} of ${plural(targets.length, "source")} for "${query}"…`;
        }
        if (!r.items.length) return;
        if (targets.length > 1) $("#src-results").append(el("span", { class: "src-source-head" }, source.name));
        $("#src-results").append(...r.items.map((it) => sourceCard(source.key, it)));
      }, { signal: stop.signal });
    } catch (e) {
      if (e.name !== "AbortError" && srcSearch === stop) $("#src-status").textContent = e.message;
      return;
    }
    if (!srcDlg.open || srcSearch !== stop) return;  // (closed meanwhile, or searched again)
    searched = query;
    if (found.some((r) => r.items.length)) break;
  }
  srcSearch = null;
  const errors = found.filter((r) => r.error).map((r) => `${r.source.name}: ${r.error}`);
  $("#src-status").textContent = [found.some((r) => r.items.length) ? "" : "None of your sources found it.", ...errors]
    .filter(Boolean).join(" · ");
  if ($("#src-find").hidden) return;  // (a series was picked while the rest were answering)
  // the series called what was searched for; else called another of its titles
  const called = (names) => {
    const want = new Set(names.map(sameTitle).filter(Boolean)), out = [];
    for (const r of found) for (const it of r.items) {
      if (want.has(sameTitle(it.title)) && !out.some((m) => m.key === r.source.key && m.it.ref === it.ref)) out.push({ key: r.source.key, it });
    }
    return out;
  };
  let one = called([searched]);
  if (!one.length) one = called(titles);
  if (one.length !== 1) {
    if (one.length) $("#src-status").textContent = `${one.length} series in your sources are called that: pick one.`;
    return;
  }
  if (!auto) return openSeries(one[0].key, one[0].it.ref);
  $("#src-status").textContent = `Getting the chapters from ${srcName(one[0].key)}…`;
  let series;
  try { series = await api("/api/sources/series", { method: "POST", json: { key: one[0].key, ref: one[0].it.ref } }); }
  catch (e) { $("#src-status").textContent = e.message; return; }
  if (!series.chapters.length) return showSeries(one[0].key, series, false);  // (it says it found none)
  try {
    await getSourceChapters(one[0].key, series, series.chapters.map(({ ref, name, url }) => ({ ref, name, url })), "number", []);
  } catch (e) { showSeries(one[0].key, series, false); $("#src-series-status").textContent = e.message; return; }
  notify(`Getting all ${plural(series.chapters.length, "chapter")} of ${series.title} from ${srcName(one[0].key)}, the one ` +
    "of your sources that has a series called that. Check it's the right one in the Chapters tab.", "Getting its chapters");
}

function showSourceStep(step) {
  $("#src-find").hidden = step !== "find";
  $("#src-series").hidden = step !== "series";
  $("#src-none").hidden = true;
  $("#src-go").hidden = step !== "series";
  $("#src-title").textContent = step === "series" ? "Choose the chapters" : "Get chapters from a site";
}

const stopSourceSearch = () => { if (srcSearch) { srcSearch.abort(); srcSearch = null; } };
srcDlg.addEventListener("close", stopSourceSearch);

function setSourceMode(mode) {
  stopSourceSearch();
  srcMode = mode;
  document.querySelectorAll(".src-modes button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  document.querySelectorAll(".src-row").forEach((r) => { r.hidden = r.dataset.for !== mode; });
  $("#src-results").replaceChildren();
  $("#src-more").hidden = true;
  $("#src-status").textContent = mode === "link" && srcAll.length && !srcAll.some((s) => s.link)
    ? "None of your sources opens links: use Search or Lists." : "";
  if (mode === "lists") loadSourceList();
  (mode === "link" ? $("#src-link") : mode === "search" ? $("#src-query") : $("#src-list")).focus();
}

function fillSourceLists() {
  const s = srcAll.find((x) => x.key === $("#src-list-source").value);
  $("#src-list").replaceChildren(...(s ? s.lists : []).map((l) => el("option", { value: l.id }, l.name)));
}

document.querySelectorAll(".src-modes button").forEach((b) => b.addEventListener("click", () => setSourceMode(b.dataset.mode)));
$("#src-list-source").addEventListener("change", () => { fillSourceLists(); loadSourceList(); });
$("#src-list").addEventListener("change", loadSourceList);
$("#src-search-go").addEventListener("click", () => runSourceSearch());
$("#src-query").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); runSourceSearch(); } });
$("#src-link").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); openSourceLink(); } });
$("#src-link-go").addEventListener("click", openSourceLink);
$("#src-more").addEventListener("click", () => srcMore && srcMore());
$("#src-back").addEventListener("click", () => { showSourceStep("find"); $("#src-go").disabled = true; });
$("#src-open-settings").addEventListener("click", () => { srcDlg.close(); openSettings("Sources"); });

async function showResults(key, load, page = 1) {
  $("#src-status").textContent = page === 1 ? `Asking ${srcName(key)}…` : "Loading more…";
  $("#src-more").hidden = true;
  if (page === 1) $("#src-results").replaceChildren();
  let r;
  try { r = await load(page); }
  catch (e) { $("#src-status").textContent = e.message; return; }
  $("#src-status").textContent = page === 1 && !r.items.length ? "Nothing found." : "";
  $("#src-results").append(...r.items.map((it) => sourceCard(key, it)));
  srcMore = r.more ? () => showResults(key, load, page + 1) : null;
  $("#src-more").hidden = !r.more;
}

const sourceCard = (key, it) => el("button", {
  type: "button", class: "src-card", title: it.summary || it.title, onclick: () => openSeries(key, it.ref),
}, it.cover ? el("img", { src: it.cover, alt: "", loading: "lazy" }) : el("div", { class: "src-nocover" }),
el("span", {}, it.title));

function runSourceSearch() {
  const key = $("#src-search-source").value, query = $("#src-query").value.trim();
  if (!key || !query) return;
  if (key === "*") return searchEvery({ titles: [query] });
  stopSourceSearch();
  return showResults(key, (page) => api("/api/sources/search", { method: "POST", json: { key, query, page } }));
}

function loadSourceList() {
  const key = $("#src-list-source").value, list = $("#src-list").value;
  if (!key || !list) return;
  showResults(key, (page) => api("/api/sources/browse", { method: "POST", json: { key, list, page } }));
}

async function openSourceLink() {
  const url = $("#src-link").value.trim();
  if (!url) return;
  let found;
  try { found = await api(`/api/sources/link?url=${encodeURIComponent(url)}`); }
  catch (e) { $("#src-status").textContent = e.message; return; }
  if (found.ref) return openSeries(found.key, found.ref);
  if (found.key && found.search !== undefined) {  // a Suwayomi source: its server can't open links, so search it
    setSourceMode("search");
    $("#src-search-source").value = found.key;
    $("#src-query").value = found.search;
    await runSourceSearch();
    if (!$("#src-status").textContent) {
      $("#src-status").textContent = `Suwayomi can't open links, so ${srcName(found.key)} was searched for "${found.search}".`;
    }
    return;
  }
  $("#src-status").textContent = "None of your sources takes links from that site. Add a plugin for it, or " +
    "gallery-dl (Settings → Sources).";
}

async function openSeries(key, ref, newOnly = false) {
  $("#src-status").textContent = `Getting the chapters from ${srcName(key)}…`;
  let series;
  try { series = await api("/api/sources/series", { method: "POST", json: { key, ref } }); }
  catch (e) { $("#src-status").textContent = e.message; return; }
  $("#src-status").textContent = "";
  showSeries(key, series, newOnly);
}

function showSeries(key, series, newOnly) {
  srcHave = new Set(current.chapters.map((c) => c.source_ref).filter(Boolean));
  srcSeries = { key, series, newOnly, rows: series.chapters.map((c) => ({ ...c, have: srcHave.has(c.ref), on: !srcHave.has(c.ref) })) };
  showSourceStep("series");
  $("#src-back").hidden = newOnly;
  $("#src-cover").hidden = !series.cover;
  if (series.cover) $("#src-cover").src = series.cover;
  $("#src-name").textContent = series.title;
  $("#src-summary").textContent = series.summary;
  $("#src-from").textContent = `From ${srcName(key)}${series.url ? ` · ${series.url}` : ""}`;
  $("#src-place").replaceChildren(
    el("option", { value: "number" }, "In order, by chapter number"),
    el("option", { value: "end" }, "At the end"),
    el("option", { value: "start" }, "At the start"));
  $("#src-place-row").hidden = !current.chapters.length;
  $("#src-skip-row").hidden = !newOnly;
  $("#src-skip").checked = false;
  $("#src-series-status").textContent = series.chapters.length ? "" : newOnly
    ? "No new chapters: the project has every chapter the site lists." : "The source found no chapters in this series.";
  renderSourceChapters();
}

function renderSourceChapters() {
  const rows = srcSeries.rows;
  $("#src-chapters").replaceChildren(...rows.map((c) => el("label", { class: "imp-row" },
    el("input", { type: "checkbox", checked: c.on, onchange: (e) => { c.on = e.target.checked; summarizeSource(); } }),
    el("span", {}, c.name, c.have ? el("span", { class: "tag" }, "in the project") : null),
    el("span", { class: "muted" }, c.date || ""))));
  summarizeSource();
}

function summarizeSource() {
  const on = srcSeries.rows.filter((c) => c.on).length;
  $("#src-summary-line").textContent = `${plural(srcSeries.rows.length, "chapter")} · getting ${on}`;
  $("#src-go").disabled = on === 0 && !(srcSeries.newOnly && $("#src-skip").checked);
}

$("#src-all").addEventListener("click", () => { srcSeries.rows.forEach((c) => (c.on = true)); renderSourceChapters(); });
$("#src-none-btn").addEventListener("click", () => { srcSeries.rows.forEach((c) => (c.on = false)); renderSourceChapters(); });
$("#src-new").addEventListener("click", () => { srcSeries.rows.forEach((c) => (c.on = !c.have)); renderSourceChapters(); });
$("#src-skip").addEventListener("change", summarizeSource);

$("#src-go").addEventListener("click", async () => {
  const { key, series, rows, newOnly } = srcSeries;
  const chosen = rows.filter((c) => c.on).map(({ ref, name, url }) => ({ ref, name, url }));
  const skip = newOnly && $("#src-skip").checked ? rows.filter((c) => !c.on).map((c) => c.ref) : [];
  $("#src-go").disabled = true;
  try {
    await getSourceChapters(key, series, chosen, $("#src-place").value || "number", skip);
  } catch (e) {
    $("#src-series-status").textContent = e.message;
    $("#src-go").disabled = false;
  }
});

// Get chapters of a series into the open project (a job), closing the dialog
async function getSourceChapters(key, series, chosen, place, skip) {
  current.job = await api(`/api/projects/${current.id}/download`, {
    method: "POST", json: { key, series: { ref: series.ref, url: series.url, title: series.title }, chapters: chosen,
                            place, skip },
  });
  srcDlg.close();
  delete newCounts[current.id];
  render();
  poll();
}

async function openNewChapters() {
  srcAll = (await api("/api/sources").catch(() => ({ sources: [] }))).sources;
  showSourceStep("series");
  $("#src-chapters").replaceChildren();
  $("#src-series-status").textContent = "Looking for new chapters…";
  let r;
  try { r = await api(`/api/projects/${current.id}/source/new`); }
  catch (e) { $("#src-series-status").textContent = e.message; return; }
  newCounts[current.id] = r.series.chapters.length;
  showSeries(r.key, r.series, true);
  renderChapters();
}

$("#get-chapters").addEventListener("click", () => openSources());
$("#new-chapters").addEventListener("click", () => openSources(true));

// Settings → Sources: plugins (grouped by the list they came from), plugin lists, gallery-dl and Suwayomi
let srcDrawn = 0;                      // the latest drawing asked for: one that answers after a later one is dropped
let srcSwitching = Promise.resolve();  // switches sent one after another, so the last one clicked is what stays

// Switch plugins on or off without drawing the section again (that asks every source, which takes a while): every
// box for them follows at once, and goes back if the switch didn't take.
function switchPlugins(ids, on) {
  const boxes = ids.flatMap((id) => [...document.querySelectorAll(`input[data-src-plugin="${CSS.escape(id)}"]`)]);
  boxes.forEach((b) => (b.checked = on));
  groupCounts();
  srcSwitching = srcSwitching.then(async () => {
    try {
      await api(ids.length === 1 ? `/api/sources/plugins/${encodeURIComponent(ids[0])}` : "/api/sources/plugins",
        { method: "PUT", json: ids.length === 1 ? { enabled: on } : { ids, enabled: on } });
    } catch (e) {
      boxes.forEach((b) => (b.checked = !on));
      groupCounts();
      notify(e.message, "Not switched");
    }
  });
  return srcSwitching;
}

// each group's "N of M on", from its boxes
function groupCounts() {
  document.querySelectorAll("#src-plugins .src-group").forEach((g) => {
    const boxes = [...g.querySelectorAll("input[data-src-plugin]")];
    const count = g.querySelector(".src-group-count");
    if (count) count.textContent = `${boxes.filter((b) => b.checked).length} of ${boxes.length} on`;
  });
}

function sourcePluginRow(p) {
  return el("div", { class: "src-plugin" },
    el("label", { class: "check" },
      el("input", { type: "checkbox", "data-src-plugin": p.id, checked: p.enabled, disabled: !!p.error,
        onchange: (e) => switchPlugins([p.id], e.target.checked) }),
      el("b", {}, p.name), p.version ? ` ${p.version}` : ""),
    el("span", { class: p.error ? "error" : "muted" },
      p.error || [p.sites.join(", "), p.language, p.nsfw ? "adult" : "", p.chapters === false ? "series details, no chapters" : ""]
        .filter(Boolean).join(" · ")),
    el("button", { type: "button", class: "ghost small danger", onclick: async () => {
      if (!await ask(`Remove ${p.name}?`, "Its file is deleted from data\\plugins. Chapters already got stay."
        + (p.repo ? " Its list won't put it back (its Plugins… can). To stop using it for now, switch it off instead." : ""),
        { ok: "Remove", danger: true })) return;
      await api(`/api/sources/plugins/${p.id}`, { method: "DELETE" });
      renderSourceSettings();
    } }, "Remove"));
}

const listName = (url, st) => (st.lists[url] && st.lists[url].name) || url;

async function renderSourceSettings() {
  const me = ++srcDrawn;
  let st;
  try { st = await api("/api/sources"); } catch (e) { if (me === srcDrawn) $("#src-error").textContent = e.message; return; }
  if (me !== srcDrawn) return;
  // the plugins: a group for each list they came from (in the lists' order), then the rest
  const groups = new Map(st.repos.map((u) => [u, []]));
  const own = [], official = [];
  for (const p of st.plugins) (p.repo === st.official_list ? official : groups.has(p.repo) ? groups.get(p.repo) : own).push(p);
  const group = (title, list) => {
    if (!list.length) return null;
    const ids = list.filter((p) => !p.error).map((p) => p.id);
    return el("div", { class: "src-group" },
      el("div", { class: "src-group-head" }, el("span", { class: "field-head" }, title),
        el("span", { class: "muted src-group-count" }),
        list.length > 1 ? el("button", { type: "button", class: "ghost small", onclick: () => switchPlugins(ids, true) }, "All on") : null,
        list.length > 1 ? el("button", { type: "button", class: "ghost small", onclick: () => switchPlugins(ids, false) }, "All off") : null),
      ...list.map(sourcePluginRow));
  };
  const lists = [...groups].map(([u, list]) => group(listName(u, st), list));
  $("#src-plugins").replaceChildren(...[...lists, group("Recap Studio's list", official),
    group(lists.some(Boolean) || official.length ? "Your own" : "Plugins", own)].filter(Boolean));
  if (!st.plugins.length) $("#src-plugins").replaceChildren(el("span", { class: "muted" }, "No plugins."));
  groupCounts();
  $("#src-repos").replaceChildren(...st.repos.map((url) => {
    const box = el("div", { class: "src-repo-list" });
    const s = st.lists[url];
    const mine = (groups.get(url) || []).length;
    const when = s && s.at ? new Date(s.at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
    const note = el("span", { class: s && s.error ? "error src-repo-note" : "muted src-repo-note" },
      s && s.error ? `Couldn't check it: ${s.error}` : [plural(mine, "plugin"), when ? `checked ${when}` : ""].filter(Boolean).join(" · "));
    const row = el("div", { class: "src-repo" },
      el("span", { class: "src-repo-url", title: url }, listName(url, st)), note,
      el("button", { type: "button", class: "ghost small", title: "Install its new plugins and get new versions now, as when Recap Studio starts",
        onclick: async (e) => {
          e.target.disabled = true;
          note.textContent = "Checking…";
          let r;
          try { r = await api("/api/sources/repos/sync", { method: "POST", json: { url } }); }
          catch (err) { e.target.disabled = false; note.textContent = err.message; return; }
          await renderSourceSettings();
          if (r.synced.installed.length || r.synced.updated.length || r.synced.failed.length) notify(listSummary(r.name, r.synced), "Plugin list checked");
        } }, "Check now"),
      el("button", { type: "button", class: "ghost small", onclick: () => showRepo(url, box) }, "Plugins…"),
      el("button", { type: "button", class: "ghost small danger", onclick: async () => {
        const how = await choose(`Remove ${listName(url, st)}?`, mine
          ? `Its ${plural(mine, "plugin")} can go with it, or stay as plugins of your own (no longer kept up to date).`
          : "It's taken out of Settings.",
          [...(mine ? [{ value: "keep", label: "Keep its plugins" }] : []),
            { value: "all", label: mine ? "Remove its plugins too" : "Remove", danger: true }]);
        if (!how) return;
        try { await api("/api/sources/repos", { method: "DELETE", json: { url, plugins: how === "all" } }); }
        catch (err) { return notify(err.message); }
        renderSourceSettings();
      } }, "Remove"));
    return el("div", {}, row, box);
  }));
  const g = st.gallery_dl;
  $("#src-gdl-status").title = g.found ? g.path : "";
  $("#src-gdl-status").textContent = g.found ? `Found: gallery-dl ${g.version}.`
    : g.error || "Not found. Install gallery-dl yourself (it isn't part of the app), then give its program here if it isn't on PATH.";
  renderSuwayomi(st.suwayomi);
  $("#src-error").textContent = "";
}

// what checking a list did, in a line or two
function listSummary(name, s) {
  const done = [s.installed.length ? `${plural(s.installed.length, "plugin")} installed` : "",
    s.updated.length ? `${plural(s.updated.length, "plugin")} updated` : ""].filter(Boolean);
  let text = `${name}: ${done.length ? done.join(", ") : "nothing new"}.`;
  if (s.off.length) text += ` The adult ones are switched off (${s.off.join(", ")}): switch on the ones you use.`;
  if (s.failed.length) text += ` Not installed: ${s.failed.join("; ")}.`;
  return text;
}

function renderSuwayomi(s) {
  $("#src-suwa-status").textContent = s.ok ? `Connected: ${plural(s.sources, "source")}${s.version ? ` (Suwayomi ${s.version})` : ""}.`
    : `Not connected: ${s.error}`;
  if (document.activeElement !== $("#src-suwa-user")) $("#src-suwa-user").value = s.user || "";
}

$("#src-suwa-save").addEventListener("click", async () => {
  $("#src-suwa-status").textContent = "Connecting…";
  try {
    renderSuwayomi(await api("/api/sources/suwayomi-login", { method: "PUT",
      json: { user: $("#src-suwa-user").value, password: $("#src-suwa-pass").value } }));
  } catch (e) { $("#src-suwa-status").textContent = e.message; }
  $("#src-suwa-pass").value = "";
});

// a list's plugins: each one installed from it, with its switch; ones you removed, to install again
async function showRepo(url, box) {
  if (box.childElementCount && !box.dataset.loading) return box.replaceChildren();  // (Plugins… again: closed)
  box.dataset.loading = "1";
  box.replaceChildren(el("span", { class: "muted" }, "Loading…"));
  let r;
  try { r = await api(`/api/sources/repo?url=${encodeURIComponent(url)}`); }
  catch (e) { box.replaceChildren(el("span", { class: "error" }, e.message)); return; }
  finally { delete box.dataset.loading; }
  box.replaceChildren(...r.plugins.map((p) => el("div", { class: "src-plugin" },
    p.kind === "source" && p.installed && !p.elsewhere
      ? el("label", { class: "check" }, el("input", { type: "checkbox", "data-src-plugin": p.id, checked: p.enabled,
        onchange: (e) => switchPlugins([p.id], e.target.checked) }), el("b", {}, p.name), p.version ? ` ${p.version}` : "")
      : el("b", {}, p.name, p.version ? ` ${p.version}` : ""),
    el("span", { class: "muted" }, [p.summary, p.sites.join(", "), p.language, p.nsfw ? "adult" : "",
      p.elsewhere ? "you have one of your own by this name, so it's left alone" : "",
      p.left_out ? "you removed it" : "",
      p.kind === "feature" ? "adds to the app: Settings → Plugins" : ""].filter(Boolean).join(" · ")),
    p.kind === "source" && !p.installed && !p.elsewhere
      ? el("button", { type: "button", class: "ghost small", onclick: async (e) => {
        e.target.disabled = true;
        try { await api("/api/sources/repos/install", { method: "POST", json: { url, id: p.id } }); }
        catch (err) { e.target.disabled = false; return notify(err.message, "Not installed"); }
        await renderSourceSettings();
        box.replaceChildren();
        showRepo(url, box);
      } }, "Install") : null)));
}

$("#src-plugin-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  if (!await ask(`Add ${file.name}?`, "A plugin is a program: it runs on this PC with your permissions, and can read " +
    "and change your files. Add it only if you trust whoever wrote it.", { ok: "Add plugin" })) return;
  const fd = new FormData();
  fd.append("file", file);
  try { await api("/api/sources/plugins", { method: "POST", body: fd }); }
  catch (err) { return notify(err.message, "Not added"); }
  renderSourceSettings();
});

$("#src-repo-add").addEventListener("click", async (e) => {
  const url = $("#src-repo-url").value.trim();
  if (!url) return;
  if (!await ask("Add this list and install its plugins?", "Every source plugin on it is installed (adult ones "
    + "switched off), and kept up to date: each time Recap Studio starts, the list's new plugins are installed and new "
    + "versions got. Each one can be switched off. A plugin is a program: it runs on this PC with your permissions, so "
    + "add a list only if you trust whoever keeps it.", { ok: "Add and install" })) return;
  const button = e.target, was = button.textContent;
  button.disabled = $("#src-repo-url").disabled = true;
  button.textContent = "Installing…";
  $("#src-error").textContent = "";
  let r;
  try { r = await api("/api/sources/repos", { method: "POST", json: { url } }); }
  catch (err) { $("#src-error").textContent = err.message; return; }
  finally { button.disabled = $("#src-repo-url").disabled = false; button.textContent = was; }
  $("#src-repo-url").value = "";
  await renderSourceSettings();
  notify(listSummary(r.name, r.synced), "Plugin list added");
});

// ---- chapters --------------------------------------------------------------

function saveChapterSelection() {
  localStorage.setItem(`sel:${current.id}`, JSON.stringify([...selectedCh]));
}

// the ticked chapters: the main button and One step work on those (none ticked: all of them)
const chosenIds = () => [...selectedCh].filter((id) => current.chapters.some((c) => c.id === id));
const inScope = (chapterId) => selectedCh.size === 0 || selectedCh.has(chapterId);

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
  for (const c of current.chapters) stats[c.id] = { pages: 0, pieces: 0, panels: 0, text: 0, untranslated: 0, scenes: 0, script: 0, lines: 0, voice: 0 };
  for (const pg of current.pages) if (stats[pg.chapter]) stats[pg.chapter].pages++;
  for (const p of current.panels) {
    const s = stats[p.chapter];
    if (!s) continue;
    s.pieces++;  // (left out ones too: a chapter with every piece left out isn't "Not started")
    if (!p.enabled) continue;
    s.panels++;
    if (p.ocr_done || p.text) s.text++;
    if (!translated(p)) s.untranslated++;
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
  $("#chapters-box").classList.toggle("empty", current.chapters.length === 0);
  $("#chapter-count").textContent = current.chapters.length ? `${current.chapters.length} · ${current.pages.length} pages` : "";
  const fresh = newCounts[current.id];
  $("#new-chapters").hidden = !current.source;
  $("#new-chapters").textContent = fresh ? `${plural(fresh, "new chapter")}…` : "Check for new chapters";
  $("#new-chapters").classList.toggle("fresh", !!fresh);
  const busy = current.job && ACTIVE.includes(current.job.status);
  if (!editingIn(box)) {
    const stats = chapterStats();
    // the head's tick box ticks every chapter, or none
    const allOn = current.chapters.length > 0 && current.chapters.every((c) => selectedCh.has(c.id));
    const pickAll = el("input", { type: "checkbox", class: "ch-pick", title: "Tick every chapter, or none", checked: allOn,
      onchange: (e) => {
        selectedCh = e.target.checked ? new Set(current.chapters.map((c) => c.id)) : new Set();
        saveChapterSelection(); shown = PAGE; render();
      } });
    pickAll.indeterminate = !allOn && current.chapters.some((c) => selectedCh.has(c.id));
    const head = el("div", { class: "ch-row ch-head" }, pickAll, el("span", {}, "Chapter"),
      el("span", { class: "ch-cell" }, "Pages"), el("span", { class: "ch-cell" }, "Scenes"), el("span", { class: "ch-cell" }, "Script"),
      el("span", { class: "ch-cell" }, "Voice"), el("span", {}), el("span", {}), el("span", {}));
    box.replaceChildren(head, ...current.chapters.map((c, i) => {
      const s = stats[c.id];
      const cell = (n, of, title = "") => el("span", { class: `ch-cell${of && n === of ? " done" : ""}`, title }, of != null ? `${n}/${of}` : String(n));
      const name = el("input", { class: "ch-name", value: c.name, title: c.source || c.source_url || "" });
      name.addEventListener("change", async () => {
        await api(`/api/projects/${current.id}/chapters/${c.id}`, { method: "PATCH", json: { name: name.value } });
        c.name = name.value;
        renderPanels();
      });
      const status = chapterStatus(s);
      const flags = wordingFlags(c);
      return el("div", { class: "ch-row" + (selectedCh.has(c.id) ? " sel" : "") },
        el("input", {
          type: "checkbox", class: "ch-pick", checked: selectedCh.has(c.id), title: "Tick chapters to work on just those: the main button and One step then run on them",
          onchange: (e) => {
            e.target.checked ? selectedCh.add(c.id) : selectedCh.delete(c.id);
            saveChapterSelection();
            shown = PAGE;
            renderChapters(); renderScope(); renderPanels();
          },
        }),
        name,
        cell(s.pages),
        s.panels ? cell(s.scenes) : el("span", { class: "ch-cell muted", title: "Detect panels finds the scenes" }, "—"),
        s.panels ? cell(s.script, s.scenes, `${s.script} of ${s.scenes} scenes written` + (s.text < s.panels ? ` · text read in ${s.text} of ${s.panels} panels` : "")) : el("span", { class: "ch-cell muted" }, "—"),
        s.panels ? cell(s.voice, s.lines, (c.narrator ? `Narrated by ${c.narrator}` : "") + (c.narrator && c.narrator.startsWith("local model (fallback)") ? " (local fallback)" : "")) : el("span", { class: "ch-cell muted" }, "—"),
        el("span", { class: "ch-state" },
          el("span", { class: `pill ${status.kind}`, title: status.title || (c.narrator ? `Narrated by ${c.narrator}` : "") }, status.text),
          flags.length ? el("button", { class: "pill warn", disabled: busy && !waitingForWording(), onclick: () => checkWording(c, flags),
            title: `${plural(flags.length, "line")} may go further than YouTube-safe allows` }, "Check wording") : null),
        el("span", { class: "ch-actions" },
          el("button", { class: "ghost" + (c.voiceover ? " on" : ""), title: c.voiceover
            ? `Your own voice-over: ${c.voiceover.name}` : "Use your own recorded voice-over for this chapter", disabled: busy,
          onclick: () => openVoiceover(c) }, "🎙"),
          el("button", { class: "ghost", title: "Move up", disabled: i === 0 || busy, onclick: () => moveChapter(i, -1) }, "↑"),
          el("button", { class: "ghost", title: "Move down", disabled: i === current.chapters.length - 1 || busy, onclick: () => moveChapter(i, 1) }, "↓"),
          el("button", { class: "ghost danger", title: "Remove chapter", disabled: busy, onclick: () => deleteChapter(c) }, "✕")),
        el("button", { type: "button", class: "link ch-open", title: "Its scenes, in the script", onclick: () => openChapterScript(c) }, "Open"));
    }));
  }
}

// A chapter's scenes, in the script (the Open link of its row)
function openChapterScript(c) {
  sceneChapter = c.id;
  sceneSel = null;
  showTab("script");
  renderPanels();
}

// Where the chapters come from (the inspector's first section at Chapters): the series at its source, or your files
function renderSourceBox() {
  const s = current.source, n = current.chapters.length;
  const line = $("#source-line");
  if (s) {
    line.replaceChildren(el("b", {}, s.title || "The series"), ` · ${srcName(s.key).replace(/^plugin:/, "")} · ${plural(n, "chapter")} here`,
      el("div", {}, "Check for new chapters, above the list, looks for the ones the site has that the project doesn't."));
    $("#source-change").textContent = "Change the series or the source";
  } else {
    line.replaceChildren(n ? `${plural(n, "chapter")} from your files.` : "No chapters yet.",
      el("div", {}, "Get chapters from a site links the project to a series, which it can then check for new chapters."));
    $("#source-change").textContent = "Get chapters from a site…";
  }
}
$("#source-change").addEventListener("click", () => openSources());

// A YouTube-safe chapter's lines that the wording check flagged and that still read the same (pipeline.wording_flags)
function wordingFlags(c) {
  if (!c.wording || !c.wording.length) return [];
  const lines = new Set(sceneGroups(current.panels).filter((g) => g[0].chapter === c.id).map((g) => g[0].narration));
  return c.wording.filter((w) => lines.has(w.line));
}

// The job waiting at Voice for flagged lines to be checked (pipeline._wording_hold)
const waitingForWording = () => !!(current.job && current.job.status === "paused" && current.job.waiting_for === "wording");
// Waiting before the script in Make the recap: it carries on once each scanned character is added or ignored
const waitingForCharacters = () => !!(current.job && current.job.status === "paused" && current.job.waiting_for === "characters");
const waitingForCredits = () => !!(current.job && current.job.status === "paused" && current.job.waiting_for === "credits");

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
        for (let i = 0; i < 40 && ACTIVE.includes(((await api(`/api/projects/${current.id}/job`)) || {}).status); i++) {
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
$("#job-characters").addEventListener("click", () => openReview());
$("#job-wording").addEventListener("click", () => {
  const scope = current.job && current.job.chapters;
  const c = current.chapters.find((x) => (!scope || scope.includes(x.id)) && wordingFlags(x).length);
  if (c) checkWording(c, wordingFlags(c));
  else showTab("chapters");
});

// One word on how far a chapter is (simple mode's chapter list)
function chapterStatus(s) {
  if (!s.panels && s.pieces) return { kind: "warn", text: "All left out", title: `Every piece of it (${s.pieces}) was left out as ` +
    "the scan group's pages, as if the chapter were only a notice. To use it, put them back: The scan group's pages → " +
    "Check what was left out…, in this stage's panel." };
  if (!s.panels) return { kind: "new", text: "Not started" };
  if (s.untranslated && !s.script) return { kind: "todo", text: "Needs translation" };
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
    $("#go-note").title = "";
    if (current) $("#go-note").textContent = `Import a folder of chapters, or drop images, ${orList(fileKinds())} files on this page.`;
    return;
  }
  const chosen = chosenIds();
  const key = `${current.id}:${chosen.join(",")}`;
  if (!force && key === estimateFor && estimate) return renderEstimate();
  estimateFor = key;
  try {
    const e = await api(`/api/projects/${current.id}/estimate?chapters=${encodeURIComponent(chosen.join(","))}`);
    if (estimateFor === key) { estimate = e; renderEstimate(); }
  } catch { $("#go-note").title = ""; }
}

function renderEstimate() {
  if (!estimate) return;
  const names = { panels: "Detect panels", ocr: "Read text", characters: "Find characters", script: "Write script", title: "Suggest a title", audio: "Voice", video: "Render video" };
  const all = estimate.run_all;
  const parts = Object.entries(names).filter(([s]) => all[s] && all[s].seconds > 0)
    .map(([s, name]) => `${name} ${aboutTime(all[s].seconds).replace("about ", "")}`);
  $("#go-note").title = parts.length ? `All of it: ${aboutTime(all.total)} (${parts.join(" · ")})` : "";
  const todo = { panels: "find the panels", ocr: "read the text", characters: "find the characters", script: "write the script", title: "suggest a title", audio: "voice it", video: "render the video" };
  const left = Object.keys(todo).filter((s) => all[s] && all[s].seconds > 0);
  const what = left.map((s) => todo[s]);
  // with chapters ticked, the note says so: that's what the main button and One step work on
  const chosen = chosenIds();
  const ticked = chosen.length && chosen.length < current.chapters.length ? ` for the ${plural(chosen.length, "ticked chapter")}` : "";
  const about = aboutTime(all.total).replace(/^about/, "About");
  $("#go-note").textContent = left.some((s) => s !== "video")
    ? `${about}${ticked}: ${what.join(", ")}.`
    : current.output ? `Everything is done${ticked}. Making the video again picks up any changes.` : `${about} to render the video${ticked}.`;
  for (const b of document.querySelectorAll(".steps button[data-steps]")) {
    const step = b.dataset.steps;
    const left = estimate.missing[step], redo = estimate.redo[step];
    const text = !left ? "" : step === "video" ? `${aboutTime(redo.seconds)} to render`
      : `${left.amount ? `${aboutTime(left.seconds)} for what's left (${plural(left.amount, left.unit)})` : "All done"} · ${aboutTime(redo.seconds)} to redo all`;
    b.querySelector("small").textContent = text;
    b.title = text;
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

// what the ticked chapters mean is said in the main button's note (renderEstimate)
function renderScope() { loadEstimate(); }

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
  translate: { name: "Translate", unit: "panel", redo: "translates every text box again, replacing English you corrected" },
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
  if (step === "translate") {
    const read = panels.filter((p) => (p.bubbles || []).length);
    return { total: read.length, done: read.filter(translated).length };
  }
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
    const before = { ocr: "Detect panels", translate: "Read text", script: "Detect panels", audio: "Write script" }[step];
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

// The main button: Run all (it picks up where things left off and never redoes finished work), or Add chapters.
// Its Finding characters step scans a project never scanned (then waits for the review, or trusts the names, as the
// project says) before the script; after it, a YouTube title and thumbnail when the project asks for them.
$("#go").addEventListener("click", async () => {
  if (!current.chapters.length) return $("#import-folder").click();
  runSteps(["panels", "ocr", "translate", "characters", "script", "title", "audio", "video"], null, "missing");
});

const stepsMenu = $("#steps-menu");
function setStepsMenu(open) {
  stepsMenu.hidden = !open;
  $("#go-steps").setAttribute("aria-expanded", open ? "true" : "false");
}
$("#go-steps").addEventListener("click", (e) => { e.stopPropagation(); setMenu(false); setStepsMenu(stepsMenu.hidden); });

document.querySelectorAll(".steps button[data-steps]").forEach((b) =>
  b.addEventListener("click", async () => {
    if (!current.chapters.length) return notify("Import a folder or add pages first.", "No pages yet");
    const steps = b.dataset.steps.split(",");
    if (steps.includes("script") && !await charactersReady()) return;
    // Run all picks up where things left off and never redoes finished work
    if (steps.length > 1) return runSteps(steps, null, "missing");
    runStep(steps[0]);
  }));

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
    const id = current.id;
    const job = await api(`/api/projects/${id}/job`);  // (the job alone: the project itself only when it matters)
    if (!current || current.id !== id) return;
    if (!job) return clearInterval(polling);
    const finished = !ACTIVE.includes(job.status);
    // refresh the lists when a step finishes, and while panels/text/script are streaming in
    const streaming = job.status === "running" && ["panels", "ocr", "script", "download"].includes(job.step);
    // (and when it pauses or resumes: Check wording works while Voice waits for it)
    const changed = job.step !== lastStep || job.status !== lastStatus;
    if (finished || changed || streaming) {
      const p = await fetchProject(id);  // null: nothing in the project has changed since this page got it
      if (!current || current.id !== id) return;
      if (p) current = p; else current.job = job;
      if (p || finished || changed) render(); else renderJob();
      if (job.status === "paused" && lastStatus !== "paused" && waitingForCharacters()) openReview();
      if (job.status === "paused" && lastStatus !== "paused" && waitingForCredits()) openCredits();
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

const liveJobs = new Set();  // jobs seen queued or running on this page (renderJob)

function renderJob() {
  const j = current.job;
  const box = $("#job");
  box.hidden = !j || j.status === "done" && Date.now() / 1000 - j.created > 600;
  const running = !!j && ACTIVE.includes(j.status);
  // (Import folder and dropping files stay open: a job goes on with the chapters it started with)
  document.querySelectorAll(".steps button, #go, #go-steps, #clear-pages").forEach((b) => (b.disabled = running));
  $(".go-row").hidden = running;
  if (!j) return;
  const left = timeLeft(j);
  const more = j.steps && j.step ? j.steps.length - j.steps.indexOf(j.step) - 1 : 0;
  $("#job-msg").textContent = j.status === "queued" ? "Waiting…"
    : j.message + (left != null ? ` · ${aboutTime(left)} left${more ? ` in this step (${plural(more, "more step")} after)` : ""}` : "")
      + (running && j.ahead ? ` · ${j.ahead}` : "");  // (Make the recap voicing and rendering beside the script)
  $("#job-bar").style.width = `${Math.round((j.progress || 0) * 100)}%`;
  $("#job-error").textContent = j.error || "";
  if (running) liveJobs.add(j.id);
  if (j.status === "error" && j.check && liveJobs.delete(j.id)) siteCheck(j.check, j.error);  // (failed while open here)
  $("#job-cancel").hidden = !running;
  // Pause takes effect after the item being worked on (for Claude, the chapter), so it can say "Pausing…" for a bit
  const pause = $("#job-pause");
  pause.hidden = !running;
  pause.textContent = j.status === "paused" ? "Resume" : j.pause ? "Pausing… (resume)" : "Pause";
  $("#job-wording").hidden = !waitingForWording();
  $("#job-characters").hidden = $("#review-wait").hidden = !waitingForCharacters();
  $("#job-credits").hidden = $("#credits-wait").hidden = !waitingForCredits();
  box.classList.toggle("paused", j.status === "paused");
}

// ---- the YouTube title and thumbnail (the YouTube tab): suggested after the script (youtube.py), or with Suggest ----
const MARK_NAMES = { label: "Label", reaction: "Reaction", line: "Speech bubble" };
let redrawing = false;  // Redraw with AI is waiting for the image model

function renderYouTube() {
  const yt = current.youtube;
  const busy = !!(current.job && ACTIVE.includes(current.job.status));
  $("#yt-out").hidden = !yt;
  $("#yt-empty").hidden = !!yt;
  $("#yt-suggest").textContent = yt ? "Suggest again" : "Suggest a title and thumbnail";
  $("#yt-suggest").disabled = busy;
  $("#yt-ai-go").hidden = !yt;
  $("#yt-ai-go").disabled = busy || redrawing;
  const why = redrawProblem();
  $("#yt-ai-setup").textContent = current.settings.redraw_thumbnail && why ? ` Before it can, ${why}.` : "";
  if (!yt) return;
  const src = fileUrl(yt.file, Math.round(yt.created || 0));
  $("#yt-thumb").src = src;
  $("#yt-thumb-link").href = src;
  $("#yt-ai").hidden = !yt.ai;
  if (yt.ai) {
    const ai = fileUrl(yt.ai.file, Math.round(yt.ai.created || 0));
    $("#yt-ai-img").src = ai;
    $("#yt-ai-link").href = ai;
    $("#yt-ai-by").textContent = `Redrawn with AI by ${yt.ai.by} (${yt.ai.model})` +
      (yt.ai.created < yt.created ? ", before the thumbnail's last change: Redraw with AI again to update it." : ".");
  }
  if (document.activeElement !== $("#yt-title")) $("#yt-title").value = yt.title;
  $("#yt-count").textContent = `${$("#yt-title").value.length}/100`;
  if (document.activeElement !== $("#yt-text")) $("#yt-text").value = yt.text;
  $("#yt-alts").replaceChildren(...(yt.alternatives || []).filter((a) => a !== yt.title).map((a) =>
    el("button", { type: "button", class: "ghost small", title: "Use this title", onclick: () => saveTitle(a) }, a)));
  const pics = yt.pictures || [yt.panel];
  if (!editingIn($("#yt-marks"))) {
    $("#yt-marks").replaceChildren(...(yt.marks || []).map((m, i) => el("label", { class: "yt-mark" },
      el("span", { class: "muted" }, MARK_NAMES[m.kind] + (m.who ? ` · ${m.who}` : "") +
        (pics.length > 1 ? ` (${pics.indexOf(m.picture) === 0 ? "left" : "right"} picture)` : "")),
      el("input", { value: m.text, maxlength: m.kind === "line" ? 40 : m.kind === "reaction" ? 6 : 24, "data-i": i,
        onkeydown: (e) => { if (e.key === "Enter") { e.preventDefault(); e.target.blur(); redrawThumb(); } } }))));
  }
  const byId = new Map(current.panels.map((x) => [x.id, x]));
  $("#yt-pics").replaceChildren(...(yt.candidates || []).map((id) => byId.get(id)).filter(Boolean).map((x) =>
    el("img", { src: fileUrl(`panels/${x.file}`), class: pics.includes(x.id) ? "on" : "",
      title: pics.includes(x.id) ? (pics.length > 1 ? "Take this picture out" : "The thumbnail's picture") : "Use this picture",
      onclick: () => togglePicture(x.id) })));
  $("#yt-by").textContent = yt.by ? `Suggested by ${yt.by}.` : "";
}

// a picture clicked: in or out of the thumbnail (one or two; a third takes the second's place)
function togglePicture(id) {
  const pics = [...(current.youtube.pictures || [current.youtube.panel])];
  if (pics.includes(id)) {
    if (pics.length === 1) return;
    pics.splice(pics.indexOf(id), 1);
  } else if (pics.length < 2) pics.push(id);
  else pics[1] = id;
  redrawThumb({ pictures: pics });
}

async function saveTitle(title) {
  try {
    current.youtube = (await api(`/api/projects/${current.id}/youtube`, { method: "PATCH", json: { title } })).youtube;
    renderYouTube();
  } catch (e) { notify(e.message); }
}

// draw the thumbnail again with the words as edited (and, from togglePicture, other pictures)
async function redrawThumb(extra = {}) {
  $("#yt-redraw").disabled = true;
  const marks = (current.youtube.marks || []).map((m, i) => {
    const input = $(`#yt-marks input[data-i="${i}"]`);
    return { ...m, text: input ? input.value.trim() : m.text };
  }).filter((m) => m.text);
  try {
    current.youtube = (await api(`/api/projects/${current.id}/youtube/thumbnail`, { method: "POST",
      json: { text: $("#yt-text").value, marks, ...extra } })).youtube;
    renderYouTube();
  } catch (e) { notify(e.message); }
  $("#yt-redraw").disabled = false;
}

$("#yt-ai-go").addEventListener("click", async () => {
  const why = redrawProblem();
  if (why) return notify(`To redraw the thumbnail with AI, ${why}.`, "Not set up yet");
  const pid = current.id;
  redrawing = true;
  renderYouTube();
  $("#yt-ai-status").textContent = "Redrawing… (it can take a minute)";
  try {
    const p = await api(`/api/projects/${pid}/youtube/redraw`, { method: "POST" });
    if (current && current.id === pid) current.youtube = p.youtube;
  } catch (e) { notify(e.message, "Couldn't redraw it"); }
  redrawing = false;
  $("#yt-ai-status").textContent = "";
  if (current && current.id === pid) renderYouTube();
});

$("#yt-title").addEventListener("change", (e) => saveTitle(e.target.value));
$("#yt-title").addEventListener("input", (e) => { $("#yt-count").textContent = `${e.target.value.length}/100`; });
$("#yt-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("#yt-title").value);
    $("#yt-copy").textContent = "Copied";
    setTimeout(() => { $("#yt-copy").textContent = "Copy"; }, 1500);
  } catch { notify("Select the title and copy it yourself.", "Couldn't copy"); }
});
$("#yt-redraw").addEventListener("click", () => redrawThumb());
$("#yt-text").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); redrawThumb(); } });
$("#yt-suggest").addEventListener("click", () => {
  if (!current.panels.some((x) => (x.narration || "").trim())) {
    return notify("The title is suggested from the story: write the script first.", "No script yet");
  }
  runSteps(["title"], null, "redo");
});

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
  const busy = !!(current.job && ACTIVE.includes(current.job.status));
  const chapter = current.chapters.find((x) => x.id === head.chapter);
  const named = current.characters.filter((c) => c.id && c.name.trim());
  const c = el("div", { class: "card scene-open" + (on ? "" : " off") });
  // the pieces, one under the other, "split here" between them; a black box count on a piece that has boxes
  const pieces = el("div", { class: "pieces" });
  g.forEach((p, i) => {
    if (i > 0) pieces.append(el("button", { class: "split", title: "Make this piece start its own scene", onclick: () => setJoined(p.id, false) }, "split here"));
    const src = fileUrl(`panels/${p.file}`);
    const img = el("img", { loading: "lazy", src, onclick: () => lightbox(src, p.id) });
    const boxes = youtube() ? pieceBoxes(p) : [];
    const pic = boxes.length ? el("span", { class: "piece-boxed" }, boxed(img, boxes),
      el("span", { class: "box-tag piece-badge", title: "Black boxes cover part of this piece in the video. Click it to change them." }, `■ ${boxCount(boxes.length)}`)) : img;
    if (!left.includes(p)) return pieces.append(pic);
    pieces.append(el("div", { class: "piece-off", title: "Left out of the video (the rest of this scene is used)" }, img,
      el("span", { class: "piece-tag" }, p.credits ? "left out: credits" : "left out"),
      el("button", { type: "button", class: "piece-use small", title: "Use this piece in the video too",
        onclick: async () => { await editPanel(p.id, { enabled: true }); renderPanels(); await followCopies([p], true); renderPanels(); } },
      "Use it")));
  });
  if (g.length > 4) pieces.classList.add("tall");  // (a scene of many pieces scrolls inside its card)
  pieces.append(el("span", { class: "muted pieces-note" }, "Click a piece to see it large" +
    (youtube() ? ", or to draw a black box" : "") + (named.length ? `${youtube() ? " or" : ", or"} to cut out a character's picture.` : ".")));
  // the head: the scene's number and place, its tags, In the video, Join, Rewrite
  const title = el("h2", { class: "scene-title" }, `Scene ${n + 1}`,
    el("span", { class: "muted" }, ` · page ${head.page + 1}${g.length > 1 ? ` · ${plural(g.length, "piece")}` : ""}`));
  const enabled = el("label", { class: "check" }, el("input", {
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
  }), " In the video");
  const isCredits = g.some((p) => p.credits) && !on;
  const flag = sceneFlag(g);
  const words = (head.narration || "").trim().split(/\s+/).filter(Boolean).length;
  const headRow = el("div", { class: "scene-head" }, title,
    isCredits ? el("span", { class: "credits", title: "Left out automatically: one of the scan group's pages at the start or end of the chapter. Tick In the video to keep it." }, "left out: credits") : null,
    flag ? el("span", { class: "word-tag", title: `The wording check flagged this line: ${flag.why}, "${flag.word}". ` +
      "Edit it, or keep it with Check wording." }, "⚠ check wording") : null,
    left.length ? el("span", { class: "credits", title: "Those pieces are dimmed: they're left out of the video, the rest of the scene is used" },
      `${left.length} of ${g.length} pieces left out`) : null,
    enabled,
    firstInChapter ? null : el("button", { class: "ghost", title: "Merge this scene into the one above (their narration is combined)",
      onclick: () => setJoined(head.id, true) }, `Join with scene ${n}`),
    el("button", { class: "ghost", disabled: busy, title: "Your script writer writes this scene's narration again, then it's voiced",
      onclick: async () => {
        if (await ask(`Rewrite scene ${n + 1}?`, "Its narration is written again by your script writer, then voiced. The rest of the chapter stays as it is.", { ok: "Rewrite" })) runSteps(["script", "audio"], [head.id]);
      } }, "Rewrite this scene"));
  // the narration, its counts and who wrote it; the voice
  const narration = el("textarea", { rows: 5, placeholder: "Narration (empty = no voice for this scene)", "aria-label": "Narration" });
  narration.value = head.narration || "";
  narration.addEventListener("change", async () => { await editPanel(head.id, { narration: narration.value }); });
  const by = chapter && chapter.narrator ? ` · Written by ${chapter.narrator.startsWith("local model (fallback)") ? "a local model (fallback)" : chapter.narrator}` : "";
  const line = el("div", { class: "scene-line muted" }, words
    ? `${plural(words, "word")} · about ${head.duration ? Math.round(head.duration) : Math.round(words / 2.6)} s spoken${by}`
    : head.script_done ? `No narration${by}` : "Not written yet");
  const voiceWho = { edge: `Edge's ${globals && globals.edge_voice} voice`, elevenlabs: "your ElevenLabs voice" }[globals && globals.voice_provider]
    || `the ${globals ? voiceName(globals.voice, true) : "…"} voice`;
  const voiceRow = el("div", { class: "scene-voice" },
    head.audio ? playButton(fileUrl(`audio/${head.audio}`, head.audio_rev), head.duration) : null,
    el("button", { class: head.audio ? "ghost" : "primary", disabled: !words || busy, title: "Voice this scene's narration",
      onclick: () => runSteps(["audio"], [head.id]) }, head.audio ? "Make the voice again" : "▶ Make the voice for this scene"),
    chapter ? el("button", { class: "ghost", title: "Use your own recording of the whole chapter instead", onclick: () => openVoiceover(chapter) }, "Record it yourself") : null,
    head.audio && head.audio_source === "recording" ? el("span", {
      class: head.voice_match < 0.6 ? "rec warn" : "rec",
      title: "From your recording: the share of this line's words the speech recognizer heard in it" },
    `🎙 ${Math.round((head.voice_match || 0) * 100)}%`) : null,
    el("span", { class: "muted" }, `${voiceWho}${globals && globals.speed && Number(globals.speed) !== 1 ? ` · ${globals.speed}×` : ""}`));
  // what the pictures say: the text read from them, by speaker when Magi guessed who, with a raw comic's translation
  const text = g.map((p) => p.text).filter(Boolean).join("\n");
  const guesses = g.flatMap((p) => p.speakers || []);
  const tr = isRaw(current.settings) && g.some((p) => (p.bubbles || []).length) ? translationBox(g) : null;
  const say = text || guesses.length || tr ? el("div", { class: "pictures-say" }, el("div", { class: "say-head" }, "The pictures say"),
    guesses.length ? el("div", { class: "say-grid" }, ...guesses.flatMap((s) => [el("span", { class: "muted" }, s.who), el("span", {}, s.text)]))
      : text ? el("div", { class: "say-text" }, text) : null,
    tr,
    el("div", { class: "muted say-note" }, guesses.length ? "Who says what, as Magi's speaker detection guesses it (often wrong)."
      : isRaw(current.settings) ? `The ${LANGUAGES[current.settings.language]}, read by the text reader and translated by your script writer` +
        `${current.settings.typeset ? "; the English is lettered into the pictures when the video is made" : ""}.`
      : "Read from the pictures by the text reader. The narrator knows it.")) : null;
  c.append(headRow, el("div", { class: "scene-body" }, pieces,
    el("div", { class: "fields" }, el("span", { class: "field-head" }, "Narration"), narration, line, voiceRow, say)));
  return c;
}

// A chapter's narration as one text, to read through (This scene → The whole chapter)
function readChapter(chapter, groups) {
  const text = groups.map((g) => (g[0].narration || "").trim()).filter(Boolean).join("\n\n");
  const close = overlay(el("div", { class: "lightbox read-box", onclick: (e) => { if (e.target.classList.contains("lightbox")) close(); } },
    el("div", { class: "read-text" }, el("h3", {}, chapter.name), el("pre", {}, text || "Nothing written yet."),
      el("button", { class: "ghost", onclick: () => close() }, "Close"))));
}

// The inspector at the Script stage: this scene (who is in it, its black boxes and wording, its chapter, details),
// then the project's own script settings below
function renderSceneInspector(open, groups, flagged, numberOf) {
  const box = $("#scene-inspector");
  box.hidden = tab !== "script";
  if (!open) return box.replaceChildren();
  const head = open[0], chapter = current.chapters.find((x) => x.id === head.chapter);
  const text = head.narration || "";
  const named = current.characters.filter((c) => c.id && c.name.trim());
  const inIt = named.filter((c) => [c.name, ...String(c.aliases || "").split(",")].map((x) => x.trim()).filter(Boolean).some((nm) => text.includes(nm)));
  const section = (title, ...kids) => el("section", { class: "si-section" }, el("h3", {}, title), ...kids);
  const link = (label, onclick) => el("button", { type: "button", class: "link", onclick }, label);
  const parts = [section("Who is in it",
    el("div", { class: "si-chips" }, ...inIt.map((c) => el("span", { class: "chip" }, c.name)),
      el("button", { type: "button", class: "chip dashed", title: "The project's characters", onclick: () => showTab("characters") },
        named.length ? "+ someone" : "Name the characters")),
    el("div", {}, inIt.length ? "Named in this scene's narration. The narrator uses these names." : "No named character in this scene's narration."))];
  if (youtube()) {
    const boxes = open.reduce((k, p) => k + pieceBoxes(p).length, 0);
    parts.push(section("Black boxes", el("div", {}, `${boxes ? `${boxCount(boxes)} on this scene's pieces.` : "None on this scene."} YouTube-safe is on for this project.`),
      link("See every box in the chapter", openBoxes)));
    const flag = sceneFlag(open), i = groups.indexOf(open);
    const next = flagged.find((g) => groups.indexOf(g) > i && g !== open) || flagged.find((g) => g !== open);
    parts.push(section("Wording",
      el("div", {}, flag ? `Flagged: ${flag.why}, "${flag.word}". Edit the line, or keep it with Check wording.`
        : `Nothing flagged here.${next ? ` Scene ${numberOf(next)} has a word to check.` : ""}`),
      flag && chapter ? link("Check wording", () => checkWording(chapter, wordingFlags(chapter)))
        : next ? link(`Go to scene ${numberOf(next)}`, () => { sceneSel = next[0].id; renderPanels(); }) : null));
  }
  if (chapter) {
    const mine = displayGroups(current.panels.filter((p) => p.chapter === chapter.id));
    const written = mine.filter((g) => (g[0].narration || "").trim()).length;
    const secs = mine.reduce((k, g) => k + (g[0].duration || (g[0].narration ? g[0].narration.trim().split(/\s+/).length / 2.6 : 0)), 0);
    parts.push(section("The whole chapter",
      el("div", {}, `${chapter.name}: ${plural(mine.length, "scene")} · ${written} written${secs ? ` · about ${Math.max(1, Math.round(secs / 60))} min of narration so far` : ""}`),
      el("div", { class: "link-row" }, link("Read it as one text", () => readChapter(chapter, mine)),
        link("Rewrite the chapter", async () => {
          if (await ask(`Rewrite ${chapter.name}?`, "Every scene's narration is written again by your script writer, then voiced.", { ok: "Rewrite" })) {
            runSteps(["script", "audio"], mine.map((g) => g[0].id));
          }
        }))));
  }
  const guesses = open.flatMap((p) => p.speakers || []);
  parts.push(el("details", { class: "details-adv si-details" }, el("summary", {}, "Details"),
    el("div", { class: "si-details-body muted" },
      el("span", {}, `Text found: ${plural(open.reduce((k, p) => k + ((p.bubbles || []).length || (p.text ? 1 : 0)), 0), "box")}` +
        `${isRaw(current.settings) ? `, ${LANGUAGES[current.settings.language]}` : ""}.`),
      guesses.length ? el("span", {}, `Speaker guesses by Magi: ${[...new Set(guesses.map((s) => s.who))].join(", ")}.`) : null,
      el("span", {}, `Pieces: ${open.map((p) => p.file.replace(/\.\w+$/, "")).join(", ")}.`))));
  parts.push(el("h2", { class: "inspector-title si-project" }, "This project"));
  box.replaceChildren(...parts);
}

// A line's voice, played from one small button (a full player on each of sixty scenes was a wall of "0:00 / 0:00"):
// one line plays at a time, and the button playing shows ■ until it ends.
let linePlaying = null;  // {audio, button}
function playButton(src, seconds) {
  const label = `▶ ${seconds.toFixed(1)}s`;
  const b = el("button", { type: "button", class: "ghost small play", title: "Play this line's voice" }, label);
  b.addEventListener("click", () => {
    if (linePlaying && linePlaying.button === b) return linePlaying.audio.pause();
    if (linePlaying) linePlaying.audio.pause();
    const audio = new Audio(src);
    linePlaying = { audio, button: b };
    const done = () => {
      if (linePlaying && linePlaying.audio === audio) linePlaying = null;
      b.textContent = label;
      b.classList.remove("on");
    };
    for (const ev of ["pause", "ended", "error"]) audio.addEventListener(ev, done);
    b.textContent = `■ ${seconds.toFixed(1)}s`;
    b.classList.add("on");
    audio.play().catch((e) => { done(); notify(e.message); });
  });
  return b;
}

// ---- a raw comic's translation (translate.py): each text box's original, its kind and its English, to correct ----

const KIND_NAMES = { speech: "Speech", thought: "Thought", narration: "Caption", sign: "Sign", sfx: "Sound effect", none: "Not text" };

function translationBox(g) {
  const rows = [];
  let missing = 0;
  for (const p of g) {
    (p.bubbles || []).forEach((b, k) => {
      if (!("english" in b)) missing++;  // (an empty answer is the writer's: nothing to say there)
      const kind = el("select", { title: "What kind of text it is: sound effects and what isn't text stay out of the script, " +
        "and only speech, thoughts and captions are lettered into their bubble", onchange: (e) => saveBubble(p, k, { kind: e.target.value }) },
      ...Object.entries(KIND_NAMES).map(([v, t]) => el("option", { value: v }, t)));
      kind.value = b.kind || "speech";
      const english = el("input", { value: b.english || "", placeholder: "english" in b ? "(nothing)" : "Not translated yet",
        onchange: (e) => saveBubble(p, k, { english: e.target.value }) });
      rows.push(el("div", { class: "tr-row" }, kind, el("span", { class: "tr-src", title: b.text }, b.text), english));
    });
  }
  return el("details", { class: "translation" + (missing ? " warn" : "") },
    el("summary", {}, `Translation · ${rows.length} text box${rows.length === 1 ? "" : "es"}` +
      (missing ? ` · ${missing} not translated` : "")), ...rows);
}

async function saveBubble(p, k, change) {
  try {
    Object.assign(p, await api(`/api/projects/${current.id}/panels/${p.id}/bubbles/${k}`, { method: "PUT", json: change }));
  } catch (e) { notify(e.message); }
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
    : "No boxes yet" + (selectedCh.size ? " in the selected chapters" : "") + ". Find boxes now (the YouTube-safe panel at Script and voice) runs the box finder.";
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

// The script as a filmstrip: every scene in a list on the left (a thumbnail, its first words, its state), the one
// chosen open on the right as a card (sceneCard). A chapter can be listed alone; To check jumps to the next scene
// the wording check flagged.
let sceneSel = null;    // the head panel's id of the scene open
let sceneChapter = "";  // the chapter listed alone ("" = all)

const sceneFlag = (g) => {
  const chapter = current.chapters.find((x) => x.id === g[0].chapter);
  return youtube() && chapter && (chapter.wording || []).find((w) => w.line === g[0].narration);
};

function sceneItem(g, n, first) {
  const head = g[0], on = g.some((p) => p.enabled), words = (head.narration || "").trim();
  const flag = sceneFlag(g);
  const [cls, state] = !on ? ["off", g.some((p) => p.credits) ? "left out: credits" : "not in the video"]
    : head.audio ? ["voiced", `${(head.duration || 0).toFixed(1)}s`]
    : words ? ["written", "no voice yet"] : ["todo", "not written"];
  const item = el("button", { type: "button", class: `scene-item${head.id === sceneSel ? " on" : ""}${on ? "" : " off"}`,
    "aria-current": head.id === sceneSel ? "true" : null, onclick: () => { sceneSel = head.id; renderPanels(); } },
  el("img", { class: "scene-thumb", loading: "lazy", src: fileUrl(`panels/${head.file}`), alt: "" }),
  el("span", { class: "scene-text" },
    el("span", { class: `scene-no${flag ? " warn" : ""}` }, `${n + 1}${g.length > 1 ? ` · ${plural(g.length, "piece")}` : ""}${flag ? " · check wording" : ""}`),
    el("span", { class: "scene-words" }, words || (head.script_done ? "No narration" : "Not written yet"))),
  el("span", { class: `scene-state ${cls}` }, state));
  return el("li", { class: first ? "first-in-chapter" : "" }, item);
}

function renderPanels() {
  const box = $("#panels");
  // keep a textarea the user is typing in
  if (box.contains(document.activeElement) && document.activeElement.tagName === "TEXTAREA") return;
  const names = Object.fromEntries(current.chapters.map((c) => [c.id, c.name]));
  const all = displayGroups(current.panels.filter((p) => inScope(p.chapter)));
  // the chapter filter: shown with two chapters or more
  const chapterIds = current.chapters.filter((c) => all.some((g) => g[0].chapter === c.id)).map((c) => c.id);
  if (!chapterIds.includes(sceneChapter)) sceneChapter = "";
  const sel = $("#scene-chapter");
  sel.replaceChildren(el("option", { value: "" }, "All chapters"), ...chapterIds.map((id) => el("option", { value: id }, names[id] || "Chapter")));
  sel.value = sceneChapter;
  const words = $("#scene-find").value.trim().toLowerCase();
  let groups = sceneChapter ? all.filter((g) => g[0].chapter === sceneChapter) : all;
  if (words) groups = groups.filter((g) => (g[0].narration || "").toLowerCase().includes(words) || g.some((p) => (p.text || "").toLowerCase().includes(words)));
  if (!groups.some((g) => g[0].id === sceneSel)) sceneSel = groups.length ? groups[0][0].id : null;
  // the list, by chapter
  const items = [], meta = new Map();
  let lastChapter = null, n = 0;
  for (const g of groups) {
    const first = g[0].chapter !== lastChapter;
    if (first) {
      items.push(el("li", { class: "chapter-divider" }, names[g[0].chapter] || "Chapter"));
      lastChapter = g[0].chapter;
      n = 0;
    }
    meta.set(g[0].id, { n, first });
    items.push(sceneItem(g, n++, first));
  }
  $("#scene-list").replaceChildren(...items, words && !groups.length ? el("li", { class: "muted scene-none" }, "No scene has those words.") : null);
  const onItem = $("#scene-list .scene-item.on");
  if (onItem) onItem.scrollIntoView({ block: "nearest" });
  // the scene open
  const open = groups.find((g) => g[0].id === sceneSel);
  box.replaceChildren(...(open ? [sceneCard(open, meta.get(open[0].id).n, meta.get(open[0].id).first)]
    : [el("div", { class: "empty-note" }, current.chapters.length ? "No scenes yet. Make the recap finds them." : "Add chapters to start.")]));
  $("#more").hidden = true;
  // the wording check's flags: the next one, from the scene open
  const flagged = groups.filter((g) => sceneFlag(g));
  $("#scene-check").hidden = !flagged.length;
  $("#scene-check").textContent = `To check · ${flagged.length}`;
  $("#scene-check").onclick = () => {
    const i = groups.findIndex((g) => g[0].id === sceneSel);
    const next = flagged.find((g) => groups.indexOf(g) > i) || flagged[0];
    sceneSel = next[0].id;
    renderPanels();
  };
  // the rail's head: the counts and the chapter chips (the chapter listed, or the open scene's, and All chapters)
  const written = groups.filter((g) => (g[0].narration || "").trim()).length;
  $("#script-back-name").textContent = current.name;
  $("#script-counts").textContent = groups.length || words
    ? `${sceneChapter ? names[sceneChapter] : "All chapters"} · ${plural(groups.length, "scene")} · ${written} written${flagged.length ? ` · ${flagged.length} to check` : ""}`
    : current.chapters.length ? "No scenes yet: Make the recap finds them" : "Add chapters to start";
  const chip = (label, on, id) => el("button", { type: "button", class: `chip${on ? " on" : ""}`, onclick: () => { sceneChapter = id; sceneSel = null; renderPanels(); } }, label);
  const chapterOf = sceneChapter || (open ? open[0].chapter : "");
  $("#scene-chips").replaceChildren(...(chapterIds.length > 1 ? [
    chapterOf ? chip(names[chapterOf] || "Chapter", !!sceneChapter, chapterOf) : null,
    chip("All chapters", !sceneChapter, ""),
  ] : []));
  renderSceneInspector(open, groups, flagged, (g) => (meta.get(g[0].id) || { n: 0 }).n + 1);
  renderCreditsCount();
  renderBoxesCount();
}
$("#scene-find").addEventListener("input", () => renderPanels());
$("#script-back").addEventListener("click", (e) => { e.preventDefault(); showTab("chapters"); });
$("#scene-chapter").addEventListener("change", (e) => { sceneChapter = e.target.value; sceneSel = null; renderPanels(); });
// the arrow keys walk the list
$("#scene-list").addEventListener("keydown", (e) => {
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  const items = [...document.querySelectorAll("#scene-list .scene-item")];
  const i = items.indexOf(e.target.closest(".scene-item"));
  if (i < 0) return;
  const next = items[i + (e.key === "ArrowDown" ? 1 : -1)];
  if (next) { e.preventDefault(); next.click(); document.querySelector("#scene-list .scene-item.on")?.focus(); }
});
$("#more").addEventListener("click", () => { shown += PAGE; renderPanels(); });

// Reviewing a script: scrolling on past the end of the open scene rolls on to the next one (and up past its top, to
// the one before, at its end), so a chapter reads through without a click per scene. It takes a little more wheel
// at the edge, so the edge itself doesn't jump, and a pause after each roll.
(() => {
  const main = $("#main");
  let edgeAt = 0, pushed = 0, rolledAt = 0;
  main.addEventListener("wheel", (e) => {
    if (!document.body.classList.contains("script-mode") || !$("#panels .scene-open")) return;
    const down = e.deltaY > 0;
    const atEdge = down ? main.scrollTop + main.clientHeight >= main.scrollHeight - 2 : main.scrollTop <= 0;
    const now = Date.now();
    if (!atEdge || now - rolledAt < 600) { pushed = 0; return; }
    if (now - edgeAt > 500) pushed = 0;  // (a fresh push)
    edgeAt = now;
    pushed += Math.abs(e.deltaY);
    if (pushed < 240) return;
    const items = [...document.querySelectorAll("#scene-list .scene-item")];
    const i = items.indexOf(document.querySelector("#scene-list .scene-item.on"));
    const next = i >= 0 ? items[i + (down ? 1 : -1)] : null;
    pushed = 0;
    if (!next) return;
    rolledAt = now;
    next.click();
    // the new scene from its top (or its end, going up): placed now, and again once its pictures have their size
    const place = () => { main.scrollTop = down ? 0 : main.scrollHeight; };
    place();
    requestAnimationFrame(place);
    setTimeout(place, 80);
  }, { passive: true });
})();

// ---- layout: tabs, the project menu, long help text ----

const stored = (key) => { try { return localStorage.getItem(key); } catch { return null; } };
const store = (key, value) => { try { localStorage.setItem(key, value); } catch { /* not remembered */ } };
let tab = stored("tab") || "chapters";

const STAGE_NAMES = { chapters: "Chapters", characters: "Characters", script: "Script and voice", video: "Video", youtube: "Publish" };
function showTab(name) {
  if (!STAGE_NAMES[name]) name = "chapters";  // (a tab remembered from an older page: Project settings, Prompt)
  tab = name;
  store("tab", name);
  for (const b of document.querySelectorAll(".tabs [data-tab]")) {
    b.classList.toggle("on", b.dataset.tab === name);
    b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false");
  }
  for (const page of document.querySelectorAll(".tab-page")) page.hidden = page.dataset.page !== name;
  $("#inspector").dataset.stage = name;  // (the inspector shows this stage's settings)
  $("#inspector-title").textContent = name === "script" ? "This scene" : STAGE_NAMES[name];
  // the Script stage is the editor: the rail lists the scenes, the strip and the head step aside
  const scripting = name === "script" && !!current;
  document.body.classList.toggle("script-mode", scripting);
  $("#script-rail").hidden = !scripting;
  $("#scene-inspector").hidden = !scripting;
  // text boxes sized while their tab was hidden measured nothing: size them now
  requestAnimationFrame(() => document.querySelectorAll(`.tab-page[data-page="${name}"] textarea`).forEach(autosize));
}
document.querySelectorAll(".tabs [data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
document.querySelectorAll(".tile[data-goto]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.goto)));

// Settings shows one section at a time, picked in the list on its left: the app's own sections, then Plugins (the
// list of plugins, and each plugin's own section, RecapStudio.addSettings). Find a setting narrows the list to the
// sections that have the words. Hidden sections aren't listed.
let settingsSection = stored("settings-section") || "";
const sectionName = (fs) => fs.dataset.nav || fs.querySelector("legend").textContent.trim();

function renderSettingsNav() {
  const all = [...document.querySelectorAll("#gs-pages > fieldset")];
  const words = $("#gs-find").value.trim().toLowerCase();
  const listed = all.filter((fs) => !fs.hidden && (!words || fs.textContent.toLowerCase().includes(words)));
  if (!listed.some((fs) => sectionName(fs) === settingsSection)) settingsSection = listed.length ? sectionName(listed[0]) : "";
  for (const fs of all) fs.classList.toggle("gs-on", listed.includes(fs) && sectionName(fs) === settingsSection);
  const item = (fs) => el("button", { type: "button", class: sectionName(fs) === settingsSection ? "on" : "",
    "aria-current": sectionName(fs) === settingsSection ? "page" : null, onclick: () => showSettings(sectionName(fs)) },
  sectionName(fs));
  const own = listed.filter((fs) => !fs.dataset.plugin && fs.id !== "plugins-box");
  const plugins = listed.filter((fs) => fs.id === "plugins-box").concat(listed.filter((fs) => fs.dataset.plugin)
    .sort((a, b) => sectionName(a).localeCompare(sectionName(b))));
  $("#gs-nav").replaceChildren(...[
    own.length ? el("span", { class: "gs-group" }, "Recap Studio") : null, ...own.map(item),
    plugins.length ? el("span", { class: "gs-group" }, "Plugins") : null, ...plugins.map(item),
    listed.length ? null : el("span", { class: "muted gs-note" }, "No setting has those words."),
  ].filter(Boolean));
}

function showSettings(name) {
  settingsSection = name;
  store("settings-section", name);
  renderSettingsNav();
  $("#gs-pages").scrollTop = 0;
}

// Settings opened at a section (its name in the list), from elsewhere in the app
function openSettings(name) {
  renderGlobal();
  loadStorage();
  if (name) { $("#gs-find").value = ""; showSettings(name); }
  $("#global-dialog").show();
}

$("#gs-find").addEventListener("input", () => { renderSettingsNav(); $("#gs-pages").scrollTop = 0; });
$("#gs-find").addEventListener("keydown", (e) => { if (e.key === "Enter") e.preventDefault(); });  // (not Close)
showTab(tab);

const projectMenu = $("#project-menu-list");
function setMenu(open) {
  projectMenu.hidden = !open;
  $("#project-menu").setAttribute("aria-expanded", open ? "true" : "false");
}
$("#project-menu").addEventListener("click", (e) => { e.stopPropagation(); setMenu(projectMenu.hidden); });
document.addEventListener("click", () => { setMenu(false); setStepsMenu(false); });  // (a menu item's own click runs first)
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { setMenu(false); setStepsMenu(false); } });

// A long help text shows its first two lines; hover it for the rest, or click it to open it.
// Warnings always show in full.
// A "More" after each one says so (a clamped text ends in "…", which says nothing about a click); "Less" once open.
let hintsQueued = false;
const moreOf = (h) => (h.nextElementSibling && h.nextElementSibling.classList.contains("hint-more") ? h.nextElementSibling : null);
function toggleHint(h) {
  h.classList.toggle("open");
  const more = moreOf(h);
  if (more) more.textContent = h.classList.contains("open") ? "Less" : "More";
}
function markLongHints() {
  hintsQueued = false;
  // a dialog's Close must close whatever is in its boxes (the server checks what's saved, as it's saved): a plugin's
  // dialog form gets novalidate like the app's own, so a browser's step or range check can't hold it open
  for (const f of document.querySelectorAll("dialog form:not([novalidate])")) f.noValidate = true;
  for (const b of document.querySelectorAll(".hint-more")) {  // (one left behind by a hint that went, or got short)
    const h = b.previousElementSibling;
    if (!h || !h.classList.contains("hint")) b.remove();
  }
  for (const h of document.querySelectorAll(".hint")) {
    const long = h.textContent.trim().length > 150 && !h.classList.contains("warn") && !h.closest(".field-head");
    h.classList.toggle("long", long);
    if (long) h.title = h.textContent.trim(); else if (h.classList.contains("was-long")) h.removeAttribute("title");
    h.classList.toggle("was-long", long);
    const more = moreOf(h);
    if (long && !more) {
      h.after(el("button", { type: "button", class: "link-btn hint-more", onclick: () => toggleHint(h) }, h.classList.contains("open") ? "Less" : "More"));
    } else if (!long && more) more.remove();
  }
}
new MutationObserver(() => {
  if (!hintsQueued) { hintsQueued = true; requestAnimationFrame(markLongHints); }
}).observe(document.body, { childList: true, subtree: true, characterData: true });
markLongHints();
document.addEventListener("click", (e) => {
  const h = e.target.closest(".hint.long");
  if (h && !e.target.closest("a, button, input, select")) toggleHint(h);
});

// ---- render ----------------------------------------------------------------

function render() {
  $("#empty").hidden = !!current;
  $("#project").hidden = !current;
  document.body.classList.toggle("in-project", !!current);  // (the rail: Library instead of New project)
  showPictures();
  if (!current) {
    document.body.classList.remove("script-mode");
    $("#script-rail").hidden = true;
    return;
  }
  if (document.activeElement !== $("#project-name")) $("#project-name").value = current.name;
  $("#page-count").textContent = "";
  $("#clear-pages").hidden = current.chapters.length === 0;
  renderSettings();
  renderSourceBox();
  renderOverview();
  renderPrompt();
  renderSteps();
  renderScanButton();
  renderChapters();
  renderScope();
  renderJob();
  renderPanels();
  renderOutput();
  renderYouTube();
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
  // the pipeline's stages: each one's state, and which the job under way is at (or stopped to ask at)
  const job = current.job && ACTIVE.includes(current.job.status) ? current.job : null;
  const asking = !!(job && job.status === "paused" && job.pause_reason);
  const busy = job ? STAGE_OF_STEP[job.step] || "" : "";
  const stage = (id, text, done) => {
    const b = document.querySelector(`.tabs.pipeline [data-tab="${id}"]`);
    $(`#tile-${id === "youtube" ? "publish" : id}`).textContent = text;
    b.classList.toggle("done", !!done && busy !== id);
    b.classList.toggle("busy", busy === id && !asking);
    b.classList.toggle("ask", busy === id && asking);
  };
  const named = current.characters.filter((c) => c.name).length;
  const toCheck = current.chapters.filter((c) => wordingFlags(c).length).length;
  stage("chapters", n ? `${plural(n, "chapter")} · ${current.pages.length} pages${found && found < n ? ` · ${found} read` : ""}` : "None yet", n > 0);
  stage("characters", named ? `${named} named` : current.settings.find_characters ? "Found before the script" : "None yet", named > 0);
  stage("script", !found ? "Not started" : `${written} of ${n} written · ${voiced} of ${n} voiced` + (toCheck ? ` · ${toCheck} to check` : ""),
    n && written === n && voiced === n && !toCheck);
  stage("video", out ? (seconds ? `Made · ${lengthText(seconds)}` : "Made") : "Not made yet", !!out);
  stage("youtube", current.youtube ? "Title and thumbnail ready" : current.settings.suggest_title ? "Title after the script" : "Nothing suggested", !!current.youtube);
  $("#go").textContent = !n ? "Add chapters" : !found ? "Make the recap" : voiced === n && out ? "Make the video again" : "Continue the recap";
  const s = current.settings, kind = kindOf(s);
  $("#project-chips").replaceChildren(...[
    kind !== "auto" ? KINDS.find((k) => k[0] === kind)[1].toLowerCase() : null,
    isRaw(s) ? `${LANGUAGES[s.language] || s.language}, translated` : null,
    s.youtube_safe ? "YouTube-safe" : null,
  ].filter(Boolean).map((t) => el("span", { class: "tag" }, t)));
}
// the stage each of the pipeline's steps belongs to (renderOverview)
const STAGE_OF_STEP = { download: "chapters", panels: "chapters", ocr: "chapters", translate: "chapters", characters: "characters",
  script: "script", audio: "script", title: "youtube", video: "video" };

// The rendered video, or its parts (Settings → Video → Split into parts), with each one's subtitles
// and YouTube chapter list; and the last export for video editors.
let shownPart = 0;

const lengthText = (s) => (s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min` : `${Math.max(1, Math.round(s / 60))} min`);
const sizeText = (bytes) => (bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`);

let shownShort = -1;  // the Short in the player (-1: the video itself)

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
  const shorts = (out && out.shorts) || [];
  $("#output-shorts").hidden = !shorts.length;
  if (!out) {
    $("#player").removeAttribute("src");
    $("#player").removeAttribute("poster");
    $("#output-parts").replaceChildren();
    return;
  }
  const parts = out.parts && out.parts.length ? out.parts : [{ video: out.video, srt: out.srt }];
  if (shownPart >= parts.length) shownPart = 0;
  if (shownShort >= shorts.length) shownShort = -1;
  const part = parts[shownPart];
  const src = `/files/${current.id}/${encodeURI(shownShort >= 0 ? shorts[shownShort] : part.video)}?t=${out.time}`;
  if (!$("#player").src.endsWith(src)) $("#player").src = src;
  // the thumbnail as the player's poster (the video's first frame is often black), not for a Short (portrait)
  const yt = current.youtube;
  if (yt && yt.file && shownShort < 0) $("#player").poster = fileUrl(yt.file, Math.round(yt.created || 0));
  else $("#player").removeAttribute("poster");
  $("#srt-link").href = fileUrl(encodeURI(part.srt), out.time);
  $("#video-download").hidden = !(env && env.server);
  $("#video-download").href = src;
  $("#video-download").textContent = shownShort >= 0 ? `Download Short ${shownShort + 1}` : parts.length > 1 ? `Download part ${shownPart + 1}` : "Download the video";
  $("#chapters-link").hidden = !part.chapters;
  if (part.chapters) $("#chapters-link").href = fileUrl(encodeURI(part.chapters), out.time);
  $("#output-parts").replaceChildren(...(parts.length > 1 ? parts.map((x, k) => el("button", {
    type: "button", class: k === shownPart && shownShort < 0 ? "ghost small on" : "ghost small", "aria-pressed": k === shownPart ? "true" : "false",
    onclick: () => { shownPart = k; shownShort = -1; renderOutput(); },
  }, `Part ${k + 1}: ${x.first}${x.last !== x.first ? ` to ${x.last}` : ""} (${lengthText(x.seconds)})`)) : []));
  $("#output-shorts").replaceChildren(el("span", { class: "muted" }, `${plural(shorts.length, "Short")}:`),
    ...(shorts.length ? [el("button", { type: "button", class: shownShort < 0 ? "ghost small on" : "ghost small",
      onclick: () => { shownShort = -1; renderOutput(); } }, "Whole video")] : []),
    ...shorts.map((x, k) => el("button", { type: "button", class: k === shownShort ? "ghost small on" : "ghost small",
      title: x.split("/").pop(), onclick: () => { shownShort = k; renderOutput(); } }, String(k + 1))));
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
    const s = await api(`/api/projects/${current.id}/settings`, { method: "PUT",  // (the project's own)
      json: { editor_export: kind, editor_transition: $("#editors-transition").value } });
    current.settings = s.settings;
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

// What was found as the scan group's pages, in one dialog: each kind once (a picture and its copies in other
// chapters, credits.kinds), to keep or leave out all of them at once; with its switch also every piece,
// chapter by chapter, to put back what shouldn't be (or leave out again one you put back).
function creditPieces(withKept) {
  return current.panels.filter((p) => p.credits && inScope(p.chapter) && (withKept || !p.enabled));
}

// left out and not checked yet (credits.unchecked)
const uncheckedCredit = (p) => p.credits && !p.enabled && !("credits_kept" in p) && !p.credits_seen;

function renderCreditsCount() {
  const all = creditPieces(true), off = all.filter((p) => !p.enabled).length;
  const open = all.filter(uncheckedCredit).length;
  $("#credits-count").hidden = !all.length;
  $("#credits-count").textContent = `${plural(off, "piece")} left out${open ? `, ${open} to check` : ""}…`;
}

let creditKinds = [];

function kindTile(k) {
  const tile = el("div", {});
  const fill = () => {
    const shown = k.show.map((id) => current.panels.find((x) => x.id === id)).filter(Boolean);
    if (!shown.length) return tile.replaceChildren();
    const p = shown[0];
    const state = k.open ? "to check" : k.kept === k.ids.length ? "kept" : k.kept ? `${k.kept} of ${k.ids.length} kept` : "left out";
    tile.className = "credit-piece kind" + (k.open ? " open" : k.kept === k.ids.length ? " kept" : "");
    const pick = (enabled) => async (e) => {
      for (const b of tile.querySelectorAll("button")) b.disabled = true;
      try {
        const r = await api(`/api/projects/${current.id}/panels/choose`, { method: "POST", json: { ids: k.ids, enabled } });
        for (const u of r.panels) current.panels[current.panels.findIndex((x) => x.id === u.id)] = u;
      } catch (err) { notify(err.message); }
      Object.assign(k, { open: 0, kept: enabled ? k.ids.length : 0 });
      fill();
      renderCreditsSummary();
      renderPanels();  // the scene list behind, and its count
    };
    const where = k.chapters > 1 ? `in ${plural(k.chapters, "chapter")}` : current.chapters.find((c) => c.id === p.chapter)?.name || "";
    tile.replaceChildren(
      el("div", { class: "kind-pics" }, ...shown.map((x) => {  // (a page cut into pieces: all of them)
        const src = fileUrl(`panels/${x.file}`);
        return el("img", { loading: "lazy", src, title: `Page ${x.page + 1}`, onclick: () => lightbox(src, x.id) });
      })),
      el("span", { class: "piece-tag" }, state),
      el("div", { class: "kind-why" }, el("b", {}, where), ` · ${k.why}`),
      el("div", { class: "kind-actions" },
        el("button", { type: "button", class: "small" + (k.open || k.kept < k.ids.length ? "" : " primary"), onclick: pick(true),
          title: k.ids.length > 1 ? `Use all ${k.ids.length} in the video` : "Use it in the video" }, "Keep"),
        el("button", { type: "button", class: "small" + (k.open || k.kept ? "" : " primary"), onclick: pick(false),
          title: k.ids.length > 1 ? `Leave all ${k.ids.length} out of the video` : "Leave it out of the video" }, "Leave out")));
  };
  fill();
  return tile;
}

async function renderKinds() {
  $("#credits-kinds").replaceChildren(el("p", { class: "muted" }, "Sorting them by picture…"));
  try {
    const chapters = selectedCh.size ? [...selectedCh].join(",") : "";
    creditKinds = (await api(`/api/projects/${current.id}/credits/kinds?chapters=${encodeURIComponent(chapters)}`)).kinds;
  } catch (e) { creditKinds = []; notify(e.message); }
  $("#credits-kinds").replaceChildren(creditKinds.length
    ? el("div", { class: "credits-grid kinds-grid" }, ...creditKinds.map(kindTile))
    : el("p", { class: "muted" }, "Nothing to check."));
  renderCreditsSummary();
}

async function openCredits() {
  $("#credits-every").checked = false;
  showCreditsView();
  if (!$("#credits-dialog").open) $("#credits-dialog").showModal();
  await renderKinds();
}

function showCreditsView() {
  const every = $("#credits-every").checked;
  $("#credits-kinds").hidden = every;
  $("#credits-list").hidden = $("#credits-kept-box").hidden = !every;
  if (every) renderCredits();
  else renderCreditsSummary();
}

$("#credits-every").addEventListener("change", () => { showCreditsView(); if (!$("#credits-every").checked) renderKinds(); });

$("#credits-rest").addEventListener("click", async (e) => {
  const ids = current.panels.filter((p) => uncheckedCredit(p) && creditKinds.some((k) => k.open && k.ids.includes(p.id))).map((p) => p.id);
  if (!ids.length) return;
  e.target.disabled = true;
  try {
    const r = await api(`/api/projects/${current.id}/panels/choose`, { method: "POST", json: { ids, enabled: false } });
    for (const u of r.panels) current.panels[current.panels.findIndex((x) => x.id === u.id)] = u;
  } catch (err) { notify(err.message); }
  e.target.disabled = false;
  renderPanels();
  renderKinds();
});

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
  const kinds = $("#credits-list").hidden;
  const open = kinds ? creditKinds.filter((k) => k.open).length : 0;
  $("#credits-summary").textContent = all.length
    ? (kinds && creditKinds.length ? `${!open ? `${plural(creditKinds.length, "kind")}, all checked`
      : open === creditKinds.length ? `${plural(open, "kind")} to check` : `${open} of ${creditKinds.length} kinds to check`} · ` : "") +
      `${plural(off.length, "piece")} left out in ${plural(chapters, "chapter")}` +
      (all.length > off.length ? `, ${all.length - off.length} put back` : "") + (selectedCh.size ? " (selected chapters)" : "")
    : "Nothing is left out as the scan group's pages" + (selectedCh.size ? " in the selected chapters" : "") +
      ". Find them now (Settings for this project) looks for them.";
  $("#credits-rest").hidden = !open;
  $("#credits-wait").hidden = !waitingForCredits();
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

for (const b of document.querySelectorAll(".credits-open")) b.addEventListener("click", () => openCredits());
$("#credits-kept").addEventListener("change", renderCredits);
$("#job-credits").addEventListener("click", () => openCredits());

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
$("#open-file-upload").addEventListener("change", async () => {
  const file = $("#open-file-upload").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  $("#open-file-go").disabled = true;
  $("#open-file-status").textContent = `Sending ${file.name}… (a big project takes a while)`;
  try {
    const r = await fetch("/api/projects/open-file/upload", { method: "POST", body: form });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const p = await r.json();
    $("#open-file-dialog").close();
    await loadProjects();
    await openProject(p.id);
  } catch (e) { $("#open-file-status").textContent = e.message; }
  $("#open-file-upload").value = "";
  $("#open-file-go").disabled = false;
});
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
const SETUP_TITLES = { computer: "Welcome to Recap Studio", writer: "Script writer", voice: "Voice", video: "Video encoding",
  extras: "Optional downloads", done: "All set" };
const WRITERS = [
  ["claude", "Claude (Anthropic)", "Read the story best in testing: kept track of who was narrating, who said what and what was only imagined. From about 1¢ (Haiku) to 40¢ (Opus) per chapter."],
  ["claude_code", "Claude Code (your Claude plan)", "Claude on your Claude subscription instead of API credit: a Claude Code agent writes each chapter. Needs Claude Code on this computer (on Windows, the Claude desktop app includes it), logged in to your Claude account."],
  ["openrouter", "OpenRouter", null],
  ["gemini", "Google Gemini", null],
  ["deepseek", "DeepSeek", null],
  ["groq", "Groq", null],
  ["mistral", "Mistral", null],
  ["ollama", "Local model (Ollama)", "Free and private: runs on this computer with Ollama. Much less accurate at following the story, and slower."],
];
const VOICE_LANGS = { a: "American English", b: "British English", e: "Spanish", f: "French", h: "Hindi", i: "Italian",
  j: "Japanese", p: "Brazilian Portuguese", z: "Mandarin" };

// A Kokoro voice's name from its code: "af_heart" is Heart (American English, female); short: just Heart
function voiceName(code, short = false) {
  const m = /^([a-z])([fm])_(.+)$/.exec(code || "");
  if (!m || !VOICE_LANGS[m[1]]) return code || "";
  const name = m[3].replace(/_/g, " ").replace(/\b\p{L}/gu, (ch) => ch.toUpperCase());
  return short ? name : `${name} (${VOICE_LANGS[m[1]]}, ${m[2] === "m" ? "male" : "female"})`;
}
let setupPage = 0, setupPoll = null, setupKeyNote = null;
let setupGpuPoll = null;  // (apart from setupPoll: the voice can download at the same time)
const setupWants = { ocr: true, bubbles: true, scan: false, magi: false, magiLicense: false };

function openSetup(page = 0) {
  setupPage = page;
  renderSetup();
  if (!$("#setup-dialog").open) $("#setup-dialog").showModal();
}

async function saveGlobal(changes) {
  globals = await api("/api/settings", { method: "PUT", json: changes });
  if (current) Object.assign(current.settings, globals);
}

function setupLine(ok, text, ...more) {
  return el("div", { class: `setup-line ${ok ? "ok" : "warn"}` }, el("span", { class: "mark" }, ok ? "✓" : "!"),
    el("div", {}, text, ...more));
}

async function renderSetup() {
  clearTimeout(setupPoll);
  clearTimeout(setupGpuPoll);
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
  const g = await api("/api/addons/gpu");
  const st = await loadStorage();
  body.append(
    el("p", {}, "Recap Studio turns comic and webtoon chapters into narrated recap videos. A few choices first; you can change any of them later in Settings."),
    el("span", { class: "field-head" }, "Your projects"),
    setupLine(!st.synced, `Saved in ${st.projects}, each in a folder named after it.` +
      (st.synced ? ` ${st.synced} syncs that folder: recaps run to several GB each, so pick another unless you want them uploaded.` : "") + " ",
      el("button", { type: "button", class: "link-btn", onclick: async () => { await changeProjectsFolder(); renderSetup(); } }, "Change…")),
    el("span", { class: "field-head" }, "This computer"),
    v.installed ? setupLine(true, `Voice model ready (${plural(env.voices.length, "voice")}).`)
      : setupLine(false, "The voice model isn't downloaded yet. It reads the narration aloud.", voiceDownload(v)),
    env.ffmpeg ? setupLine(true, "ffmpeg found: it puts the videos together.")
      : setupLine(false, onWindows() ? "ffmpeg is missing, so videos can't be made. Run setup.ps1 again, or install ffmpeg and restart the app."
        : "ffmpeg is missing, so videos can't be made. Install it with your system's package manager (sudo apt install ffmpeg, " +
          "or sudo dnf install ffmpeg on Fedora with RPM Fusion), then restart the app."),
    ...[await fontsLine().catch(() => null), await menuLine().catch(() => null)].filter(Boolean),
    env.gpu.available ? setupLine(true, `NVIDIA GPU found${env.gpu.card ? ` (${env.gpu.card})` : ""}: text reading and the voice run on it.`)
      : env.gpu.card && g.offered ? setupLine(false, `Text reading and the voice run on the CPU. Your ${env.gpu.card} still encodes ` +
        "the videos, but this copy carries the CPU build of the engine that reads text and speaks. That works, just more " +
        "slowly; GPU support, a download, runs them on the card.", gpuDownload(g))
      : env.gpu.card ? setupLine(false, `Text reading and the voice run on the CPU. Your ${env.gpu.card} still encodes the videos, ` +
        "but this copy carries the CPU build of the engine that reads text and speaks (a GPU build is about 1.3 GB more). " +
        "That works, just more slowly.")
      : setupLine(false, "No NVIDIA GPU found: text reading and the voice run on the CPU. That works, just more slowly."),
    env.video && env.video.nvenc ? setupLine(true, `Videos are encoded on the ${env.video.encoder || ""} GPU.`.replace("  ", " "))
      : setupLine(false, "Videos are encoded on the CPU (about half the speed of a GPU encoder)."));
}

function voiceDownload(st) {
  const box = el("div", { class: "setup-dl" });
  const draw = (s) => {
    clearTimeout(setupPoll);
    if (s.installed) {  // the line turns into a tick once the voices are loaded
      box.replaceChildren(el("span", { class: "ok" }, "Downloaded."));
      loadEnv().then(() => { if ($("#setup-dialog").open && SETUP_PAGES[setupPage] === "computer") renderSetup(); });
      return;
    }
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

function gpuDownload(st) {
  const box = el("div", { class: "setup-dl" });
  const act = (path, method) => async () => {
    try { draw(await api(path, { method })); } catch (e) { draw({ ...st, state: "error", error: e.message }); }
  };
  const draw = (s) => {
    clearTimeout(setupGpuPoll);
    const was = st.installed || st.active;
    st = s;
    if (s.installed || s.active) {  // used from the next start
      box.replaceChildren(el("span", { class: "ok" }, gpuNote(s)));
      if (!was) loadEnv();  // the sidebar's GPU line
      return;
    }
    const busy = s.state === "installing";
    box.replaceChildren(
      busy ? el("div", { class: "bar" }, el("div", { style: `width: ${Math.round(s.progress * 100)}%` })) : "",
      el("span", { class: s.error ? "error" : "muted" }, busy ? s.message : s.error ? `The download failed: ${s.error}`
        : `${gpuGB(s)} GB from PyPI (${s.unpacked_gb} GB once unpacked): ONNX Runtime's GPU build, with NVIDIA's CUDA and cuDNN.`),
      !busy && s.driver_old ? el("span", { class: "hint warn" }, gpuDriver(s)) : "",
      busy ? el("button", { type: "button", class: "ghost small", onclick: act("/api/addons/gpu/cancel", "POST") }, "Cancel")
        : el("button", { type: "button", class: "small", onclick: act("/api/addons/gpu/install", "POST") }, "Download GPU support"));
    if (busy) setupGpuPoll = setTimeout(async () => draw(await api("/api/addons/gpu")), 800);
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

// Claude Code (claude_code.py): on this PC, and logged in? As Settings → Script writer shows it
function claudeCodeDetails(box) {
  const status = el("div", { class: "muted" }, "Looking for Claude Code…");
  const login = el("button", { type: "button", hidden: true }, "Log in to Claude Code");
  const check = async (refresh) => {
    let st;
    try { st = await api(`/api/claude-code${refresh ? "?refresh=true" : ""}`); }
    catch (e) { status.className = "bad"; status.textContent = e.message; return; }
    login.hidden = !st.found || st.logged_in;
    status.className = st.found && st.logged_in && st.skill ? "ok" : "bad";
    status.textContent = !st.found ? `Claude Code wasn't found on this computer. Install it (claude.com/code)${onWindows() ? ", or the Claude desktop app, which includes it," : ""} then press Check again.`
      : !st.logged_in ? `Claude Code ${st.version} is here, but not logged in yet.`
      : !st.skill ? "Claude Code is ready, but the narration skill its agents follow is missing from the app's folder."
      : `✓ Claude Code ${st.version}, logged in to your Claude account.`;
  };
  login.addEventListener("click", async () => {
    try {
      const r = await api("/api/claude-code/login", { method: "POST" });
      status.className = "muted";
      status.textContent = claudeLoginSaid(r);
    } catch (e) { notify(e.message); }
  });
  const sel = el("select", { onchange: () => saveGlobal({ claude_code_model: sel.value }) },
    el("option", { value: "opus" }, "Opus (the best writer)"), el("option", { value: "sonnet" }, "Sonnet (faster, uses less of your plan)"));
  sel.value = globals.claude_code_model || "opus";
  box.append(status, el("div", { class: "key-row" }, login, el("button", { type: "button", class: "ghost small", onclick: () => check(true) }, "Check again")),
    el("label", {}, "Model ", sel),
    el("span", { class: "hint" }, "Each chapter uses your Claude plan's usage. If it runs out, the job pauses until it resets."));
  check(false);
  return box;
}

function writerDetails(p) {
  const box = el("div", { class: "setup-details" });
  if (p === "claude_code") return claudeCodeDetails(box);
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
  const sel = el("select", {}, ...env.voices.map((v) => el("option", { value: v }, voiceName(v))));
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
  const showSpeed = async () => {  // (at the usual 1080p, 30 fps)
    const q = new URLSearchParams({ resolution: 1080, fps: 30, video_style: "auto", video_encoder: globals.video_encoder });
    try {
      const r = await api(`/api/video-speed?${q}`);
      line.textContent = `At 1080p and 30 fps, an hour of video takes ${aboutTime(r.seconds_per_hour)} to make on this computer.`;
    } catch { line.textContent = ""; }
  };
  const gpu = env.video && env.video.nvenc;
  const sel = el("select", {}, el("option", { value: "auto" }, gpu ? `${env.video.encoder || "GPU"} GPU (fast)` : "Automatic"),
    el("option", { value: "cpu" }, "CPU only"));
  sel.value = globals.video_encoder;
  sel.addEventListener("change", async () => { await saveGlobal({ video_encoder: sel.value }); showSpeed(); });
  body.append(el("p", {}, "Each project picks its own format, size and style when you create it. Here: what turns the frames into a video file, for every project."),
    gpu ? el("label", {}, "Encoder ", sel)
      : setupLine(false, "No GPU video encoder was found, so videos are encoded on the CPU (about half the speed)."),
    line);
  showSpeed();
}

async function setupExtras(body) {
  const [o, b, c, m] = await Promise.all([api("/api/addons/ocr-en"), api("/api/addons/bubbles"), api("/api/addons/charscan"),
    api("/api/addons/magi")]);
  const row = (key, st, title, about) => (st.installed ? setupLine(true, `${title}: installed.`)
    : st.state === "installing" ? setupLine(true, `${title}: downloading (see Settings).`)
    : el("label", { class: "setup-choice" },
      el("input", { type: "checkbox", checked: setupWants[key], onchange: (e) => { setupWants[key] = e.target.checked; } }),
      el("div", {}, el("b", {}, title), el("div", { class: "muted" }, about))));
  const magiOpen = !m.installed && m.state !== "installing";
  body.append(el("p", {}, "Optional extras that improve the results. Tick what you want; it downloads in the background after you press Next, and you can add or remove these later in Settings."),
    row("ocr", o, `English text reading (${o.download_mb} MB)`, "Reads English comic lettering far more accurately than the built-in model. Recommended."),
    row("bubbles", b, `Bubble finder (${b.download_mb} MB)`, "Keeps a webtoon's cuts out of its speech bubbles and captions, so no scene shows half a bubble. Recommended for webtoons."),
    row("scan", c, `Character scan (${c.download_mb} MB)`, "Finds the characters in your chapters so you can name them. Named characters help the narrator tell who is who."),
    row("magi", m, `Speaker detection, Magi v2 (about ${m.download_gb} GB)`, `Guesses who says each line. ${m.license}`),
    magiOpen ? el("label", { class: "check setup-license" },
      el("input", { type: "checkbox", checked: setupWants.magiLicense, onchange: (e) => { setupWants.magiLicense = e.target.checked; } }),
      "For Magi: I'll use it only for personal, non-commercial purposes") : "");
}

async function startSetupDownloads() {
  const tries = [];
  if (setupWants.ocr) tries.push(api("/api/addons/ocr-en/install", { method: "POST" }));
  if (setupWants.bubbles) tries.push(api("/api/addons/bubbles/install", { method: "POST" }));
  if (setupWants.scan) tries.push(api("/api/addons/charscan/install", { method: "POST" }));
  if (setupWants.magi) {
    if (!setupWants.magiLicense) return notify("Tick the box agreeing to Magi's license, or untick Magi.", "Magi's license");
    tries.push(api("/api/addons/magi/install", { method: "POST", json: { accept_license: true } }));
  }
  const failed = (await Promise.allSettled(tries)).filter((r) => r.status === "rejected" && !/already/i.test(r.reason.message));
  if (failed.length) notify(failed.map((r) => r.reason.message).join("\n"), "A download couldn't start");
  return true;
}

async function setupDone(body) {
  const gpu = env.video && env.video.nvenc && globals.video_encoder !== "cpu";
  const g = await api("/api/addons/gpu").catch(() => ({}));  // GPU support, if it was downloaded on the first page
  body.append(el("p", {}, "You're set. Everything chosen here is also in Settings, and Settings → Run setup again brings this back."),
    el("ul", { class: "setup-summary" },
      el("li", {}, `Script: ${writerLabel()}`),
      el("li", {}, `Voice: ${globals.voice}, speed ${globals.speed}`),
      el("li", {}, `Videos encoded on the ${gpu ? `${env.video.encoder || ""} GPU`.trim() : "CPU"}`),
      g.offered && !g.active && g.installed ? el("li", {}, "GPU support downloaded: close Recap Studio and open it again to use it")
        : g.offered && g.state === "installing" ? el("li", {}, "GPU support still downloading: once it's done, close Recap " +
          "Studio and open it again to use it") : ""),
    el("p", {}, $("#projects").children.length ? "Open a project from the list to carry on."
      : "To start, type a name at the top left and press Create: a few questions set up how its video looks."));
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

// ---- where projects are saved (Settings → Projects folder, first-run setup; store.move_projects) ----
let storage = null;

async function loadStorage() {
  try { storage = await api("/api/storage"); } catch { return null; }
  renderStorage();
  return storage;
}

function renderStorage() {
  if (!storage) return;
  $("#projects-path").textContent = storage.projects;
  $("#projects-synced").hidden = !storage.synced;
  $("#projects-synced").textContent = storage.synced
    ? `${storage.synced} syncs this folder, so every project is uploaded as it grows (several GB per recap).` : "";
  const m = storage.move, moving = m.state === "moving";
  $("#projects-moving").hidden = !moving;
  $("#projects-move-msg").textContent = moving ? `${m.message} (${m.done} of ${m.total})` : "";
  $("#projects-move-bar").style.width = `${Math.round(100 * m.done / Math.max(1, m.total))}%`;
  $("#projects-change").disabled = moving;
  $("#storage-other").textContent = `Settings and API keys: ${storage.data}. Voice model: ${storage.models}. ` +
    `Add-ons: ${storage.addons}.`;
}

async function pickFolder(start) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder) return window.pywebview.api.pick_folder();
  return window.prompt("The full path of the folder:", start || "");  // (in a browser, there's no folder picker)
}

async function changeProjectsFolder() {
  const folder = await pickFolder(storage && storage.projects);
  if (!folder) return;
  let checked;
  try { checked = await api("/api/storage/check", { method: "POST", json: { folder } }); }
  catch (e) { return notify(e.message, "Can't use that folder"); }
  const synced = checked.synced ? `\n\n${checked.synced} syncs that folder, so projects there are uploaded as they grow ` +
    "(several GB per recap)." : "";
  const n = (await api("/api/projects")).length;
  let move = true;
  if (n) {
    const pick = await choose(`Move your projects to ${checked.folder}?`,
      `${plural(n, "project")} move there, each in its own folder. On the same drive that's instant; to another drive ` +
      "they're copied first, and the old copies are removed once every one has arrived." + synced +
      "\n\nOr switch without moving them: projects left in the old folder don't show until you switch back.",
      [{ value: "switch", label: "Switch without moving" }, { value: "move", label: "Move them", primary: true }]);
    if (!pick) return;
    move = pick === "move";
  } else if (synced && !await ask(`Save projects in ${checked.folder}?`, synced.trim(), { ok: "Use it anyway" })) return;
  try { storage = await api("/api/storage/projects", { method: "POST", json: { folder: checked.folder, move } }); }
  catch (e) { return notify(e.message, "The projects weren't moved"); }
  renderStorage();
  while (storage.move.state === "moving") {
    await new Promise((r) => setTimeout(r, 700));
    await loadStorage();
  }
  if (storage.move.state === "error") notify(storage.move.error, "The projects weren't moved");
  const list = await api("/api/projects");
  if (current && !list.some((p) => p.id === current.id)) {  // (left behind in the old folder)
    current = null;
    clearInterval(polling);
    localStorage.removeItem("last");
    render();
  } else if (current) await openProject(current.id);
  loadProjects();
}

$("#projects-change").addEventListener("click", changeProjectsFolder);
$("#projects-show").addEventListener("click", () => api("/api/storage/show", { method: "POST" }));
$("#show-folder").addEventListener("click", () => {
  setMenu(false);
  api(`/api/projects/${current.id}/show-folder`, { method: "POST" }).catch((e) => notify(e.message));
});

// ---- updates from GitHub (updates.py): a button in the sidebar when a newer release is out, Settings → Updates and this PC ----
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
  $("#app-label").textContent = u.label;
  $("#update-edition").hidden = u.kind === "source" || u.edition !== "lite";
  $("#update-info").textContent = u.kind === "source" ? `${u.label}, running from source: git pull updates it.`
    : `${u.label} (${{ installed: "installed", appimage: "AppImage", container: "in Docker" }[u.kind] || "portable"}). ` +
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
  const pick = await choose(`Recap Studio ${u.edition_name} ${latest.version} is out`,
    `You have ${u.version}.` + (notes ? `\n\n${notes}` : "") + "\n\n" +
    (u.kind === "container" ? "Recap Studio runs in Docker here, so it's updated with a newer image: docker " +
      "compose pull, then docker compose up -d (or your NAS's container app's update). Your projects and settings " +
      "stay in /data."
      : canInstall ? `Update now downloads it from GitHub (${Math.round(latest.asset.size / 1e6)} MB), checks it, closes ` +
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

// ---- feature plugins (plugins.py): Settings → Plugins, and what a plugin's page script is given ----

const pluginHooks = {};      // event: [functions] (RecapStudio.on)
const pluginSettings = [];   // [{box, render}] (RecapStudio.addSettings), drawn with the rest of Settings
const libraryPanels = [];    // [{box, render}] (RecapStudio.addLibraryPanel), drawn again with the library's projects

// Publish → Where it goes: the posting plugins' buttons, or, with none, how to set one up
function renderPublishWhere() {
  $("#publish-none").hidden = $("#publish-plugins").children.length > 0;
}
$("#publish-setup").addEventListener("click", () => openSettings("Manage plugins"));

// a status chip: the words at the right of a rail button or a library panel's title (tone: "", "ok", "warn", "on")
const chipOf = (chip) => (text, tone = "") => {
  chip.textContent = text || "";
  chip.className = `side-chip${tone ? ` ${tone}` : ""}`;
};

async function emitPlugins(event, data) {
  for (const fn of pluginHooks[event] || []) {
    try { await fn(data); } catch (e) { console.error(e); notify(`A plugin failed (${event}): ${e.message}`); }
  }
}

// A site answered a source with its check for a person (Cloudflare) instead of its page (sources.SiteCheck): the
// plugins are told ("site-check"), so one can offer the site's window. Not awaited: what asked goes on to show its error.
const siteCheck = (check, message) => { emitPlugins("site-check", { ...check, message }); };

// A feature plugin's web\plugin.js runs after this file and adds to the page through these (docs/plugins.md). Events:
// "new-project-open" ({name, series}), "new-project-page" ({page, body, state}: add to body; state.name, state.kind,
// state.setName(), state.setKind()), "project-created" ({project}: awaited before it opens), "project-opened"
// ({project}), "site-check" ({host, url, source, refused, message}: a site answered a source with its check for a
// person, such as Cloudflare's, instead of its page).
window.RecapStudio = {
  version: 1,
  api, el, ask, notify, plural, showTab,
  // one search in several sources, each one's answer as it comes: searchSources(keys, query, got, {signal}) (0.2.6)
  searchSources,
  current: () => current,
  reload: () => (current ? openProject(current.id) : loadProjects()),
  on(event, fn) { (pluginHooks[event] ||= []).push(fn); },
  // where addButton can put a button (a plugin checks for "publish" before asking for it: an older app has no such place)
  places: ["characters", "chapters", "video", "publish", "sidebar"],
  // a button in the Characters tab ("characters"), beside Get chapters from a site ("chapters"), with the finished
  // video's buttons in the Video tab ("video"), with the posting services at Publish ("publish": Post to YouTube,
  // Post to TikTok), or in the rail's Automation section ("sidebar", for what's about every project: its words, and
  // a status chip at its right, `status` now and setStatus(text, tone) later)
  addButton(where, label, onclick, { title = "", status = "" } = {}) {
    const b = el("button", { type: "button", class: where === "sidebar" || where === "video" ? "" : "ghost", title, onclick });
    if (where === "sidebar") {
      const chip = el("span", { class: "side-chip" });
      b.append(el("span", { class: "side-text" }, label), chip);
      b.setStatus = chipOf(chip);
      b.setStatus(status);
      $("#sidebar-plugins").append(b);
      $("#sidebar-plugins-box").hidden = false;
    } else {
      b.append(label);
      if (where === "chapters") $("#get-chapters").after(b);
      else if (where === "video") $("#output-plugins").append(b);
      else if (where === "publish") { $("#publish-plugins").append(b); renderPublishWhere(); }
      else $(".char-buttons").append(b);
    }
    return b;
  },
  // a panel of its own on the home page, under the projects (for what's about every project: a plan, a queue):
  // render(box) draws into it now and each time the library's projects are loaded again, and the plugin can draw
  // into box whenever its own state changes; the panel's chip is `status` now and setStatus(text, tone) later
  addLibraryPanel(title, render, { status = "" } = {}) {
    const chip = el("span", { class: "side-chip" });
    const box = el("div", { class: "lib-panel-body" });
    $("#library-panels").append(el("section", { class: "lib-panel", "aria-label": title },
      el("div", { class: "lib-panel-head" }, el("b", {}, title), chip), box));
    $("#library-panels").hidden = false;
    libraryPanels.push({ box, render });
    const setStatus = chipOf(chip);
    setStatus(status);
    try { render(box); } catch (e) { console.error(e); }
    return { box, setStatus };
  },
  // Settings, open at a section: the app's own by name ("Sources"), or a plugin's own (its addSettings title)
  openSettings: (name) => openSettings(name),
  // a page of its own in the New project dialog, after the dialog's own (the last page has Create project):
  // render({body, state}) draws it into body; the plugin keeps the choices and applies them on "project-created"
  addNewProjectPage({ id, title, render }) {
    const key = `plugin:${id}`;
    if (!pluginPages.some((p) => p.id === key)) pluginPages.push({ id: key, title, render });
  },
  // a folder on this PC, from the window's folder picker (or typed, in a browser)
  pickFolder: (start) => pickFolder(start),
  // a choice of its own in Project settings (in a Plugins section), for a setting its Python side registered with
  // plugin.project_setting: options [[value, text]]; saved like the rest as soon as it changes
  addProjectSetting({ key, label, options, hint = "" }) {
    let box = $("#plugin-project-settings");
    if (!box) {
      box = el("fieldset", { id: "plugin-project-settings" }, el("legend", {}, "Plugins"));
      $("#settings").append(box);
    }
    const input = el("select", { "data-key": key }, ...options.map(([v, text]) => el("option", { value: v }, text)));
    bindProjectInput(input);
    box.append(...[el("label", {}, `${label} `, input), hint ? el("span", { class: "hint" }, hint) : null].filter(Boolean));
    if (current) setInputs(box, current.settings);
    return input;
  },
  // a language a comic can be in (the plugin's Python side registers how it's read): offered in New project and
  // Project settings, as a raw translated into English
  addLanguage(code, name) {
    LANGUAGES[code] = name;
    document.querySelectorAll('select[data-key="language"]').forEach((sel) => {
      if (![...sel.options].some((o) => o.value === code)) sel.append(el("option", { value: code }, `${name} (translated into English)`));
    });
  },
  // New project, as the sidebar's box opens it: named, of a kind of comic ("manga", "webtoon", "comic"), and of a
  // series a site lists ({url, title, cover, summary, kind, year}: Find the series picks it, with its characters)
  newProject({ name = "", kind = "", series = null } = {}) {
    return openNew(String(name || "").trim(), { kind, series });
  },
  // Get chapters from a site, for the open project, searching every source that can for a series by its titles (the
  // first ones in turn, until one finds something): a series called exactly one of them, in one source only, is
  // opened to choose its chapters (auto: they're all got, without asking). Whether any source can search is
  // RecapStudio.api("/api/sources") (chapters !== false and search).
  getChapters({ titles = [], auto = false } = {}) {
    if (!current) return;
    return openSources(false, { titles: [].concat(titles).map(String), auto: !!auto });
  },
  // the sidebar's box for a new project: its placeholder, its button's word and the label above it (what's typed
  // opens New project)
  setNewProjectBox({ placeholder, button, title, label } = {}) {
    // (the rail has one New project button now; a plugin's words for the box, its button and its label are kept but
    // not shown: the search happens in the dialog)
    if (placeholder) $("#new-name").placeholder = placeholder;
    if (title) $("#new-project button[type=submit]").title = title;
    if (label) $("#new-project-label").textContent = label;
    void button;
  },
  // a section of its own in Settings (listed under Plugins); render(box) runs each time Settings is drawn
  addSettings(title, render) {
    const box = el("div", {});
    $("#plugins-box").after(el("fieldset", { "data-plugin": "1" }, el("legend", {}, title), box));
    pluginSettings.push({ box, render });
    if ($("#global-dialog").open) { render(box); renderSettingsNav(); }  // (Settings opened before the plugin loaded)
  },
};

async function loadPluginScripts() {
  let st;
  try { st = await api("/api/plugins"); } catch { return; }
  showFileTypes(st.file_types);
  for (const p of st.plugins.filter((x) => x.running && x.web)) {
    const base = `/plugin-web/${encodeURIComponent(p.id)}/`, v = `?v=${encodeURIComponent(p.version)}`;  // (an update is never cached)
    document.head.append(el("link", { rel: "stylesheet", href: `${base}plugin.css${v}`, onerror: (e) => e.target.remove() }));
    await new Promise((done) => document.body.append(el("script", { src: `${base}plugin.js${v}`, onload: done,
      onerror: () => { console.error(`${p.name}: plugin.js didn't load`); done(); } })));
  }
}

let pluginList = null;  // Recap Studio's own plugin list, once asked for (Show the plugins)

async function renderPluginSettings() {
  for (const s of pluginSettings) { try { s.render(s.box); } catch (e) { console.error(e); } }
  let st;
  try { st = await api("/api/plugins"); } catch (e) { $("#plg-error").textContent = e.message; return; }
  $("#plg-restart").hidden = !st.restart;
  const state = (p) => p.error ? p.error : !p.enabled ? "Off" + (p.running ? " from the next start" : "")
    : p.running ? "On" : "On from the next start";
  $("#plg-installed").replaceChildren(...st.plugins.map((p) => el("div", { class: "src-plugin" },
    el("label", { class: "check" },
      el("input", { type: "checkbox", checked: p.enabled, disabled: !!p.error && !p.enabled, onchange: async (e) => {
        try { await api(`/api/plugins/${p.id}`, { method: "PUT", json: { enabled: e.target.checked } }); }
        catch (err) { notify(err.message); }
        renderPluginSettings();
      } }),
      el("b", {}, p.name), p.version ? ` ${p.version}` : ""),
    el("span", { class: p.error ? "error" : "muted" },
      [p.summary, p.where === "bundled" ? "comes with Full Suite" : "", state(p)].filter(Boolean).join(" · ")),
    p.where === "installed" ? el("button", { type: "button", class: "ghost small danger", onclick: async () => {
      if (!await ask(`Remove ${p.name}?`, "Its folder is deleted from data\\plugins. It stops from the next start.",
        { ok: "Remove", danger: true })) return;
      try { await api(`/api/plugins/${p.id}`, { method: "DELETE" }); } catch (err) { return notify(err.message); }
      renderPluginSettings();
    } }, "Remove") : null)));
  if (!st.plugins.length) $("#plg-installed").replaceChildren(el("span", { class: "muted" }, "No plugins yet."));
  $("#plg-error").textContent = "";
  if (pluginList) showPluginList();
}

async function showPluginList() {
  const box = $("#plg-available");
  if (!pluginList) box.replaceChildren(el("span", { class: "muted" }, "Loading…"));
  let r;
  try { r = pluginList = await api("/api/plugins/list"); }
  catch (e) { box.replaceChildren(el("span", { class: "error" }, e.message)); return; }
  $("#plg-show").hidden = true;
  box.replaceChildren(...r.plugins.map((p) => el("div", { class: "src-plugin" },
    el("b", {}, p.name, p.version ? ` ${p.version}` : ""),
    el("span", { class: "muted" }, [p.summary, p.kind === "feature" ? "adds to the app" : "a source (Settings → Sources)",
      p.sites.join(", ")].filter(Boolean).join(" · ")),
    p.too_new ? el("span", { class: "muted" }, "Needs a newer Recap Studio")
      : p.installed && !p.update ? el("span", { class: "ok" }, "Installed")
      : el("button", { type: "button", class: "ghost small", onclick: async (e) => {
        e.target.disabled = true;
        try { await api("/api/plugins/install", { method: "POST", json: { id: p.id } }); }
        catch (err) { e.target.disabled = false; return notify(err.message, "Not installed"); }
        await renderPluginSettings();
        renderSourceSettings();
      } }, p.update ? "Update" : "Install"))));
  if (!r.plugins.length) box.replaceChildren(el("span", { class: "muted" }, "The list is empty."));
}

$("#plg-show").addEventListener("click", showPluginList);

// Restart now: the app closes and opens again (in the window, a new window opens; in a browser, this page waits for
// it and reloads). A running job stops where it is, as when the app is closed.
$("#plg-restart-go").addEventListener("click", async () => {
  let r;
  try { r = await api("/api/restart", { method: "POST", json: {} }); }
  catch (e) {
    if (!/running/.test(e.message)) return notify(e.message);
    if (!await ask("Restart anyway?", `${e.message}. It stops where it is, as when Recap Studio is closed: Continue picks ` +
      "it up afterwards.", { ok: "Restart" })) return;
    try { r = await api("/api/restart", { method: "POST", json: { force: true } }); } catch (err) { return notify(err.message); }
  }
  if (!r || !r.restarting) return;
  ask("Restarting…", "Recap Studio is closing and opening again.", { ok: "OK", cancel: false });
  const before = Date.now();
  const wait = setInterval(async () => {
    if (Date.now() - before < 2500) return;  // (the old copy is still closing)
    try {
      const st = await api("/api/plugins");
      if (!st.restart) { clearInterval(wait); location.reload(); }
    } catch { /* not back yet */ }
  }, 1000);
});

(async () => {
  globals = await api("/api/settings");
  applyTheme(globals.theme);
  await loadPluginScripts();
  renderPublishWhere();  // (the posting plugins have added their buttons, or there are none)
  await loadProjects();
  const last = localStorage.getItem("last");
  if (last) { try { await openProject(last); } catch { localStorage.removeItem("last"); } }
  render();
  await loadEnv();
  renderGlobal();
  if (!globals.setup_done) openSetup();
  loadUpdate();
})();

// In the app's own window, the keyboard's Back key would go back to the "Starting Recap Studio…" page and stay there.
// The app's page is the only one to be on, so going back lands here again. (A browser tab keeps its history.)
function stayOnThisPage() {
  history.pushState(null, "", location.href);
  window.addEventListener("popstate", () => history.pushState(null, "", location.href));
}
if (window.pywebview) stayOnThisPage(); else window.addEventListener("pywebviewready", stayOnThisPage, { once: true });

// Settings is a page beside the rail (the dialog opened without a backdrop), so Escape has to close it itself
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("#global-dialog").open && !document.querySelector("dialog:modal")) $("#global-dialog").close();
});
