// UI: upload a video -> POST /api/v1/analyze -> poll /api/v1/jobs/{id} -> show violations.
// Open with ?demo to see sample results without calling the backend.
// History lives in page memory only: it is cleared when the page is reloaded.

const DEMO = new URLSearchParams(location.search).has("demo");
const HISTORY_LIMIT = 10;
const $ = (id) => document.getElementById(id);
const video = $("video");
let uploads = [];   // newest first: { file, url, addedAt, status: ready|processing|done|failed, result, error }
let current = null; // entry shown in the player
let violations = [];

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

// Progress: stage "upload" (sending the file) then "analyze" (server processing), pct 0..1.
function report(entry, stage, pct) {
  entry.progress = { stage, pct };
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
  report(entry, "upload", 0);
  const { job_id } = await upload(form, (p) => report(entry, "upload", p));
  report(entry, "analyze", 0);
  for (;; await new Promise((r) => setTimeout(r, 1000))) {
    const job = await api(`/jobs/${job_id}`);
    if (job.status === "done") return job;
    if (job.status === "failed") throw new Error("Не удалось обработать видео");
    report(entry, "analyze", (await api(`/jobs/${job_id}/progress`)).progress);
  }
}

async function demoResult(entry) {
  for (const stage of ["upload", "analyze"]) {
    for (let p = 0; p <= 1; p += 0.1) { report(entry, stage, p); await new Promise((r) => setTimeout(r, 120)); }
  }
  const { start, end } = entry.range || { start: 0, end: video.duration || 60 };
  const at = (k) => start + (end - start) * k;
  return new Promise((resolve) => setTimeout(() => resolve({ verdict: "violation", violations: [
    { type: "Демо: деньги без присмотра", timecode_sec: at(0.22), description: "Кассир отходит от рабочего места, денежные средства остаются на столе 38 с.", evidence_frames: [] },
    { type: "Демо: открытое хранилище", timecode_sec: at(0.58), description: "Дверца сейфа остаётся открытой, в зоне кассы нет сотрудника 21 с.", evidence_frames: [] },
  ] }), 1500));
}

// ---------- Rendering ----------

const fmt = (s) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter(Boolean));
  return node;
}

function setStatus(text, kind = "") {
  $("status").textContent = text;
  $("status").className = kind ? `pill is-${kind}` : "pill";
}

function showState(title, text) {
  $("results-body").replaceChildren(el("div", { className: "state" },
    el("p", { textContent: title }), el("p", { textContent: text })));
}

function showProgress({ stage, pct }) {
  const percent = Math.round(pct * 100);
  $("results-body").replaceChildren(el("div", { className: "state progress-state" },
    el("p", { textContent: stage === "upload" ? "Загрузка видео" : "Анализируем видео" }),
    el("div", { className: "percent", textContent: `${percent}%` }),
    el("div", { className: "bar" }, el("div", { style: `width: ${percent}%` })),
    el("p", { textContent: stage === "upload" ? "Шаг 1 из 2 · отправка файла на сервер"
                                              : "Шаг 2 из 2 · обычно занимает до 3 минут" })));
}

