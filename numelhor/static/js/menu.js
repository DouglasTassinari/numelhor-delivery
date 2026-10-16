// Cardápio: busca, montar o lanche (tamanho, adicionais, tirar ingredientes, molhos) e sacola.
(() => {
  const { brl, el, $, cart, toast, EMOJI, openModal } = NM;
  const menu = JSON.parse($("#menu-data").textContent);
  const store = JSON.parse($("#store-data").textContent);

  const products = new Map();
  for (const c of menu.categories) for (const p of c.products) products.set(p.id, p);
  const sauces = new Map(menu.sauces.map((s) => [s.id, s]));
  const optionsOf = (p) => new Map(p.groups.flatMap((g) => g.options.map((o) => [o.id, { ...o, group: g }])));

  // Sacola salva: limpa itens/opções que saíram do cardápio
  let lines = cart.get().filter((l) => products.has(l.product_id)).map((l) => {
    const p = products.get(l.product_id);
    const removable = new Set(p.ingredients.filter((i) => i.removable).map((i) => i.id));
    const opts = optionsOf(p);
    return {
      product_id: p.id,
      qty: Math.min(20, Math.max(1, parseInt(l.qty, 10) || 1)),
      removed: (l.removed || []).filter((id) => removable.has(id)),
      sauces: p.allows_sauces ? (l.sauces || []).filter((id) => sauces.has(id)) : [],
      options: (l.options || []).filter((id) => opts.has(id)),
      notes: String(l.notes || "").slice(0, 140),
    };
  });

  const unitPrice = (l) => {
    const p = products.get(l.product_id);
    const opts = optionsOf(p);
    return p.price_cents
      + l.sauces.reduce((s, id) => s + sauces.get(id).price_cents, 0)
      + l.options.reduce((s, id) => s + (opts.get(id) ? opts.get(id).price_cents : 0), 0);
  };
  const total = () => lines.reduce((s, l) => s + unitPrice(l) * l.qty, 0);
  const keyOf = (l) => [l.product_id, [...l.removed].sort().join("."), [...l.sauces].sort().join("."),
    [...l.options].sort().join("."), l.notes.trim()].join("|");

  function describe(l) {
    const p = products.get(l.product_id);
    const opts = optionsOf(p);
    const out = [];
    const byGroup = new Map();
    for (const id of l.options) {
      const o = opts.get(id);
      if (!o) continue;
      if (!byGroup.has(o.group.name)) byGroup.set(o.group.name, []);
      byGroup.get(o.group.name).push(o.name);
    }
    byGroup.forEach((names, g) => out.push({ text: `${g}: ${names.join(", ")}` }));
    if (l.removed.length) {
      const names = p.ingredients.filter((i) => l.removed.includes(i.id)).map((i) => i.name.toLowerCase());
      out.push({ text: `SEM ${names.join(", ")}`, sem: true });
    }
    if (l.sauces.length) out.push({ text: `Molhos: ${l.sauces.map((id) => sauces.get(id).name).join(", ")}` });
    if (l.notes) out.push({ text: `Obs.: ${l.notes}` });
    return out;
  }

  function save() {
    cart.set(lines);
    const n = lines.reduce((s, l) => s + l.qty, 0);
    $("#cartbar").hidden = n === 0;
    $("#cart-count").textContent = n;
    $("#cart-total").textContent = brl(total());
    const wa = $("#bar-wa");
    wa.classList.toggle("solo", n === 0);
    wa.querySelector(".lbl").textContent = n === 0 ? "Chamar no WhatsApp" : "WhatsApp";
  }

  // ---------- Janela do produto ----------
  const choice = ({ type, name, checked, label, extra, cls = "", onChange }) => {
    const input = el("input", { type, name });
    input.checked = !!checked;
    input.addEventListener("change", () => onChange(input));
    return el("label", { class: `choice ${cls}` }, input,
      el("span", { class: "box" }, el("span", { class: "mark", "aria-hidden": "true" }),
        el("span", { class: "name" }, label), extra ? el("span", { class: "extra" }, extra) : null));
  };

  function openProduct(id, editIndex = null) {
    const p = products.get(id);
    if (!p) return;
    const existing = editIndex != null ? lines[editIndex] : null;
    const state = existing
      ? { ...existing, removed: [...existing.removed], sauces: [...existing.sauces], options: [...existing.options] }
      : { product_id: p.id, qty: 1, removed: [], sauces: [], options: [], notes: "" };
    let close;

    const qtyOut = el("output", { "aria-live": "polite" }, state.qty);
    const addBtn = el("button", { class: "btn btn-yellow btn-lg", type: "button" });
    const refresh = () => {
      qtyOut.textContent = state.qty;
      addBtn.textContent = `${existing ? "Salvar" : "Adicionar"} • ${brl(unitPrice(state) * state.qty)}`;
    };

    const body = el("div", { class: "sheet-body" },
      el("h3", { id: "sheet-title" }, p.name),
      p.description ? el("p", { class: "desc" }, p.description) : null,
      el("div", { class: "price-row" },
        el("span", { class: "price" }, brl(p.from_cents)),
        p.old_price_cents ? el("span", { class: "old" }, brl(p.old_price_cents)) : null));

    // Grupos: Tamanho, Sabor, Adicionais...
    const groupBlocks = [];
    for (const g of p.groups) {
      const single = g.max === 1;
      const required = g.min > 0;
      const hint = single ? (required ? "Escolha 1 opção" : "Se quiser, escolha 1")
        : `Escolha até ${g.max}${required ? ` (mínimo ${g.min})` : ""}`;
      const list = el("div", { class: "choices two" });
      const block = el("div", { class: "block" },
        el("div", { class: "block-title" }, el("h4", {}, g.name),
          el("span", { class: required ? "req" : "req opt" }, required ? "OBRIGATÓRIO" : "OPCIONAL")),
        el("p", { class: "hint" }, hint), list);
      for (const o of g.options) {
        list.append(choice({
          type: single ? "radio" : "checkbox", name: `g${g.id}`, checked: state.options.includes(o.id),
          label: o.name, extra: o.price_cents ? `+ ${brl(o.price_cents)}` : null,
          onChange: (input) => {
            const ids = new Set(g.options.map((x) => x.id));
            if (single) state.options = state.options.filter((x) => !ids.has(x)).concat(input.checked ? [o.id] : []);
            else if (input.checked) {
              if (state.options.filter((x) => ids.has(x)).length >= g.max) {
                input.checked = false;
                toast(`Pode escolher até ${g.max} em ${g.name.toLowerCase()}.`);
                return;
              }
              state.options.push(o.id);
            } else state.options = state.options.filter((x) => x !== o.id);
            block.classList.remove("invalid");
            refresh();
          },
        }));
      }
      groupBlocks.push({ g, block });
      body.append(block);
    }

    const removable = p.ingredients.filter((i) => i.removable);
    const fixed = p.ingredients.filter((i) => !i.removable);
    if (removable.length) {
      const list = el("div", { class: "choices two" });
      for (const ing of removable) {
        list.append(choice({
          type: "checkbox", name: "ing", checked: !state.removed.includes(ing.id), label: ing.name, cls: "ing",
          extra: el("span", {}, el("span", { class: "on" }, "Com"), el("span", { class: "off" }, "SEM")),
          onChange: (input) => {
            state.removed = input.checked ? state.removed.filter((x) => x !== ing.id) : [...state.removed, ing.id];
          },
        }));
      }
      body.append(el("div", { class: "block" },
        el("div", { class: "block-title" }, el("h4", {}, "Quer tirar algum ingrediente?")),
        el("p", { class: "hint" }, "Tudo marcado vem no lanche. Toque para tirar o que você não quer."),
        list,
        fixed.length ? el("p", { class: "fixed-ings" }, `Sempre vem: ${fixed.map((i) => i.name).join(", ")}.`) : null));
    }

    if (p.allows_sauces && sauces.size) {
      const list = el("div", { class: "choices two" });
      for (const s of sauces.values()) {
        list.append(choice({
          type: "checkbox", name: "molho", checked: state.sauces.includes(s.id), label: s.name,
          extra: s.price_cents ? `+ ${brl(s.price_cents)}` : "grátis",
          onChange: (input) => {
            state.sauces = input.checked ? [...state.sauces, s.id] : state.sauces.filter((x) => x !== s.id);
            refresh();
          },
        }));
      }
      body.append(el("div", { class: "block" },
        el("div", { class: "block-title" }, el("h4", {}, "Molhos"), el("span", { class: "req opt" }, "OPCIONAL")),
        el("p", { class: "hint" }, "Se não marcar nenhum, vai sem molho."), list));
    }

    const notes = el("textarea", { maxlength: "140", rows: "2", id: "item-notes", placeholder: "Ex.: bem passado, cortar ao meio…" });
    notes.value = state.notes;
    body.append(el("div", { class: "block" },
      el("div", { class: "block-title" }, el("label", { for: "item-notes" }, el("h4", {}, "Alguma observação?")),
        el("span", { class: "req opt" }, "OPCIONAL")), notes));

    const qty = el("div", { class: "qty" },
      el("button", { type: "button", "aria-label": "Diminuir quantidade", onclick: () => { if (state.qty > 1) state.qty--; refresh(); } }, "−"),
      qtyOut,
      el("button", { type: "button", "aria-label": "Aumentar quantidade", onclick: () => { if (state.qty < 20) state.qty++; refresh(); } }, "+"));

    addBtn.addEventListener("click", () => {
      if (!store.open) { toast(`A loja está fechada agora. ${store.label}.`); return; }
      for (const { g, block } of groupBlocks) {
        const n = state.options.filter((x) => g.options.some((o) => o.id === x)).length;
        if (n < g.min) {
          block.classList.add("invalid");
          block.scrollIntoView({ behavior: "smooth", block: "center" });
          toast(`Falta escolher: ${g.name}`);
          return;
        }
      }
      state.notes = notes.value.trim().slice(0, 140);
      if (existing) lines[editIndex] = state;
      else {
        const same = lines.find((l) => keyOf(l) === keyOf(state));
        if (same) same.qty = Math.min(20, same.qty + state.qty);
        else lines.push(state);
      }
      save();
      close();
      toast(existing ? "Item atualizado ✔" : `✔ ${p.name} na sacola!`);
    });

    const img = el("div", { class: "sheet-img" },
      p.image ? el("img", { src: p.image, alt: "" }) : el("span", { class: "emoji" }, EMOJI[p.icon] || "🍽️"),
      el("button", { class: "sheet-close", type: "button", "aria-label": "Fechar", onclick: () => close() }, "×"));
    refresh();
    close = openModal([el("div", { class: "sheet-scroll" }, img, body), el("div", { class: "sheet-foot" }, qty, addBtn)], "sheet-title");
  }

  // ---------- Sacola ----------
  function openCart() {
    let close;
    const scroll = el("div", { class: "sheet-scroll" });
    const foot = el("div", { class: "sheet-foot" });
    const head = el("div", { class: "sheet-head" },
      el("h3", { id: "cart-title" }, "Sua sacola"),
      el("button", { class: "sheet-close", type: "button", "aria-label": "Fechar", onclick: () => close() }, "×"));

    const render = () => {
      scroll.replaceChildren();
      if (!lines.length) {
        scroll.append(el("div", { class: "empty" },
          el("img", { src: "/static/img/que-gatuuu.webp", alt: "" }),
          el("p", {}, "Sua sacola está vazia. Bora escolher um lanche brabo?")));
        foot.replaceChildren(el("button", { class: "btn btn-yellow btn-lg btn-block", type: "button", onclick: () => close() }, "Ver o cardápio"));
        return;
      }
      const ul = el("ul", { class: "cart-lines" });
      lines.forEach((l, idx) => {
        const p = products.get(l.product_id);
        ul.append(el("li", { class: "cart-line" },
          el("div", { class: "top" }, el("span", {}, p.name), el("span", {}, brl(unitPrice(l) * l.qty))),
          el("div", { class: "details" }, describe(l).map((d) => el("div", { class: d.sem ? "sem" : "" }, d.text))),
          el("div", { class: "row" },
            el("div", { class: "qty sm" },
              el("button", { type: "button", "aria-label": `Diminuir ${p.name}`, onclick: () => { if (l.qty > 1) l.qty--; else lines.splice(idx, 1); save(); render(); } }, "−"),
              el("output", {}, l.qty),
              el("button", { type: "button", "aria-label": `Aumentar ${p.name}`, onclick: () => { if (l.qty < 20) l.qty++; save(); render(); } }, "+")),
            el("span", {},
              el("button", { class: "link-btn", type: "button", onclick: () => { close(); openProduct(p.id, idx); } }, "Mudar"),
              el("button", { class: "link-btn", type: "button", onclick: () => { lines.splice(idx, 1); save(); render(); } }, "Tirar")))));
      });
      scroll.append(ul, el("div", { class: "totals" },
        el("div", { class: "grand" }, el("span", {}, "Subtotal"), el("span", {}, brl(total()))),
        el("p", { class: "hint" }, "A taxa de entrega aparece no próximo passo.")));
      foot.replaceChildren(store.open
        ? el("a", { class: "btn btn-yellow btn-lg btn-block", href: "/checkout" }, "Continuar →")
        : el("button", { class: "btn btn-yellow btn-lg btn-block", type: "button", disabled: true }, "Loja fechada agora"));
    };
    render();
    close = openModal([head, scroll, foot], "cart-title");
  }

  // ---------- Entrega e horários ----------
  function openInfo() {
    const frag = $("#info-template").content.cloneNode(true);
    const close = openModal([...frag.children], "info-title");
    document.querySelector(".backdrop [data-close]").addEventListener("click", () => close());
  }

  // ---------- Busca ----------
  const norm = (t) => t.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const search = $("#search");
  const clearBtn = $("#search-clear");
  const sections = [...document.querySelectorAll(".menu-section")];
  function filter() {
    const q = norm(search.value.trim());
    clearBtn.hidden = !q;
    let any = false;
    for (const sec of sections) {
      let shown = 0;
      sec.querySelectorAll("[data-product]").forEach((card) => {
        const ok = !q || norm(card.textContent).includes(q);
        card.hidden = !ok;
        if (ok) shown++;
      });
      sec.hidden = (q && sec.id === "destaques") || shown === 0;
      if (!sec.hidden) any = true;
    }
    $("#no-results").hidden = any;
  }
  search.addEventListener("input", filter);
  clearBtn.addEventListener("click", () => { search.value = ""; filter(); search.focus(); });

  // ---------- Ligações ----------
  document.querySelectorAll("[data-product]").forEach((b) => b.addEventListener("click", () => openProduct(Number(b.dataset.product))));
  document.querySelectorAll("[data-open-info]").forEach((b) => b.addEventListener("click", openInfo));
  $("#cartbar").addEventListener("click", openCart);

  const chips = new Map([...document.querySelectorAll(".chip")].map((c) => [c.getAttribute("href").slice(1), c]));
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      chips.forEach((c) => c.classList.remove("active"));
      const chip = chips.get(e.target.id);
      if (chip) {
        chip.classList.add("active");
        chip.parentElement.scrollTo({ left: chip.offsetLeft - 16, behavior: "smooth" });
      }
    }
  }, { rootMargin: "-30% 0px -65% 0px" });
  sections.forEach((s) => io.observe(s));

  save();
  if (new URLSearchParams(location.search).get("sacola") && lines.length) openCart();
})();
