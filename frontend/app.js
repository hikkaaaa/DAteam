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
// GET /jobs/{id}/progress. Two sets: the ML server's real stages (backend/pipeline/remote.py)
// and the local pipeline / ?demo stages (backend/pipeline/runner.py).
const ML_STEPS = [ // [stage, icon, color, label]
  ["upload", "upload", "c-sky", "Загрузка видео"],
  ["frames", "film", "c-sky", "Нарезка кадров"],
  ["roles", "users", "c-teal", "Определение ролей в кадре"],
  ["checklist", "eye", "c-amber", "Анализ кадров (Qwen)"],
  ["detector", "box", "c-amber", "Детекция предметов"],
  ["lora", "sparkles", "c-violet", "Проверка дообученной моделью"],
  ["rules", "scale", "c-violet", "Сопоставление с нормами РК"],
  ["report", "clipboardCheck", "c-emerald", "Вердикт и кадры-доказательства"],
];
const LOCAL_STEPS = [
  ["upload", "upload", "c-sky", "Загрузка и нарезка кадров"],
  ["detect", "scan", "c-amber", "Детекция действий и объектов кассира"],
  ["rules", "scale", "c-violet", "Проверка по нормативным актам РК"],
  ["report", "clipboardCheck", "c-emerald", "Формирование вердикта и кадров-доказательств"],
];
const ML_ONLY = new Set(["queued", "frames", "roles", "checklist", "detector", "lora"]);
let mlOnline = false; // set by checkMl(): pick the ML step list right from the upload

// info: { title, message } from the ML server (current stage text, e.g. "Анализ кадров: 8/12")
function setProgress(entry, stage, pct, info = {}) {
  // Upload is ~15% of the bar, server processing the remaining 85%.
  // never moves backwards (e.g. the ML server's per-stage percent restarting)
  const overall = Math.max(entry.progress?.overall || 0, stage === "upload" ? 0.15 * pct : 0.15 + 0.85 * pct);
  if (ML_ONLY.has(stage)) entry.mlSteps = true;
  if (stage === "detect") entry.mlSteps = false;
  entry.progress = { stage, overall, ...info, ml: entry.mlSteps ?? (DEMO || mlOnline),
                     startedAt: entry.progress?.startedAt ?? Date.now() };
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
  if (entry.analyzedRoi) form.append("roi", entry.analyzedRoi.map((v) => v.toFixed(4)).join(",")); // optional: cash desk zone
  setProgress(entry, "upload", 0);
  const { job_id } = await upload(form, (p) => setProgress(entry, "upload", p));
  entry.jobId = job_id;
  setProgress(entry, "upload", 1);
  for (;; await new Promise((r) => setTimeout(r, 1000))) {
    const job = await api(`/jobs/${job_id}`);
    if (job.status === "failed") {
      const { error } = await api(`/jobs/${job_id}/progress`).catch(() => ({}));
      throw new Error(error || "Не удалось обработать видео");
    }
    if (job.status === "done") {
      const details = await api(`/jobs/${job_id}/details`).then((d) => d.violations).catch(() => []);
      return { result: job, details };
    }
    const { progress, stage, title, message } = await api(`/jobs/${job_id}/progress`);
    setProgress(entry, stage, progress, { title, message });
  }
}

