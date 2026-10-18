// Audit library (all local, see storage.js):
//   2.1 history search by violation type + one-click evidence review (training mode)
//   2.2 manual annotations (custom violation tags + notes), searchable like AI findings
//   2.3 custom categories, a session can belong to several
//   2.4 category feed: all videos of a category stacked in one scrollable stream with inline playback
// Uses globals from app.js (uploads, current, open, render, select, el, fmt, $ ...).

let categories = []; // { id, name, createdAt }
let selectedCategoryId = null;

// ---------- Persistence ----------

function toRecord(entry) {
  const { id, file, addedAt, status, result, details, jobId, elapsedSec, analyzedAt, range, notes, categoryIds,
          roi, roiOn, analyzedRoi, feedback, missed, trainable } = entry;
  return { id, file, addedAt, status, result, details, jobId, elapsedSec, analyzedAt, range, notes, categoryIds,
           roi, roiOn, analyzedRoi, feedback, missed, trainable };
}

async function saveSession(entry) {
  if (entry.status === "done") await dbPut("sessions", toRecord(entry));
}

function forgetSession(entry) {
  URL.revokeObjectURL(entry.url);
  dbDelete("sessions", entry.id);
}

async function loadLibrary() {
  const [sessions, cats] = await Promise.all([dbGetAll("sessions"), dbGetAll("categories")]);
  categories = cats.sort((a, b) => a.createdAt - b.createdAt);
  uploads = sessions
    .map((r) => ({ ...r, url: URL.createObjectURL(r.file), error: null, notes: r.notes || [], categoryIds: r.categoryIds || [] }))
    .sort((a, b) => b.addedAt - a.addedAt);
}

// ---------- Tags & search ----------

const isDone = (entry) => entry.status === "done";
const categoryName = (id) => categories.find((c) => c.id === id)?.name;

// Violation labels of a session: AI findings + manual annotations.
function tagsOf(entry) {
  if (!isDone(entry)) return [];
  return [...new Set([...(entry.result.violations || []).map((v) => v.type), ...entry.notes.map((n) => n.type)])];
}

function searchText(entry) {
  return [entry.file.name, ...tagsOf(entry), ...entry.notes.map((n) => n.text),
          ...entry.categoryIds.map(categoryName)].join(" ").toLowerCase();
}

// ---------- 2.1 History page: list with search (left), selected session (right) ----------

let selectedHistoryId = null;

