// "Нормативная база РК" page: rules/norms.yaml grouped by document, with search.
// loadNormsBase() is shared with the "Фильтры" page (filters.js).

let normsBase = null; // { updated_at, documents, norms, violation_types } from GET /api/v1/norms

async function loadNormsBase() {
  if (!normsBase) normsBase = await api("/norms");
  return normsBase;
}

async function loadNorms() {
  if (!getKey()) {
    $("norms-list").replaceChildren(normsState("Нужен API-ключ", "Введите ключ кнопкой «API-ключ» вверху, чтобы загрузить базу."));
    return;
  }
  try {
    await loadNormsBase();
  } catch (err) {
    $("norms-list").replaceChildren(normsState("Не удалось загрузить базу", err.message));
    return;
  }
  renderUpdatedAt();
  renderNorms();
}

function normsState(title, text) {
  return el("div", { className: "state" }, el("p", { textContent: title }), el("p", { textContent: text }));
}

const formatDate = (iso) => new Date(iso).toLocaleDateString("ru", { day: "numeric", month: "long", year: "numeric" });

// Footer of this page: when the normative base was last updated (norms.yaml → updated_at).
function renderUpdatedAt() {
  $("footer-updated").replaceChildren(icon("history", "c-sky"),
    normsBase?.updated_at ? `База нормативных актов РК обновлена: ${formatDate(normsBase.updated_at)}`
                          : "Дата обновления базы не указана (rules/norms.yaml → updated_at)");
}

function renderNorms() {
  const { documents, norms } = normsBase;
  if (!norms.length) {
    $("norms-list").replaceChildren(normsState("База пока пуста", "Добавьте пункты нормативных актов в rules/norms.yaml."));
    return;
  }
  const q = $("norms-search").value.trim().toLowerCase();
  const matches = (n) => !q || [n.id, n.clause, n.quote, n.note, n.document, n.document_short, n.approved_by,
                                ...n.types.map((t) => `${t.id} ${t.type}`)].join(" ").toLowerCase().includes(q);
  const sections = documents.map((doc) => ({ doc, items: norms.filter((n) => n.doc === doc.key && matches(n)) }))
                            .filter((s) => s.items.length);
  $("norms-summary").textContent = q ? `Найдено пунктов: ${sections.reduce((k, s) => k + s.items.length, 0)}`
                                     : `${documents.length} документа · ${norms.length} пунктов`;
  $("norms-list").replaceChildren(...(sections.length ? sections.map(({ doc, items }) => el("section", { className: "doc-section" },
    el("header", { className: "doc-head" },
      el("span", { className: "doc-badge" }, icon("book"), doc.short),
      el("div", {},
        el("h3", { textContent: doc.title }),
        el("p", { className: "muted", textContent: [doc.approved_by, doc.edition].filter(Boolean).join(" · ") })),
      el("a", { className: "doc-link", href: doc.url, target: "_blank", rel: "noopener" }, "adilet.zan.kz ", icon("external"))),
    el("div", { className: "norms-grid" }, ...items.map(renderNorm))))
    : [normsState("Ничего не найдено", "Попробуйте другой запрос: номер пункта, слово из цитаты или тип нарушения.")]));
}

// From a violation's "Правило": open this page and bring the norm into view, highlighted.
async function goToNorm(id) {
  $("norms-search").value = ""; // a search filter could hide it
  showView("norms-view");
  await loadNorms();
  const card = $(`norm-${id}`);
  if (!card) return;
  card.scrollIntoView({ block: "center", behavior: "smooth" });
  card.classList.remove("spotlight");
  void card.offsetWidth;
  card.classList.add("spotlight");
}

function renderNorm(n) {
  return el("article", { className: "norm", id: `norm-${n.id}` },
    el("div", { className: "norm-top" },
      el("strong", { className: "norm-clause", textContent: n.clause }),
      el("span", { className: "pill", textContent: n.id })),
    el("q", { textContent: n.quote }),
    n.note && el("p", { className: "norm-note" }, icon("alert", "c-amber"), n.note),
    n.types.length > 0 && el("div", { className: "chips" }, ...n.types.map((t) =>
      el("span", { className: "chip", title: t.id, textContent: `${t.id} · ${t.type}` }))),
    el("div", { className: "norm-foot" },
      n.adilet_checked_at
        ? el("span", { className: "verify ok" }, icon("checkCircle"), `Сверено на adilet: ${formatDate(n.adilet_checked_at)}`)
        : el("span", { className: "verify pending", title: n.source_checked ? `Текст сверен: ${n.source_checked}` : "" },
            icon("alert"), "Не сверено на adilet"),
      el("a", { href: n.url, target: "_blank", rel: "noopener" }, "Открыть источник ", icon("external"))));
}

$("norms-search").oninput = () => normsBase && renderNorms();
