// Main flow: upload a video -> POST /api/v1/analyze -> poll /api/v1/jobs/{id} -> show violations.
// Other files: icons.js, storage.js (IndexedDB), player.js (audit player tools), norms.js (normative base tab),
// library.js (history search, manual annotations, categories, category feed), report.js (PDF report).
// Open with ?demo to see sample results without calling the backend.
// Analyzed sessions are saved in this browser (storage.js) and survive page reloads.

const DEMO = new URLSearchParams(location.search).has("demo");
const HISTORY_LIMIT = 50; // sessions kept in the history / browser storage
const $ = (id) => document.getElementById(id);
const video = $("video");
// newest first: { id, file, url, addedAt, status: ready|processing|done|failed, result, details, error, range,
//                 progress, jobId, elapsedSec, analyzedAt, notes: [manual annotations], categoryIds: [] }
let uploads = [];
let current = null; // entry shown in the player
let violations = []; // current result: ТЗ fields + `extra` (confidence, interval, rule) from /details

// ---------- API ----------

const getKey = () => { try { return localStorage.getItem("api-key") || ""; } catch { return ""; } };
const setKey = (key) => { try { localStorage.setItem("api-key", key); } catch {} };

async function api(path, options = {}) {
  const res = await fetch(`/api/v1${path}`, { ...options, headers: { "X-API-Key": getKey() } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(body.detail || `Ошибка ${res.status}`), { status: res.status });
  return body;
}

// POST with upload progress (fetch can't report it, XMLHttpRequest can).
function upload(form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/v1/analyze");
    xhr.setRequestHeader("X-API-Key", getKey());
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(Object.assign(new Error(body.detail || `Ошибка ${xhr.status}`), { status: xhr.status }));
    };
    xhr.onerror = () => reject(new Error("Нет связи с сервером"));
    xhr.send(form);
  });
}

// Stages shown in the step-by-step progress. "upload" happens in the browser, the rest come from
// GET /jobs/{id}/progress (see backend/pipeline/runner.py).
const STEPS = [ // [stage, icon, color, label]
  ["upload", "upload", "c-sky", "Загрузка и нарезка кадров"],
  ["detect", "scan", "c-amber", "Детекция действий и объектов кассира"],
  ["rules", "scale", "c-violet", "Проверка по нормативным актам РК"],
  ["report", "clipboardCheck", "c-emerald", "Формирование вердикта и кадров-доказательств"],
];

function setProgress(entry, stage, pct) {
  // Upload is ~15% of the bar, server processing the remaining 85%.
  const overall = stage === "upload" ? 0.15 * pct : 0.15 + 0.85 * pct;
  entry.progress = { stage, overall };
  if (entry === current && entry.status === "processing") showProgress(entry.progress);
}

async function analyze(entry) {
  if (DEMO) return demoResult(entry);
  const form = new FormData();
  form.append("file", entry.file);
  if (entry.range) { // optional, not in the ТЗ: analyze only the selected segment
    form.append("start_sec", entry.range.start.toFixed(2));
    form.append("end_sec", entry.range.end.toFixed(2));
  }
  setProgress(entry, "upload", 0);
  const { job_id } = await upload(form, (p) => setProgress(entry, "upload", p));
  entry.jobId = job_id;
  setProgress(entry, "detect", 0);
  for (;; await new Promise((r) => setTimeout(r, 1000))) {
    const job = await api(`/jobs/${job_id}`);
    if (job.status === "failed") throw new Error("Не удалось обработать видео");
    if (job.status === "done") {
      const details = await api(`/jobs/${job_id}/details`).then((d) => d.violations).catch(() => []);
      return { result: job, details };
    }
    const { progress, stage } = await api(`/jobs/${job_id}/progress`);
    setProgress(entry, stage, progress);
  }
}

