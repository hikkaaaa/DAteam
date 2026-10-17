// "Нормативная база РК" tab: lists rules/norms.yaml (GET /api/v1/norms) with search.

let norms = null;

async function loadNorms() {
  if (norms) return renderNorms();
  if (!getKey() && !DEMO) {
    $("norms-list").replaceChildren(normsState("Нужен API-ключ", "Введите ключ кнопкой «API-ключ» вверху, чтобы загрузить базу."));
    return;
  }
  try {
    norms = DEMO ? DEMO_NORMS : await api("/norms");
  } catch (err) {
    $("norms-list").replaceChildren(normsState("Не удалось загрузить базу", err.message));
    return;
  }
  renderNorms();
}

function normsState(title, text) {
  return el("div", { className: "state" }, el("p", { textContent: title }), el("p", { textContent: text }));
}

function renderNorms() {
  if (!norms.length) {
    $("norms-list").replaceChildren(normsState("База пока пуста",
      "Добавьте пункты нормативных актов в rules/norms.yaml (с дословной цитатой и ссылкой на adilet.zan.kz)."));
    return;
  }
  const q = $("norms-search").value.trim().toLowerCase();
  const found = norms.filter((n) => !q || [n.document, n.clause, n.quote, n.number_date, n.id, n.interpretation]
    .join(" ").toLowerCase().includes(q));
  $("norms-list").replaceChildren(...(found.length ? found.map((n) => el("article", { className: "norm" },
    el("div", { className: "norm-top" },
      el("span", { className: "pill", textContent: n.id }),
      el("span", { className: "muted", textContent: `${n.number_date} · проверено ${n.checked_at}` })),
    el("h3", { textContent: `${n.clause} · ${n.document}` }),
    el("q", { textContent: n.quote }),
    el("p", { className: "muted", textContent: n.interpretation }),
    el("div", { className: "norm-foot" },
      el("span", { className: "muted", textContent: `Типы нарушений: ${n.applies_to.join(", ") || "—"}` }),
      el("a", { href: n.url, target: "_blank", rel: "noopener", textContent: "Открыть на adilet.zan.kz " }, icon("external")))))
    : [normsState("Ничего не найдено", "Попробуйте другой запрос.")]));
}

$("norms-search").oninput = () => norms && renderNorms();

const DEMO_NORMS = [{
  id: "DEMO", document: "Демо-документ (здесь будет акт из rules/norms.yaml)", number_date: "—", clause: "п. 0",
  quote: "Здесь появится дословная цитата пункта нормативного акта.", url: "https://adilet.zan.kz/",
  checked_at: "—", applies_to: ["V01"], interpretation: "Как пункт связан с тем, что видно на видео.",
}];
