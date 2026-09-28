/* LastMile Alert - shared frontend helpers (vanilla JS, no libraries).
   All API calls use relative paths so the app works on any host/port. */

(function () {
  "use strict";

  // ---- diagnostics: record every API call for the diagnostics panel ----
  window.__diag = [];
  async function api(path, method, body) {
    // Wrapper around fetch that times the call and records diagnostics.
    const t0 = performance.now();
    // Accept both api(path, "POST", data) and the existing citizen fetch-style calls.
    const options = method && typeof method === "object" ? method : null;
    if (options) {
      method = options.method || "GET";
      body = options.body;
      if (typeof body === "string") {
        try { body = JSON.parse(body); }
        catch (_) { return {ok:false, status:0, data:{error:"Invalid request data. Please refresh and try again."}}; }
      }
    }
    const opts = { method: method || "GET", headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    let status = 0, payload = null, ok = false;
    try {
      const resp = await fetch(path, opts);
      status = resp.status; ok = resp.ok;
      const text = await resp.text();
      try { payload = JSON.parse(text); } catch (e) { payload = text; }
    } catch (err) {
      payload = { error: "Backend unreachable. Is the server running? (python app.py)" };
    }
    window.__diag.unshift({
      ts: new Date().toLocaleTimeString(),
      endpoint: (method || "GET") + " " + path,
      status: status,
      ms: Math.round(performance.now() - t0),
      payload: body === undefined ? null : body,
      response: typeof payload === "string" ? payload.slice(0, 200) : payload,
    });
    if (window.__diag.length > 60) window.__diag.pop();
    return { ok: ok, status: status, data: payload };
  }
  window.api = api;

  // ---- backend health indicator in the navbar ----
  async function pollHealth() {
    const dot = document.getElementById("health-dot");
    const txt = document.getElementById("health-text");
    if (!dot || !txt) return;
    try {
      const r = await fetch("/api/health");
      if (r.ok) {
        dot.className = "dot ok";
        txt.textContent = "Backend Online";
        return;
      }
      throw new Error("bad status");
    } catch (e) {
      dot.className = "dot bad";
      txt.textContent = "Backend Offline \u2014 run python app.py";
    }
  }
  window.pollHealth = pollHealth;
  setInterval(pollHealth, 15000);
  document.addEventListener("DOMContentLoaded", pollHealth);

  // ---- accessibility: big text / high contrast (persisted) ----
  function safeGet(key) { try { return localStorage.getItem(key); } catch (_) { return null; } }
  function safeSet(key, value) { try { localStorage.setItem(key, value); } catch (_) {} }
  function applyA11y() {
    document.documentElement.classList.toggle("big-text", safeGet("lm_big") === "1");
    document.documentElement.classList.toggle("high-contrast", safeGet("lm_hc") === "1");
    const b = document.getElementById("btn-big"), h = document.getElementById("btn-hc");
    if (b) b.classList.toggle("on", safeGet("lm_big") === "1");
    if (h) h.classList.toggle("on", safeGet("lm_hc") === "1");
  }
  window.toggleBigText = function () {
    safeSet("lm_big", safeGet("lm_big") === "1" ? "0" : "1");
    applyA11y();
  };
  window.toggleHighContrast = function () {
    safeSet("lm_hc", safeGet("lm_hc") === "1" ? "0" : "1");
    applyA11y();
  };
  document.addEventListener("DOMContentLoaded", applyA11y);

  // ---- read aloud via the browser's built-in speech synthesis ----
  window.readAloud = function (text, lang) {
    // Speaks text with no API key and no internet.
    if (!("speechSynthesis" in window)) { alert("Speech synthesis is not supported in this browser."); return; }
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    if (lang) u.lang = lang;
    window.speechSynthesis.speak(u);
  };
  window.stopAloud = function () {
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  };

  // Clear an old validation error before a form is submitted again.
  window.clearError = function (boxId) {
    const box = document.getElementById(boxId);
    if (box) box.textContent = "";
  };

  // ---- friendly error banner (never shows tracebacks) ----
  window.showError = function (boxId, message) {
    const box = document.getElementById(boxId);
    if (!box) { alert(message); return; }
    box.innerHTML = "";
    const div = document.createElement("div");
    div.className = "pending";
    div.style.borderColor = "#d32f2f"; div.style.background = "#ffebee"; div.style.color = "#7f0000";
    div.textContent = "\u26a0 " + message;
    box.appendChild(div);
  };

  // ---- one-click demo reset ----
  window.demoReset = async function () {
    if (!confirm("Reset demo? This wipes the database and reseeds the sample alerts + recipients.")) return;
    const r = await api("/api/reset", "POST", {});
    if (r.ok) { alert("Demo reset complete."); location.reload(); }
    else window.showError("page-error", (r.data && r.data.error) || "Reset failed.");
  };

  // ---- copy error details (used by the friendly error page) ----
  window.copyErrorDetails = function () {
    const el = document.getElementById("error-details");
    const text = el ? el.textContent : document.title;
    if (navigator.clipboard) navigator.clipboard.writeText(text).then(
      function () { alert("Error details copied."); },
      function () { alert("Copy failed."); });
  };

  window.escapeHtml = function (s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
})();
