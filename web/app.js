// TeX Tools web app: the settings, the files, and both panes. The work is
// done by textools (Python) in worker.js.

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

// Bumped when the defaults change, so that they apply to everybody once
const SETTINGS_KEY = "textools.settings.v1";
const THEME_KEY = "textools.theme";
const TEX_FILE = /\.(tex|ltx|sty|cls|dtx|ins|bbx|cbx|lbx|tikz|pgf)$/i;
const ZIP_NAME = "tex-tools.zip";

const DEFAULT_SETTINGS = {
  comments: { enabled: true, empty: true, lines: true, blank: true, space: true },
  keep: { verbatim: true, magic: true },
};

const state = {
  settings: loadSettings(),
  sources: [],  // {name, text, encoding, bom} of the files
  active: 0,    // the file shown in the panes
  response: null,
  runId: 0,
  zipId: 0,
  ready: false,
  error: null,
  linkScroll: true,
};

const panes = {
  original: { el: $("#original"), lines: [], link: null, expected: null },
  result: { el: $("#result"), lines: [], link: null, expected: null },
};

/* Helpers */

function loadJson(key, fallback) {
  try {
    const value = JSON.parse(localStorage.getItem(key));
    return value === null ? fallback : value;
  } catch {
    return fallback;
  }
}

function saveJson(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
}

// Settings from storage: keep only known keys with the right type
function merge(base, extra) {
  const result = structuredClone(base);
  if (!extra || typeof extra !== "object") return result;
  for (const [key, value] of Object.entries(extra)) {
    if (!(key in result)) continue;
    if (result[key] && typeof result[key] === "object") result[key] = merge(result[key], value);
    else if (typeof value === typeof result[key]) result[key] = value;
  }
  return result;
}

function loadSettings() {
  return merge(DEFAULT_SETTINGS, loadJson(SETTINGS_KEY, {}));
}

function saveSettings() {
  saveJson(SETTINGS_KEY, state.settings);
}

function getPath(obj, path) {
  return path.split(".").reduce((value, key) => value[key], obj);
}

function setPath(obj, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  keys.reduce((target, key) => target[key], obj)[last] = value;
}

function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}

// The lines of a text, like textools splits them
function splitLines(text) {
  const body = text.replace(/\r\n/g, "\n");
  return (body.endsWith("\n") ? body.slice(0, -1) : body).split("\n");
}

function plural(count, word, words = word + "s") {
  return `${count} ${count === 1 ? word : words}`;
}

let toastTimer;
function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

const currentFile = () => (state.response ? state.response.files[state.active] : null);

/* Python worker */

const worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module" });

worker.onmessage = ({ data }) => {
  if (data.type === "progress") {
    setEngine("loading", data.text);
    $("#overlay-text").textContent = data.text;
  } else if (data.type === "ready") {
    $("#about-version").textContent = `textools ${data.version} · Pyodide ${data.pyodide}`;
    state.ready = true;
    setEngine("ready", "Ready");
    schedule(0);
  } else if (data.type === "result") {
    if (data.id !== state.runId) return; // a newer run is on its way
    setBusy(false);
    state.error = null;
    state.response = JSON.parse(data.response);
    render();
  } else if (data.type === "error") {
    if (data.id !== state.runId) return;
    setBusy(false);
    showError(data.message);
  } else if (data.type === "zip") {
    saveZip(data);
  } else if (data.type === "fatal") {
    setEngine("error", "Python could not start");
    showError(`Python could not start: ${data.message}`);
  }
};

worker.onerror = (event) => {
  setEngine("error", "Python could not start");
  showError(`Python could not start: ${event.message || "unknown error"}`);
};

function setEngine(kind, text) {
  $("#engine-dot").className = `dot ${kind === "ready" ? "ready" : kind === "error" ? "error" : ""}`;
  $("#engine-state").textContent = text;
}

function setBusy(busy) {
  $("#pane-result").classList.toggle("busy", busy && state.ready);
  if (state.ready) setEngine("ready", busy ? "Working…" : "Ready");
}

