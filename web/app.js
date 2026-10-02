// TeX Tools web app: the settings, the files, and both panes. The work is
// done by textools (Python) in worker.js.

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

// Bumped when the defaults change, so that they apply to everybody once
const SETTINGS_KEY = "textools.settings.v1";
const THEME_KEY = "textools.theme";
const FOLD_KEY = "textools.fold";
// The files that are cleaned, like _TEX_FILE in textools/web.py
const TEX_FILE = /\.(tex|ltx|sty|cls|dtx|ins|bbx|cbx|lbx|tikz|pgf)$/i;
// Archives of projects, e.g., .zip from Overleaf, or .tar.gz from arXiv
const ARCHIVE_FILE = /\.(zip|tar|tgz|gz)$/i;
const ARCHIVE_TYPES = { zip: "application/zip", tar: "application/x-tar", "tar.gz": "application/gzip" };

const DEFAULT_SETTINGS = {
  comments: { enabled: true, empty: true, lines: true, blank: true, space: true },
  keep: { verbatim: true, magic: true },
};

const state = {
  settings: loadSettings(),
  // {name, text, encoding, bom, archive, excluded, unfolded} of the files. A
  // file from a zip file has its path as name and the id of the zip file as
  // archive. An excluded file stays as it is. Unfolded are the lines that you
  // unfolded.
  sources: [],
  archives: [], // {id, name, data, others, shown} of the opened zip files
  archiveId: 0,
  active: 0,    // the file shown in the panes
  response: null,
  runId: 0,
  packId: 0,
  packName: null,
  ready: false,
  error: null,
  linkScroll: true,
  fold: loadJson(FOLD_KEY, false) === true,
  folds: { original: [], result: [] }, // the folded lines of the shown file
};

// The element of each line of a pane is in lines, which is the fold for a
// folded line. Shown is the source or the result in the pane.
const panes = {
  original: { el: $("#original"), lines: [], link: null, expected: null, shown: null },
  result: { el: $("#result"), lines: [], link: null, expected: null, shown: null },
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
  } else if (data.type === "unpack") {
    const pending = unpacking.get(data.id);
    unpacking.delete(data.id);
    if (pending) addArchive(pending, data.error ? { error: data.error } : JSON.parse(data.response));
  } else if (data.type === "pack") {
    savePacked(data);
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
    sources: state.sources.map(({ name, text, excluded }) => ({ name, text, exclude: Boolean(excluded) })),
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
  state.archives = state.archives.filter((archive) => sources.some((s) => s.archive === archive.id));
  state.active = Math.min(Math.max(active, 0), Math.max(sources.length - 1, 0));
  state.response = null;
  state.error = null;
  resetScroll();
  render();
  schedule(0);
}

// The name of a file without ./ at the start, which is common in the .tar.gz
// files from arXiv. The name stays as it is in the archive.
const shownName = (name) => name.replace(/^(\.\/)+/, "");

// Add files and show one of them, the first by default. A file with the
// name of an open file replaces it, e.g., after you changed it, and keeps its
// place in a zip file.
function addSources(added, show = 0) {
  const sources = [...state.sources];
  let active = null;
  let replaced = 0;
  added.forEach((source, i) => {
    let idx = sources.findIndex((s) => shownName(s.name) === shownName(source.name));
    if (idx >= 0) {
      const old = sources[idx];
      // A file of an archive keeps its path, and a single file takes the
      // path of the file in the archive that it replaces
      sources[idx] = source.archive != null ? { ...source, excluded: old.excluded }
        : { ...source, name: old.name, archive: old.archive, excluded: old.excluded };
      replaced += 1;
    } else {
      idx = sources.push(source) - 1;
    }
    if (i === show) active = idx;
  });
  setSources(sources, active);
  if (replaced) toast(`Updated ${plural(replaced, "open file")}`);
}

// Whether the start of a file is a zip, gzip, or tar file, e.g., the source
// of arXiv without an extension, like 2401.12345v1
function isArchive(bytes) {
  const text = String.fromCharCode(...bytes.subarray(0, 4));
  return text === "PK\x03\x04" || text === "PK\x05\x06" || (bytes[0] === 0x1f && bytes[1] === 0x8b) ||
    String.fromCharCode(...bytes.subarray(257, 262)) === "ustar";
}