function showWelcome() {
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

function showResult(result) {
  violations = (result.violations || []).sort((a, b) => a.timecode_sec - b.timecode_sec);
  if (!violations.length) {
    setStatus("Нарушений нет", "clean");
    $("results-body").replaceChildren(
      el("div", { className: "verdict is-clean" },
        el("strong", { textContent: "✓ Нарушений не обнаружено" }),
        el("p", { textContent: "Признаков возможных нарушений на видео не найдено." })));
  } else {
    setStatus(`Найдено: ${violations.length}`, "violation");
    $("results-body").replaceChildren(
      el("div", { className: "verdict is-violation" },
        el("strong", { textContent: `Обнаружено нарушений: ${violations.length}` }),
        el("p", { textContent: "Нажмите на эпизод, чтобы перейти к нему в видео." })),
      ...violations.map((v, i) => {
      const frame = v.evidence_frames?.[0];
      return el("article", { className: "violation", onclick: () => select(i) },
        frame ? el("img", { className: "thumb", src: `data:image/jpeg;base64,${frame.image_base64}`, alt: "",
                            onclick: (e) => { e.stopPropagation(); openFrame(frame); } })
              : el("div", { className: "thumb" }),
        el("div", {},
          el("span", { className: "timecode", textContent: fmt(v.timecode_sec) }),
          el("h3", { textContent: v.type }),
          el("p", { textContent: v.description })));
    }));
  }
  renderMarkers();
}

function renderMarkers() {
  document.querySelectorAll(".marker").forEach((m) => m.remove());
  if (!video.duration) return;
  violations.forEach((v, i) => $("timeline").append(el("button", {
    className: "marker", type: "button", title: `${fmt(v.timecode_sec)} · ${v.type}`,
    style: `left: ${(v.timecode_sec / video.duration) * 100}%`,
    onclick: (e) => { e.stopPropagation(); select(i); },
  })));
}

function openFrame(frame) {
  $("frame-image").src = `data:image/jpeg;base64,${frame.image_base64}`;
  $("frame-caption").textContent = `Кадр на ${fmt(frame.timestamp_sec)}`;
  $("frame-dialog").showModal();
}

function select(i) {
  video.currentTime = Math.max(0, violations[i].timecode_sec - 2); // a little context before the event
  document.querySelectorAll(".marker").forEach((m, j) => m.classList.toggle("active", i === j));
  document.querySelectorAll(".violation").forEach((c, j) => c.classList.toggle("active", i === j));
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

function renderHistory() {
  $("history-count").textContent = uploads.length || "";
  $("history-list").replaceChildren(...(uploads.length ? uploads.map((entry) => {
    const [label, kind] = statusOf(entry);
    return el("button", {
      type: "button",
      className: `history-item ${entry === current ? "active" : ""}`,
      onclick: () => { open(entry); $("history-dialog").close(); },
    },
      el("span", { className: "history-name", textContent: entry.file.name }),
      el("span", { className: "muted", textContent: entry.addedAt.toLocaleTimeString("ru", { hour: "2-digit", minute: "2-digit" }) }),
      el("span", { className: kind ? `pill is-${kind}` : "pill", textContent: label }));
  }) : [el("p", { className: "muted", textContent: "Вы ещё не загружали видео." })]));
}

// ---------- Actions ----------

function selectFile(f) {
  if (!f) return;
  const entry = { file: f, url: URL.createObjectURL(f), addedAt: new Date(), status: "ready", result: null, error: null, range: null };
  uploads.unshift(entry);
  for (const removed of uploads.splice(HISTORY_LIMIT)) URL.revokeObjectURL(removed.url);
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
  const [label, kind] = statusOf(entry);
  setStatus(label, kind);
  $("analyze-button").disabled = entry.status === "processing";
  if (entry.status === "ready") showState("Видео загружено", "Нажмите «Анализировать», чтобы найти признаки возможных нарушений.");
  if (entry.status === "processing") showProgress(entry.progress || { stage: "upload", pct: 0 });
  if (entry.status === "failed") showState(entry.error.title, entry.error.text);
  if (entry.status === "done") showResult(entry.result);
  else renderMarkers();
  renderRange();
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

async function start() {
  if (!getKey() && !DEMO) return $("key-dialog").showModal();
  const entry = current;
  entry.status = "processing";
  render(entry);
  try {
    entry.result = await analyze(entry);
    entry.status = "done";
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

$("back-button").onclick = goHome;
$("file-input").onchange = (e) => selectFile(e.target.files[0]);
$("change-button").onclick = () => $("file-input").click();
$("analyze-button").onclick = start;
$("history-button").onclick = () => { renderHistory(); $("history-dialog").showModal(); };
$("history-dialog").onclick = (e) => { if (e.target === $("history-dialog")) $("history-dialog").close(); }; // click outside closes
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
video.onloadedmetadata = () => {
  renderMarkers();
  renderRange();
  if (current) $("file-meta").textContent = `${fmt(video.duration).replace(/\.\d$/, "")} · ${(current.file.size / 1e6).toFixed(1)} МБ`;
};
// Sample buttons inside the dropzone: load a demo video as if the user picked it.
document.querySelectorAll(".sample").forEach((button) => button.onclick = async (e) => {
  e.preventDefault(); // the dropzone is a <label>: don't open the file picker
  const blob = await (await fetch(button.dataset.src)).blob();
  selectFile(new File([blob], button.dataset.name, { type: "video/quicktime" }));
});
video.ontimeupdate = video.onseeked = () => {
  $("progress").style.width = `${(video.currentTime / video.duration) * 100 || 0}%`;
};
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

$("key-dot").classList.toggle("set", Boolean(getKey()));
renderHistory();
showWelcome();
