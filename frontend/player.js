// Audit player tools: frame step, speed, episode playback, previous/next violation, hotkeys,
// frame preview on timeline hover, ROI (cash desk zone) overlay. Uses globals from app.js.

const FRAME_SEC = 1 / 30; // the browser doesn't expose the video's fps; 30 fps is the usual camera rate

// ---------- Playback ----------

const togglePlay = () => (video.paused ? video.play() : video.pause());
const stepFrame = (dir) => { video.pause(); video.currentTime = Math.max(0, video.currentTime + dir * FRAME_SEC); };

let episodeEnd = null;
// Play one violation: from its interval start (or 2 s before) to its end (or 3 s after), then pause.
function playEpisode(i) {
  const v = violations[i];
  select(i);
  video.currentTime = Math.max(0, v.extra.start_sec ?? v.timecode_sec - 2);
  episodeEnd = v.extra.end_sec ?? v.timecode_sec + 3;
  video.play();
}
video.addEventListener("timeupdate", () => {
  if (episodeEnd != null && video.currentTime >= episodeEnd) { video.pause(); episodeEnd = null; }
});
video.addEventListener("seeking", () => { if (!video.paused) return; episodeEnd = null; });

function jumpViolation(dir) {
  if (!violations.length) return;
  let i = selected + dir;
  if (selected < 0) i = dir > 0 ? Math.max(0, violations.findIndex((v) => v.timecode_sec > video.currentTime)) : violations.length - 1;
  select(Math.min(violations.length - 1, Math.max(0, i)));
}

$("play-toggle").onclick = togglePlay;
$("frame-back").onclick = () => stepFrame(-1);
$("frame-forward").onclick = () => stepFrame(1);
$("prev-violation").onclick = () => jumpViolation(-1);
$("next-violation").onclick = () => jumpViolation(1);
const setPlayIcon = (name) => $("play-icon").replaceWith(Object.assign(icon(name), { id: "play-icon" }));
video.addEventListener("play", () => setPlayIcon("pause"));
video.addEventListener("pause", () => setPlayIcon("play"));

document.querySelectorAll(".speed").forEach((button) => button.onclick = () => {
  video.playbackRate = Number(button.dataset.rate);
  document.querySelectorAll(".speed").forEach((b) => b.classList.toggle("active", b === button));
});
video.addEventListener("loadedmetadata", () => {
  video.playbackRate = Number(document.querySelector(".speed.active").dataset.rate);
});

// Hotkeys: Space play/pause · ←/→ previous/next violation · , / . one frame back/forward.
document.addEventListener("keydown", (e) => {
  if ($("player").hidden || $("analyzer-view").hidden || document.querySelector("dialog[open]")) return;
  if (e.target.closest("input, textarea, select, button") && e.key === " ") return;
  if (e.target.closest("input, textarea")) return;
  const actions = { " ": togglePlay, ArrowLeft: () => jumpViolation(-1), ArrowRight: () => jumpViolation(1),
                    ",": () => stepFrame(-1), ".": () => stepFrame(1) };
  if (actions[e.key]) { e.preventDefault(); actions[e.key](); }
});

// ---------- Frame grabbing (timeline preview, demo evidence) ----------

const previewVideo = Object.assign(document.createElement("video"), { muted: true, preload: "auto" });

// Returns a JPEG (base64, no "data:" prefix) of the current video at time `t`, or null.
async function captureFrame(t) {
  const grab = Object.assign(document.createElement("video"), { muted: true, preload: "auto", src: video.currentSrc });
  const once = (type) => new Promise((resolve) => grab.addEventListener(type, resolve, { once: true }));
  const timeout = new Promise((resolve) => setTimeout(() => resolve(null), 3000));
  return Promise.race([timeout, (async () => {
    await once("loadeddata");
    grab.currentTime = t;
    await once("seeked");
    const canvas = Object.assign(document.createElement("canvas"), { width: grab.videoWidth, height: grab.videoHeight });
    canvas.getContext("2d").drawImage(grab, 0, 0);
    return canvas.toDataURL("image/jpeg", 0.85).split(",")[1];
  })().catch(() => null)]);
}

// Small frame preview above the timeline while hovering it.
let previewBusy = false;
let previewWanted = null;
function seekPreview(t) {
  if (previewBusy) { previewWanted = t; return; }
  previewBusy = true;
  previewVideo.currentTime = t;
}
previewVideo.addEventListener("seeked", () => {
  $("hover-canvas").getContext("2d").drawImage(previewVideo, 0, 0, 160, 90);
  previewBusy = false;
  if (previewWanted != null) { const t = previewWanted; previewWanted = null; seekPreview(t); }
});
video.addEventListener("loadedmetadata", () => { previewVideo.src = video.currentSrc; });

$("timeline").addEventListener("pointermove", (e) => {
  if (!video.duration || drag?.moved) return $("hover-preview").hidden = true;
  const rect = $("timeline").getBoundingClientRect();
  const x = Math.min(rect.width - 80, Math.max(80, e.clientX - rect.left));
  const t = timeAt(e.clientX);
  $("hover-preview").style.left = `${x}px`;
  $("hover-time").textContent = fmt(t);
  $("hover-preview").hidden = false;
  seekPreview(t);
});
$("timeline").addEventListener("pointerleave", () => { $("hover-preview").hidden = true; });

