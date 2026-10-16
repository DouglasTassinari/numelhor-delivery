// Busca os pedidos a cada 5 segundos. Fica num "worker" porque o navegador não congela os
// relógios dele quando a aba do painel está em segundo plano (os da página, sim).
let url = null;
let timer = null;

async function tick() {
  try {
    const r = await fetch(url, { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } });
    const data = await r.json().catch(() => null);
    postMessage({ status: r.status, data });
  } catch {
    postMessage({ status: 0, data: null });
  }
}

onmessage = (e) => {
  if (!e.data || !e.data.url) return;
  url = e.data.url;
  clearInterval(timer);
  tick();
  timer = setInterval(tick, 5000);
};