function renderHistory() {
  $("history-count").textContent = uploads.length || "";
  const q = $("history-search").value.trim().toLowerCase();
  const found = uploads.filter((entry) => !q || searchText(entry).includes(q));
  if (!found.some((e) => e.id === selectedHistoryId)) selectedHistoryId = found[0]?.id ?? null;
  $("history-summary").textContent = q
    ? `Найдено сессий: ${found.length}`
    : `Сохранено сессий: ${uploads.filter(isDone).length} из ${HISTORY_LIMIT} · только в этом браузере`;

  $("history-list").replaceChildren(...(found.length ? found.map((entry) => {
    const [label, kind] = statusOf(entry);
    const date = entry.addedAt.toLocaleString("ru", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
    return el("div", { className: `history-row ${entry.id === selectedHistoryId ? "active" : ""}` },
      el("button", { type: "button", className: "history-item",
                     onclick: () => { selectedHistoryId = entry.id; renderHistory(); } },
        el("span", { className: "history-line" },
          el("span", { className: "history-name", textContent: entry.file.name }),
          el("span", { className: kind ? `pill is-${kind}` : "pill", textContent: label })),
        el("span", { className: "muted", textContent: date }),
        (tagsOf(entry).length || entry.categoryIds.length) && el("span", { className: "chips" },
          ...tagsOf(entry).map((t) => el("span", { className: `chip ${q && t.toLowerCase().includes(q) ? "hit" : ""}`, textContent: t })),
          ...entry.categoryIds.map((id) => el("span", { className: "chip category" }, icon("folder"), categoryName(id) || "—")))),
      el("button", { type: "button", className: "icon-button danger", title: "Удалить из истории",
                     onclick: () => removeSession(entry) }, icon("trash")));
  }) : [el("p", { className: "muted", textContent: q ? "Ничего не найдено." : "Вы ещё не загружали видео." })]));
  renderHistoryDetail(q);
}

// The selected session, shown right on the history page (video, findings, frames, laws, notes, categories).
// Training mode: with a search query, matching findings are highlighted and the video starts at the first one.
function renderHistoryDetail(q) {
  const entry = uploads.find((u) => u.id === selectedHistoryId);
  // Don't rebuild (and restart the video) when nothing visible changed.
  const signature = entry && [entry.id, q, entry.status, entry.notes.length, entry.categoryIds.join(),
                              categories.map((c) => c.name).join()].join("|");
  if (signature && $("history-detail").dataset.signature === signature) return;
  $("history-detail").dataset.signature = signature || "";
  if (!entry) {
    $("history-detail").replaceChildren(el("div", { className: "state" },
      el("p", { textContent: "История пуста" }),
      el("p", { textContent: "Проанализированные видео сохраняются здесь вместе с результатами." })));
    return;
  }
  $("history-detail").replaceChildren(...[isDone(entry) && renderCategoryBar(entry), renderFeedItem(entry, q)].filter(Boolean));
}

function removeSession(entry) {
  if (!confirm(`Удалить «${entry.file.name}» из истории? Видео и отметки будут удалены из браузера.`)) return;
  uploads = uploads.filter((e) => e !== entry);
  forgetSession(entry);
  if (entry === current) goHome();
  renderHistory();
  if (!$("categories-view").hidden) renderCategories();
}

$("history-search").oninput = renderHistory;

// ---------- 2.2 Manual annotations ----------

function renderNotes(entry) {
  return el("section", { className: "notes" },
    el("div", { className: "section-head" },
      el("h3", {}, icon("pencil", "c-violet"), "Ручные отметки"),
      el("button", { type: "button", className: "link add", onclick: () => openNoteDialog(entry) }, icon("plus"), "Добавить")),
    ...(entry.notes.length ? [...entry.notes].sort((a, b) => (a.timecode_sec ?? 1e9) - (b.timecode_sec ?? 1e9)).map((n) =>
      el("article", { className: "note" },
        el("div", { className: "violation-top" },
          n.timecode_sec != null
            ? el("button", { type: "button", className: "timecode manual", textContent: fmt(n.timecode_sec),
                             onclick: () => { video.currentTime = n.timecode_sec; } })
            : el("span", { className: "muted", textContent: "без таймкода" }),
          el("span", { className: "badge", textContent: "вручную" }),
          el("button", { type: "button", className: "icon-button danger", title: "Удалить отметку",
                         onclick: () => deleteNote(entry, n) }, icon("trash"))),
        el("h3", { textContent: n.type }),
        n.text && el("p", { textContent: n.text })))
      : [el("p", { className: "muted", textContent: "Добавьте свою отметку: тип нарушения, момент и комментарий. Она сразу попадёт в поиск по истории." })]));
}

function openNoteDialog(entry) {
  $("note-type").value = "";
  $("note-text").value = "";
  $("note-time").value = fmt(video.currentTime || 0);
  $("note-no-time").checked = false;
  $("note-time").disabled = false;
  // Suggest labels already used anywhere in the library.
  $("known-types").replaceChildren(...[...new Set(uploads.flatMap(tagsOf))].map((t) => el("option", { value: t })));
  $("note-dialog").dataset.entryId = entry.id;
  $("note-dialog").showModal();
}

// "1:23.4" or "83.4" -> seconds
function parseTime(text) {
  const parts = text.trim().replace(",", ".").split(":").map(Number);
  if (parts.some(Number.isNaN)) return null;
  return parts.reduce((acc, p) => acc * 60 + p, 0);
}

$("note-no-time").onchange = () => { $("note-time").disabled = $("note-no-time").checked; };
$("note-form").onsubmit = (e) => {
  if (e.submitter?.value !== "save") return;
  const entry = uploads.find((u) => u.id === $("note-dialog").dataset.entryId);
  const type = $("note-type").value.trim();
  if (!entry || !type) { e.preventDefault(); $("note-type").focus(); return; }
  const timecode = $("note-no-time").checked ? null : parseTime($("note-time").value);
  entry.notes.push({ id: newId(), type, timecode_sec: timecode, text: $("note-text").value.trim(), createdAt: new Date() });
  saveSession(entry);
  if (entry === current) render(entry);
  renderHistory();
};

function deleteNote(entry, note) {
  entry.notes = entry.notes.filter((n) => n !== note);
  saveSession(entry);
  if (entry === current) render(entry);
}

// ---------- 2.3 Categories ----------

function renderCategoryBar(entry) {
  return el("div", { className: "category-bar" },
    icon("folder", "c-rose"),
    ...entry.categoryIds.map((id) => el("button", { type: "button", className: "chip category",
      textContent: categoryName(id) || "—", onclick: () => openCategory(id) })),
    el("button", { type: "button", className: "link add", onclick: () => openAssignDialog(entry) },
      icon("plus"), entry.categoryIds.length ? "Изменить" : "В категорию"));
}

async function createCategory(name) {
  name = name.trim();
  if (!name) return null;
  const existing = categories.find((c) => c.name.toLowerCase() === name.toLowerCase());
  if (existing) return existing;
  const category = { id: newId(), name, createdAt: Date.now() };
  categories.push(category);
  await dbPut("categories", category);
  return category;
}

function openAssignDialog(entry) {
  $("category-dialog").dataset.entryId = entry.id;
  $("category-new").value = "";
  renderAssignList(entry.categoryIds);
  $("category-dialog").showModal();
}

function renderAssignList(checkedIds) {
  $("category-options").replaceChildren(...(categories.length ? categories.map((c) =>
    el("label", { className: "check" },
      el("input", { type: "checkbox", value: c.id, checked: checkedIds.includes(c.id) }),
      el("span", { textContent: c.name })))
    : [el("p", { className: "muted", textContent: "Категорий пока нет — создайте первую ниже." })]));
}

$("category-add").onclick = async () => {
  const checked = [...$("category-options").querySelectorAll("input:checked")].map((i) => i.value);
  const category = await createCategory($("category-new").value);
  if (!category) return;
  $("category-new").value = "";
  renderAssignList([...checked, category.id]);
};
$("category-new").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("category-add").click(); } };
$("category-form").onsubmit = (e) => {
  if (e.submitter?.value !== "save") return;
  const entry = uploads.find((u) => u.id === $("category-dialog").dataset.entryId);
  if (!entry) return;
  entry.categoryIds = [...$("category-options").querySelectorAll("input:checked")].map((i) => i.value);
  saveSession(entry);
  if (entry === current) render(entry);
  renderHistory();
};