// ?demo: fake but realistic result, frames captured from the selected video in the browser.
async function demoResult(entry) {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  entry.mlSteps = true; // same stages as the real ML server
  for (let p = 0; p <= 1; p += 0.25) { setProgress(entry, "upload", p); await wait(150); }
  setProgress(entry, "queued", 0, { title: "В очереди на ML-сервере", message: "Сервер обрабатывает другое видео" });
  await wait(1200);
  const stages = [["frames", 4], ["roles", 3], ["checklist", 12], ["detector", 4], ["lora", 4], ["rules", 2]];
  const total = stages.reduce((n, [, k]) => n + k, 0);
  let done = 0;
  for (const [stage, count] of stages) {
    const [, , , title] = ML_STEPS.find(([key]) => key === stage);
    for (let k = 1; k <= count; k++, done++) {
      setProgress(entry, stage, 0.95 * done / total, { title, message: `${title}: ${k}/${count}` });
      await wait(350);
    }
  }
  setProgress(entry, "report", 0.96, { title: "Вердикт и кадры-доказательства" });
  await wait(500);
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

// Built once per analysis, then updated in place so the bar, the number and the steps animate
// smoothly from one stage to the next (instead of re-rendering on every poll).
function showProgress(progress) {
  $("results-foot").hidden = true;
  const steps = progress.ml ? ML_STEPS : LOCAL_STEPS;
  let view = $("results-body").querySelector(".progress-state");
  if (!view || view.dataset.steps !== String(progress.ml)) view = buildProgress(steps, progress.ml);

  const { stage, overall, title, message } = progress;
  const queued = stage === "queued";
  // queued = upload done, waiting for the ML server; an unknown stage keeps the last known one
  let active = queued ? 1 : steps.findIndex(([key]) => key === stage);
  if (active < 0) active = +(view.dataset.active || 0);
  view.dataset.active = active;
  view.querySelector(".bar div").style.width = `${overall * 100}%`;
  tweenPercent(view.querySelector(".percent"), Math.round(overall * 100));
  view.querySelectorAll(".stage-list li").forEach((li, i) => {
    const state = i < active ? "done" : i === active && !queued ? "active" : "";
    if (li.dataset.state === state) return;
    li.dataset.state = state;
    li.className = state;
    const [, name, color, label] = steps[i];
    li.querySelector(".stage-mark").className = `stage-mark ${state === "done" ? "c-emerald" : color}`;
    li.querySelector(".stage-mark").replaceChildren(icon(state === "done" ? "check" : name));
    li.querySelector(".stage-label").textContent = label;
  });
  // ML server's own text for the current stage, e.g. "Анализ кадров (Qwen)"
  if (title && !queued && steps[active]) view.querySelectorAll(".stage-label")[active].textContent = title;
  setLine(view.querySelector(".progress-message"), queued ? [icon("clock", "c-amber"), title || "В очереди"] : null,
          message || (queued ? "" : "Обычно занимает до 3 минут"));
}

function buildProgress(steps, ml) {
  const view = el("div", { className: "state progress-state" },
    el("p", { textContent: "Идёт анализ видео" }),
    el("div", { className: "percent", textContent: "0%" }),
    el("div", { className: "bar" }, el("div", { style: "width: 0%" })),
    el("p", { className: "progress-message muted" }),
    el("ol", { className: "stage-list" }, ...steps.map(([, name, color, label]) =>
      el("li", {}, el("span", { className: `stage-mark ${color}` }, icon(name)), el("span", { className: "stage-label", textContent: label })))),
    el("p", { className: "progress-elapsed muted" }));
  view.dataset.steps = String(ml);
  $("results-body").replaceChildren(view);
  return view;
}

// Message under the bar: fades when its text changes.
function setLine(node, lead, text) {
  const key = `${lead ? lead[1] : ""}|${text}`;
  if (node.dataset.key === key) return;
  node.dataset.key = key;
  node.replaceChildren(...(lead ? [el("strong", {}, ...lead), lead[1] && text ? " · " : ""] : []), text);
  node.classList.remove("swap");
  void node.offsetWidth; // restart the animation
  node.classList.add("swap");
}

// Percent number glides to the new value instead of jumping.
function tweenPercent(node, to) {
  const from = parseInt(node.textContent) || 0;
  cancelAnimationFrame(node._raf);
  if (from === to) return;
  const started = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - started) / 600);
    node.textContent = `${Math.round(from + (to - from) * (1 - (1 - k) ** 3))}%`;
    if (k < 1) node._raf = requestAnimationFrame(step);
  };
  node._raf = requestAnimationFrame(step);
}

