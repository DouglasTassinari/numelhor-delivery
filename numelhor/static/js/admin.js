// Painel: quadro de pedidos em tempo real, alertas, editor de ingredientes e mapas.
(() => {
  const { el, $, api } = NM;

  // Confirmações antes de ações destrutivas
  document.querySelectorAll("form[data-confirm]").forEach((f) => {
    f.addEventListener("submit", (e) => { if (!confirm(f.dataset.confirm)) e.preventDefault(); });
  });
  document.querySelectorAll("[data-confirm-click]").forEach((b) => {
    b.addEventListener("click", (e) => { if (!confirm(b.dataset.confirmClick)) e.preventDefault(); });
  });

  // Botões "Copiar"
  document.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const input = $(btn.dataset.copy);
      try { await navigator.clipboard.writeText(input.value); } catch { input.select(); document.execCommand("copy"); }
      btn.textContent = "Copiado ✔";
      setTimeout(() => { btn.textContent = "Copiar"; }, 2000);
    });
  });

  // ---------- Quadro de pedidos ----------
  const board = $("#board");
  if (board) {
    const groups = {
      novo: ["novo"], andamento: ["confirmado", "em_preparo"],
      saida: ["saiu_entrega", "pronto_retirada"], fim: ["concluido", "cancelado"],
    };
    const baseTitle = document.title;
    let known = null;
    let audio = null;
    let unseen = 0;

    function beep() {
      if (!audio) return;
      const now = audio.currentTime;
      [0, 0.22, 0.44].forEach((t, i) => {
        const osc = audio.createOscillator();
        const gain = audio.createGain();
        osc.frequency.value = i === 2 ? 1175 : 880;
        gain.gain.setValueAtTime(0.0001, now + t);
        gain.gain.exponentialRampToValueAtTime(0.35, now + t + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + t + 0.18);
        osc.connect(gain).connect(audio.destination);
        osc.start(now + t);
        osc.stop(now + t + 0.2);
      });
    }

    $("#alerts-btn").addEventListener("click", async (e) => {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      await audio.resume();
      beep();
      if ("Notification" in window && Notification.permission === "default") {
        try { await Notification.requestPermission(); } catch { /* ignore */ }
      }
      e.target.textContent = "🔔 Alerta ativo";
      e.target.classList.replace("btn-yellow", "btn-dark");
    });

    function alertNew(count) {
      unseen += count;
      document.title = `(${unseen}) Novo pedido! — NuMelhor`;
      beep();
      if ("Notification" in window && Notification.permission === "granted" && document.hidden) {
        new Notification("NuMelhor: novo pedido!", { body: `${count} pedido(s) novo(s) aguardando confirmação.` });
      }
    }
    const clearTitle = () => { if (!document.hidden) { unseen = 0; document.title = baseTitle; } };
    document.addEventListener("visibilitychange", clearTitle);
    window.addEventListener("focus", clearTitle);

    const card = (o) => el("a", { class: `ocard${o.status === "novo" ? " fresh" : ""}`, href: o.url },
      el("div", { class: "top" }, el("span", { class: "id" }, `#${o.id}`), el("span", {}, o.time)),
      el("div", { class: "who" }, o.name),
      el("div", { class: "meta" },
        el("span", {}, o.fulfillment === "entrega" ? "🛵 Entrega" : "🏪 Retirada"),
        el("strong", {}, o.total)),
      el("div", { class: `pay${o.paid ? " paid" : ""}` }, o.status === "concluido" || o.status === "cancelado" ? `${o.status_label} · ${o.payment}` : o.payment));

    function fill(container, list, emptyText) {
      container.querySelector(".n").textContent = list.length;
      const box = container.querySelector(".cards");
      box.replaceChildren(...(list.length ? list.map(card) : [el("div", { class: "none" }, emptyText)]));
    }

    async function load() {
      const r = await api(board.dataset.api);
      if (!r.ok) {
        if (/Sessão/.test(r.error || "")) location.reload();
        $("#last-update").textContent = "⚠️ Sem conexão, tentando de novo…";
        return;
      }
      for (const [col, statuses] of Object.entries(groups)) {
        fill(board.querySelector(`[data-col="${col}"]`), r.orders.filter((o) => statuses.includes(o.status)),
          col === "novo" ? "Nenhum pedido novo" : "—");
      }
      fill($("#waiting"), r.orders.filter((o) => o.status === "aguardando_pagamento"), "Nenhum");
      const novos = r.orders.filter((o) => o.status === "novo").map((o) => o.id);
      if (known) {
        const fresh = novos.filter((id) => !known.has(id));
        if (fresh.length) alertNew(fresh.length);
      }
      known = new Set([...(known || []), ...novos]);
      $("#last-update").textContent = `Atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
    }
    load();
    setInterval(load, 5000);
  }

  // ---------- Ingredientes ----------
  const rows = $("#ing-rows");
  if (rows) {
    const tpl = $("#ing-template");
    $("#add-ing").addEventListener("click", () => {
      rows.append(tpl.content.cloneNode(true));
      rows.lastElementChild.querySelector("input").focus();
    });
    rows.addEventListener("click", (e) => {
      if (e.target.matches("[data-remove-row]")) e.target.closest(".ing-row").remove();
    });
  }

  const imgInput = $("#image");
  if (imgInput) {
    imgInput.addEventListener("change", () => {
      const f = imgInput.files[0];
      const prev = $("#img-preview");
      if (!f) return;
      prev.src = URL.createObjectURL(f);
      prev.hidden = false;
    });
  }

  // ---------- Opções (tamanho, sabor, adicionais) ----------
  const groupsBox = $("#groups");
  if (groupsBox) {
    const TYPES = [["1-1", "Cliente escolhe 1 (obrigatório)"], ["0-1", "Cliente escolhe 1 (opcional)"], ["0-N", "Cliente escolhe vários (opcional)"]];
    const PRESETS = {
      tamanho: { name: "Tamanho", type: "1-1", options: [{ name: "Pequeno", price: "0,00" }, { name: "Grande", price: "5,00" }] },
      sabor: { name: "Sabor", type: "1-1", options: [{ name: "", price: "0,00" }] },
      adicionais: { name: "Adicionais", type: "0-N", options: [{ name: "Bacon", price: "5,00" }] },
      outro: { name: "", type: "0-1", options: [{ name: "", price: "0,00" }] },
    };
    const typeOf = (g) => (g.min >= 1 ? "1-1" : g.max === 1 ? "0-1" : "0-N");

    function optRow(o = { name: "", price: "0,00" }) {
      return el("div", { class: "opt-row" },
        el("input", { type: "text", class: "o-name", value: o.name, maxlength: "50", placeholder: "Nome da opção", "aria-label": "Nome da opção" }),
        el("span", { class: "muted" }, "+ R$"),
        el("input", { type: "text", class: "o-price", value: o.price, inputmode: "decimal", "aria-label": "Valor a mais" }),
        el("button", { type: "button", class: "x", "aria-label": "Remover opção", onclick: (e) => e.target.closest(".opt-row").remove() }, "×"));
    }

    function groupCard(g) {
      const sel = el("select", { class: "g-type", "aria-label": "Tipo de escolha" },
        TYPES.map(([v, t]) => el("option", { value: v }, t)));
      sel.value = g.type || typeOf(g);
      const rows = el("div", { class: "opt-rows" }, (g.options || []).map(optRow));
      return el("div", { class: "group-card" },
        el("div", { class: "group-head" },
          el("input", { type: "text", class: "g-name", value: g.name, maxlength: "40", placeholder: "Nome do grupo (ex.: Tamanho)", "aria-label": "Nome do grupo" }),
          sel,
          el("button", { type: "button", class: "btn btn-danger btn-sm", onclick: (e) => { if (confirm("Remover este grupo de opções?")) e.target.closest(".group-card").remove(); } }, "Remover grupo")),
        rows,
        el("button", { type: "button", class: "btn btn-ghost btn-sm", onclick: () => { rows.append(optRow()); rows.lastElementChild.querySelector("input").focus(); } }, "+ Opção"));
    }

    JSON.parse($("#groups-data").textContent).forEach((g) => groupsBox.append(groupCard(g)));
    document.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => {
      const card = groupCard(JSON.parse(JSON.stringify(PRESETS[b.dataset.preset])));
      groupsBox.append(card);
      card.querySelector(".g-name").focus();
    }));
    $("#product-form").addEventListener("submit", () => {
      const data = [...groupsBox.querySelectorAll(".group-card")].map((c) => {
        const [mn, mx] = c.querySelector(".g-type").value.split("-");
        return {
          name: c.querySelector(".g-name").value,
          min: Number(mn), max: mx === "N" ? 20 : Number(mx),
          options: [...c.querySelectorAll(".opt-row")].map((r) => ({ name: r.querySelector(".o-name").value, price: r.querySelector(".o-price").value })),
        };
      });
      $("#groups_json").value = JSON.stringify(data);
    });
  }

  // ---------- Mapas ----------
  const tiles = () => L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap" });

  const mini = $("#mini-map");
  if (mini && window.L && mini.dataset.lat !== "None") {
    const pos = [Number(mini.dataset.lat), Number(mini.dataset.lng)];
    const m = L.map(mini, { scrollWheelZoom: false }).setView(pos, 16);
    tiles().addTo(m);
    L.marker(pos).addTo(m);
  }

  const storeMap = $("#store-map");
  if (storeMap && window.L) {
    const pos = [Number(storeMap.dataset.lat), Number(storeMap.dataset.lng)];
    const m = L.map(storeMap, { scrollWheelZoom: false }).setView(pos, 15);
    tiles().addTo(m);
    const marker = L.marker(pos, { draggable: true }).addTo(m);
    const write = (ll) => {
      $("#store_lat").value = ll.lat.toFixed(6);
      $("#store_lng").value = ll.lng.toFixed(6);
    };
    marker.on("dragend", () => write(marker.getLatLng()));
    m.on("click", (e) => { marker.setLatLng(e.latlng); write(e.latlng); });
  }
})();
