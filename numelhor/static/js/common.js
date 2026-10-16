// Utilidades compartilhadas do site do cliente.
window.NM = (() => {
  const CART_KEY = "nm_cart_v1";
  const HISTORY_KEY = "nm_orders_v1";
  const EMOJI = { xis: "🥪", burger: "🍔", pastel: "🥟", batata: "🍟", combo: "🍱", refri: "🥤", doce: "🍫" };
  let memoryCart = [];
  let skipPops = 0;

  const brl = (cents) => (cents / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  const csrf = () => document.querySelector('meta[name="csrf-token"]').content;
  const $ = (sel, root = document) => root.querySelector(sel);

  // Cria elementos sem innerHTML (nada digitado vira HTML).
  function el(tag, props, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return node;
  }

  function load(key, fallback) {
    try {
      const v = JSON.parse(localStorage.getItem(key) || "null");
      return v == null ? fallback : v;
    } catch {
      return fallback;
    }
  }
  function store(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* modo privado */ }
  }

  const cart = {
    get() {
      const v = load(CART_KEY, memoryCart);
      return Array.isArray(v) ? v : [];
    },
    set(lines) { memoryCart = lines; store(CART_KEY, lines); },
    clear() {
      memoryCart = [];
      try { localStorage.removeItem(CART_KEY); } catch { /* ignore */ }
    },
  };

  // Pedidos feitos neste aparelho (para "Meus pedidos" e "Pedir de novo")
  const history = {
    list() {
      const v = load(HISTORY_KEY, []);
      return Array.isArray(v) ? v : [];
    },
    add(entry) {
      const list = history.list().filter((o) => o.token !== entry.token);
      list.unshift(entry);
      store(HISTORY_KEY, list.slice(0, 20));
    },
    update(token, patch) {
      store(HISTORY_KEY, history.list().map((o) => (o.token === token ? { ...o, ...patch } : o)));
    },
  };

  async function api(url, body) {
    try {
      const r = await fetch(url, {
        method: body ? "POST" : "GET",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
        body: body ? JSON.stringify(body) : undefined,
        credentials: "same-origin",
      });
      const data = await r.json().catch(() => null);
      if (data) return data;
      return { ok: false, error: `Erro ${r.status}. Tente de novo.` };
    } catch {
      return { ok: false, error: "Sem conexão. Confira sua internet e tente de novo." };
    }
  }

  let toastTimer = null;
  function toast(text) {
    document.querySelectorAll(".toast").forEach((t) => t.remove());
    const t = el("div", { class: "toast", role: "status" }, text);
    document.body.append(t);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.remove(), 2600);
  }

  // Janela por cima da página (celular: sobe de baixo; computador: centralizada)
  function openModal(children, labelledBy) {
    const lastFocus = document.activeElement;
    const sheet = el("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-labelledby": labelledBy }, children);
    const backdrop = el("div", { class: "backdrop" }, sheet);
    const onKey = (e) => { if (e.key === "Escape") close(); };
    const onPop = () => {
      if (skipPops > 0) { skipPops--; return; }
      close(true);
    };
    function close(fromHistory) {
      if (!backdrop.isConnected) return;
      backdrop.remove();
      if (!document.querySelector(".backdrop")) document.body.style.overflow = "";
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("popstate", onPop);
      if (!fromHistory && history_pushed) { skipPops++; window.history.back(); }
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    }
    backdrop.addEventListener("click", (e) => { if (e.target === backdrop) close(); });
    document.addEventListener("keydown", onKey);
    // O botão "voltar" do celular fecha a janela em vez de sair do site
    let history_pushed = false;
    try {
      window.history.pushState({ modal: true }, "");
      history_pushed = true;
      window.addEventListener("popstate", onPop);
    } catch { /* ignore */ }
    document.body.append(backdrop);
    document.body.style.overflow = "hidden";
    setTimeout(() => { const f = sheet.querySelector(".sheet-close, button, a"); if (f) f.focus({ preventScroll: true }); }, 60);
    return () => close();
  }

  return { brl, csrf, el, $, cart, history, api, toast, openModal, load, store, EMOJI };
})();