async function openFiles(files) {
  const tex = [];
  const archives = [];
  let skipped = 0;
  for (const file of files) {
    if (TEX_FILE.test(file.name)) tex.push(file);
    else if (ARCHIVE_FILE.test(file.name) || isArchive(new Uint8Array(await file.slice(0, 512).arrayBuffer()))) {
      archives.push(file);
    } else skipped += 1;
  }
  if (skipped) {
    toast(`Skipped ${plural(skipped, "file")} that ${skipped === 1 ? "is" : "are"} not .tex, .zip, or .tar.gz`);
  }
  if (tex.length) addSources(await Promise.all(tex.map(readFile)));
  for (const file of archives) openArchive(file.name, new Uint8Array(await file.arrayBuffer()));
}

// Archives are read by Python in the worker, which answers with their .tex
// files. The archives are kept to write them again with the cleaned files.
const unpacking = new Map();

function openArchive(name, data) {
  state.archiveId += 1;
  unpacking.set(state.archiveId, { id: state.archiveId, name, data });
  worker.postMessage({ type: "unpack", id: state.archiveId, data });
  toast(`Opening ${name}…`);
}

function addArchive({ id, name, data }, response) {
  if (response.error) {
    toast(`Could not open ${name}: ${response.error}`);
    return;
  }
  // A single gzipped file, e.g., the source of a paper with one file on
  // arXiv, is opened like a .tex file
  if (response.kind === "file") {
    const [file] = response.files;
    let fileName = (file.name || name.replace(/\.gz$/i, "")).split("/").pop();
    if (!TEX_FILE.test(fileName)) fileName += ".tex";
    addSources([{ ...file, name: fileName }]);
    toast(`Opened ${fileName} from ${name}`);
    return;
  }
  if (!response.files.length) {
    toast(`There are no .tex files in ${name}`);
    return;
  }
  // An archive with the name of an open one replaces it
  // and keeps its excluded files
  const old = state.archives.find((archive) => archive.name === name);
  const excluded = new Set(state.sources.filter((s) => old && s.archive === old.id && s.excluded)
    .map((s) => s.name));
  if (old) state.sources = state.sources.filter((s) => s.archive !== old.id);
  state.archives = [...state.archives.filter((archive) => archive !== old),
    { id, name, data, kind: response.kind, compression: response.compression, others: response.others, shown: null }];
  const files = response.files.map((file) => ({ ...file, archive: id, excluded: excluded.has(file.name) }));
  // Show the main file first
  addSources(files, Math.max(0, files.findIndex((file) => /\\documentclass/.test(file.text))));
  toast(`Opened ${plural(files.length, ".tex file")} of ${name}.` +
    (response.others ? ` ${othersStay(response.others)}` : ""));
}

function othersStay(count, name = "") {
  const of = name ? ` of ${name}` : "";
  return count === 1 ? `The other file${of} stays as it is.` : `The ${count} other files${of} stay as they are.`;
}

// Close the files for which `keep` is false, and show the same file as
// before, or else the next one
function closeSources(keep) {
  const shown = state.sources[state.active];
  const sources = state.sources.filter(keep);
  const active = sources.includes(shown) ? sources.indexOf(shown)
    : state.sources.slice(0, state.active).filter(keep).length;
  setSources(sources, active);
}

const archiveOf = (source) => state.archives.find((archive) => archive.id === source.archive);