function buildRequest() {
  const { comments, keep } = state.settings;
  const on = (value) => comments.enabled && value;
  return {
    sources: state.sources.map(({ name, text }) => ({ name, text })),
    options: {
      empty_comments: on(comments.empty),
      comment_lines: on(comments.lines),
      blank_lines: on(comments.blank),
      space_before: on(comments.space),
      keep_verbatim: keep.verbatim,
      keep_magic: keep.magic,
    },
  };
}

let runTimer;
function schedule(delay = 80) {
  clearTimeout(runTimer);
  runTimer = setTimeout(run, delay);
}

function run() {
  if (!state.sources.length) return;
  state.runId += 1;
  if (!state.ready) return;
  setBusy(true);
  worker.postMessage({ type: "run", id: state.runId, request: buildRequest() });
}

function showError(message) {
  state.error = message;
  const overlay = $("#overlay");
  overlay.classList.add("show", "error");
  $("#overlay-text").textContent = message;
  renderStatus();
}

/* Settings */

function applySettingsToUI() {
  for (const input of $$("[data-setting]")) {
    input.checked = Boolean(getPath(state.settings, input.dataset.setting));
  }
  for (const card of $$(".card")) {
    const toggle = $(".card-head [data-setting]", card);
    const on = toggle ? toggle.checked : true;
    card.classList.toggle("on", on && Boolean(toggle));
    card.classList.toggle("off", !on);
  }
}

// Changing a step of a card that is off turns the card on
function enableSectionOf(input) {
  const toggle = $(".card-head [data-setting]", input.closest(".card"));
  if (toggle && !toggle.checked) setPath(state.settings, toggle.dataset.setting, true);
}

$("#options").addEventListener("change", (event) => {
  const input = event.target;
  if (!input.dataset.setting) return;
  setPath(state.settings, input.dataset.setting, input.checked);
  if (input.closest(".card-body")) enableSectionOf(input);
  saveSettings();
  applySettingsToUI();
  schedule();
});

/* Files */

// Files are read as UTF-8, or else byte by byte as Latin-1, so that they are
// saved with the same bytes, except for the removed comments
function latin1(bytes) {
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return text;
}

async function readFile(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const bom = bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf;
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bom ? bytes.subarray(3) : bytes);
    return { name: file.name, text, encoding: "utf-8", bom };
  } catch {
    return { name: file.name, text: latin1(bytes), encoding: "latin-1", bom: false };
  }
}

function encode(source, text) {
  const body = source.encoding === "latin-1"
    ? Uint8Array.from(text, (c) => c.charCodeAt(0))
    : new TextEncoder().encode(text);
  if (!source.bom) return body;
  const data = new Uint8Array(body.length + 3);
  data.set([0xef, 0xbb, 0xbf]);
  data.set(body, 3);
  return data;
}

function setSources(sources, active = 0) {
  state.sources = sources;
  state.active = Math.min(Math.max(active, 0), Math.max(sources.length - 1, 0));
  state.response = null;
  state.error = null;
  resetScroll();
  render();
  schedule(0);
}

// Add files and show the first of them. A file with the name of an open file
// replaces it, e.g., after you changed it.
function addSources(added) {
  const sources = [...state.sources];
  let active = null;
  let replaced = 0;
  for (const source of added) {
    let idx = sources.findIndex((s) => s.name === source.name);
    if (idx >= 0) {
      sources[idx] = source;
      replaced += 1;
    } else {
      idx = sources.push(source) - 1;
    }
    if (active === null) active = idx;
  }
  setSources(sources, active);
  if (replaced) toast(`Updated ${plural(replaced, "open file")}`);
}

async function openFiles(files) {
  const tex = [...files].filter((file) => TEX_FILE.test(file.name));
  const skipped = files.length - tex.length;
  if (skipped) {
    toast(`Skipped ${plural(skipped, "file")} that ${skipped === 1 ? "is" : "are"} not a .tex file`);
  }
  if (tex.length) addSources(await Promise.all(tex.map(readFile)));
}

function closeFile(idx) {
  const active = idx < state.active || (idx === state.active && idx === state.sources.length - 1)
    ? state.active - 1 : state.active;
  setSources(state.sources.filter((_, i) => i !== idx), active);
}

function setActive(idx) {
  if (idx === state.active) return;
  state.active = idx;
  resetScroll();
  render();
}