// ---------- Categories view: management (left) + feed (right) ----------

const sessionsIn = (id) => uploads.filter((u) => isDone(u) && u.categoryIds.includes(id));

function openCategory(id) {
  selectedCategoryId = id;
  showView("categories-view");
}

function renderCategories() {
  if (!categories.some((c) => c.id === selectedCategoryId)) selectedCategoryId = categories[0]?.id ?? null;
  $("category-list").replaceChildren(...(categories.length ? categories.map((c) => {
    const name = el("span", { className: "category-name", textContent: c.name });
    const row = el("div", { className: `category-row ${c.id === selectedCategoryId ? "active" : ""}`,
                            onclick: () => { selectedCategoryId = c.id; renderCategories(); } },
      icon("folder", "c-rose"), name,
      el("span", { className: "count", textContent: sessionsIn(c.id).length }),
      el("button", { type: "button", className: "icon-button", title: "Переименовать",
                     onclick: (e) => { e.stopPropagation(); startRename(c, name); } }, icon("pencil")),
      el("button", { type: "button", className: "icon-button danger", title: "Удалить категорию",
                     onclick: (e) => { e.stopPropagation(); deleteCategory(c); } }, icon("trash")));
    return row;
  }) : [el("p", { className: "muted", textContent: "Создайте первую категорию, например «Ночные смены»." })]));
  renderFeed();
}

function startRename(category, nameNode) {
  const input = el("input", { className: "rename", value: category.name, onclick: (e) => e.stopPropagation() });
  const finish = async (save) => {
    if (save && input.value.trim()) {
      category.name = input.value.trim();
      await dbPut("categories", category);
    }
    renderCategories();
    renderHistory();
  };
  input.onkeydown = (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); };
  input.onblur = () => finish(true);
  nameNode.replaceWith(input);
  input.focus();
  input.select();
}

async function deleteCategory(category) {
  if (!confirm(`Удалить категорию «${category.name}»? Видео останутся в истории.`)) return;
  categories = categories.filter((c) => c !== category);
  await dbDelete("categories", category.id);
  for (const entry of uploads.filter((u) => u.categoryIds.includes(category.id))) {
    entry.categoryIds = entry.categoryIds.filter((id) => id !== category.id);
    saveSession(entry);
  }
  renderCategories();
  renderHistory();
}