function setActive(idx) {
  if (idx === state.active || !state.sources[idx]) return;
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

const ZIP_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 8v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/></svg>`;

function chip({ icon, name, comments, title, active, onOpen, onClose }) {
  const el = document.createElement("span");
  el.className = "chip" + (active ? " active" : "");
  const open = document.createElement("button");
  open.className = "chip-open";
  open.title = title;
  open.setAttribute("aria-pressed", String(active));
  open.innerHTML = icon;
  open.append(name);
  if (comments !== null) {
    const count = document.createElement("span");
    count.className = "count";
    count.textContent = `· ${comments}`;
    open.title += ` (${plural(comments, "comment")} removed)`;
    open.append(count);
  }
  open.addEventListener("click", onOpen);
  const close = document.createElement("button");
  close.className = "chip-close";
  close.textContent = "×";
  close.title = `Close ${name}`;
  close.addEventListener("click", onClose);
  el.append(open, close);
  return el;
}

// A chip for each file, and one for each zip file with all its files
function renderFiles() {
  const files = state.response ? state.response.files : null;
  const shown = state.sources[state.active];
  const chips = [];
  state.sources.forEach((source, idx) => {
    if (source.archive == null) {
      chips.push(chip({
        icon: FILE_ICON, name: source.name, comments: files ? files[idx].comments : null,
        title: `Show ${source.name}`, active: idx === state.active,
        onOpen: () => setActive(idx), onClose: () => closeSources((s) => s !== source),
      }));
      return;
    }
    const archive = archiveOf(source);
    if (state.sources.findIndex((s) => s.archive === archive.id) !== idx) return;
    const members = state.sources.map((s, i) => [s, i]).filter(([s]) => s.archive === archive.id);
    chips.push(chip({
      icon: ZIP_ICON, name: archive.name,
      comments: files ? members.reduce((sum, [, i]) => sum + files[i].comments, 0) : null,
      title: `Show the files of ${archive.name}: ${plural(members.length, ".tex file")}` +
        (archive.others ? ` and ${plural(archive.others, "other file")}` : ""),
      active: shown.archive === archive.id,
      onOpen: () => {
        const last = members.find(([s]) => s.name === archive.shown);
        setActive((last || members[0])[1]);
      },
      onClose: () => closeSources((s) => s.archive !== archive.id),
    }));
  });
  $("#files").replaceChildren(...chips);
}

// Choose the shown file, e.g., one of the files of a zip file
function renderFileSelect() {
  const select = $("#file-select");
  select.hidden = state.sources.length < 2;
  if (select.hidden) return;
  const files = state.response ? state.response.files : null;
  const groups = new Map();
  select.replaceChildren();
  state.sources.forEach((source, idx) => {
    let parent = select;
    if (source.archive != null) {
      if (!groups.has(source.archive)) {
        const group = Object.assign(document.createElement("optgroup"), { label: archiveOf(source).name });
        groups.set(source.archive, group);
        select.append(group);
      }
      parent = groups.get(source.archive);
    }
    const comments = source.excluded ? " · excluded"
      : files ? ` · ${plural(files[idx].comments, "comment")}` : "";
    parent.append(new Option(shownName(source.name) + comments, String(idx)));
  });
  select.value = String(state.active);
}

$("#file-select").addEventListener("change", (event) => setActive(Number(event.target.value)));

// Exclude the shown file: it is downloaded as it is, with its comments
$("#exclude").addEventListener("change", (event) => {
  const source = state.sources[state.active];
  if (!source) return;
  source.excluded = event.target.checked;
  render();
  schedule(0);
});

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

function renderOriginal(lines, file, folds) {
  const kept = keptLines(file);
  const pane = panes.original;
  pane.el.innerHTML = withFolds(lines.length, folds, (i) => {
    const text = lines[i];
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
  pane.lines = lineElements(pane.el, lines.length, folds);
}

function renderResult(original, file, folds) {
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
  pane.el.innerHTML = withFolds(lines.length, folds, (j) =>
    lineHtml(j + 1, escapeHtml(lines[j]), changed.has(j) ? "chg" : ""));
  pane.lines = lineElements(pane.el, lines.length, folds);
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
  // A pane that shows the same as before stays at the same line, e.g., when
  // lines are folded
  const anchors = [[panes.original, source], [panes.result, file]]
    .filter(([pane, shown]) => shown && pane.shown === shown)
    .map(([pane]) => [pane, topLine(pane)]);
  state.folds = foldsOf(source, lines, file);
  if (source) {
    renderOriginal(lines, file, state.folds.original);
    renderResult(lines, file, state.folds.result);
  } else {
    for (const pane of Object.values(panes)) {
      pane.el.innerHTML = "";
      pane.lines = [];
    }
  }
  panes.original.shown = source;
  panes.result.shown = file;
  for (const [pane, anchor] of anchors) scrollToLine(pane, anchor);
  panes.original.link = panes.result.link = null;
  // The original stays where it is, and the result follows it
  if (state.linkScroll) align(panes.original, panes.result);

  const overlay = $("#overlay");
  const waiting = source && (!state.ready || !file);
  overlay.classList.toggle("show", Boolean(waiting || state.error));
  if (!state.error) overlay.classList.remove("error");
  if (waiting && state.ready) $("#overlay-text").textContent = "Working…";

  // With a zip file, the main download is the zip file
  const zip = $("#download-all");
  const archives = state.archives.length > 0;
  $("#copy").disabled = !file;
  $("#download").disabled = !file;
  $("#download").classList.toggle("primary", !archives);
  zip.hidden = state.sources.length < 2 && !archives;
  zip.disabled = !state.response;
  zip.classList.toggle("primary", archives);
  $("#download-all-label").textContent = archives ? `Download .${packFormat()}` : "All as .zip";
  zip.title = archives
    ? `Download ${packName()} with the comments removed from its .tex files, and the other files as they are (Ctrl+S)`
    : "Download the results of all files in a .zip file";
  if (source && source.archive != null) archiveOf(source).shown = source.name;
  // Files can be excluded, when there are several, e.g., in a zip file
  $("#exclude-label").hidden = !source || (state.sources.length < 2 && source.archive == null);
  $("#exclude").checked = Boolean(source && source.excluded);
  renderFiles();
  renderFileSelect();
  renderMeta(source, lines, file);
  renderStatus();
}

function renderMeta(source, lines, file) {
  // The name is in the list of files, if there are several
  $("#original-meta").textContent = source ? [
    state.sources.length < 2 ? shownName(source.name) : null,
    plural(lines.length, "line"),
    source.encoding === "latin-1" ? "Latin-1" : null,
  ].filter(Boolean).join(" · ") : "";
  if (!file) {
    $("#result-meta").textContent = "";
    return;
  }
  if (source.excluded) {
    $("#result-meta").textContent = `Excluded: stays as it is · ${plural(file.count, "line")}`;
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
    const excluded = state.sources.filter((source) => source.excluded).length;
    if (excluded) counts.innerHTML += ` · ${plural(excluded, "file")} excluded`;
    const archive = archiveOf(state.sources[state.active]);
    const notes = archive && archive.others
      ? [["info", othersStay(archive.others, archive.name)]] : [];
    for (const [level, text] of [...r.files[state.active].messages, ...notes]) {
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

/* Folding */

// Runs of unchanged lines are folded, except for a few lines around the
// changes, so that you see what changed
const FOLD_CONTEXT = 2; // unchanged lines shown before and after a change
const FOLD_MIN = 3;     // fewer lines are not folded, since a fold is a line too

const UNFOLD_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 22v-6"/><path d="M12 8V2"/><path d="M4 12H2"/><path d="M10 12H8"/><path d="M16 12h-2"/><path d="M22 12h-2"/><path d="m15 19-3 3-3-3"/><path d="m15 5-3-3-3 3"/></svg>`;

// The folded lines of a file as [start, end) in the original and the same
// lines in the result. The changed and removed lines are shown, and the
// kept text with a %, with the lines around them, and the lines that you
// unfolded.
function foldsOf(source, lines, file) {
  const folds = { original: [], result: [] };
  if (!state.fold || !source || !file) return folds;
  const shown = new Uint8Array(lines.length);
  const show = (i) => shown.fill(1, Math.max(0, i - FOLD_CONTEXT), i + FOLD_CONTEXT + 1);
  file.lines.forEach(([out, keep], i) => {
    if (out === null || keep < lines[i].length) show(i);
  });
  for (const { lines: [first, last] } of file.kept) {
    show(first);
    for (let i = first + 1; i <= last; i++) if (lines[i].includes("%")) show(i);
  }
  for (const i of source.unfolded || []) shown[i] = 1;
  for (let start = 0; start < lines.length; start++) {
    if (shown[start]) continue;
    let end = start;
    while (end < lines.length && !shown[end]) end++;
    // The lines are unchanged, so they are in the result too
    if (end - start >= FOLD_MIN) {
      folds.original.push([start, end]);
      folds.result.push([file.lines[start][0], file.lines[end - 1][0] + 1]);
    }
    start = end;
  }
  return folds;
}

// The HTML of the lines of a pane, with a fold in place of the folded lines
function withFolds(count, folds, line) {
  const parts = [];
  let f = 0;
  for (let i = 0; i < count; i++) {
    const fold = folds[f];
    if (fold && fold[0] === i) {
      const text = plural(fold[1] - fold[0], "unchanged line");
      parts.push(`<div class="l fold" role="button" tabindex="0" data-fold="${f}" title="Show the ${text}">` +
        `<span class="n">${UNFOLD_ICON}</span><span class="t">${text}</span></div>`);
      i = fold[1] - 1;
      f += 1;
    } else parts.push(line(i));
  }
  return parts.join("");
}

// The element of each line of a pane, which is the fold for a folded line
function lineElements(el, count, folds) {
  const lines = new Array(count);
  let child = el.firstElementChild;
  let f = 0;
  for (let i = 0; i < count; child = child.nextElementSibling) {
    const fold = folds[f];
    if (fold && fold[0] === i) {
      lines.fill(child, i, fold[1]);
      i = fold[1];
      f += 1;
    } else lines[i++] = child;
  }
  return lines;
}

function unfold(idx) {
  const source = state.sources[state.active];
  const fold = state.folds.original[idx];
  if (!source || !fold) return;
  source.unfolded = source.unfolded || new Set();
  for (let i = fold[0]; i < fold[1]; i++) source.unfolded.add(i);
  render();
}

for (const pane of Object.values(panes)) {
  pane.el.addEventListener("click", (event) => {
    const fold = event.target.closest(".fold");
    if (fold) unfold(Number(fold.dataset.fold));
  });
  pane.el.addEventListener("keydown", (event) => {
    const fold = event.target.closest(".fold");
    if (!fold || (event.key !== "Enter" && event.key !== " ")) return;
    event.preventDefault();
    unfold(Number(fold.dataset.fold));
    pane.el.focus({ preventScroll: true });
  });
}

function renderFold() {
  const button = $("#fold");
  button.setAttribute("aria-pressed", String(state.fold));
  button.title = state.fold
    ? "Unchanged lines are folded. Click to show all lines."
    : "Fold the unchanged lines, to see only what changed.";
}

// Folding again folds the lines that you unfolded too
$("#fold").addEventListener("click", () => {
  state.fold = !state.fold;
  saveJson(FOLD_KEY, state.fold);
  for (const source of state.sources) delete source.unfolded;
  renderFold();
  render();
});
renderFold();

/* Linked scrolling */

const other = (name) => (name === "original" ? "result" : "original");

function resetScroll() {
  for (const pane of Object.values(panes)) setScroll(pane, 0);
}

// The first line at the top of a pane, and how far it is scrolled past it
function topLine(pane) {
  const { lines } = pane;
  if (!lines.length) return null;
  const top = pane.el.scrollTop;
  let lo = 0;
  let hi = lines.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].offsetTop + lines[mid].offsetHeight <= top) lo = mid + 1;
    else hi = mid;
  }
  return { line: lo, offset: top - lines[lo].offsetTop };
}

