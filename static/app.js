/**
 * Google Reviews Web — Frontend
 */
const App = (() => {
  let _token = "";
  let _jobPollTimer = null;
  let _previewData = null;
  let _searchTimer = null;

  // ── Init ────────────────────────────────────────────────────────────────────
  async function init() {
    try {
      const cfg = await fetch("/api/config").then(r => r.json());

      if (!cfg.google_client_id) {
        showSetupError();
        return;
      }

      window.handleCredentialResponse = (response) => {
        _token = response.credential;
        _goToMain();
      };

      function _initGis() {
        if (typeof google === "undefined" || !google.accounts) {
          setTimeout(_initGis, 200);
          return;
        }
        google.accounts.id.initialize({
          client_id: cfg.google_client_id,
          callback: window.handleCredentialResponse,
          auto_select: false,
        });
        google.accounts.id.renderButton(
          document.getElementById("google-signin-btn"),
          { theme: "outline", size: "large", locale: "pt-BR", width: 260, text: "signin_with" }
        );
      }

      _initGis();

    } catch (e) {
      showSetupError("Erro ao conectar com o servidor.");
    }
  }

  function showSetupError(msg) {
    const el = document.getElementById("setup-error");
    el.textContent = msg || 'GOOGLE_CLIENT_ID não configurado no .env — consulte o README.';
    el.classList.remove("hidden");
    document.getElementById("google-signin-btn").classList.add("hidden");
  }

  function _goToMain() {
    document.getElementById("login-screen").classList.add("hidden");
    document.getElementById("main-screen").classList.remove("hidden");

    try {
      const payload = JSON.parse(atob(_token.split(".")[1]));
      document.getElementById("user-name").textContent = payload.name || payload.email;
      if (payload.picture) document.getElementById("user-photo").src = payload.picture;
      document.getElementById("user-info").classList.remove("hidden");
    } catch (_) {}
  }

  // ── Busca de empresas ────────────────────────────────────────────────────────
  function onSearchInput() {
    const q = document.getElementById("search-input").value.trim();
    const resultsEl = document.getElementById("search-results");

    // Limpa seleção anterior
    document.getElementById("maps-url").value = "";
    document.getElementById("verify-btn").classList.add("hidden");
    document.getElementById("main-error").classList.add("hidden");

    if (q.length < 2) {
      resultsEl.classList.add("hidden");
      resultsEl.innerHTML = "";
      return;
    }

    clearTimeout(_searchTimer);
    _searchTimer = setTimeout(() => _doSearch(q), 500);
  }

  async function _doSearch(q) {
    const spinner = document.getElementById("search-spinner");
    const resultsEl = document.getElementById("search-results");
    spinner.classList.remove("hidden");
    resultsEl.classList.add("hidden");

    try {
      const res = await _api(`/api/search?q=${encodeURIComponent(q)}`);
      if (!res.ok) throw new Error();
      const items = await res.json();

      resultsEl.innerHTML = "";

      if (!items || items.length === 0) {
        resultsEl.innerHTML = '<div class="search-empty">Nenhuma empresa encontrada.</div>';
      } else {
        items.forEach(item => {
          const div = document.createElement("div");
          div.className = "search-item";
          div.innerHTML = `
            <span class="search-item-name">${item.name}</span>
            ${item.address ? `<span class="search-item-addr">${item.address}</span>` : ""}
            ${item.rating ? `<span class="search-item-rating">★ ${item.rating}</span>` : ""}
          `;
          div.addEventListener("click", () => _selectResult(item));
          resultsEl.appendChild(div);
        });
      }

      resultsEl.classList.remove("hidden");
    } catch (_) {
      resultsEl.innerHTML = '<div class="search-empty">Erro na busca. Tente novamente.</div>';
      resultsEl.classList.remove("hidden");
    } finally {
      spinner.classList.add("hidden");
    }
  }

  function _selectResult(item) {
    document.getElementById("maps-url").value = item.url;
    document.getElementById("search-results").classList.add("hidden");
    document.getElementById("search-input").value = "";
    document.getElementById("search-input").placeholder = "Buscar outra empresa...";

    // Mostra card de empresa selecionada
    document.getElementById("selected-name").textContent = item.name;
    document.getElementById("selected-addr").textContent = item.address || "";
    document.getElementById("selected-company").classList.remove("hidden");
    document.getElementById("verify-btn").classList.add("hidden");
    document.getElementById("main-error").classList.add("hidden");

    // Dispara verify automaticamente
    verify();
  }

  function clearSelection() {
    document.getElementById("maps-url").value = "";
    document.getElementById("selected-company").classList.add("hidden");
    document.getElementById("verify-btn").classList.add("hidden");
    document.getElementById("search-input").value = "";
    document.getElementById("search-input").placeholder = "Ex: Nubank, iFood, Magazine Luiza...";
    document.getElementById("search-input").focus();
  }

  // ── Verificar empresa ────────────────────────────────────────────────────────
  async function verify() {
    const url = document.getElementById("maps-url").value.trim();
    const errorEl = document.getElementById("main-error");
    errorEl.classList.add("hidden");

    if (!url) { _err(errorEl, "Selecione uma empresa na busca."); return; }
    if (!url.includes("google.com") || !url.includes("/maps")) { _err(errorEl, "URL inválida."); return; }

    const btn = document.getElementById("verify-btn");
    _loading(btn, true);

    try {
      const res = await _api("/api/preview", { method: "POST", body: JSON.stringify({ url }) });

      if (res.status === 401) { const e = await res.json().catch(() => ({})); _err(errorEl, e.detail || "Sessão expirada. Recarregue a página."); return; }
      if (res.status === 503) {
        _err(errorEl, "Sessão Google Maps não configurada no servidor. Consulte o admin.");
        return;
      }
      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || "Erro ao verificar empresa");
      }

      _previewData = await res.json();
      _showPopup(_previewData);

    } catch (e) {
      _err(errorEl, e.message || "Falha ao conectar.");
    } finally {
      _loading(btn, false);
    }
  }

  // ── Popup ────────────────────────────────────────────────────────────────────
  function _showPopup(data) {
    document.getElementById("popup-company-name").textContent = data.name || "—";
    document.getElementById("popup-segment").textContent      = data.segment || "—";
    document.getElementById("popup-address").textContent      = data.address || "—";
    document.getElementById("popup-phone").textContent        = data.phone || "—";
    document.getElementById("popup-count").textContent =
      data.review_count > 0 ? `${data.review_count} avaliações` : "—";
    document.getElementById("popup-rating").textContent =
      data.rating && data.rating !== "—" ? `${data.rating} ★` : "—";
    document.getElementById("popup-overlay").classList.remove("hidden");
  }

  function closePopup(event) {
    if (!event || event.target.id === "popup-overlay" || event.target.classList.contains("popup-close")) {
      document.getElementById("popup-overlay").classList.add("hidden");
    }
  }

  // ── Extração ─────────────────────────────────────────────────────────────────
  async function extract() {
    const url     = document.getElementById("maps-url").value.trim();
    const empresa = _previewData?.name || "Empresa";

    document.getElementById("popup-overlay").classList.add("hidden");
    document.getElementById("progress-panel").classList.remove("hidden");
    _progress(0, 0, "Iniciando...");

    try {
      const res = await _api("/api/extract", {
        method: "POST",
        body: JSON.stringify({ url, empresa }),
      });

      if (!res.ok) {
        const e = await res.json().catch(() => ({}));
        throw new Error(e.detail || "Erro ao iniciar extração");
      }

      const { job_id } = await res.json();
      _pollJob(job_id);

    } catch (e) {
      document.getElementById("progress-panel").classList.add("hidden");
      _showErrPanel(e.message);
    }
  }

  function _pollJob(job_id) {
    _jobPollTimer = setInterval(async () => {
      try {
        const data = await fetch(`/api/status/${job_id}`).then(r => r.json());
        _progress(data.progress, data.total, data.message);

        if (data.status === "done") {
          clearInterval(_jobPollTimer);
          document.getElementById("progress-panel").classList.add("hidden");
          document.getElementById("done-summary").textContent = data.message;
          document.getElementById("download-link").href = `/api/download/${job_id}`;
          document.getElementById("done-panel").classList.remove("hidden");
        } else if (data.status === "error") {
          clearInterval(_jobPollTimer);
          document.getElementById("progress-panel").classList.add("hidden");
          _showErrPanel(data.error || data.message);
        }
      } catch (_) {}
    }, 2000);
  }

  function _showErrPanel(msg) {
    document.getElementById("error-detail").textContent = msg;
    document.getElementById("error-panel").classList.remove("hidden");
  }

  function reset() {
    if (_jobPollTimer) clearInterval(_jobPollTimer);
    _previewData = null;
    ["done-panel","error-panel","progress-panel","main-error"].forEach(id =>
      document.getElementById(id).classList.add("hidden")
    );
    document.getElementById("maps-url").value = "";
    document.getElementById("search-input").value = "";
    document.getElementById("search-results").classList.add("hidden");
    document.getElementById("verify-btn").classList.add("hidden");
  }

  // ── Helpers ───────────────────────────────────────────────────────────────────
  function _api(url, opts = {}) {
    return fetch(url, {
      ...opts,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${_token}`, ...(opts.headers || {}) },
    });
  }

  function _loading(btn, on) {
    btn.disabled = on;
    btn.querySelector(".btn-text").classList.toggle("hidden", on);
    btn.querySelector(".btn-spinner").classList.toggle("hidden", !on);
  }

  function _err(el, msg) { el.textContent = msg; el.classList.remove("hidden"); }

  function _progress(cur, tot, msg) {
    const pct = tot > 0 ? Math.min(Math.round(cur / tot * 100), 100) : 0;
    document.getElementById("progress-bar").style.width = pct + "%";
    document.getElementById("progress-text").textContent = msg;
    document.getElementById("progress-pct").textContent  = tot > 0 ? pct + "%" : "";
  }

  return { init, verify, closePopup, extract, reset, onSearchInput, clearSelection };
})();

document.addEventListener("DOMContentLoaded", App.init);
