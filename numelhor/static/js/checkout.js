// Checkout: dados, entrega com mapa, pagamento. Valores sempre vêm do servidor (/api/quote).
(() => {
  const { brl, el, $, cart, api, load, store, history } = NM;
  const data = JSON.parse($("#checkout-data").textContent);
  const form = $("#checkout-form");
  const submitBtn = $("#submit-btn");
  const errorBox = $("#form-error");
  const lines = cart.get();

  if (!lines.length) {
    $("#checkout").hidden = true;
    $("#empty-cart").hidden = false;
    return;
  }

  const state = { fulfillment: null, lat: null, lng: null, quoteOk: false };
  let map = null;
  let marker = null;

  const radio = (name) => (form.querySelector(`input[name="${name}"]:checked`) || {}).value || null;

  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.hidden = !msg;
  }

  // Dados lembrados neste aparelho (só nome e telefone)
  const saved = load("nm_customer", null);
  if (saved) {
    $("#name").value = saved.name || "";
    $("#phone").value = saved.phone || "";
  }

  // Máscara simples do telefone
  $("#phone").addEventListener("input", (e) => {
    const d = e.target.value.replace(/\D/g, "").slice(0, 11);
    e.target.value = d.length > 6 ? `(${d.slice(0, 2)}) ${d.slice(2, d.length - 4)}-${d.slice(-4)}`
      : d.length > 2 ? `(${d.slice(0, 2)}) ${d.slice(2)}` : d;
  });

  // ---------- Resumo com valores do servidor ----------
  let quoteSeq = 0;
  async function requote() {
    const seq = ++quoteSeq;
    const r = await api("/api/quote", {
      items: lines, fulfillment: state.fulfillment, delivery: { lat: state.lat, lng: state.lng },
    });
    if (seq !== quoteSeq) return;
    if (!r.ok) {
      showError(r.error);
      submitBtn.disabled = true;
      return;
    }
    const box = $("#summary-lines");
    box.replaceChildren(...r.lines.map((l) => {
      const extra = [];
      for (const o of l.options) extra.push(`${o.group}: ${o.name}`);
      if (l.removed.length) extra.push(`SEM ${l.removed.join(", ").toLowerCase()}`);
      if (l.sauces.length) extra.push(`Molhos: ${l.sauces.map((s) => s.name).join(", ")}`);
      if (l.notes) extra.push(`Obs.: ${l.notes}`);
      return el("div", { class: "sum-line" },
        el("div", {}, `${l.qty}x ${l.name}`, extra.map((t) => el("small", {}, t))),
        el("strong", {}, brl(l.line_total_cents)));
    }));
    $("#sum-subtotal").textContent = brl(r.subtotal_cents);
    const quote = $("#delivery-quote");
    const isDelivery = state.fulfillment === "entrega";
    $("#sum-fee-row").hidden = !isDelivery;
    state.quoteOk = !isDelivery;
    if (isDelivery) {
      if (!r.delivery || state.lat == null) {
        quote.className = "quote";
        quote.textContent = "Marque o local de entrega no mapa para calcular a taxa.";
        $("#sum-fee").textContent = "—";
      } else if (r.delivery.ok) {
        state.quoteOk = true;
        quote.className = "quote ok";
        quote.textContent = `Entrega: ${brl(r.delivery.fee_cents)} (≈ ${r.delivery.distance_km.toFixed(1).replace(".", ",")} km)`;
        $("#sum-fee").textContent = brl(r.fee_cents);
      } else {
        quote.className = "quote err";
        quote.textContent = r.delivery.error;
        $("#sum-fee").textContent = "—";
      }
    }
    $("#sum-total").textContent = brl(r.total_cents);
    submitBtn.disabled = !data.open;
    if (!errorBox.dataset.sticky) showError("");
  }

  // ---------- Mapa ----------
  function initMap() {
    if (map) { map.invalidateSize(); return; }
    map = L.map("map", { scrollWheelZoom: false }).setView([data.store.lat, data.store.lng], 14);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "&copy; OpenStreetMap",
    }).addTo(map);
    map.on("click", (e) => setPoint(e.latlng.lat, e.latlng.lng, true));
  }

  function setPoint(lat, lng, fillAddress) {
    state.lat = lat;
    state.lng = lng;
    if (!marker) {
      marker = L.marker([lat, lng], { draggable: true, title: "Local de entrega" }).addTo(map);
      marker.on("dragend", () => {
        const p = marker.getLatLng();
        setPoint(p.lat, p.lng, true);
      });
    } else {
      marker.setLatLng([lat, lng]);
    }
    map.setView([lat, lng], Math.max(map.getZoom(), 17));
    if (fillAddress) reverseFill(lat, lng);
    requote();
  }

  async function reverseFill(lat, lng) {
    const r = await api(`/api/geo/reverse?lat=${lat.toFixed(6)}&lng=${lng.toFixed(6)}`);
    if (!r.ok || !r.address) return;
    $("#address").value = r.address;
    if (r.number) $("#number").value = r.number;
  }

  // ---------- Busca de endereço ----------
  const search = $("#addr-search");
  const results = $("#addr-results");
  let searchTimer = null;
  let searchSeq = 0;

  async function runSearch() {
    const q = search.value.trim();
    if (q.length < 4) { results.hidden = true; return; }
    const seq = ++searchSeq;
    const r = await api(`/api/geo/search?q=${encodeURIComponent(q)}`);
    if (seq !== searchSeq) return;
    results.replaceChildren();
    if (!r.ok) {
      results.append(el("li", {}, el("button", { type: "button", disabled: true }, r.error)));
    } else if (!r.results.length) {
      results.append(el("li", {}, el("button", { type: "button", disabled: true }, "Nada encontrado. Tente com o bairro, ou marque direto no mapa.")));
    } else {
      for (const item of r.results) {
        results.append(el("li", { role: "option" }, el("button", {
          type: "button",
          onclick: () => {
            results.hidden = true;
            search.value = item.label;
            $("#address").value = item.address;
            $("#number").value = item.number || "";
            setPoint(item.lat, item.lng, false);
            if (!item.number) $("#number").focus();
          },
        }, item.label)));
      }
    }
    results.hidden = false;
  }

  search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 550);
  });
  search.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); clearTimeout(searchTimer); runSearch(); }
    if (e.key === "Escape") results.hidden = true;
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".search-wrap")) results.hidden = true;
  });

  $("#use-location").addEventListener("click", () => {
    if (!navigator.geolocation) { showError("Seu navegador não informa a localização. Use a busca ou o mapa."); return; }
    const btn = $("#use-location");
    btn.disabled = true;
    btn.textContent = "Localizando…";
    navigator.geolocation.getCurrentPosition((pos) => {
      btn.disabled = false;
      btn.textContent = "📍 Usar minha localização";
      setPoint(pos.coords.latitude, pos.coords.longitude, true);
    }, () => {
      btn.disabled = false;
      btn.textContent = "📍 Usar minha localização";
      showError("Não consegui pegar sua localização. Use a busca ou toque no mapa.");
    }, { enableHighAccuracy: true, timeout: 12000 });
  });

  // ---------- Troca de opções ----------
  function syncOptions() {
    state.fulfillment = radio("fulfillment");
    const delivery = state.fulfillment === "entrega";
    $("#delivery-box").hidden = !delivery;
    $("#pickup-box").hidden = state.fulfillment !== "retirada";
    if (delivery) initMap();
    $("#offline-title").textContent = state.fulfillment === "retirada" ? "🤝 Pagar na retirada" : "🤝 Pagar na entrega";

    const when = radio("pay_when");
    $("#pay-online").hidden = when !== "online";
    $("#pay-offline").hidden = when !== "entrega";
    $("#email-field").hidden = !(when === "online" && radio("online_method") === "pix");
    $("#change-field").hidden = !(when === "entrega" && radio("offline_method") === "dinheiro");
  }
  form.querySelectorAll('input[type="radio"]').forEach((r) => r.addEventListener("change", (e) => {
    syncOptions();
    if (e.target.name === "fulfillment") requote();
  }));

  // ---------- Enviar ----------
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = $("#name").value.trim();
    const phone = $("#phone").value.replace(/\D/g, "");
    const when = radio("pay_when");
    const method = when === "online" ? radio("online_method") : radio("offline_method");
    let problem = null;
    if (name.length < 2) problem = "Informe seu nome.";
    else if (phone.length < 10) problem = "Informe seu WhatsApp com DDD.";
    else if (!state.fulfillment) problem = "Escolha entrega ou retirada.";
    else if (state.fulfillment === "entrega" && state.lat == null) problem = "Marque o local de entrega no mapa.";
    else if (state.fulfillment === "entrega" && !state.quoteOk) problem = $("#delivery-quote").textContent || "Endereço fora da área de entrega.";
    else if (state.fulfillment === "entrega" && $("#address").value.trim().length < 3) problem = "Preencha a rua.";
    else if (state.fulfillment === "entrega" && !$("#number").value.trim()) problem = "Preencha o número (ou S/N).";
    else if (!when || !method) problem = "Escolha a forma de pagamento.";
    if (problem) { showError(problem); return; }

    showError("");
    submitBtn.disabled = true;
    submitBtn.textContent = "Enviando pedido…";
    store("nm_customer", { name, phone: $("#phone").value });
    const r = await api("/api/pedidos", {
      items: lines,
      customer: { name, phone, email: $("#email").value.trim() },
      fulfillment: state.fulfillment,
      delivery: {
        lat: state.lat, lng: state.lng,
        address: $("#address").value, number: $("#number").value,
        complement: $("#complement").value, reference: $("#reference").value,
      },
      payment: { when, method, change_for: $("#change_for").value },
      notes: $("#notes").value,
    });
    if (r.ok) {
      history.add({ token: r.token, id: r.id, total: r.total, created: Date.now(), items: lines });
      cart.clear();
      location.href = r.redirect;
      return;
    }
    showError(r.error);
    submitBtn.disabled = false;
    submitBtn.textContent = "Fazer pedido";
  });

  syncOptions();
  requote();
})();
