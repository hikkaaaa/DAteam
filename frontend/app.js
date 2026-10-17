// UI: upload a video -> POST /api/v1/analyze -> poll /api/v1/jobs/{id} -> show violations.
// Open with ?demo to see sample results without calling the backend.

const DEMO = new URLSearchParams(location.search).has("demo");
const $ = (id) => document.getElementById(id);
const video = $("video");
let file = null;
let violations = [];
let run = 0; // bumps on every new file / analysis, so stale polling stops

// ---------- API ----------

const getKey = () => { try { return localStorage.getItem("api-key") || ""; } catch { return ""; } };
const setKey = (key) => { try { localStorage.setItem("api-key", key); } catch {} };

async function api(path, options = {}) {
  const res = await fetch(`/api/v1${path}`, { ...options, headers: { "X-API-Key": getKey() } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(body.detail || `Ошибка ${res.status}`), { status: res.status });
  return body;
}

async function analyze() {
  if (DEMO) return demoResult();
  const form = new FormData();
  form.append("file", file);
  const { job_id } = await api("/analyze", { method: "POST", body: form });
  for (const current = run; current === run; await new Promise((r) => setTimeout(r, 2000))) {
    const job = await api(`/jobs/${job_id}`);
    if (job.status === "done") return job;
    if (job.status === "failed") throw new Error("Не удалось обработать видео");
  }
}

function demoResult() {
  const d = video.duration || 60;
  return new Promise((resolve) => setTimeout(() => resolve({ verdict: "violation", violations: [
    { type: "Демо: деньги без присмотра", timecode_sec: d * 0.22, description: "Кассир отходит от рабочего места, денежные средства остаются на столе 38 с.", evidence_frames: [] },
    { type: "Демо: открытое хранилище", timecode_sec: d * 0.58, description: "Дверца сейфа остаётся открытой, в зоне кассы нет сотрудника 21 с.", evidence_frames: [] },
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

function showState(title, text, spinner = false) {
  $("results-body").replaceChildren(el("div", { className: "state" },
    spinner && el("div", { className: "spinner" }), el("p", { textContent: title }), el("p", { textContent: text })));
}

function showResult(result) {
  violations = (result.violations || []).sort((a, b) => a.timecode_sec - b.timecode_sec);
  if (!violations.length) {
    setStatus("Нарушений нет", "clean");
    showState("Нарушений не обнаружено", "Признаков возможных нарушений на видео не найдено.");
  } else {
    setStatus(`Найдено: ${violations.length}`, "violation");
    $("results-body").replaceChildren(...violations.map((v, i) => {
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

// ---------- Actions ----------

function selectFile(f) {
  if (!f) return;
  run++;
  file = f;
  violations = [];
  video.src = URL.createObjectURL(f);
  $("file-name").textContent = f.name;
  $("dropzone").hidden = true;
  $("player").hidden = false;
  $("analyze-button").disabled = false;
  setStatus("Готово к анализу");
  showState("Видео загружено", "Нажмите «Анализировать», чтобы найти признаки возможных нарушений.");
}

async function start() {
  if (!getKey() && !DEMO) return $("key-dialog").showModal();
  const current = ++run;
  $("analyze-button").disabled = true;
  setStatus("Анализ…", "processing");
  showState("Анализируем видео", "Обычно это занимает до 3 минут.", true);
  try {
    const result = await analyze();
    if (current === run) showResult(result);
  } catch (err) {
    if (current !== run) return;
    setStatus("Ошибка", "failed");
    showState(err.status === 401 ? "Неверный API-ключ" : "Ошибка анализа", err.message);
  } finally {
    if (current === run) $("analyze-button").disabled = false;
  }
}

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
video.onloadedmetadata = renderMarkers;
video.ontimeupdate = video.onseeked = () => {
  $("progress").style.width = `${(video.currentTime / video.duration) * 100 || 0}%`;
};
$("timeline").onclick = (e) => {
  const rect = $("timeline").getBoundingClientRect();
  if (video.duration) video.currentTime = ((e.clientX - rect.left) / rect.width) * video.duration;
};

$("key-dot").classList.toggle("set", Boolean(getKey()));
showState("Здесь появятся найденные эпизоды", "Загрузите видео и запустите анализ.");
