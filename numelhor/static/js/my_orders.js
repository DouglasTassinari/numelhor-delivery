// Meus pedidos: lista os pedidos feitos neste aparelho, com status atual e "Pedir de novo".
(() => {
  const { el, $, api, history, cart, toast } = NM;
  const list = history.list();
  const box = $("#orders-list");

  if (!list.length) {
    $("#orders-empty").hidden = false;
    return;
  }

  const color = (s) => (s === "cancelado" ? "red" : s === "aguardando_pagamento" ? "yellow" : "green");
  const when = (ts) => new Date(ts).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

  for (const o of list) {
    const badge = el("span", { class: "badge" }, "Carregando…");
    const card = el("section", { class: "panel" },
      el("div", { class: "my-order" },
        el("div", {},
          el("div", { class: "who" }, `Pedido #${o.id || "?"}`),
          el("div", { class: "muted" }, `${when(o.created)} · ${o.total || ""}`),
          badge),
        el("div", { class: "actions" },
          el("a", { class: "btn btn-ghost", href: `/pedido/${o.token}` }, "Ver pedido"),
          o.items && o.items.length
            ? el("button", {
              class: "btn btn-yellow", type: "button",
              onclick: () => {
                cart.set(o.items.map((l) => ({ ...l })));
                toast("Itens colocados na sacola ✔");
                setTimeout(() => { location.href = "/?sacola=1"; }, 500);
              },
            }, "Pedir de novo")
            : null)));
    box.append(card);
    api(`/api/pedido/${o.token}`).then((r) => {
      if (r.ok) {
        badge.textContent = r.status_label;
        badge.className = `badge ${color(r.status)}`;
      } else {
        badge.textContent = "Pedido não encontrado";
      }
    });
  }
})();