// ?demo: fake but realistic result, frames captured from the selected video in the browser.
async function demoResult(entry) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  for (let p = 0; p <= 1; p += 0.25) { setProgress(entry, "upload", p); await wait(150); }
  for (const [stage, from, to] of [["detect", 0, 0.85], ["rules", 0.85, 0.9], ["report", 0.9, 1]]) {
    for (let p = from; p <= to; p += 0.05) { setProgress(entry, stage, p); await wait(60); }
  }
  const { start, end } = entry.range || { start: 0, end: video.duration || 60 };
  const at = (k) => +(start + (end - start) * k).toFixed(1);
  // Real violation types and norms (rules/*.yaml) when the base is reachable, placeholders otherwise.
  const base = getKey() ? await loadNormsBase().catch(() => null) : null;
  const norm = (id) => base?.norms.find((n) => n.id === id) || { id, document: "Демо-документ", document_short: "Демо",
    clause: "п. —", quote: "Здесь появится дословная цитата пункта из rules/norms.yaml.", url: "https://adilet.zan.kz/" };
  const demo = [
    [at(0.22), "Наличные оставлены без присмотра", "С 0:05 на столе кассира видны наличные, кассира в зоне рабочего места нет (≈38 с). Это может указывать на хранение наличных вне запирающейся ёмкости.", 0.94, "N231_20_2", []],
    [at(0.58), "Сейф или денежный ящик открыт без присмотра", "С 0:14 сейф или ящик для хранения наличных открыт, рядом никого нет (≈21 с). Это может указывать на несоблюдение порядка хранения наличных на рабочем месте.", 0.81, "N231_20_2", ["N231_76"]],
  ];
  const result = { job_id: "demo", status: "done", verdict: "violation", violations: [] };
  const details = [];
  for (const [t, type, description, confidence, main, others] of demo) {
    const image = await captureFrame(t);
    result.violations.push({ type, timecode_sec: t, description: `${description} (демо-данные)`,
                             evidence_frames: image ? [{ timestamp_sec: t, image_base64: image }] : [] });
    details.push({ confidence, start_sec: Math.max(0, t - 3), end_sec: t + 3, rule: norm(main), other_norms: others.map(norm) });
  }
  entry.jobId = "demo";
  return { result, details };
}

// ---------- Rendering helpers ----------

const fmt = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter(Boolean));
  return node;
}

// Smoothly count a number up from 0 (verdict count, confidence); always ends on the exact value.
function countUp(node, to, suffix = "") {
  const started = performance.now();
  let finished = false;
  const finish = () => { finished = true; node.textContent = `${to}${suffix}`; };
  const tick = () => {
    if (finished) return;
    const k = Math.min(1, Math.max(0, (performance.now() - started) / 700));
    node.textContent = `${Math.round(to * (1 - (1 - k) ** 3))}${suffix}`;
    if (k < 1) requestAnimationFrame(tick); else finish();
  };
  requestAnimationFrame(tick);
  setTimeout(finish, 800); // also covers background tabs, where animation frames pause
}

function setStatus(text, kind = "") {
  $("status").textContent = text;
  $("status").className = kind ? `pill is-${kind}` : "pill";
}

function showState(title, text) {
  $("results-foot").hidden = true;
  $("results-body").replaceChildren(el("div", { className: "state" },
    el("p", { textContent: title }), el("p", { textContent: text })));
}

function showProgress({ stage, overall }) {
  $("results-foot").hidden = true;
  const active = STEPS.findIndex(([key]) => key === stage);
  const percent = Math.round(overall * 100);
  $("results-body").replaceChildren(el("div", { className: "state progress-state" },
    el("p", { textContent: "Идёт анализ видео" }),
    el("div", { className: "percent", textContent: `${percent}%` }),
    el("div", { className: "bar" }, el("div", { style: `width: ${percent}%` })),
    el("ol", { className: "stage-list" }, ...STEPS.map(([, name, color, label], i) =>
      el("li", { className: i < active ? "done" : i === active ? "active" : "" },
        el("span", { className: `stage-mark ${i < active ? "c-emerald" : color}` }, icon(i < active ? "check" : name)),
        el("span", { textContent: label })))),
    el("p", { className: "muted", textContent: "Обычно занимает до 3 минут" })));
}

function showWelcome() {
  $("results-foot").hidden = true;
  const steps = [["Загрузите видео", "файл .mov или пример"], ["AI проанализирует кадры", "локально, без облака"],
                 ["Получите вердикт", "с таймкодами и кадрами"]];
  $("results-body").replaceChildren(el("div", { className: "welcome" },
    el("div", { className: "welcome-icon", innerHTML:
      '<svg viewBox="0 0 24 24"><path d="M12 3 5 6v5c0 4.4 3 8.3 7 9.5 4-1.2 7-5.1 7-9.5V6z"/><path d="m9 12 2 2 4-4"/></svg>' }),
    el("p", { className: "welcome-title", textContent: "Здесь появятся найденные эпизоды" }),
    el("ol", { className: "steps" }, ...steps.map(([title, text], i) =>
      el("li", {}, el("span", { className: "step-num", textContent: i + 1 }),
        el("span", {}, el("strong", { textContent: title }), el("span", { className: "muted", textContent: text })))))));
}

