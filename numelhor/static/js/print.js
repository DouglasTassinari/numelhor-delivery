// Comanda: botão imprimir e impressão automática ao abrir (fora do modo "embutido").
(() => {
  const paper = (document.body.className.match(/paper-(\w+)/) || [])[1];
  if (paper && new URLSearchParams(location.search).get("papel")) {
    try { localStorage.setItem("nm_paper", JSON.stringify(paper)); } catch { /* ignore */ }
  }
  const btn = document.getElementById("print-now");
  if (btn) btn.addEventListener("click", () => window.print());
  if (document.body.dataset.autoprint === "1") {
    window.addEventListener("load", () => setTimeout(() => window.print(), 300));
  }
})();
