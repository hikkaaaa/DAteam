// "Фильтры" page: every video submitted so far (the saved library), with search, sorting
// and a filter by laws — a popup listing all norms from "Нормативная база РК" with its own search.

const lawFilter = new Set(); // selected norm ids

const SORTS = {
  newest: ["Сначала новые", (a, b) => b.addedAt - a.addedAt],
  oldest: ["Сначала старые", (a, b) => a.addedAt - b.addedAt],
  az: ["По алфавиту А → Я", (a, b) => a.file.name.localeCompare(b.file.name, "ru")],
  za: ["По алфавиту Я → А", (a, b) => b.file.name.localeCompare(a.file.name, "ru")],
  most: ["Больше нарушений", (a, b) => violationCount(b) - violationCount(a) || b.addedAt - a.addedAt],
  least: ["Меньше нарушений", (a, b) => violationCount(a) - violationCount(b) || b.addedAt - a.addedAt],
};

const violationCount = (entry) => (isDone(entry) ? (entry.result.violations?.length || 0) + entry.notes.length : -1);

// Norms a session is linked to: the norms of its AI findings (from /details) plus the norms of every
// violation type whose name matches an AI finding or a manual annotation.
function normIdsOf(entry) {
  if (!isDone(entry)) return new Set();
  const ids = new Set();
  for (const d of entry.details || []) {
    if (d.rule?.id) ids.add(d.rule.id);
    for (const other of d.other_norms || []) ids.add(other.id);
  }
  const byName = new Map((normsBase?.violation_types || []).map((t) => [t.type.toLowerCase(), t.norms]));
  for (const name of tagsOf(entry)) for (const id of byName.get(name.toLowerCase()) || []) ids.add(id);
  return ids;
}

const normById = (id) => normsBase?.norms.find((n) => n.id === id);
const lawLabel = (n) => `${n.clause.split(" (")[0]} · ${n.document_short}`; // "п. 22 · Правила № 231"

async function renderFilters() {
  $("filter-sort").replaceChildren(...Object.entries(SORTS).map(([key, [label]]) =>
    el("option", { value: key, textContent: label, selected: key === ($("filter-sort").value || "newest") })));
  if (getKey() && !normsBase) { try { await loadNormsBase(); } catch {} }
  renderLawChips();
  renderVideoList();
}

function renderVideoList() {
  const q = $("filter-search").value.trim().toLowerCase();
  const sort = SORTS[$("filter-sort").value || "newest"][1];
  const found = uploads
    .filter((e) => !q || searchText(e).includes(q))
    .filter((e) => !lawFilter.size || [...lawFilter].every((id) => normIdsOf(e).has(id)))
    .sort(sort);
  $("filter-summary").textContent = `Видео: ${found.length} из ${uploads.length}`;

  if (!uploads.length) {
    $("filter-list").replaceChildren(normsState("Видео пока нет", "Загрузите и проанализируйте видео — оно появится здесь."));
    return;
  }
  $("filter-list").replaceChildren(...(found.length ? found.map(renderVideoCard)
    : [normsState("Ничего не найдено", "Измените поиск или снимите часть фильтров по законам.")]));
}

function renderVideoCard(entry) {
  const [label, kind] = statusOf(entry);
  const frame = entry.result?.violations?.find((v) => v.evidence_frames?.length)?.evidence_frames[0];
  const laws = [...normIdsOf(entry)].map(normById).filter(Boolean);
  return el("article", { className: "video-card", onclick: () => { showView("analyzer-view"); open(entry); } },
    frame ? el("img", { className: "video-thumb", src: frameSrc(frame), alt: "" })
          : el("video", { className: "video-thumb", src: `${entry.url}#t=1`, preload: "metadata", muted: true }),
    el("div", { className: "video-card-body" },
      el("div", { className: "history-line" },
        el("strong", { className: "history-name", textContent: entry.file.name }),
        el("span", { className: kind ? `pill is-${kind}` : "pill", textContent: label })),
      el("span", { className: "muted", textContent: entry.addedAt.toLocaleString("ru", { dateStyle: "medium", timeStyle: "short" }) }),
      el("div", { className: "chips" },
        ...tagsOf(entry).map((t) => el("span", { className: "chip", textContent: t })),
        ...laws.map((n) => el("span", { className: `chip law-chip ${lawFilter.has(n.id) ? "hit" : ""}` }, icon("scale"), lawLabel(n))),
        ...entry.categoryIds.map((id) => el("span", { className: "chip category" }, icon("folder"), categoryName(id) || "—")))));
}

// ---------- Laws popup ----------

function renderLawChips() {
  $("law-count").textContent = lawFilter.size || "";
  $("law-chips").replaceChildren(...[...lawFilter].map(normById).filter(Boolean).map((n) =>
    el("button", { type: "button", className: "chip law-chip hit", title: "Убрать фильтр",
                   onclick: () => { lawFilter.delete(n.id); renderLawChips(); renderVideoList(); } },
      icon("scale"), lawLabel(n), " ×")),
    ...(lawFilter.size ? [el("button", { type: "button", className: "link", textContent: "Сбросить законы",
                                          onclick: () => { lawFilter.clear(); renderLawChips(); renderVideoList(); } })] : []));
}

function renderLawOptions() {
  if (!normsBase) {
    $("law-options").replaceChildren(el("p", { className: "muted", textContent: "Введите API-ключ, чтобы загрузить нормативную базу." }));
    return;
  }
  const q = $("law-search").value.trim().toLowerCase();
  const groups = normsBase.documents.map((doc) => ({
    doc,
    items: normsBase.norms.filter((n) => n.doc === doc.key && (!q || [n.id, n.clause, n.quote, n.document_short,
      ...n.types.map((t) => t.type)].join(" ").toLowerCase().includes(q))),
  })).filter((g) => g.items.length);
  $("law-options").replaceChildren(...(groups.length ? groups.map(({ doc, items }) => el("div", { className: "law-group" },
    el("p", { className: "law-group-title" }, icon("book", "c-violet"), doc.short),
    ...items.map((n) => el("label", { className: "law-option" },
      el("input", { type: "checkbox", checked: lawFilter.has(n.id),
                    onchange: (e) => { e.target.checked ? lawFilter.add(n.id) : lawFilter.delete(n.id); renderLawChips(); renderVideoList(); } }),
      el("span", {},
        el("strong", { textContent: n.clause }),
        el("span", { className: "muted", textContent: n.types.map((t) => t.type).join(" · ") || n.quote.slice(0, 90) }))))))
    : [el("p", { className: "muted", textContent: "Ничего не найдено." })]));
}

$("law-button").onclick = async (e) => {
  e.stopPropagation();
  const popup = $("law-popup");
  popup.hidden = !popup.hidden;
  if (popup.hidden) return;
  if (getKey() && !normsBase) { try { await loadNormsBase(); } catch {} }
  $("law-search").value = "";
  renderLawOptions();
  $("law-search").focus();
};
$("law-popup").onclick = (e) => e.stopPropagation();
document.addEventListener("click", () => { $("law-popup").hidden = true; });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("law-popup").hidden = true; });
$("law-search").oninput = renderLawOptions;
$("filter-search").oninput = renderVideoList;
$("filter-sort").onchange = renderVideoList;