// ---------- Results ----------

function showResult(entry) {
  // Pair each ТЗ violation with its optional details (same order), then sort by time.
  violations = (entry.result.violations || [])
    .map((v, i) => ({ ...v, extra: entry.details?.[i] || {} }))
    .sort((a, b) => a.timecode_sec - b.timecode_sec);
  const confidences = violations.map((v) => v.extra.confidence).filter((c) => c != null);
  const meta = [entry.elapsedSec != null && `Проанализировано за ${Math.max(1, Math.round(entry.elapsedSec))} с`,
                entry.jobId && `Задача ${entry.jobId}`].filter(Boolean).join(" · ");

  const count = el("span", { textContent: "0" });
  const verdict = violations.length
    ? el("div", { className: "verdict is-violation fade-in" },
        el("strong", {}, icon("alert"), "Обнаружено нарушений: ", count),
        el("p", { textContent: "Нажмите на эпизод, чтобы перейти к нему в видео." }))
    : el("div", { className: "verdict is-clean fade-in" },
        el("strong", {}, icon("checkCircle"), "Нарушений не обнаружено"),
        el("p", { textContent: "Признаков возможных нарушений на видео не найдено." }));
  if (confidences.length) {
    const value = el("b", { textContent: "0%" });
    const top = Math.round(Math.max(...confidences) * 100);
    verdict.append(el("div", { className: "confidence" },
      el("span", {}, "Уверенность AI ", value),
      el("div", { className: "bar" }, el("div", { style: `width: ${top}%` }))));
    countUp(value, top, "%");
  }
  if (meta) verdict.append(el("p", { className: "verdict-meta", textContent: meta }));

  setStatus(violations.length ? `Найдено: ${violations.length}` : "Нарушений нет", violations.length ? "violation" : "clean");
  $("results-body").replaceChildren(verdict, renderCategoryBar(entry), ...violations.map(renderViolation), renderNotes(entry));
  $("results-foot").hidden = false;
  if (violations.length) countUp(count, violations.length);
  renderMarkers();
}

const frameSrc = (frame) => `data:image/jpeg;base64,${frame.image_base64}`;

function renderViolation(v, i) {
  const frames = v.evidence_frames || [];
  const rule = v.extra.rule;
  const card = el("article", { className: "violation fade-in", style: `animation-delay: ${120 + i * 70}ms`,
                               onclick: () => select(i) },
    frames[0] ? el("img", { className: "thumb", src: frameSrc(frames[0]), alt: "",
                            onclick: (e) => { e.stopPropagation(); openFrame(frames[0]); } })
              : el("div", { className: "thumb" }),
    el("div", { className: "violation-body" },
      el("div", { className: "violation-top" },
        el("span", { className: "timecode", textContent: fmt(v.timecode_sec) }),
        v.extra.confidence != null && el("span", { className: "muted", textContent: `${Math.round(v.extra.confidence * 100)}%` })),
      el("h3", { textContent: v.type }),
      el("p", { textContent: v.description }),
      frames.length > 1 && el("div", { className: "evidence" }, ...frames.map((f) => el("img", {
        src: frameSrc(f), alt: `Кадр ${fmt(f.timestamp_sec)}`, title: fmt(f.timestamp_sec),
        onclick: (e) => { e.stopPropagation(); openFrame(f); } }))),
      rule && el("div", { className: "law" },
        el("span", { className: "law-title", title: rule.document }, icon("scale", "c-violet"),
          `${rule.clause} · ${rule.document_short || rule.document}`),
        el("q", { textContent: rule.quote }),
        rule.url && el("a", { href: rule.url, target: "_blank", rel: "noopener", textContent: "Открыть источник ",
                              onclick: (e) => e.stopPropagation() }, icon("external"))),
      el("button", { className: "link episode", type: "button",
                     onclick: (e) => { e.stopPropagation(); playEpisode(i); } }, icon("play"), "Смотреть эпизод")));
  return card;
}

function renderMarkers() {
  document.querySelectorAll(".marker").forEach((m) => m.remove());
  if (!video.duration) return;
  violations.forEach((v, i) => $("timeline").append(el("button", {
    className: "marker", type: "button", title: `${fmt(v.timecode_sec)} · ${v.type}`,
    style: `left: ${(v.timecode_sec / video.duration) * 100}%`,
    onclick: (e) => { e.stopPropagation(); select(i); },
  })));
  // Manual annotations (library.js) get their own violet markers.
  (current?.status === "done" ? current.notes || [] : []).filter((n) => n.timecode_sec != null).forEach((n) =>
    $("timeline").append(el("button", {
      className: "marker manual", type: "button", title: `${fmt(n.timecode_sec)} · ${n.type} (вручную)`,
      style: `left: ${(n.timecode_sec / video.duration) * 100}%`,
      onclick: (e) => { e.stopPropagation(); video.currentTime = n.timecode_sec; },
    })));
}