$("#pick").addEventListener("change", async (event) => {
  await openFiles(event.target.files);
  event.target.value = "";
});

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-pick]")) $("#pick").click();
  if (event.target.closest("[data-paste]")) openPasteDialog();
});

$("#example").addEventListener("click", async () => {
  const response = await fetch("examples/example.tex");
  addSources([{ name: "example.tex", text: await response.text(), encoding: "utf-8", bom: false }]);
});

const FILE_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>`;

function renderFiles() {
  $("#files").replaceChildren(...state.sources.map((source, idx) => {
    const chip = document.createElement("span");
    chip.className = "chip" + (idx === state.active ? " active" : "");
    const open = document.createElement("button");
    open.className = "chip-open";
    open.title = `Show ${source.name}`;
    open.setAttribute("aria-pressed", String(idx === state.active));
    open.innerHTML = FILE_ICON;
    open.append(source.name);
    const file = state.response && state.response.files[idx];
    if (file) {
      const count = document.createElement("span");
      count.className = "count";
      count.textContent = `· ${file.comments}`;
      open.title += ` (${plural(file.comments, "comment")} removed)`;
      open.append(count);
    }
    open.addEventListener("click", () => setActive(idx));
    const close = document.createElement("button");
    close.className = "chip-close";
    close.textContent = "×";
    close.title = `Close ${source.name}`;
    close.addEventListener("click", () => closeFile(idx));
    chip.append(open, close);
    return chip;
  }));
}

/* Paste */

const pasteDialog = $("#paste-dialog");

function pastedName() {
  const names = new Set(state.sources.map((source) => source.name));
  let name = "pasted.tex";
  for (let i = 2; names.has(name); i++) name = `pasted-${i}.tex`;
  return name;
}

function addPasted(text) {
  if (!text.trim()) return false;
  const name = pastedName();
  addSources([{ name, text, encoding: "utf-8", bom: false }]);
  toast(`Added the pasted text as ${name}`);
  return true;
}

function openPasteDialog() {
  $("#paste-text").value = "";
  $("#paste-error").textContent = "";
  pasteDialog.showModal();
  $("#paste-text").focus();
}

function addFromDialog() {
  if (addPasted($("#paste-text").value)) pasteDialog.close();
  else $("#paste-error").textContent = "Paste some LaTeX first.";
}

$("#paste-add").addEventListener("click", addFromDialog);
$("#paste-text").addEventListener("input", () => { $("#paste-error").textContent = ""; });
$("#paste-text").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) addFromDialog();
});
pasteDialog.addEventListener("click", (event) => {
  if (event.target === pasteDialog || event.target.closest("[data-dialog-close]")) pasteDialog.close();
});

// Paste anywhere on the page, except into fields and dialogs
document.addEventListener("paste", async (event) => {
  const target = event.target instanceof Element ? event.target : document.body;
  if (target.closest("input, textarea, [contenteditable]") || document.querySelector("dialog[open]")) return;
  const data = event.clipboardData;
  if (data.files.length) {
    event.preventDefault();
    await openFiles(data.files);
  } else if (addPasted(data.getData("text/plain"))) {
    event.preventDefault();
  }
});

if (/Mac|iPhone|iPad/.test(navigator.platform)) {
  $$(".paste-key").forEach((key) => { key.textContent = "⌘V"; });
}

/* Menu to add another file */

const addButton = $("#add-button");
const addMenu = $("#add-menu");

function setAddMenu(open) {
  addMenu.hidden = !open;
  addButton.setAttribute("aria-expanded", String(open));
  if (open) $("button", addMenu).focus();
}

addButton.addEventListener("click", () => setAddMenu(addMenu.hidden));
document.addEventListener("click", (event) => {
  if (!addMenu.hidden && !event.target.closest("#add-wrap")) setAddMenu(false);
});
addMenu.addEventListener("click", (event) => {
  if (event.target.closest("[data-pick], [data-paste]")) setAddMenu(false);
});
addMenu.addEventListener("keydown", (event) => {
  const items = $$("[role=menuitem]", addMenu);
  const idx = items.indexOf(document.activeElement);
  if (event.key === "Escape") {
    setAddMenu(false);
    addButton.focus();
  } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    items[(idx + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus();
  }
});

/* Drag and drop */

let dragDepth = 0;
window.addEventListener("dragenter", (event) => {
  if (![...event.dataTransfer.types].includes("Files")) return;
  dragDepth += 1;
  document.body.classList.add("dragging");
});
window.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) document.body.classList.remove("dragging");
});
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", async (event) => {
  event.preventDefault();
  dragDepth = 0;
  document.body.classList.remove("dragging");
  if (event.dataTransfer.files.length) await openFiles(event.dataTransfer.files);
});

/* Rendering */

function lineHtml(number, html, cls = "", badges = "", title = "") {
  if (badges) cls = cls ? `${cls} has-badges` : "has-badges";
  return `<div class="l${cls ? " " + cls : ""}"${title ? ` title="${escapeHtml(title)}"` : ""}>` +
    `<span class="n">${number}</span><span class="t">${html || " "}</span>` +
    (badges ? `<span class="badges">${badges}</span>` : "") + "</div>";
}

const KEPT_LABELS = {
  verbatim: (name) => `kept · ${name}`,
  inline: (name) => `kept · ${name}`,
  url: (name) => `kept · ${name}`,
  magic: () => "kept · magic comment",
};
const REASONS = {
  "comment": "The comment is removed",
  "comment line": "A line with only a comment",
  "blank line": "An extra blank line",
};

// The lines of the kept text with a %, and the badges on their first lines
function keptLines(file) {
  const lines = new Set();
  const badges = new Map();
  for (const { kind, lines: [first, last], name } of (file ? file.kept : [])) {
    for (let i = first; i <= last; i++) lines.add(i);
    const badge = `<span class="badge kept" title="The % in it is not a comment">${escapeHtml(KEPT_LABELS[kind](name))}</span>`;
    badges.set(first, (badges.get(first) || "") + badge);
  }
  return { lines, badges };
}

function renderOriginal(lines, file) {
  const kept = keptLines(file);
  const parts = lines.map((text, i) => {
    const [out, keep, reason] = (file && file.lines[i]) || [i, text.length, ""];
    let cls = kept.lines.has(i) ? "kept" : "";
    let html;
    if (out === null) {
      cls = "rm";
      html = escapeHtml(text);
    } else if (keep < text.length) {
      cls = "chg";
      html = escapeHtml(text.slice(0, keep)) + `<span class="cut">${escapeHtml(text.slice(keep))}</span>`;
    } else {
      html = escapeHtml(text);
    }
    return lineHtml(i + 1, html, cls, kept.badges.get(i), REASONS[reason]);
  });
  panes.original.el.innerHTML = parts.join("");
  panes.original.lines = [...panes.original.el.children];
}

function renderResult(original, file) {
  const pane = panes.result;
  if (!file) {
    pane.el.innerHTML = "";
    pane.lines = [];
    return;
  }
  const lines = file.count ? splitLines(file.text) : [];
  const changed = new Set();
  file.lines.forEach(([out, keep], i) => {
    if (out !== null && keep < original[i].length) changed.add(out);
  });
  pane.el.innerHTML = lines.map((text, j) =>
    lineHtml(j + 1, escapeHtml(text), changed.has(j) ? "chg" : "")).join("");
  pane.lines = [...pane.el.children];
  if (!lines.length) {
    pane.el.innerHTML = `<div class="l"><span class="n"></span>` +
      `<span class="t empty-note">Nothing is left of this file with these settings.</span></div>`;
  }
}

function render() {
  const source = state.sources[state.active];
  const file = currentFile();
  document.body.classList.toggle("has-files", state.sources.length > 0);
  const lines = source ? splitLines(source.text) : [];
  if (source) {
    renderOriginal(lines, file);
    renderResult(lines, file);
  } else {
    for (const pane of Object.values(panes)) {
      pane.el.innerHTML = "";
      pane.lines = [];
    }
  }
  panes.original.link = panes.result.link = null;
  // The original stays where it is, and the result follows it
  if (state.linkScroll) align(panes.original, panes.result);

  const overlay = $("#overlay");
  const waiting = source && (!state.ready || !file);
  overlay.classList.toggle("show", Boolean(waiting || state.error));
  if (!state.error) overlay.classList.remove("error");
  if (waiting && state.ready) $("#overlay-text").textContent = "Working…";

  $("#copy").disabled = !file;
  $("#download").disabled = !file;
  $("#download-all").hidden = state.sources.length < 2;
  $("#download-all").disabled = !state.response;
  renderFiles();
  renderMeta(source, lines, file);
  renderStatus();
}

function renderMeta(source, lines, file) {
  $("#original-meta").textContent = source ? [
    source.name,
    plural(lines.length, "line"),
    source.encoding === "latin-1" ? "Latin-1" : null,
  ].filter(Boolean).join(" · ") : "";
  if (!file) {
    $("#result-meta").textContent = "";
    return;
  }
  const removed = lines.length - file.count;
  const changed = file.lines.filter(([out, keep], i) => out !== null && keep < lines[i].length).length;
  const smaller = source.text.length ? Math.round(100 * (1 - file.text.length / source.text.length)) : 0;
  $("#result-meta").textContent = [
    plural(file.count, "line"),
    removed ? `${removed} removed` : null,
    changed ? `${changed} changed` : null,
    smaller ? `${smaller}% smaller` : null,
  ].filter(Boolean).join(" · ");
}

const ICON_WARN = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2 21h20z"/><path d="M12 10v5"/><path d="M12 18h.01"/></svg>`;
const ICON_INFO = `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><path d="M12 7h.01"/></svg>`;