// Scroll a pane to the same line as before, or its fold
function scrollToLine(pane, anchor) {
  const el = anchor && pane.lines[Math.min(anchor.line, pane.lines.length - 1)];
  if (!el) return;
  setScroll(pane, el.offsetTop + Math.min(anchor.offset, el.offsetHeight - 1));
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

// The text to download. An excluded file stays exactly as it is, also its
// line endings.
const outputText = (idx) =>
  (state.sources[idx].excluded ? state.sources[idx].text : state.response.files[idx].text);

// The file keeps its name, so that \input and \include still find it
function download() {
  const source = state.sources[state.active];
  if (!source || !currentFile()) return;
  saveBlob(new Blob([encode(source, outputText(state.active))], { type: "text/x-tex" }),
    source.name.split("/").pop());
}

// One archive is downloaded in its format, e.g., a .tar.gz from arXiv, and
// several archives or files together in a .zip
function packFormat() {
  if (state.archives.length !== 1) return "zip";
  const [archive] = state.archives;
  if (archive.kind !== "tar") return "zip";
  return archive.compression === "gz" ? "tar.gz" : "tar";
}

function packName() {
  if (state.archives.length !== 1) return "tex-tools.zip";
  const format = packFormat();
  const { name } = state.archives[0];
  const extension = { zip: /\.zip$/i, tar: /\.tar$/i, "tar.gz": /\.(tar\.gz|tgz)$/i }[format];
  return `clean-${name}${extension.test(name) ? "" : "." + format}`;
}

// The files of the opened archives with the cleaned files in place of the
// original ones, and the other open files
function downloadAll() {
  if (!state.response) return;
  state.packId += 1;
  state.packName = packName();
  toast(`Writing ${state.packName}…`);
  worker.postMessage({
    type: "pack",
    id: state.packId,
    format: packFormat(),
    files: state.sources.map((source, idx) => ({
      name: source.name, text: outputText(idx),
      encoding: source.encoding, bom: source.bom,
    })),
    archives: state.archives.map((archive) => archive.data),
  });
}

function savePacked(data) {
  if (data.id !== state.packId) return;
  if (data.error) toast(`Could not write ${state.packName}: ${data.error}`);
  else {
    const format = state.packName.endsWith(".tar") ? "tar" : state.packName.endsWith(".zip") ? "zip" : "tar.gz";
    saveBlob(new Blob([data.data], { type: ARCHIVE_TYPES[format] }), state.packName);
  }
}

$("#download").addEventListener("click", download);
$("#download-all").addEventListener("click", downloadAll);
$("#copy").addEventListener("click", async () => {
  if (!currentFile()) return;
  try {
    await navigator.clipboard.writeText(outputText(state.active));
    toast("Copied the result to the clipboard");
  } catch {
    toast("Could not copy to the clipboard");
  }
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "s") {
    event.preventDefault();
    if (state.archives.length) downloadAll();
    else download();
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