// "Прошло 0:15" under the steps, ticking every second while the analysis runs.
setInterval(() => {
  const node = $("results-body").querySelector(".progress-elapsed");
  if (!node || current?.status !== "processing" || !current.progress) return;
  const s = Math.floor((Date.now() - current.progress.startedAt) / 1000);
  node.textContent = `Прошло ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}, 1000);

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
                entry.analyzedRoi && "только зона кассы",
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
                            onclick: (e) => { e.stopPropagation(); openFrame(frames[0], frames); } })
              : el("div", { className: "thumb" }),
    el("div", { className: "violation-body" },
      el("div", { className: "violation-top" },
        el("span", { className: "timecode", textContent: fmt(v.timecode_sec) }),
        v.extra.confidence != null && el("span", { className: "muted", textContent: `${Math.round(v.extra.confidence * 100)}%` })),
      el("h3", { textContent: v.type }),
      el("p", { textContent: v.description }),
      frames.length > 1 && el("div", { className: "evidence" }, ...frames.map((f) => el("img", {
        src: frameSrc(f), alt: `Кадр ${fmt(f.timestamp_sec)}`, title: fmt(f.timestamp_sec),
        onclick: (e) => { e.stopPropagation(); openFrame(f, frames); } }))),
      rule && el("div", { className: "law" },
        el("span", { className: "law-title", title: rule.document }, icon("scale", "c-violet"),
          `${rule.clause} · ${rule.document_short || rule.document}`),
        el("q", { textContent: rule.quote }),
        rule.url && el("a", { href: rule.url, target: "_blank", rel: "noopener", textContent: "Открыть источник ",
                              onclick: (e) => e.stopPropagation() }, icon("external"))),
      el("button", { className: "link episode", type: "button",
                     onclick: (e) => { e.stopPropagation(); playEpisode(i); } }, icon("play"), "Смотреть эпизод")),
    renderSummary(v, i)); // full card width, at the very end
  return card;
}

// "Кратко": the same violation as big bullet points that slide in one after another.
function renderSummary(v, i) {
  // Description = observed facts, then "Это может указывать на …", then the norm: take the parts apart.
  const sentences = (v.description || "").split(/(?<=[.;])\s+(?=[А-ЯЁA-Z])/);
  const what = sentences[0]?.replace(/[.;]$/, "");
  const why = v.description?.match(/может указывать на ([^.;]+)/)?.[1];
  const rule = v.extra.rule;
  const { start_sec: from, end_sec: to, confidence } = v.extra;
  const frames = v.evidence_frames?.length || 0;
  const plural = (n, one, few, many) => (n % 10 === 1 && n % 100 !== 11 ? one
    : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 10 || n % 100 >= 20) ? few : many);
  const rows = [ // [icon, color, label, text, action on click]
    ["alert", "c-rose", "Что произошло", what],
    ["eye", "c-amber", "Почему это важно", why && `Может указывать на ${why}`],
    ["scale", "c-violet", "Правило", rule && `${rule.clause.split(" (")[0]} · ${rule.document_short || rule.document}`,
     rule?.id && (() => goToNorm(rule.id))],
    ["clock", "c-sky", "Таймкод", fmt(v.timecode_sec) + (from != null && to != null ? `  ·  эпизод ${fmt(from)}–${fmt(to)}` : ""),
     () => playEpisode(i)],
    ["film", "c-teal", "Доказательства", frames && `${frames} ${plural(frames, "кадр", "кадра", "кадров")} из видео`,
     () => openFrame(v.evidence_frames[0], v.evidence_frames)],
    ["sparkles", "c-emerald", "Уверенность AI", confidence != null && `${Math.round(confidence * 100)}%`],
  ].filter(([, , , text]) => text);
  const block = el("div", { className: "summary" },
    el("p", { className: "summary-title", textContent: "Кратко" }),
    el("ul", {}, ...rows.map(([name, color, label, text, action], j) =>
      el("li", { style: `animation-delay: ${j * 110}ms`, className: action ? "clickable" : "",
                 onclick: action && ((e) => { e.stopPropagation(); action(); }) },
        el("span", { className: `summary-mark ${color}` }, icon(name)),
        el("span", {}, el("span", { className: "summary-label", textContent: label }),
                       el("strong", { textContent: text })),
        action && el("span", { className: "summary-go" }, icon("chevronRight"))))));
  // the bullets slide in when the block scrolls into view
  summaryObserver.observe(block);
  return block;
}
const summaryObserver = new IntersectionObserver((seen) => seen.forEach(({ isIntersecting, target }) => {
  if (!isIntersecting) return;
  target.classList.add("in");
  summaryObserver.unobserve(target);
}), { threshold: 0.25 });

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

// Evidence slideshow: all frames of a violation, arrows / ← → / thumbnails switch between them.
let slides = [], slide = 0;
function openFrame(frame, frames = [frame]) {
  slides = frames;
  $("frame-thumbs").replaceChildren(...(frames.length > 1 ? frames.map((f, i) =>
    el("button", { type: "button", className: "slide-thumb", title: fmt(f.timestamp_sec), onclick: () => showSlide(i) },
      el("img", { src: frameSrc(f), alt: "" }))) : []));
  showSlide(Math.max(0, frames.indexOf(frame)), 0);
  $("frame-dialog").showModal();
}

function showSlide(i, dir = Math.sign(i - slide)) {
  slide = (i + slides.length) % slides.length;
  const frame = slides[slide], img = $("frame-image");
  img.src = frameSrc(frame);
  img.classList.remove("from-left", "from-right");
  void img.offsetWidth; // restart the slide animation
  if (dir) img.classList.add(dir > 0 ? "from-right" : "from-left");
  const many = slides.length > 1;
  $("frame-prev").hidden = $("frame-next").hidden = !many;
  $("frame-caption").textContent = `${many ? `Кадр ${slide + 1} из ${slides.length} · ` : "Кадр на "}${fmt(frame.timestamp_sec)}`;
  document.querySelectorAll(".slide-thumb").forEach((t, j) => t.classList.toggle("active", j === slide));
}
$("frame-prev").onclick = () => showSlide(slide - 1, -1);
$("frame-next").onclick = () => showSlide(slide + 1, 1);
$("frame-dialog").addEventListener("keydown", (e) => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  e.preventDefault();
  e.stopPropagation(); // the player's ← / → hotkeys jump between violations
  if (slides.length > 1) showSlide(slide + (e.key === "ArrowRight" ? 1 : -1), e.key === "ArrowRight" ? 1 : -1);
});

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
  if (entry.status === "processing") showProgress(entry.progress || { stage: "upload", overall: 0, ml: DEMO || mlOnline });
  if (entry.status === "failed") showState(entry.error.title, entry.error.text);
  if (entry.status === "done") showResult(entry);
  else renderMarkers();
  renderRange();
  updateRoi();
  renderHistory();
}

async function start() {
  if (!getKey() && !DEMO) return $("key-dialog").showModal();
  const entry = current;
  entry.status = "processing";
  entry.progress = null; // a re-run starts its progress from zero
  entry.analyzedRoi = entry.roiOn && entry.roi ? [...entry.roi] : null;
  entry.mlSteps = undefined;
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
  updateRoi();
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
  checkMl();
};

// ML server indicator in the top bar (the models run on the team's GPU server).
async function checkMl() {
  if (DEMO || !getKey()) return;
  const { status, busy } = await api("/ml/health").catch(() => ({ status: "offline" }));
  $("ml-status").hidden = status === "off";
  const ok = status === "ok";
  mlOnline = ok;
  $("ml-status").querySelector(".dot").className = `dot ${ok ? "set" : "down"}`;
  $("ml-status-text").textContent = ok ? (busy ? "ML-сервер: занят" : "ML-сервер на связи") : "ML-сервер недоступен";
}
setInterval(checkMl, 30000);
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
checkMl();
// Start once every script is loaded: restore the saved library, then draw the start screen.
document.addEventListener("DOMContentLoaded", async () => {
  await loadLibrary();
  renderHistory();
  showWelcome();
});
