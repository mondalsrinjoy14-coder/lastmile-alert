/* Offline attention cue only: never changes alert wording, severity or actions. */
(function () {
  "use strict";
  let context = null, enabled = false, pending = [], nodes = [], endTimer = null;
  const heard = new Set();
  try { JSON.parse(sessionStorage.getItem("lm_buzzer_heard") || "[]").forEach(key => heard.add(key)); } catch (_) {}

  // Update the visible, accessible sound status.
  function status(message) {
    const element = document.getElementById("buzzer-status");
    if (element) element.textContent = message;
  }
  // Persist deduplication only in this tab; blocked storage never breaks alerts.
  function remember(items) {
    items.forEach(item => heard.add(item.key));
    try { sessionStorage.setItem("lm_buzzer_heard", JSON.stringify(Array.from(heard).slice(-300))); } catch (_) {}
  }
  // Stop scheduled oscillator nodes immediately without disabling future warnings.
  function stop() {
    clearTimeout(endTimer);
    nodes.forEach(node => { try { node.stop(); } catch (_) {} });
    nodes = [];
    pending = [];
    status(enabled ? "Sound stopped. Listening for new Extreme or Severe alerts." : "Sound off. Enable for Extreme and Severe alerts.");
  }
  // Synthesize a short, volume-limited warning pattern entirely on this device.
  function playPending() {
    if (!enabled || !pending.length) return;
    if (!context || context.state !== "running") {
      status("An urgent alert is waiting. Tap Enable alert sound to allow audio.");
      enabled = false;
      updateButton();
      return;
    }
    const items = pending.slice();
    const extreme = items.some(item => item.severity === "extreme");
    stop();
    const count = extreme ? 6 : 3, duration = extreme ? 0.24 : 0.3, gap = 0.18;
    const start = context.currentTime + 0.03;
    for (let i = 0; i < count; i++) {
      const oscillator = context.createOscillator(), gain = context.createGain();
      const at = start + i * (duration + gap);
      oscillator.type = "sine";
      oscillator.frequency.setValueAtTime(extreme ? (i % 2 ? 980 : 740) : 620, at);
      gain.gain.setValueAtTime(0, at);
      gain.gain.linearRampToValueAtTime(0.12, at + 0.02);
      gain.gain.setValueAtTime(0.12, at + duration - 0.03);
      gain.gain.linearRampToValueAtTime(0, at + duration);
      oscillator.connect(gain); gain.connect(context.destination);
      oscillator.onended = function () { oscillator.disconnect(); gain.disconnect(); };
      oscillator.start(at); oscillator.stop(at + duration);
      nodes.push(oscillator);
    }
    remember(items);
    status((extreme ? "Extreme" : "Severe") + " alert — warning sound playing. Read the official instructions.");
    endTimer = setTimeout(function () {
      nodes = [];
      status("Sound enabled. Listening for new Extreme or Severe alerts.");
    }, (count * (duration + gap) + 0.05) * 1000);
  }
  // Show whether this page has actually unlocked browser audio.
  function updateButton() {
    const button = document.getElementById("buzzer-enable");
    if (button) {
      button.textContent = enabled ? "Mute alert sound" : "Enable alert sound";
      button.setAttribute("aria-pressed", String(enabled));
    }
  }
  // A deliberate click unlocks Web Audio under browser autoplay rules.
  async function toggle() {
    if (enabled) { enabled = false; stop(); updateButton(); return; }
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) { status("Alert sound is not supported in this browser. Visual alerts remain available."); return; }
    try {
      context = context || new Audio();
      await context.resume();
      enabled = context.state === "running";
      updateButton();
      status(enabled ? "Sound enabled. Listening for new Extreme or Severe alerts." : "Audio blocked. Tap Enable alert sound again.");
      playPending();
    } catch (_) { enabled = false; updateButton(); status("Audio could not start. Check site sound permissions and try again."); }
  }
  // Only explicit Extreme/Severe levels qualify; colours and keywords never infer urgency.
  function consider(alerts, scope) {
    pending = alerts.map(function (alert) {
      return {key: [scope, alert.id, alert.created_at || "", String(alert.severity).toLowerCase()].join(":"), severity: String(alert.severity).trim().toLowerCase()};
    }).filter(item => ["extreme", "severe"].includes(item.severity) && !heard.has(item.key));
    if (pending.length) {
      if (enabled) playPending();
      else status("Extreme or Severe alert available. Tap Enable alert sound to hear the warning.");
    }
  }
  window.LastMileBuzzer = {consider: consider, stop: stop};
  document.addEventListener("DOMContentLoaded", function () {
    const enable = document.getElementById("buzzer-enable"), cancel = document.getElementById("buzzer-stop");
    if (enable) enable.addEventListener("click", toggle);
    if (cancel) cancel.addEventListener("click", stop);
  });
  window.addEventListener("pagehide", stop);
})();