// ---------- ROI: the cash desk zone, drawn with the mouse per video ----------
// entry.roi = [x1, y1, x2, y2] in 0..1 of the frame, entry.roiOn = the switch. When both are set,
// the analysis sends `roi` and the backend crops the video to it before the ML server (experimental).

const ROI_MIN = 0.05; // same as config.yaml → roi.min_side

// The video is letterboxed (object-fit: contain): the layer covers exactly the picture.
function placeRoiLayer() {
  const layer = $("roi-layer");
  if (!video.videoWidth) return;
  const scale = Math.min(video.clientWidth / video.videoWidth, video.clientHeight / video.videoHeight);
  const w = video.videoWidth * scale, h = video.videoHeight * scale;
  Object.assign(layer.style, { left: `${(video.clientWidth - w) / 2}px`, top: `${(video.clientHeight - h) / 2}px`,
                               width: `${w}px`, height: `${h}px` });
}

function updateRoi() {
  const on = Boolean(current?.roiOn), roi = current?.roi;
  $("roi-toggle").checked = on;
  $("roi-layer").hidden = !on;
  placeRoiLayer();
  const pct = (v) => `${Math.round(v * 100)}%`;
  $("roi-layer").replaceChildren(roi
    ? el("div", { className: roi[1] < 0.06 ? "roi-box at-top" : "roi-box", style: `left:${pct(roi[0])}; top:${pct(roi[1])}; width:${pct(roi[2] - roi[0])}; height:${pct(roi[3] - roi[1])}` },
        el("span", { className: "roi-label" }, icon("scan"), "Зона кассы"),
        ...["nw", "ne", "sw", "se"].map((corner) => {
          const handle = el("span", { className: `roi-handle ${corner}` });
          handle.dataset.corner = corner;
          return handle;
        }))
    : el("div", { className: "roi-hint" }, el("span", { className: "roi-hint-chip" }, icon("scan"), "Обведите стол кассира")));
  $("roi-note").hidden = !on;
  $("roi-note").replaceChildren(...(roi
    ? [icon("scan", "c-amber"), "Анализ пойдёт только по выделенной зоне — видео обрезается перед отправкой в модель. Экспериментально. ",
       el("button", { className: "link", type: "button", textContent: "Сбросить зону", onclick: () => setRoi(null) })]
    : [icon("scan", "c-amber"), "Нарисуйте прямоугольник на видео: только эта область пойдёт в анализ."]));
}

function setRoi(roi) {
  current.roi = roi;
  updateRoi();
  saveSession(current);
}

// Drawing: drag on empty space = new zone, drag inside = move, drag a corner = resize.
let roiDrag = null;
$("roi-layer").addEventListener("pointerdown", (e) => {
  const box = $("roi-layer").getBoundingClientRect();
  const at = (ev) => [Math.min(1, Math.max(0, (ev.clientX - box.left) / box.width)),
                      Math.min(1, Math.max(0, (ev.clientY - box.top) / box.height))];
  const [x, y] = at(e), start = current.roi;
  const corner = e.target.dataset.corner;
  const mode = corner ? "resize" : e.target.closest(".roi-box") ? "move" : "draw";
  roiDrag = { at, x, y, start, mode, corner };
  $("roi-layer").setPointerCapture(e.pointerId);
  $("roi-layer").classList.add("dragging");
  e.preventDefault();
});
$("roi-layer").addEventListener("pointermove", (e) => {
  if (!roiDrag) return;
  const { at, x, y, start, mode, corner } = roiDrag;
  const [px, py] = at(e);
  let roi;
  if (mode === "draw") roi = [Math.min(x, px), Math.min(y, py), Math.max(x, px), Math.max(y, py)];
  if (mode === "move") {
    const w = start[2] - start[0], h = start[3] - start[1];
    const nx = Math.min(1 - w, Math.max(0, start[0] + px - x)), ny = Math.min(1 - h, Math.max(0, start[1] + py - y));
    roi = [nx, ny, nx + w, ny + h];
  }
  if (mode === "resize") {
    roi = [...start];
    if (corner.includes("w")) roi[0] = Math.min(px, start[2] - ROI_MIN); else roi[2] = Math.max(px, start[0] + ROI_MIN);
    if (corner.includes("n")) roi[1] = Math.min(py, start[3] - ROI_MIN); else roi[3] = Math.max(py, start[1] + ROI_MIN);
  }
  current.roi = roi;
  updateRoi();
});
$("roi-layer").addEventListener("pointerup", () => {
  if (!roiDrag) return;
  const roi = current.roi;
  // a click or a too small box keeps the previous zone
  if (roi && (roi[2] - roi[0] < ROI_MIN || roi[3] - roi[1] < ROI_MIN)) current.roi = roiDrag.start;
  roiDrag = null;
  $("roi-layer").classList.remove("dragging");
  setRoi(current.roi);
});

$("roi-toggle").onchange = () => {
  current.roiOn = $("roi-toggle").checked;
  updateRoi();
  saveSession(current);
};
video.addEventListener("loadedmetadata", placeRoiLayer);
new ResizeObserver(placeRoiLayer).observe(video);
