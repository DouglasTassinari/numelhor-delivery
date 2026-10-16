// Página do pedido: atualiza sozinha quando o pagamento cai ou a loja muda o status.
(() => {
  const { $, api, toast, csrf, history } = NM;
  const root = $("#order");
  const token = root.dataset.token;
  if (!history.list().some((o) => o.token === token)) {
    history.add({ token, id: Number(root.dataset.id), total: root.dataset.total, created: Date.now(), items: [] });
  }
  const status = root.dataset.status;
  const payment = root.dataset.payment;
  const finished = status === "concluido" || status === "cancelado";

  document.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const input = $(btn.dataset.copy);
      try {
        await navigator.clipboard.writeText(input.value);
      } catch {
        input.select();
        document.execCommand("copy");
      }
      toast("Código Pix copiado ✔");
    });
  });

  const countdown = $("#countdown");
  if (countdown && countdown.dataset.expires) {
    const end = Date.parse(countdown.dataset.expires);
    const tick = () => {
      const s = Math.max(0, Math.round((end - Date.now()) / 1000));
      countdown.textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
      if (s === 0) setTimeout(() => location.reload(), 4000);
      else setTimeout(tick, 1000);
    };
    tick();
  }

  const sim = $("#sim-pay");
  if (sim) {
    sim.addEventListener("click", async () => {
      sim.disabled = true;
      await fetch(sim.dataset.url, { method: "POST", headers: { "X-CSRF-Token": csrf() }, credentials: "same-origin" });
      location.reload();
    });
  }

  if (finished) return;
  const waitingPayment = status === "aguardando_pagamento";
  const interval = waitingPayment ? 4000 : 20000;
  const poll = async () => {
    const r = await api(`/api/pedido/${token}`);
    if (r.ok && (r.status !== status || r.payment_status !== payment)) {
      location.reload();
      return;
    }
    setTimeout(poll, interval);
  };
  setTimeout(poll, interval);
})();
