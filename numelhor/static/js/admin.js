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

  // ---------- Alerta de pedido novo (em todas as telas do painel) ----------
  const alertBtn = $("#alerts-btn");
  const onBoard = $("#board");
  if (alertBtn) {
    const ON_KEY = "nm_alert_on";
    const PREFS_KEY = "nm_alert_prefs";
    const KNOWN_KEY = "nm_known_orders";
    const RING_KEY = "nm_last_ring";
    const banner = $("#sound-banner");
    const offBtn = $("#alerts-off");
    const chime = new Audio(alertBtn.dataset.sound);
    const voiceNew = new Audio(alertBtn.dataset.voiceNew);
    const voiceRemind = new Audio(alertBtn.dataset.voiceRemind);
    [chime, voiceNew, voiceRemind].forEach((a) => { a.preload = "auto"; });
    const baseTitle = document.title;
    let enabled = NM.load(ON_KEY, false) === true;
    let prefs = Object.assign({ mode: "voz", repeat: 60 }, NM.load(PREFS_KEY, {}));
    let unlocked = false;
    let wakeLock = null;
    const ss = {
      get(k, d) { try { const v = JSON.parse(sessionStorage.getItem(k)); return v === null ? d : v; } catch { return d; } },
      set(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } },
    };
    let firstData = ss.get(KNOWN_KEY, null) === null;
    const known = new Set(ss.get(KNOWN_KEY, []));
    let pendingNew = 0;

    // ---- Voz (português do Brasil) ----
    const synth = window.speechSynthesis || null;
    let voice = null;
    function pickVoice() {
      if (!synth) return;
      const all = synth.getVoices().filter((v) => /^pt[-_]BR/i.test(v.lang) || /portugu[eê]s.*brasil/i.test(v.name));
      const pt = all.length ? all : synth.getVoices().filter((v) => /^pt/i.test(v.lang));
      voice = pt.find((v) => /natural|neural|online/i.test(v.name))
        || pt.find((v) => /google/i.test(v.name))
        || pt.find((v) => /francisca|thalita|maria|luciana/i.test(v.name))
        || pt[0] || null;
    }
    if (synth) {
      pickVoice();
      synth.addEventListener("voiceschanged", pickVoice);
    }
    const PHRASES = [
      "Opa! Chegou pedido novo!",
      "Pedido novo na área! Bora pra chapa!",
      "Olha o pedido! Que lanche brabo!",
      "Partiu NuMelhor! Tem pedido novo!",
      "Deu fome em alguém! Chegou pedido!",
    ];
    const pick = (list) => list[Math.floor(Math.random() * list.length)];
    function emReais(total) {
      const n = parseFloat(String(total).replace(/[^\d,]/g, "").replace(",", "."));
      if (!isFinite(n)) return "";
      const reais = Math.floor(n);
      const cents = Math.round((n - reais) * 100);
      return `${reais} ${reais === 1 ? "real" : "reais"}${cents ? ` e ${cents} centavos` : ""}`;
    }

    function play(audio) {
      audio.currentTime = 0;
      return audio.play().then(() => true).catch(() => { unlocked = false; render(); return false; });
    }

    // Fala o texto; se não houver voz em português, toca o áudio gravado (ou o toque)
    function say(text, fallbackAudio) {
      if (prefs.mode === "toque") { play(chime); return; }
      if (synth && voice) {
        synth.cancel();
        const u = new SpeechSynthesisUtterance(text);
        u.voice = voice;
        u.lang = voice.lang || "pt-BR";
        u.rate = 1.05;
        u.pitch = 1.2;
        u.volume = 1;
        u.onerror = (e) => {
          if (e.error === "not-allowed") { unlocked = false; render(); } else play(fallbackAudio);
        };
        synth.speak(u);
        return;
      }
      play(fallbackAudio);
    }

    function render() {
      banner.hidden = !(enabled && !unlocked);
      offBtn.hidden = !enabled;
      alertBtn.classList.remove("btn-yellow", "btn-dark", "btn-red", "pulse");
      if (!enabled) {
        alertBtn.textContent = "🔔 Ligar alerta de pedido";
        alertBtn.classList.add("btn-yellow");
      } else if (!unlocked) {
        alertBtn.textContent = "🔔 Clique para ligar o som";
        alertBtn.classList.add("btn-red", "pulse");
      } else {
        alertBtn.textContent = prefs.mode === "voz" ? "🗣️ Alerta ligado" : "🔔 Alerta ligado";
        alertBtn.classList.add("btn-dark");
        alertBtn.title = "Clique para testar";
      }
    }

    // O navegador só libera som depois de um clique na página: testamos tocando mudo.
    async function tryUnlock() {
      try {
        chime.muted = true;
        await chime.play();
        chime.pause();
        chime.currentTime = 0;
        unlocked = true;
        if (synth) { synth.cancel(); synth.resume(); }
      } catch {
        unlocked = false;
      }
      chime.muted = false;
      render();
      return unlocked;
    }

    function announceNew(list) {
      if (!enabled || !unlocked) return;
      ss.set(RING_KEY, Date.now());
      const o = list[list.length - 1];
      const head = list.length > 1 ? `Opa! Chegaram ${list.length} pedidos novos!` : pick(PHRASES);
      const detail = `Pedido número ${o.id}, ${o.fulfillment === "entrega" ? "para entrega" : "para retirar"}, ${emReais(o.total)}.`;
      say(`${head} ${detail}`, voiceNew);
    }

    function remind(force) {
      if (!enabled || !unlocked || !pendingNew) return;
      const every = Number(prefs.repeat) * 1000;
      if (!force && (!every || Date.now() - ss.get(RING_KEY, 0) < every)) return;
      ss.set(RING_KEY, Date.now());
      say(`Ei! Ainda tem ${pendingNew} ${pendingNew > 1 ? "pedidos esperando" : "pedido esperando"} confirmação!`, voiceRemind);
    }

    async function keepAwake() {
      if (!enabled || !("wakeLock" in navigator) || document.visibilityState !== "visible" || wakeLock) return;
      try {
        wakeLock = await navigator.wakeLock.request("screen");
        wakeLock.addEventListener("release", () => { wakeLock = null; });
      } catch { /* sem suporte ou negado */ }
    }

    function notify(list) {
      if (!("Notification" in window) || Notification.permission !== "granted") return;
      for (const o of list.slice(0, 3)) {
        try {
          const n = new Notification(`🍔 Pedido novo #${o.id} — ${o.total}`, {
            body: `${o.name} · ${o.fulfillment === "entrega" ? "Entrega" : "Retirada"}\nClique para abrir o pedido.`,
            tag: `pedido-${o.id}`, requireInteraction: true, icon: alertBtn.dataset.icon,
          });
          n.onclick = () => { window.focus(); location.href = o.url; n.close(); };
        } catch { /* ignore */ }
      }
    }

    const testAlert = () => say("Alerta ligado! Quando chegar pedido, eu aviso!", voiceNew);
    alertBtn.addEventListener("click", async () => {
      if (enabled && unlocked) { testAlert(); return; } // já ligado: o botão testa
      enabled = true;
      NM.store(ON_KEY, true);
      await tryUnlock();
      testAlert();
      if ("Notification" in window && Notification.permission === "default") {
        try { await Notification.requestPermission(); } catch { /* ignore */ }
      }
      keepAwake();
      render();
    });
    offBtn.addEventListener("click", () => {
      enabled = false;
      NM.store(ON_KEY, false);
      if (synth) synth.cancel();
      [chime, voiceNew, voiceRemind].forEach((a) => a.pause());
      render();
    });

    // Preferências (tela Pedidos): voz ou toque, e de quanto em quanto tempo lembrar
    const modeSel = $("#alert-mode");
    const repeatSel = $("#alert-repeat");
    if (modeSel && repeatSel) {
      modeSel.value = prefs.mode;
      repeatSel.value = String(prefs.repeat);
      const savePrefs = () => {
        prefs = { mode: modeSel.value, repeat: Number(repeatSel.value) };
        NM.store(PREFS_KEY, prefs);
        render();
      };
      modeSel.addEventListener("change", () => { savePrefs(); if (enabled && unlocked) testAlert(); });
      repeatSel.addEventListener("change", savePrefs);
      const testBtn = $("#alert-test");
      if (testBtn) testBtn.addEventListener("click", async () => {
        if (!unlocked) await tryUnlock();
        say("Opa! Chegou pedido novo! Pedido número 1, para entrega, 25 reais.", voiceNew);
      });
    }

    // Qualquer clique, toque ou tecla na tela religa o som (o navegador exige isso a cada página)
    const onGesture = async () => {
      if (enabled && !unlocked && await tryUnlock()) {
        if (pendingNew) remind(true);
        keepAwake();
      }
    };
    ["pointerdown", "keydown", "touchstart"].forEach((ev) => document.addEventListener(ev, onGesture, true));
    banner.addEventListener("click", onGesture);
    document.addEventListener("visibilitychange", keepAwake);

    // ---- Quadro de pedidos (só na tela Pedidos) ----
    const groups = {
      novo: ["novo"], andamento: ["confirmado", "em_preparo"],
      saida: ["saiu_entrega", "pronto_retirada"], fim: ["concluido", "cancelado"],
    };
    const card = (o) => el("a", { class: `ocard${o.status === "novo" ? " fresh" : ""}`, href: o.url },
      el("div", { class: "top" }, el("span", { class: "id" }, `#${o.id}`), el("span", {}, o.time)),
      el("div", { class: "who" }, o.name),
      el("div", { class: "meta" },
        el("span", {}, o.fulfillment === "entrega" ? "🛵 Entrega" : "🏪 Retirada"),
        el("strong", {}, o.total)),
      el("div", { class: `pay${o.paid ? " paid" : ""}` }, o.status === "concluido" || o.status === "cancelado" ? `${o.status_label} · ${o.payment}` : o.payment));
    function fill(container, list, emptyText) {
      container.querySelector(".n").textContent = list.length;
      container.querySelector(".cards").replaceChildren(...(list.length ? list.map(card) : [el("div", { class: "none" }, emptyText)]));
    }

    function onData({ status, data }) {
      if (status === 401) { location.href = "/admin/login?next=" + encodeURIComponent(location.pathname); return; }
      if (!data || !data.ok) {
        if (onBoard) $("#last-update").textContent = "⚠️ Sem conexão, tentando de novo…";
        return;
      }
      const novos = data.orders.filter((o) => o.status === "novo");
      const fresh = novos.filter((o) => !known.has(o.id));
      fresh.forEach((o) => known.add(o.id));
      ss.set(KNOWN_KEY, [...known].slice(-300));
      pendingNew = novos.length;
      if (fresh.length && !firstData) {
        notify(fresh);
        announceNew(fresh);
      } else {
        remind(false); // lembra no intervalo escolhido enquanto houver pedido sem confirmar
      }
      firstData = false;
      document.title = pendingNew ? `(${pendingNew}) Pedido novo! — NuMelhor` : baseTitle;

      if (onBoard) {
        for (const [col, statuses] of Object.entries(groups)) {
          fill(onBoard.querySelector(`[data-col="${col}"]`), data.orders.filter((o) => statuses.includes(o.status)),
            col === "novo" ? "Nenhum pedido novo" : "—");
        }
        fill($("#waiting"), data.orders.filter((o) => o.status === "aguardando_pagamento"), "Nenhum");
        $("#last-update").textContent = `Atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
      }
    }

    // Busca dos pedidos num "worker" (não congela em segundo plano); sem suporte, na própria página
    try {
      const worker = new Worker(alertBtn.dataset.worker);
      worker.onmessage = (e) => onData(e.data);
      worker.postMessage({ url: alertBtn.dataset.api });
    } catch {
      const poll = async () => {
        try {
          const r = await fetch(alertBtn.dataset.api, { credentials: "same-origin", cache: "no-store" });
          onData({ status: r.status, data: await r.json().catch(() => null) });
        } catch { onData({ status: 0, data: null }); }
      };
      poll();
      setInterval(poll, 5000);
    }

    render();
    if (enabled) { tryUnlock(); keepAwake(); }
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