function renderStatus() {
  const r = state.response;
  const counts = $("#status-counts");
  const messages = $("#status-messages");
  messages.replaceChildren();
  if (!state.sources.length) {
    counts.textContent = "No file opened";
  } else if (!r) {
    counts.textContent = "";
  } else {
    const comments = r.files.reduce((sum, file) => sum + file.comments, 0);
    const linesIn = r.files.reduce((sum, file) => sum + file.lines.length, 0);
    const linesOut = r.files.reduce((sum, file) => sum + file.count, 0);
    counts.innerHTML = `<strong>${comments}</strong> ${comments === 1 ? "comment" : "comments"} removed · ` +
      `<strong>${linesIn}</strong> → <strong>${linesOut}</strong> lines` +
      (r.files.length > 1 ? ` in ${r.files.length} files` : "");
    for (const [level, text] of r.files[state.active].messages) {
      const msg = document.createElement("span");
      msg.className = `msg ${level}`;
      msg.title = text;
      msg.innerHTML = level === "warning" ? ICON_WARN : ICON_INFO;
      msg.append(text);
      messages.append(msg);
    }
  }
  if (state.error) {
    const msg = document.createElement("span");
    msg.className = "msg error";
    msg.innerHTML = ICON_WARN;
    msg.append(state.error);
    messages.prepend(msg);
  }
}

