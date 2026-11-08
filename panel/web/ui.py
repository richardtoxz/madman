from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

# Aba -> caminho. O servidor serve a mesma página em todos eles; o cliente escolhe a aba pelo caminho.
TAB_PATHS = {
    "home": "/inicio",
    "cleanup": "/limpeza",
    "protection": "/privacidade",
    "social": "/amigos",
    "guilds": "/servidores",
    "tracker": "/acompanhar",
    "quests": "/missoes",
    "history": "/historico",
}

_CSS_PATH = Path(__file__).with_name("madman.css")
_TEMPLATE_PATH = Path(__file__).with_name("dashboard.html")
# Lido uma vez no import; com WEB_DEV_RELOAD=1 o recarregamento do módulo relê o arquivo.
_TEMPLATE = _TEMPLATE_PATH.read_text(encoding="utf-8")


_ROOT = Path(__file__).resolve().parents[2]
_DESIGN_SYSTEM = _ROOT / "design-system" / "madman"
_BUILD_CSS_SCRIPT = _ROOT / "scripts" / "build_web_css.py"

# Só existe com WEB_DEV_RELOAD=1: pergunta ao servidor a cada segundo se algum arquivo do front mudou
# e recarrega a página quando muda.
_DEV_RELOAD_SNIPPET = """<script>
(function () {
  let version = null;
  async function poll() {
    if (!document.hidden) {
      try {
        const res = await fetch("/__dev/version", { cache: "no-store" });
        const data = await res.json();
        if (version !== null && version !== data.v) { location.reload(); return; }
        version = data.v;
      } catch (e) { /* servidor reiniciando: tenta de novo */ }
    }
    setTimeout(poll, 1000);
  }
  poll();
})();
</script>
"""

# Só existe no modo demo (python -m panel.main --demo): troca o estado do DemoManager sem reiniciar.
_DEMO_TOOLBAR_SNIPPET = """<div id="demo-toolbar" class="demo-toolbar" role="region" aria-label="Controles do modo demo">
  <strong class="demo-tag">DEMO</strong>
  <a id="demo-enter" class="demo-enter" href="/__demo/entrar" hidden>Entrar na conta demo</a>
  <span id="demo-controls" class="demo-controls" hidden>
    <label>Estado <select id="demo-mode"><option value="normal">Normal</option><option value="empty">Vazio</option><option value="error">Erro</option></select></label>
    <label>Latência <select id="demo-latency"><option value="0">0 ms</option><option value="800">800 ms</option><option value="3000">3 s</option></select></label>
    <label>Conta <select id="demo-connected"><option value="true">Conectada</option><option value="false">Desconectada</option></select></label>
  </span>
</div>
<style>
.demo-toolbar { position: fixed; left: 12px; bottom: 88px; z-index: 60; display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  max-width: calc(100vw - 24px); padding: 6px 10px; border-radius: var(--radius-md); background: var(--surface-dock);
  border: 1px dashed #f5a524; color: var(--text-muted); font: 500 11.5px/1.4 var(--font-sans); backdrop-filter: blur(16px); }
.demo-tag { color: #f5a524; letter-spacing: 0.08em; }
.demo-toolbar [hidden] { display: none; }
.demo-controls { display: inline-flex; flex-wrap: wrap; gap: 8px; }
.demo-toolbar select { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; font: inherit; padding: 2px 4px; }
.demo-enter { color: var(--text); }
</style>
<script>
(function () {
  const ids = { mode: "demo-mode", latency_ms: "demo-latency", connected: "demo-connected" };
  function show(state) {
    document.getElementById("demo-mode").value = state.mode;
    document.getElementById("demo-latency").value = String(state.latency_ms);
    document.getElementById("demo-connected").value = String(state.connected);
    document.getElementById("demo-controls").hidden = false;
  }
  async function save(key, raw) {
    const value = key === "latency_ms" ? Number(raw) : key === "connected" ? raw === "true" : raw;
    const res = await fetch("/api/demo/state", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": typeof csrfToken === "string" ? csrfToken : "" },
      body: JSON.stringify({ [key]: value }),
    });
    // Latência vale na próxima chamada; estado e conta mudam o que as telas já carregaram.
    if (res.ok && key !== "latency_ms") location.reload();
  }
  Object.entries(ids).forEach(([key, id]) => {
    document.getElementById(id).addEventListener("change", (e) => save(key, e.target.value));
  });
  fetch("/api/demo/state", { cache: "no-store" }).then(async (res) => {
    if (res.status === 401) { document.getElementById("demo-enter").hidden = false; return; }
    if (res.ok) show(await res.json());
  });
})();
</script>
"""


def dev_reload_enabled() -> bool:
    return os.environ.get("WEB_DEV_RELOAD", "").strip() == "1"


def _rebuild_css_if_stale() -> None:
    """Regenera madman.css a partir de design-system/ (o mesmo que scripts/build_web_css.py)."""
    spec = importlib.util.spec_from_file_location("_madman_build_web_css", _BUILD_CSS_SCRIPT)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    css = module.build()
    current = _CSS_PATH.read_text(encoding="utf-8") if _CSS_PATH.exists() else ""
    if current != css:
        _CSS_PATH.write_text(css, encoding="utf-8")


def dev_version() -> str:
    """Muda sempre que o HTML, o design system ou o CSS gerado mudam. Também regenera o CSS."""
    _rebuild_css_if_stale()
    paths = [_TEMPLATE_PATH, _CSS_PATH, *(p for p in _DESIGN_SYSTEM.rglob("*") if p.is_file())]
    return str(max(p.stat().st_mtime_ns for p in paths))


def _load_css() -> str:
    if dev_reload_enabled():
        _rebuild_css_if_stale()
    return _CSS_PATH.read_text(encoding="utf-8")


def render_dashboard_html(*, demo: bool = False) -> str:
    html = _TEMPLATE.replace("/*__MADMAN_CSS__*/", _load_css()).replace("__TAB_PATHS__", json.dumps(TAB_PATHS))
    if dev_reload_enabled():
        html = html.replace("</body>", _DEV_RELOAD_SNIPPET + "</body>")
    if demo:
        html = html.replace("</body>", _DEMO_TOOLBAR_SNIPPET + "</body>")
    return html
