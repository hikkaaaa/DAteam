// Reviewer feedback on the model ("Верно" / "Неверно" / "Модель пропустила") and the "Обратная связь" page.
// Each verdict goes to POST /api/v1/feedback and is stored on the server (storage/feedback/), so the ML team
// can download it as a dataset and re-train the model. A copy stays on the entry (saved in the browser):
//   entry.feedback = { [violation index]: { verdict, comment, trainable } },  entry.missed = [{ id, type, timecode_sec, ... }]

async function sendFeedback(body) {
  const res = await fetch("/api/v1/feedback", {
    method: "POST", headers: { "X-API-Key": getKey(), "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error(res.status === 401 ? "Нужен верный API-ключ" : `Не удалось отправить (${res.status})`);
}

const findingId = (entry, idx) => `${entry.id}:${idx}`;

// Redraw the result after a vote without jumping back to the top of the list.
function refreshResult(entry) {
  if (entry !== current || entry.status !== "done") return;
  const top = $("results-body").scrollTop;
  showResult(entry);
  $("results-body").scrollTop = top;
}

function findingPayload(entry, v, extra) {
  return { id: findingId(entry, v.idx), kind: "finding", job_id: entry.jobId, file_name: entry.file.name,
           type: v.type, timecode_sec: v.timecode_sec, description: v.description,
           frame_base64: v.evidence_frames?.[0]?.image_base64 || null, ...extra };
}

// Under each violation card: was the model right?
function renderFeedbackBar(entry, v) {
  const fb = entry.feedback?.[v.idx];
  const vote = (verdict) => async (e) => {
    e.stopPropagation();
    if (verdict === "incorrect") return openFeedbackDialog(entry, v);
    if (fb?.verdict === "correct") { // second click on "Верно" takes the vote back
      await sendFeedback(findingPayload(entry, v, { verdict: "correct", deleted: true })).catch(() => {});
      delete entry.feedback[v.idx];
    } else {
      try { await sendFeedback(findingPayload(entry, v, { verdict: "correct", trainable: entry.trainable || false })); }
      catch (err) { return flashError(e.target.closest(".fb-bar"), err.message); }
      (entry.feedback ||= {})[v.idx] = { verdict: "correct", comment: "", trainable: entry.trainable || false };
    }
    saveSession(entry);
    refreshResult(entry);
  };
  return el("div", { className: `fb-bar ${fb ? `is-${fb.verdict}` : ""}`, onclick: (e) => e.stopPropagation() },
    el("span", { className: "fb-question", textContent: fb ? (fb.verdict === "correct" ? "Отмечено как верное" : "Отмечено как неверное")
                                                        : "Модель права?" }),
    el("div", { className: "fb-buttons" },
      el("button", { type: "button", className: `fb-button ok ${fb?.verdict === "correct" ? "active" : ""}`, onclick: vote("correct") },
        icon("thumbsUp"), "Верно"),
      el("button", { type: "button", className: `fb-button bad ${fb?.verdict === "incorrect" ? "active" : ""}`, onclick: vote("incorrect") },
        icon("thumbsDown"), "Неверно")),
    fb?.comment && el("p", { className: "fb-comment" }, icon("messageSquare"), fb.comment));
}

// Under the verdict: report a violation the model didn't find.
function renderMissedButton(entry) {
  const count = entry.missed?.length || 0;
  return el("button", { type: "button", className: "fb-missed", onclick: () => openFeedbackDialog(entry, null) },
    icon("searchX"), count ? `Отмечено пропусков: ${count} · добавить ещё` : "Модель что-то пропустила?");
}

function flashError(node, text) {
  node.querySelector(".fb-flash")?.remove();
  node.append(el("p", { className: "fb-flash fb-error", textContent: text }));
}

// One dialog for both: "Неверно" on a finding (v) and "Модель пропустила" (v = null).
function openFeedbackDialog(entry, v) {
  const missed = !v;
  const old = v && entry.feedback?.[v.idx];
  $("fb-title").textContent = missed ? "Модель пропустила нарушение" : "Ответ модели неверный";
  $("fb-sub").textContent = missed ? "Укажите, что и когда произошло. Кадр на этом моменте приложится автоматически."
                                   : `«${v.type}» на ${fmt(v.timecode_sec)}. Что на самом деле видно на видео?`;
  $("fb-missed-fields").hidden = !missed;
  $("fb-type").value = "";
  $("fb-time").value = fmt(video.currentTime || 0);
  $("fb-comment").value = old?.comment || "";
  $("fb-trainable").checked = entry.trainable || false;
  $("fb-error").hidden = true;
  $("fb-types").replaceChildren(...(normsBase?.violation_types || []).map((t) => el("option", { value: t.type })));
  feedbackTarget = { entry, v };
  $("feedback-dialog").showModal();
  (missed ? $("fb-type") : $("fb-comment")).focus();
}

let feedbackTarget = null;
$("feedback-form").onsubmit = async (e) => {
  if (e.submitter?.value !== "save") return;
  e.preventDefault(); // close only after the server saved it
  const { entry, v } = feedbackTarget;
  const comment = $("fb-comment").value.trim(), trainable = $("fb-trainable").checked;
  const fail = (text) => { $("fb-error").textContent = text; $("fb-error").hidden = false; };
  try {
    if (v) {
      await sendFeedback(findingPayload(entry, v, { verdict: "incorrect", comment, trainable }));
      (entry.feedback ||= {})[v.idx] = { verdict: "incorrect", comment, trainable };
    } else {
      const type = $("fb-type").value.trim(), t = parseTime($("fb-time").value);
      if (!type) return fail("Укажите тип нарушения.");
      if (t == null) return fail("Таймкод в формате 0:44.2 или 44.2");
      const report = { id: `${entry.id}:missed:${newId()}`, kind: "missed", verdict: "missed", type, timecode_sec: t,
                       comment, trainable, job_id: entry.jobId, file_name: entry.file.name };
      await sendFeedback({ ...report, frame_base64: await captureFrame(t) });
      (entry.missed ||= []).push(report);
    }
  } catch (err) {
    return fail(err.message);
  }
  entry.trainable = trainable; // remember the choice for this video
  $("feedback-dialog").close();
  saveSession(entry);
  refreshResult(entry);
};

// ---------- "Обратная связь" page ----------

const VERDICTS = { correct: ["Верно", "ok", "thumbsUp"], incorrect: ["Неверно", "bad", "thumbsDown"], missed: ["Пропущено", "miss", "searchX"] };

async function renderFeedbackPage() {
  if (!getKey()) {
    $("feedback-stats").replaceChildren();
    $("feedback-body").replaceChildren(normsState("Нужен API-ключ", "Введите ключ кнопкой «API-ключ» вверху."));
    return;
  }
  let s;
  try { s = await api("/feedback"); }
  catch (err) { $("feedback-body").replaceChildren(normsState("Не удалось загрузить отзывы", err.message)); return; }
  const rated = s.correct + s.incorrect;
  const stat = (value, label, cls, i) => el("div", { className: `fb-stat ${cls}`, style: `animation-delay: ${i * 70}ms` },
    el("strong", { textContent: value }), el("span", { textContent: label }));
  $("feedback-stats").replaceChildren(
    stat(s.total, "отзывов всего", "", 0),
    stat(rated ? `${Math.round(s.correct / rated * 100)}%` : "—", "находок модели верны", "ok", 1),
    stat(s.incorrect, "неверных находок", "bad", 2),
    stat(s.missed, "пропущенных нарушений", "miss", 3),
    stat(s.trainable, "можно в обучение", "train", 4));
  if (!s.total) {
    $("feedback-body").replaceChildren(normsState("Отзывов пока нет",
      "Откройте результат анализа и нажмите «Верно» / «Неверно» под нарушением или «Модель что-то пропустила?»."));
    return;
  }
  const max = Math.max(...s.per_type.map((t) => t.correct + t.incorrect + t.missed));
  $("feedback-body").replaceChildren(
    el("section", { className: "fb-section" },
      el("h3", { textContent: "По типам нарушений" }),
      ...s.per_type.map((t) => {
        const n = t.correct + t.incorrect + t.missed;
        return el("div", { className: "fb-type" },
          el("div", { className: "fb-type-head" },
            el("strong", { textContent: t.type }),
            el("span", { className: "muted", textContent: [t.correct && `верно ${t.correct}`, t.incorrect && `неверно ${t.incorrect}`,
                                                          t.missed && `пропущено ${t.missed}`].filter(Boolean).join(" · ") })),
          // grey rail = the most reviewed type; the coloured part = this type's share of it
          el("div", { className: "fb-rail" },
            el("div", { className: "fb-track", style: `width: ${Math.max(8, n / max * 100)}%` },
              ...["correct", "incorrect", "missed"].filter((k) => t[k]).map((k) =>
                el("span", { className: `fb-seg ${VERDICTS[k][1]}`, style: `flex: ${t[k]}` })))));
      })),
    el("section", { className: "fb-section" },
      el("h3", { textContent: "Все отзывы" }),
      el("div", { className: "fb-list" }, ...s.items.map(renderFeedbackItem))));
}

function renderFeedbackItem(r, i) {
  const [label, cls, name] = VERDICTS[r.verdict];
  return el("article", { className: "fb-item fade-in", style: `animation-delay: ${Math.min(i, 12) * 40}ms` },
    r.frame_base64 ? el("img", { className: "fb-thumb", src: `data:image/jpeg;base64,${r.frame_base64}`, alt: "",
                                 onclick: () => openFrame({ timestamp_sec: r.timecode_sec ?? 0, image_base64: r.frame_base64 }) })
                   : el("div", { className: "fb-thumb" }),
    el("div", { className: "fb-item-body" },
      el("div", { className: "fb-item-top" },
        el("span", { className: `fb-badge ${cls}` }, icon(name), label),
        r.timecode_sec != null && el("span", { className: "timecode", textContent: fmt(r.timecode_sec) }),
        r.trainable && el("span", { className: "chip", textContent: "в обучение" })),
      el("strong", { textContent: r.type || "—" }),
      r.comment && el("p", { className: "fb-item-comment", textContent: `«${r.comment}»` }),
      el("span", { className: "muted", textContent: `${r.file_name} · ${new Date(r.created_at * 1000).toLocaleString("ru",
        { dateStyle: "medium", timeStyle: "short" })}` })));
}

// The download needs the X-API-Key header, so fetch it and save the blob.
$("feedback-export").onclick = async () => {
  const res = await fetch("/api/v1/feedback/export", { headers: { "X-API-Key": getKey() } });
  if (!res.ok) return alert(res.status === 401 ? "Нужен верный API-ключ" : `Ошибка ${res.status}`);
  const url = URL.createObjectURL(await res.blob());
  el("a", { href: url, download: "feedback-dataset.zip" }).click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