$("category-create").onsubmit = async (e) => {
  e.preventDefault();
  const category = await createCategory($("category-create-name").value);
  if (!category) return;
  $("category-create-name").value = "";
  selectedCategoryId = category.id;
  renderCategories();
};

// ---------- 2.4 Category feed: every video of the category, one after another ----------

function renderFeed() {
  const category = categories.find((c) => c.id === selectedCategoryId);
  if (!category) {
    $("category-feed").replaceChildren(el("div", { className: "state" },
      el("p", { textContent: "Лента категории" }),
      el("p", { textContent: "Выберите или создайте категорию. Добавлять видео можно из результата анализа: «В категорию»." })));
    return;
  }
  const sessions = sessionsIn(category.id);
  $("category-feed").replaceChildren(
    el("div", { className: "feed-head" },
      el("h2", { textContent: category.name }),
      el("span", { className: "muted", textContent: `Видео: ${sessions.length}` })),
    ...(sessions.length ? sessions.map(renderFeedItem) : [el("div", { className: "state" },
      el("p", { textContent: "В категории пока нет видео" }),
      el("p", { textContent: "Откройте проанализированное видео и нажмите «В категорию»." }))]));
}

function renderFeedItem(entry, q = "") {
  const [label, kind] = statusOf(entry);
  const head = el("div", { className: "feed-item-head" },
    el("div", {},
      el("strong", { textContent: entry.file.name }),
      el("p", { className: "muted", textContent: entry.addedAt.toLocaleString("ru", { dateStyle: "medium", timeStyle: "short" }) })),
    el("span", { className: kind ? `pill is-${kind}` : "pill", textContent: label }),
    el("button", { type: "button", className: "button ghost", textContent: "Открыть в анализаторе",
                   onclick: () => { showView("analyzer-view"); open(entry); } }));
  const player = el("video", { className: "feed-video", controls: true, preload: "metadata", src: entry.url });
  player.addEventListener("play", () => document.querySelectorAll(".feed-video").forEach((v) => v !== player && v.pause()));
  const seek = (t) => { player.currentTime = Math.max(0, t - 2); player.play(); };
  if (!isDone(entry)) {
    return el("article", { className: "feed-item" }, head, player,
      el("p", { className: "muted", textContent: "Видео ещё не проанализировано — откройте его в анализаторе и нажмите «Анализировать»." }));
  }

  const ai = (entry.result.violations || []).map((v, i) => ({ ...v, extra: entry.details?.[i] || {} }));
  const events = [
    ...ai.map((v) => ({ t: v.timecode_sec, type: v.type, text: v.description, frames: v.evidence_frames || [], rule: v.extra.rule })),
    ...entry.notes.map((n) => ({ t: n.timecode_sec, type: n.type, text: n.text, frames: [], manual: true })),
  ].sort((a, b) => (a.t ?? 1e9) - (b.t ?? 1e9));
  const isHit = (ev) => q && `${ev.type} ${ev.text || ""}`.toLowerCase().includes(q);
  const firstHit = events.find((ev) => isHit(ev) && ev.t != null);
  if (firstHit) player.addEventListener("loadedmetadata", () => { player.currentTime = Math.max(0, firstHit.t - 2); }, { once: true });

  return el("article", { className: "feed-item" }, head, player,
    el("div", { className: "feed-events" }, ...(events.length ? events.map((ev) =>
      el("div", { className: `feed-event ${isHit(ev) ? "hit" : ""}` },
        ev.t != null
          ? el("button", { type: "button", className: `timecode ${ev.manual ? "manual" : ""}`, textContent: fmt(ev.t), onclick: () => seek(ev.t) })
          : el("span", { className: "muted", textContent: "—" }),
        el("div", {},
          el("strong", { textContent: ev.type }), ev.manual && el("span", { className: "badge", textContent: "вручную" }),
          ev.text && el("p", { textContent: ev.text }),
          ev.rule && el("p", { className: "muted", title: ev.rule.document },
            icon("scale", "c-violet"), ` ${ev.rule.clause} · ${ev.rule.document_short || ev.rule.document}`),
          ev.frames.length > 0 && el("div", { className: "evidence" }, ...ev.frames.map((f) =>
            el("img", { src: frameSrc(f), alt: "", title: fmt(f.timestamp_sec), onclick: () => openFrame(f, ev.frames) }))))))
      : [el("p", { className: "muted", textContent: "Нарушений не найдено, ручных отметок нет." })])));
}
