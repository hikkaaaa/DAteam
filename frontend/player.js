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

// ---------- ROI overlay: zones from config.yaml (scene.zones), normalized x1, y1, x2, y2 ----------

const ZONE_NAMES = { cash_desk: "Зона кассы", safe: "Сейф" };
let zones = null; // loaded once from GET /api/v1/zones

async function loadZones() {
  if (zones) return zones;
  if (DEMO) return zones = { cash_desk: [0.25, 0.35, 0.75, 0.95] };
  try { zones = await api("/zones"); } catch { zones = {}; }
  return zones;
}

function drawZones() {
  const layer = $("roi-layer");
  if (layer.hidden || !zones || !video.videoWidth) return;
  // The video is letterboxed (object-fit: contain): place boxes over the actual picture.
  const scale = Math.min(video.clientWidth / video.videoWidth, video.clientHeight / video.videoHeight);
  const w = video.videoWidth * scale, h = video.videoHeight * scale;
  const left = (video.clientWidth - w) / 2, top = (video.clientHeight - h) / 2;
  layer.replaceChildren(...Object.entries(zones).map(([name, [x1, y1, x2, y2]]) => el("div", {
    className: "roi-box", textContent: ZONE_NAMES[name] || name,
    style: `left:${left + x1 * w}px; top:${top + y1 * h}px; width:${(x2 - x1) * w}px; height:${(y2 - y1) * h}px`,
  })));
}

$("roi-toggle").onchange = async () => {
  const on = $("roi-toggle").checked;
  await loadZones();
  const empty = !Object.keys(zones).length;
  $("roi-layer").hidden = !on || empty;
  $("roi-note").hidden = !(on && empty);
  $("roi-note").textContent = "Зоны кассы ещё не размечены — задайте их в config.yaml → scene.zones.";
  drawZones();
};
video.addEventListener("loadedmetadata", drawZones);
new ResizeObserver(drawZones).observe(video);