function openFrame(frame) {
  $("frame-image").src = frameSrc(frame);
  $("frame-caption").textContent = `Кадр на ${fmt(frame.timestamp_sec)}`;
  $("frame-dialog").showModal();
}

let selected = -1;
function select(i) {
  selected = i;
  video.currentTime = Math.max(0, violations[i].timecode_sec - 2); // a little context before the event
  document.querySelectorAll(".marker").forEach((m, j) => m.classList.toggle("active", i === j));
  document.querySelectorAll(".violation").forEach((c, j) => {
    c.classList.toggle("active", i === j);
    if (i === j) c.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
}

// ---------- History ----------

function statusOf(entry) {
  if (entry.status === "processing") return ["Анализ…", "processing"];
  if (entry.status === "failed") return ["Ошибка", "failed"];
  if (entry.status === "done") {
    const n = entry.result.violations?.length || 0;
    return n ? [`Найдено: ${n}`, "violation"] : ["Нарушений нет", "clean"];
  }
  return ["Готово к анализу", ""];
}

// ---------- Actions ----------

function selectFile(f) {
  if (!f) return;
  const entry = { id: newId(), file: f, url: URL.createObjectURL(f), addedAt: new Date(), status: "ready",
                  result: null, details: [], error: null, range: null, notes: [], categoryIds: [] };
  uploads.unshift(entry);
  for (const removed of uploads.splice(HISTORY_LIMIT)) forgetSession(removed);
  open(entry);
}

function open(entry) {
  current = entry;
  if (video.src !== entry.url) video.src = entry.url;
  $("file-name").textContent = entry.file.name;
  $("file-meta").textContent = `${(entry.file.size / 1e6).toFixed(1)} МБ`;
  $("dropzone").hidden = true;
  $("player").hidden = false;
  render(entry);
}

function render(entry) {
  violations = [];
  selected = -1;
  const [label, kind] = statusOf(entry);
  setStatus(label, kind);
  $("analyze-button").disabled = entry.status === "processing";
  if (entry.status === "ready") showState("Видео загружено", "Нажмите «Анализировать», чтобы найти признаки возможных нарушений.");
  if (entry.status === "processing") showProgress(entry.progress || { stage: "upload", overall: 0 });
  if (entry.status === "failed") showState(entry.error.title, entry.error.text);
  if (entry.status === "done") showResult(entry);
  else renderMarkers();
  renderRange();
  renderHistory();
}

async function start() {
  if (!getKey() && !DEMO) return $("key-dialog").showModal();
  const entry = current;
  entry.status = "processing";
  render(entry);
  const started = performance.now();
  try {
    const { result, details } = await analyze(entry);
    Object.assign(entry, { result, details, status: "done", analyzedAt: new Date(),
                           elapsedSec: (performance.now() - started) / 1000 });
    saveSession(entry); // keep it in the browser library
  } catch (err) {
    entry.status = "failed";
    entry.error = { title: err.status === 401 ? "Неверный API-ключ" : "Ошибка анализа", text: err.message };
  }
  if (entry === current) render(entry);
  else renderHistory();
}

// Back to the start screen; the video stays in the history.
function goHome() {
  current = null;
  violations = [];
  video.pause();
  video.removeAttribute("src");
  video.load();
  renderMarkers();
  renderRange();
  $("player").hidden = true;
  $("dropzone").hidden = false;
  $("file-input").value = "";
  setStatus("Ожидание");
  showWelcome();
  renderHistory();
}

// ---------- Segment selection (drag on the timeline) ----------

function renderRange() {
  const range = current?.range;
  const show = Boolean(range && video.duration);
  $("selection").hidden = !show;
  $("range-clear").hidden = !show;
  $("range-text").classList.toggle("active", show);
  if (show) {
    $("selection").style.left = `${(range.start / video.duration) * 100}%`;
    $("selection").style.width = `${((range.end - range.start) / video.duration) * 100}%`;
    $("range-text").textContent = `Участок для анализа: ${fmt(range.start)} – ${fmt(range.end)}`;
  } else {
    $("range-text").textContent = "Протяните мышью по полосе, чтобы выбрать участок для анализа";
  }
}

const timeAt = (x) => {
  const rect = $("timeline").getBoundingClientRect();
  return Math.min(1, Math.max(0, (x - rect.left) / rect.width)) * video.duration;
};
let drag = null;

// Click = seek, drag = select a segment.
$("timeline").onpointerdown = (e) => {
  if (!video.duration || e.target.classList.contains("marker")) return;
  drag = { x: e.clientX, from: timeAt(e.clientX), moved: false };
  try { $("timeline").setPointerCapture(e.pointerId); } catch {} // keep dragging outside the bar
};
$("timeline").onpointermove = (e) => {
  if (!drag) return;
  drag.moved ||= Math.abs(e.clientX - drag.x) > 4;
  if (!drag.moved) return;
  const to = timeAt(e.clientX);
  current.range = { start: Math.min(drag.from, to), end: Math.max(drag.from, to) };
  renderRange();
};
$("timeline").onpointerup = (e) => {
  if (!drag) return;
  video.currentTime = drag.moved ? current.range.start : timeAt(e.clientX);
  drag = null;
};
$("range-clear").onclick = () => { current.range = null; renderRange(); };

// ---------- Tabs, theme, dialogs, wiring ----------

const VIEWS = ["analyzer-view", "history-view", "norms-view", "filters-view", "categories-view"];
function showView(view) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  for (const id of VIEWS) $(id).hidden = id !== view;
  // The disclaimer is shown only on the analyzer; the norms page shows the base's update date instead.
  $("footer-disclaimer").hidden = view !== "analyzer-view";
  $("footer-updated").hidden = view !== "norms-view";
  if (view !== "analyzer-view") video.pause();
  document.querySelectorAll(".feed-video").forEach((v) => v.pause());
  if (view === "history-view") renderHistory();
  if (view === "norms-view") loadNorms();
  if (view === "filters-view") renderFilters();
  if (view === "categories-view") renderCategories();
}
document.querySelectorAll(".tab").forEach((tab) => tab.onclick = () => showView(tab.dataset.view));

$("theme-button").onclick = () => {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("theme", root.dataset.theme); } catch {}
  updateThemeIcon();
};
// Sun in dark mode (click -> light), moon in light mode (click -> dark).
function updateThemeIcon() {
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  $("theme-icon").replaceWith(Object.assign(icon(dark ? "sun" : "moon", "c-amber"), { id: "theme-icon" }));
}
updateThemeIcon();

