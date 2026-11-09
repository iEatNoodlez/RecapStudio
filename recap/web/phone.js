// Recap Studio's phone page (phone.py): every project's progress, why one stopped, and what's worth doing from a
// phone: pause, resume, cancel, run again or continue, two of a project's settings; a new project; the characters
// a recap stopped to ask about, named; and chapters got from the sources the PC has.

const $ = (s) => document.querySelector(s);

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "class") e.className = v;
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids) if (k != null && k !== "") e.append(k);
  return e;
}

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers = { "Content-Type": "application/json" };
  }
  const r = await fetch(path, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

const ACTIVE = ["queued", "running", "paused"];
const active = (job) => !!job && ACTIVE.includes(job.status);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const kids = (...xs) => xs.filter((x) => x != null && x !== "");  // (append would print "null" for an empty slot)
let timer = null, shown = null, busy = false;

function ago(t) {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

function stateLine(p) {
  return el("div", { class: `state ${p.state.kind}` }, p.state.text);
}

function bar(job) {
  return active(job) && job.status !== "paused"
    ? el("div", { class: "bar" }, el("div", { style: `width:${Math.round((job.progress || 0) * 100)}%` })) : null;
}

// ---- all projects ----

let newOpen = false;  // the New project form, open at the top of the list

async function showList() {
  shown = null;
  getState = null;
  $("#back").hidden = true;
  $("#new-btn").hidden = false;
  $("#title").textContent = "Recap Studio";
  $("#project").hidden = true;
  $("#list").hidden = false;
  const projects = await api("/api/m/projects");
  // what needs you first (stopped to ask), then what's running, then the rest as they were last worked on
  const rank = (p) => (p.job && p.job.status === "paused" ? 0 : active(p.job) ? 1 : 2);
  projects.sort((x, y) => rank(x) - rank(y) || (y.updated || 0) - (x.updated || 0));
  if (editing($("#list"))) return schedule(10000);  // (the form being filled in isn't redrawn)
  const open = (p) => { location.hash = `p=${p.id}`; };
  const stop = (fn) => (e) => { e.stopPropagation(); fn(); };
  // a recap that stopped to ask, or one running: a card of its own, with its buttons
  const busyCard = (p) => {
    const paused = p.job.status === "paused";
    const c = el("div", { class: `card link ${paused ? "paused" : "running"}`, onclick: () => open(p) },
      el("h2", {}, paused ? `${p.name} stopped to ask` : p.name), stateLine(p), bar(p.job));
    if (!paused && p.job.label) c.append(el("div", { class: "muted" }, p.job.label));
    const row = el("div", { class: "row" });
    if (paused) {
      row.append(el("button", { class: "primary", onclick: stop(() => open(p)) }, "Answer"),
        el("button", { onclick: stop(() => act(() => api(`/api/jobs/${p.job.id}/resume`, { method: "POST" }))) }, "Resume"));
    } else {
      row.append(el("button", { onclick: stop(() => act(() => api(`/api/jobs/${p.job.id}/pause`, { method: "POST" }))) }, "Pause"),
        el("button", { class: "danger", onclick: stop(() => confirm("Cancel what it's doing? What's finished is kept.") &&
          act(() => api(`/api/jobs/${p.job.id}/cancel`, { method: "POST" }))) }, "Cancel"));
    }
    c.append(row);
    return c;
  };
  // the rest as rows: a cover, the name and state, and Make it or Continue where there's something to make
  const rowCard = (p) => {
    const thumb = p.cover ? el("img", { class: "row-thumb", alt: "", loading: "lazy", src: `/files/${p.id}/${p.cover}?v=${Math.round(p.updated || 0)}` })
      : el("div", { class: "row-thumb" });
    const made = p.counts.done === p.chapters && p.video;
    const go = p.chapters && p.make && !made ? el("button", { class: `row-btn${p.counts.new === p.chapters ? " primary" : ""}`,
      onclick: stop(() => act(() => api(`/api/projects/${p.id}/run`, { method: "POST", json: { steps: p.make, mode: "missing" } }))) },
    p.counts.new === p.chapters ? "Make it" : "Continue") : null;
    return el("div", { class: "card link row-card", onclick: () => open(p) }, thumb,
      el("div", { class: "row-text" }, el("h2", {}, p.name), stateLine(p),
        el("div", { class: "muted small" }, `${plural(p.chapters, "chapter")} · changed ${ago(p.updated)}`)), go);
  };
  const busy = projects.filter((p) => p.job && (p.job.status === "paused" || active(p.job)));
  const rest = projects.filter((p) => !busy.includes(p));
  $("#list").replaceChildren(...kids(newOpen ? newForm() : null,
    ...busy.map(busyCard),
    rest.length ? el("div", { class: "section-label" }, "Your recaps") : null,
    ...rest.map(rowCard),
    projects.length ? null : el("div", { class: "empty" }, "No projects yet. Make one here, or in Recap Studio on your PC.")));
  schedule(projects.some((p) => active(p.job)) ? 4000 : 10000);
}

const editing = (root) => root.contains(document.activeElement) && ["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName);

// ---- a new project: its name, the kind of comic and its language; the rest from your last project's choices ----

const KINDS = [["webtoon", "Webtoon", { layout: "webtoon", reading_direction: "ltr" }], ["manga", "Manga", { layout: "pages", reading_direction: "rtl" }],
  ["comic", "Comic", { layout: "pages", reading_direction: "ltr" }], ["auto", "Not sure", { layout: "auto", reading_direction: "ltr" }]];
const LANGUAGES = [["en", "English"], ["ko", "Korean, translated into English"], ["zh", "Chinese, translated into English"], ["ja", "Japanese, translated into English"]];
const newDraft = { name: "", kind: "webtoon", language: "en" };

function newForm() {
  const name = el("input", { class: "field", placeholder: "The series' name", value: newDraft.name, autocomplete: "off",
    oninput: (e) => { newDraft.name = e.target.value; } });
  const chips = el("div", { class: "chips" }, ...KINDS.map(([id, label]) => el("label", { class: `chip${newDraft.kind === id ? " on" : ""}` },
    el("input", { type: "radio", name: "kind", value: id, checked: newDraft.kind === id,
      onchange: () => { newDraft.kind = id; chips.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c.querySelector("input").value === id)); } }),
    label)));
  const lang = el("select", { class: "field", onchange: (e) => { newDraft.language = e.target.value; } },
    ...LANGUAGES.map(([v, t]) => el("option", { value: v, selected: v === newDraft.language }, t)));
  const status = el("div", { class: "error" });
  const create = el("button", { class: "primary", onclick: async () => {
    create.disabled = true;
    status.textContent = "";
    try {
      const settings = { ...await api("/api/projects/new-settings"), ...KINDS.find((k) => k[0] === newDraft.kind)[2], language: newDraft.language };
      const p = await api("/api/projects", { method: "POST", json: { name: newDraft.name.trim() || "Untitled", settings } });
      newOpen = false;
      Object.assign(newDraft, { name: "", kind: "webtoon", language: "en" });
      location.hash = `p=${p.id}`;
    } catch (e) { status.textContent = e.message; create.disabled = false; }
  } }, "Create the project");
  setTimeout(() => name.focus(), 0);
  return el("div", { class: "card form" }, el("h2", {}, "New project"),
    el("label", { class: "stack" }, "Name", name),
    el("div", { class: "stack" }, "What kind of comic is it?", chips),
    el("label", { class: "stack" }, "Its text is in", lang),
    el("div", { class: "note" }, "The video, captions and the rest come from your last project's choices; change them on the PC later."),
    el("div", { class: "row" }, create, el("button", { onclick: () => { newOpen = false; refresh(); } }, "Cancel")), status);
}

$("#new-btn").addEventListener("click", () => { newOpen = !newOpen; refresh(); });

// ---- one project ----

async function act(fn) {
  if (busy) return;
  busy = true;
  try { await fn(); } catch (e) { alert(e.message); }
  busy = false;
  refresh();
}

function jobCard(p) {
  const job = p.job;
  const c = el("div", { class: `card ${job && job.status === "paused" ? "paused" : active(job) ? "running" : ""}` }, stateLine(p), bar(job));
  if (job && active(job) && job.label) {
    const n = (job.steps || []).indexOf(job.step);
    c.append(el("div", { class: "muted" }, `${job.label}${n >= 0 && job.steps.length > 1 ? ` · step ${n + 1} of ${job.steps.length}` : ""}`));
  }
  if (job && job.status === "paused" && job.waiting_for)
    c.append(el("div", { class: "note" }, job.waiting_for === "characters" && p.review && p.review.length
      ? "Name the people below, then Resume: the script uses the names."
      : "Check it on the PC, or Resume to carry on without."));
  if (job && job.note && !active(job)) c.append(el("div", { class: "note" }, job.note));
  const row = el("div", { class: "row" });
  if (job && job.status === "paused") {
    row.append(el("button", { class: "primary", onclick: () => act(() => api(`/api/jobs/${job.id}/resume`, { method: "POST" })) }, "Resume"));
  } else if (active(job)) {
    row.append(el("button", { onclick: () => act(() => api(`/api/jobs/${job.id}/pause`, { method: "POST" })) }, "Pause"));
  }
  if (active(job)) {
    row.append(el("button", { class: "danger", onclick: () => confirm("Cancel what it's doing? What's finished is kept.") &&
      act(() => api(`/api/jobs/${job.id}/cancel`, { method: "POST" })) }, "Cancel"));
  } else if (job && job.status === "error") {
    c.append(el("div", { class: "note" }, "Once it's sorted (topped up the script writer's credit, say), run it again: it carries on where it stopped."));
    row.append(el("button", { class: "primary", onclick: () => act(() => api(`/api/projects/${p.id}/run`, { method: "POST",
      json: { steps: job.steps, chapters: job.chapters, mode: "missing" } })) }, "Run again"));
  } else if (p.chapters) {
    const done = p.counts.done === p.chapters && p.video;
    row.append(el("button", { class: done ? "" : "primary", onclick: () => act(() => api(`/api/projects/${p.id}/run`, {
      method: "POST", json: { steps: p.make, mode: "missing" } })) }, done ? "Make the video again" : p.counts.new === p.chapters ? "Make the recap" : "Continue the recap"));
  }
  if (row.children.length) c.append(row);
  return c;
}

// ---- the characters the scan found, to name (the review): add as suggested, as someone already named, or ignore ----

const reviewDraft = {};    // the names typed, by group (kept across the page's refreshes)
let reviewSmall = false;   // the small groups shown too

function reviewCard(p) {
  const groups = p.review || [];
  if (!groups.length) return null;
  const big = groups.filter((g) => !g.small), small = groups.filter((g) => g.small);
  const listed = reviewSmall ? groups : big;
  const suggested = listed.filter((g) => g.suggestion && g.suggestion.name);
  const post = (path, json) => act(() => api(`/api/projects/${p.id}${path}`, { method: "POST", json }));
  const groupBox = (g) => {
    const name = (reviewDraft[g.id] ?? (g.suggestion ? g.suggestion.name : "")) || "";
    const input = el("input", { class: "field", placeholder: "Their name", value: name, autocomplete: "off",
      oninput: (e) => { reviewDraft[g.id] = e.target.value; } });
    const same = el("select", { class: "field", "aria-label": "The same person as", onchange: (e) => {
      if (!e.target.value) return;
      post(`/scan/groups/${g.id}/add`, { character: e.target.value, name: input.value.trim() });
    } }, el("option", { value: "" }, "The same as…"), ...p.characters.map((c) => el("option", { value: c.id }, c.name)));
    return el("div", { class: "group" },
      el("div", { class: "snaps" }, ...g.snapshots.map((f) => el("img", { src: `/files/${p.id}/${f}`, alt: "", loading: "lazy" }))),
      el("div", { class: "muted" }, (g.suggestion && g.suggestion.description ? `${g.suggestion.description} · ` : "") +
        `${plural(g.size, "appearance")} in ${plural(g.pieces, "panel")}`),
      el("div", { class: "row" }, input,
        el("button", { class: "primary", onclick: () => {
          const n = input.value.trim();
          if (!n) return input.focus();
          post(`/scan/groups/${g.id}/add`, { name: n, gender: (g.suggestion || {}).gender || "", description: (g.suggestion || {}).description || "",
            aliases: (g.suggestion || {}).aliases || "" });
        } }, "Add")),
      el("div", { class: "row" }, p.characters.length ? same : null,
        el("button", { onclick: () => post(`/scan/groups/${g.id}/ignore`, {}) }, "Ignore")));
  };
  const people = (n) => (n === 1 ? "1 person" : `${n} people`);
  return el("div", { class: "card review" }, el("h2", {}, listed.length ? `${people(listed.length)} to name` : "Seen only a few times"),
    el("div", { class: "note" }, "Found by the character scan. A name makes a character the narrator can use; Ignore leaves one out (an extra, say)."),
    suggested.length > 1 ? el("div", { class: "row" }, el("button", { onclick: () => post("/scan/add-named", { items: suggested.map((g) => ({
      group: g.id, name: reviewDraft[g.id] || g.suggestion.name, gender: g.suggestion.gender, aliases: g.suggestion.aliases, description: g.suggestion.description })) }) },
    `Add all ${suggested.length} as suggested`)) : null,
    ...listed.map(groupBox),
    small.length ? el("button", { class: "small-toggle", onclick: () => { reviewSmall = !reviewSmall; refresh(); } },
      reviewSmall ? "Hide the ones seen fewer than 3 times" : `${plural(small.length, "more")} seen fewer than 3 times`) : null);
}

function videoCard(p) {
  if (!p.video && !p.thumbnail) return null;
  const c = el("div", { class: "card" });
  if (p.thumbnail) c.append(el("img", { class: "thumb", alt: "Thumbnail", src: `/files/${p.id}/${p.thumbnail}?v=${Math.round(p.thumbnail_time || 0)}` }));
  if (p.title) c.append(el("div", {}, p.title));
  if (p.video) {
    const watch = el("button", { onclick: () => watch.replaceWith(el("video", { controls: true, autoplay: true, playsinline: true,
      src: `/files/${p.id}/${p.video.file.split("/").map(encodeURIComponent).join("/")}?t=${p.video.time || 0}` })) },
    `Watch the video (${Math.round(p.video.seconds / 60)} min)`);
    c.append(watch);
  }
  return c;
}

function chaptersCard(p) {
  const c = el("div", { class: "card" }, el("h2", {}, "Chapters"));
  if (p.chapter_list.length) c.append(el("div", { class: "chapters" }, ...p.chapter_list.map((x) =>
    el("div", { class: "chapter" }, el("span", {}, x.name), el("span", { class: `pill ${x.kind}` }, x.text)))));
  else c.append(el("div", { class: "muted" }, "None yet."));
  if (!active(p.job)) c.append(el("div", { class: "row" },
    p.source ? el("button", { onclick: () => startGet(p, "new") }, "Check for new chapters") : null,
    el("button", { onclick: () => startGet(p, "find") }, "Get chapters from a site…")));
  return c;
}

function settingsCard(p) {
  const s = p.settings;
  const toggle = (key, label, hint) => el("label", { class: "toggle" },
    el("span", {}, label, hint ? el("div", { class: "muted" }, hint) : null),
    el("input", { type: "checkbox", checked: !!s[key], disabled: active(p.job),
      onchange: (e) => act(() => api(`/api/m/projects/${p.id}/settings`, { method: "PUT", json: { [key]: e.target.checked } })) }));
  return el("div", { class: "card" }, el("h2", {}, "Settings"),
    toggle("background_music", "Background music", s.tracks ? `${s.tracks} track${s.tracks === 1 ? "" : "s"}` : "Add tracks on the PC first"),
    s.language !== "en" ? toggle("typeset", "English in the pictures") : null,
    el("div", { class: "muted" }, "They apply the next time the video is made."));
}

async function showProject(pid) {
  shown = pid;
  const p = await api(`/api/m/projects/${pid}`);
  if (shown !== pid) return;
  $("#back").hidden = false;
  $("#new-btn").hidden = true;
  $("#title").textContent = p.name;
  $("#list").hidden = true;
  $("#project").hidden = false;
  if (getState && getState.pid === pid) {  // (getting chapters: its own screen, drawn as it goes)
    if (!getState.drawn) renderGet(p);
    return schedule(10000);
  }
  if (!busy && !editing($("#project"))) {
    $("#project").replaceChildren(...[jobCard(p), reviewCard(p), videoCard(p), chaptersCard(p), settingsCard(p)].filter(Boolean));
  }
  schedule(active(p.job) ? 3000 : 10000);
}

// ---- chapters from a site, through the sources the PC has: search (or browse a list), pick the series, tick
// its chapters, get them; or the new chapters of the project's own series ----

let getState = null;  // {pid, mode, sources, source, query, list, items, more, series, key, picked, status, drawn}
let sourcesCache = null;

async function startGet(p, mode) {
  getState = { pid: p.id, mode, status: "", picked: new Set(), have: new Set(p.have_refs || []), page: 1 };
  renderGet(p);
  try {
    if (mode === "new") {
      getState.status = "Looking for new chapters…";
      renderGet(p);
      const r = await api(`/api/projects/${p.id}/source/new`);
      getState.key = r.key;
      openSeries(p, r.series, true);
      return;
    }
    getState.status = "Looking for sources…";
    renderGet(p);
    sourcesCache = sourcesCache || (await api("/api/sources")).sources;
    getState.sources = sourcesCache.filter((s) => s.chapters !== false && (s.search || (s.lists || []).length));
    getState.source = getState.sources[0] ? getState.sources[0].key : "";
    getState.status = getState.sources.length ? "" : "None of your sources can search or list series. Add one on the PC: Settings → Sources.";
  } catch (e) { getState.status = e.message; }
  renderGet(p);
}

function openSeries(p, series, allNew) {
  getState.series = series;
  getState.picked = new Set(series.chapters.filter((c) => allNew || !getState.have.has(c.ref)).map((c) => c.ref));
  getState.status = series.chapters.length ? "" : "It lists no chapters.";
  renderGet(p);
}

async function runGet(p, fn, status) {
  getState.status = status;
  renderGet(p);
  try { await fn(); } catch (e) { getState.status = e.message; }
  renderGet(p);
}

function renderGet(p) {
  const g = getState;
  if (!g) return;
  g.drawn = true;
  const back = el("button", { class: "ghost", onclick: () => { getState = null; refresh(); } }, "‹ Back");
  const box = el("div", { class: "card get" });
  if (g.series) {
    const s = g.series, key = g.key;
    const rows = s.chapters.map((c) => el("label", { class: "pick" },
      el("input", { type: "checkbox", checked: g.picked.has(c.ref), onchange: (e) => { e.target.checked ? g.picked.add(c.ref) : g.picked.delete(c.ref); update(); } }),
      el("span", { class: "pick-name" }, c.name), g.have.has(c.ref) ? el("span", { class: "pill done" }, "have it") : null));
    const go = el("button", { class: "primary", onclick: () => runGet(p, async () => {
      const chosen = s.chapters.filter((c) => g.picked.has(c.ref)).map(({ ref, name, url }) => ({ ref, name, url }));
      await api(`/api/projects/${p.id}/download`, { method: "POST", json: { key, series: { ref: s.ref, url: s.url, title: s.title }, chapters: chosen, place: "number", skip: [] } });
      getState = null;
      refresh();
    }, "Starting…") });
    const update = () => { go.textContent = `Get ${plural(g.picked.size, "chapter")}`; go.disabled = !g.picked.size; };
    update();
    box.append(el("h2", {}, s.title || "The series"),
      el("div", { class: "muted" }, `${plural(s.chapters.length, "chapter")} at ${sourceName(key)}${g.have.size ? `; ${g.have.size} already here` : ""}`),
      el("div", { class: "row" },
        el("button", { onclick: () => { g.picked = new Set(s.chapters.map((c) => c.ref)); renderGet(p); } }, "All"),
        el("button", { onclick: () => { g.picked = new Set(s.chapters.filter((c) => !g.have.has(c.ref)).map((c) => c.ref)); renderGet(p); } }, "Only new"),
        el("button", { onclick: () => { g.picked = new Set(); renderGet(p); } }, "None")),
      el("div", { class: "picks" }, ...rows),
      el("div", { class: "error" }, g.status), el("div", { class: "row" }, go));
  } else if (g.mode === "new") {
    box.append(el("h2", {}, "New chapters"), el("div", { class: "muted" }, g.status || "…"));
  } else if (!g.sources) {
    box.append(el("h2", {}, "Get chapters from a site"), el("div", { class: "muted" }, g.status || "…"));
  } else if (!g.sources.length) {
    box.append(el("h2", {}, "Get chapters from a site"), el("div", { class: "muted" }, g.status));
  } else {
    const src = g.sources.find((s) => s.key === g.source);
    const pick = el("select", { class: "field", "aria-label": "Source", onchange: (e) => { g.source = e.target.value; g.items = null; g.status = ""; renderGet(p); } },
      ...g.sources.map((s) => el("option", { value: s.key, selected: s.key === g.source }, s.name + (s.language ? ` (${s.language})` : ""))));
    const query = el("input", { class: "field", placeholder: "The series' title", value: g.query || "", autocomplete: "off",
      oninput: (e) => { g.query = e.target.value; }, onkeydown: (e) => { if (e.key === "Enter") search.click(); } });
    const search = el("button", { class: "primary", onclick: () => {
      if (!(g.query || "").trim()) return query.focus();
      g.page = 1;
      runGet(p, async () => { const r = await api("/api/sources/search", { method: "POST", json: { key: g.source, query: g.query.trim(), page: 1 } }); g.items = r.items; g.more = r.more; g.list = null; g.status = r.items.length ? "" : "Nothing found."; }, "Searching…");
    } }, "Search");
    const lists = src && (src.lists || []).length ? el("div", { class: "row" }, ...src.lists.map((l) => el("button", { class: g.list === l.id ? "on" : "", onclick: () => {
      g.page = 1;
      runGet(p, async () => { const r = await api("/api/sources/browse", { method: "POST", json: { key: g.source, list: l.id, page: 1 } }); g.items = r.items; g.more = r.more; g.list = l.id; g.status = r.items.length ? "" : "Nothing listed."; }, `Asking ${src.name}…`);
    } }, l.name))) : null;
    box.append(...kids(el("h2", {}, "Get chapters from a site"),
      el("label", { class: "stack" }, "Source", pick),
      src && src.search ? el("div", { class: "row" }, query, search) : null,
      lists ? el("div", { class: "stack" }, "Or a list", lists) : null,
      g.status && !g.items ? el("div", { class: "muted" }, g.status) : null,
      g.items ? el("div", { class: "results" }, ...g.items.map((it) => el("button", { class: "result", onclick: () => runGet(p, async () => {
        g.key = g.source;
        openSeries(p, await api("/api/sources/series", { method: "POST", json: { key: g.source, ref: it.ref } }), false);
      }, "Getting the series…") },
      it.cover ? el("img", { src: it.cover, alt: "", loading: "lazy" }) : el("span", { class: "nocover" }), el("span", {}, it.title)))) : null,
      g.items && g.more ? el("button", { onclick: () => runGet(p, async () => {
        g.page += 1;
        const r = g.list ? await api("/api/sources/browse", { method: "POST", json: { key: g.source, list: g.list, page: g.page } })
          : await api("/api/sources/search", { method: "POST", json: { key: g.source, query: g.query.trim(), page: g.page } });
        g.items = g.items.concat(r.items); g.more = r.more;
      }, "Loading more…") }, "More") : null,
      g.status && g.items ? el("div", { class: "muted" }, g.status) : null));
  }
  $("#project").replaceChildren(back, box);
}

const sourceName = (key) => ((sourcesCache || []).find((s) => s.key === key) || {}).name || "the site";

// ---- refreshing ----

function schedule(ms) {
  clearTimeout(timer);
  $("#when").textContent = `on the PC · ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  $("#when").classList.remove("off");
  if (!document.hidden) timer = setTimeout(refresh, ms);
}

async function refresh() {
  const m = location.hash.match(/^#p=([0-9a-f]+)$/);
  try {
    const playing = document.querySelector("#project video");
    if (m && playing && !playing.paused) return schedule(10000);  // (a video being watched isn't replaced)
    await (m ? showProject(m[1]) : showList());
  } catch (e) {
    $("#when").textContent = "Can't reach the PC";
    $("#when").classList.add("off");
    schedule(10000);
  }
}

$("#back").addEventListener("click", () => { if (getState) { getState = null; refresh(); } else location.hash = ""; });
window.addEventListener("hashchange", () => { getState = null; refresh(); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
refresh();
