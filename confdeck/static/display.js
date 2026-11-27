import { $, esc, api, liveSocket } from "./common.js";
import { syncOutput } from "./player.js";
let closeSocket,
  heartbeat,
  connected = false,
  metrics = {},
  lastState;
async function enter() {
  try {
    render(await api("/view/state"));
    $("#pairing").innerHTML = "";
    closeSocket?.();
    closeSocket = liveSocket("/view/ws", render, (ok) => {
      connected = ok;
      $("#display-status").textContent = ok
        ? ""
        : "Reconnecting to the laptop…";
    });
    clearInterval(heartbeat);
    heartbeat = setInterval(async () => {
      try {
        const players = [...document.querySelectorAll("camera-player")];
        const latest = players
          .map((p) => p.metrics)
          .filter((m) => m.fps != null);
        const fps = latest.length
          ? Math.min(...latest.map((m) => m.fps))
          : null;
        const note =
          players.length > 1 && fps != null && fps < 8
            ? "This display may be overloaded. Choose Single view and select one camera."
            : "";
        await api("/view/heartbeat", { ...metrics, fps, note });
        if (note) $("#display-status").textContent = note;
        else if (connected) $("#display-status").textContent = "";
      } catch (error) {
        if (error.status === 401) {
          closeSocket?.();
          clearInterval(heartbeat);
          document.querySelectorAll("camera-player").forEach((p) => p.remove());
          $("#display-grid").handoff?.remove();
          pair();
        }
      }
    }, 3000);
  } catch (error) {
    if (error.status === 401) pair();
    else {
      $("#display-status").textContent =
        "Waiting for ConfDeck. Check the laptop and local network.";
      setTimeout(enter, 3000);
    }
  }
}
function pair() {
  const code = new URLSearchParams(location.hash.slice(1)).get("code");
  history.replaceState(null, "", location.pathname);
  $("#pairing").innerHTML =
    `<main class="login-card"><div class="brand-mark">C<span>▦</span></div><h1>Connect this display</h1><p>On the laptop, click “Connect to TV” and enter the six-digit pairing code here.</p><form id="pair-form"><label>Pairing code<input name="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" value="${esc(code || "")}" autocomplete="one-time-code" required></label><label style="margin-top:16px">Display name<input name="name" value="Room display" style="font-size:15px;letter-spacing:0" maxlength="80" required></label><button class="primary" type="submit">Connect display →</button><p id="pair-error" class="bad" role="alert"></p></form></main>`;
  if (code) $("#pair-form").requestSubmit();
}
function render(data) {
  lastState = data;
  const grid = $("#display-grid");
  const ordered=data.display.cameras.length?data.display.cameras:data.cameras.map(c=>c.id);
  syncOutput(grid,data.cameras,data.display.layout,ordered,data.display.blank);
  $("#blackout").hidden = !data.display.blank;
  if (!data.cameras.length && !grid.handoff)
    $("#display-status").textContent =
      "Waiting for the operator to select cameras.";
}
document.addEventListener("submit", async (event) => {
  if (event.target.id !== "pair-form") return;
  event.preventDefault();
  const f = new FormData(event.target);
  try {
    await api("/pair", { code: f.get("code"), name: f.get("name") });
    await enter();
  } catch (error) {
    $("#pair-error").textContent = error.detail?.detail || error.message;
  }
});
document.addEventListener("metrics", (event) => {
  metrics = event.detail;
});
$("#fullscreen").onclick = () =>
  document.documentElement.requestFullscreen?.().catch(() => {
    $("#display-status").textContent = "Use the browser’s full-screen option.";
  });
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    api("/view/state")
      .then(render)
      .catch(() => enter());
  }
});
window.addEventListener("online", () => enter());
enter();