/* Linked scrolling */

const other = (name) => (name === "original" ? "result" : "original");

function resetScroll() {
  for (const pane of Object.values(panes)) setScroll(pane, 0);
}

// Scroll a pane from the code. Its scroll event is recognized by the
// position, so that it is not taken for the user's.
function setScroll(pane, top) {
  if (Math.abs(pane.el.scrollTop - top) >= 1) pane.el.scrollTop = top;
  pane.expected = pane.el.scrollTop;
}

// Map from the content of a pane to the content of the other pane, as points
// with straight lines between them. The middle of each line is mapped to the
// middle of its line on the other side, which spreads removed lines over the
// lines around them, so that the other pane moves without leaps.
function linkMap(from, to) {
  if (from.link) return from.link;
  const file = currentFile();
  const original = panes.original.lines;
  const result = panes.result.lines;
  const points = [[0, 0]];
  if (file && original.length && result.length) {
    const forward = from === panes.original;
    file.lines.forEach(([out], i) => {
      const a = original[i];
      const b = out === null ? null : result[out];
      if (!a || !b) return;
      const middles = [a.offsetTop + a.offsetHeight / 2, b.offsetTop + b.offsetHeight / 2];
      points.push(forward ? middles : middles.reverse());
    });
  }
  points.push([from.el.scrollHeight, to.el.scrollHeight]);
  from.link = points;
  return points;
}

function mapLink(points, position) {
  if (position <= points[0][0]) return points[0][1];
  // The last point at or before the position
  let lo = 0;
  let hi = points.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (points[mid][0] <= position) lo = mid;
    else hi = mid - 1;
  }
  const [x, y] = points[lo];
  const next = points[lo + 1];
  return next && next[0] > x ? y + (position - x) / (next[0] - x) * (next[1] - y) : y;
}