$("back-button").onclick = goHome;
$("file-input").onchange = (e) => selectFile(e.target.files[0]);
$("change-button").onclick = () => $("file-input").click();
$("analyze-button").onclick = start;
$("key-button").onclick = () => { $("key-input").value = getKey(); $("key-dialog").showModal(); };
$("key-dialog").onclose = () => {
  if ($("key-dialog").returnValue === "save") setKey($("key-input").value.trim());
  $("key-dot").classList.toggle("set", Boolean(getKey()));
};
for (const type of ["dragenter", "dragover", "dragleave", "drop"]) {
  $("dropzone").addEventListener(type, (e) => {
    e.preventDefault();
    $("dropzone").classList.toggle("dragging", type === "dragenter" || type === "dragover");
    if (type === "drop") selectFile(e.dataTransfer.files[0]);
  });
}
// Sample buttons inside the dropzone: load a demo video as if the user picked it.
document.querySelectorAll(".sample").forEach((button) => button.onclick = async (e) => {
  e.preventDefault(); // the dropzone is a <label>: don't open the file picker
  const blob = await (await fetch(button.dataset.src)).blob();
  selectFile(new File([blob], button.dataset.name, { type: "video/quicktime" }));
});
video.addEventListener("loadedmetadata", () => {
  renderMarkers();
  renderRange();
  if (current) $("file-meta").textContent = `${fmt(video.duration).replace(/\.\d$/, "")} · ${(current.file.size / 1e6).toFixed(1)} МБ`;
});
video.addEventListener("timeupdate", () => {
  $("progress").style.width = `${(video.currentTime / video.duration) * 100 || 0}%`;
});
video.addEventListener("seeked", () => {
  $("progress").style.width = `${(video.currentTime / video.duration) * 100 || 0}%`;
});

$("key-dot").classList.toggle("set", Boolean(getKey()));
// Start once every script is loaded: restore the saved library, then draw the start screen.
document.addEventListener("DOMContentLoaded", async () => {
  await loadLibrary();
  renderHistory();
  showWelcome();
});
