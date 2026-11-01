// Recap Studio's phone page (phone.py): every project's progress, why one stopped, and the few things worth doing
// from a phone: pause, resume, cancel, run again or continue, and two of a project's settings.

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

async function showList() {
  shown = null;
  $("#back").hidden = true;
  $("#title").textContent = "Recap Studio";
  $("#project").hidden = true;
  $("#list").hidden = false;
  const projects = await api("/api/m/projects");
  $("#list").replaceChildren(...(projects.length ? projects.map((p) => el("div", {
    class: "card link", onclick: () => { location.hash = `p=${p.id}`; } },
  el("h2", {}, p.name), stateLine(p), bar(p.job),
  el("div", { class: "muted" }, `${p.chapters} chapter${p.chapters === 1 ? "" : "s"} · changed ${ago(p.updated)}`)))
    : [el("div", { class: "empty" }, "No projects yet. Make one in Recap Studio on your PC.")]));
  schedule(projects.some((p) => active(p.job)) ? 4000 : 10000);
}

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
  const c = el("div", { class: "card" }, stateLine(p), bar(job));
  if (job && active(job) && job.label) {
    const n = (job.steps || []).indexOf(job.step);
    c.append(el("div", { class: "muted" }, `${job.label}${n >= 0 && job.steps.length > 1 ? ` · step ${n + 1} of ${job.steps.length}` : ""}`));
  }
  if (job && job.status === "paused" && job.waiting_for)
    c.append(el("div", { class: "note" }, "Check it on the PC, or Resume to carry on without."));
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
  if (!p.chapter_list.length) return null;
  return el("div", { class: "card" }, el("h2", {}, "Chapters"),
    el("div", { class: "chapters" }, ...p.chapter_list.map((c) =>
      el("div", { class: "chapter" }, el("span", {}, c.name), el("span", { class: `pill ${c.kind}` }, c.text)))));
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
  $("#title").textContent = p.name;
  $("#list").hidden = true;
  $("#project").hidden = false;
  if (!busy) $("#project").replaceChildren(...[jobCard(p), videoCard(p), chaptersCard(p), settingsCard(p)].filter(Boolean));
  schedule(active(p.job) ? 3000 : 10000);
}

// ---- refreshing ----

function schedule(ms) {
  clearTimeout(timer);
  $("#when").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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
    schedule(10000);
  }
}

$("#back").addEventListener("click", () => { location.hash = ""; });
window.addEventListener("hashchange", refresh);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
refresh();
