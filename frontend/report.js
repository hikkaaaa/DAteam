// PDF report: fills the hidden #report block and opens the browser's print dialog
// ("Сохранить как PDF"). Native printing keeps Cyrillic text sharp and works offline — no libraries.

function downloadReport() {
  const entry = current;
  if (!entry || entry.status !== "done") return;
  const date = (entry.analyzedAt || new Date()).toLocaleString("ru", { dateStyle: "long", timeStyle: "short" });
  const verdictText = violations.length ? `Обнаружено нарушений: ${violations.length}` : "Нарушений не обнаружено";
  const segment = entry.range ? `${fmt(entry.range.start)} – ${fmt(entry.range.end)}` : "всё видео";
  const logo = document.querySelector(".brand-logo")?.cloneNode() || document.querySelector(".brand-mark").cloneNode(true);

  $("report").replaceChildren(
    el("header", { className: "report-head" }, logo,
      el("div", {}, el("h1", { textContent: "Отчёт о проверке кассовой дисциплины" }),
        el("p", { textContent: "ForteBank · Кассовый контроль" }))),
    el("table", { className: "report-meta" }, ...[
      ["Дата проверки", date], ["Файл", entry.file.name], ["ID задачи (job_id)", entry.jobId || "—"],
      ["Проанализирован участок", segment],
    ].map(([k, v]) => el("tr", {}, el("th", { textContent: k }), el("td", { textContent: v })))),
    el("div", { className: `report-verdict ${violations.length ? "is-violation" : "is-clean"}`, textContent: verdictText }),
    violations.length ? el("table", { className: "report-events" },
      el("thead", {}, el("tr", {}, ...["№", "Таймкод", "Нарушение и описание", "Кадры", "Норма РК"]
        .map((h) => el("th", { textContent: h })))),
      el("tbody", {}, ...violations.map((v, i) => el("tr", {},
        el("td", { textContent: i + 1 }),
        el("td", { textContent: fmt(v.timecode_sec) }),
        el("td", {}, el("strong", { textContent: v.type }), el("p", { textContent: v.description }),
          v.extra.confidence != null && el("p", { className: "muted", textContent: `Уверенность AI: ${Math.round(v.extra.confidence * 100)}%` })),
        el("td", {}, ...(v.evidence_frames || []).map((f) => el("figure", {},
          el("img", { src: frameSrc(f) }), el("figcaption", { textContent: fmt(f.timestamp_sec) })))),
        el("td", {}, v.extra.rule
          ? el("div", {}, el("strong", { textContent: `${v.extra.rule.clause} ${v.extra.rule.document}` }),
              el("q", { textContent: v.extra.rule.quote }), el("p", { className: "muted", textContent: v.extra.rule.url }))
          : "см. описание"))))) : null,
    entry.notes?.length ? el("div", {},
      el("h2", { className: "report-section", textContent: "Ручные отметки проверяющего" }),
      el("table", { className: "report-events" },
        el("thead", {}, el("tr", {}, ...["Таймкод", "Тип", "Комментарий"].map((h) => el("th", { textContent: h })))),
        el("tbody", {}, ...entry.notes.map((n) => el("tr", {},
          el("td", { textContent: n.timecode_sec != null ? fmt(n.timecode_sec) : "—" }),
          el("td", { textContent: n.type }), el("td", { textContent: n.text || "" })))))) : null,
    el("footer", { textContent: "Результат — подсказка проверяющему, а не юридическое заключение. " +
                                "Сформировано автоматически локальной AI-системой." }));

  const title = document.title;
  document.title = `Отчёт_${entry.file.name.replace(/\.[^.]+$/, "")}`; // default PDF file name
  window.print();
  document.title = title;
}

$("report-button").onclick = downloadReport;