// The line at which the panes are linked, as a fraction of the height of a
// pane: the top at its start, the middle, and the bottom at its end, so that
// both panes start and end together
function linkLine(pane) {
  const max = pane.el.scrollHeight - pane.el.clientHeight;
  if (max <= 0) return 0;
  const zone = Math.min(pane.el.clientHeight, max) / 2;
  const position = Math.min(Math.max(pane.el.scrollTop, 0), max);
  return (Math.min(position, zone) + zone - Math.min(max - position, zone)) / (2 * zone);
}

// Scroll the other pane to what a pane shows
function align(from, to) {
  if (!from.lines.length || !to.lines.length) return;
  const line = linkLine(from);
  const at = mapLink(linkMap(from, to), from.el.scrollTop + line * from.el.clientHeight);
  const max = to.el.scrollHeight - to.el.clientHeight;
  setScroll(to, Math.min(Math.max(0, at - line * to.el.clientHeight), max));
}

for (const name of ["original", "result"]) {
  const pane = panes[name];
  pane.el.addEventListener("scroll", () => {
    if (pane.expected !== null && Math.abs(pane.el.scrollTop - pane.expected) < 1) return;
    pane.expected = null;
    if (state.linkScroll) align(pane, panes[other(name)]);
  }, { passive: true });
}

// Lines wrap, so their positions change with the width
const resizeObserver = new ResizeObserver(() => {
  for (const pane of Object.values(panes)) pane.link = null;
});
for (const pane of Object.values(panes)) resizeObserver.observe(pane.el);

function renderLinkScroll() {
  const button = $("#link-scroll");
  button.setAttribute("aria-pressed", String(state.linkScroll));
  button.parentElement.classList.toggle("linked", state.linkScroll);
  button.title = state.linkScroll
    ? "Scrolling is linked: both sides show the same lines. Click to scroll them separately."
    : "Scrolling is not linked. Click to scroll both sides together.";
}

$("#link-scroll").addEventListener("click", () => {
  state.linkScroll = !state.linkScroll;
  renderLinkScroll();
  if (state.linkScroll) align(panes.original, panes.result);
});
renderLinkScroll();

/* About */

const aboutDialog = $("#about-dialog");
$("#about-open").addEventListener("click", () => aboutDialog.showModal());
aboutDialog.addEventListener("click", (event) => {
  if (event.target === aboutDialog || event.target.closest("[data-dialog-close]")) aboutDialog.close();
});

/* Actions */

function saveBlob(blob, name) {
  const link = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(blob), download: name,
  });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

// The file keeps its name, so that \input and \include still find it
function download() {
  const source = state.sources[state.active];
  const file = currentFile();
  if (!source || !file) return;
  saveBlob(new Blob([encode(source, file.text)], { type: "text/x-tex" }), source.name);
}

function downloadAll() {
  if (!state.response) return;
  state.zipId += 1;
  worker.postMessage({
    type: "zip",
    id: state.zipId,
    files: state.sources.map((source, idx) => ({
      name: source.name, text: state.response.files[idx].text,
      encoding: source.encoding, bom: source.bom,
    })),
  });
}

function saveZip(data) {
  if (data.id !== state.zipId) return;
  if (data.error) toast(`Could not zip the files: ${data.error}`);
  else saveBlob(new Blob([data.data], { type: "application/zip" }), ZIP_NAME);
}

$("#download").addEventListener("click", download);
$("#download-all").addEventListener("click", downloadAll);
$("#copy").addEventListener("click", async () => {
  const file = currentFile();
  if (!file) return;
  try {
    await navigator.clipboard.writeText(file.text);
    toast("Copied the result to the clipboard");
  } catch {
    toast("Could not copy to the clipboard");
  }
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "s") {
    event.preventDefault();
    download();
  }
});

function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
}

$("#theme").addEventListener("click", () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  const theme = dark ? "light" : "dark";
  applyTheme(theme);
  try { localStorage.setItem(THEME_KEY, theme); } catch { /* private mode */ }
});

/* Start */

try { applyTheme(localStorage.getItem(THEME_KEY)); } catch { /* private mode */ }
applySettingsToUI();
render();
