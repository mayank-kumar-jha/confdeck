export const room = {state:null, socket:null};
export function sendManual(value){if(room.socket?.readyState===1)room.socket.send(JSON.stringify(value));}
export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [
  ...root.querySelectorAll(selector),
];
export const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        ch
      ],
  );
export const icon = (name) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${{ home: '<path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/>', camera: '<rect x="3" y="6" width="13" height="12" rx="3"/><path d="m16 10 5-3v10l-5-3"/>', display: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8m-4-4v4"/>', mic: '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2m-7 9v3m-4 0h8"/>', health: '<path d="M2 12h5l3-8 4 16 3-8h5"/>', settings: '<circle cx="12" cy="12" r="3"/><path d="m9 3 1-1h4l1 3 3 1 3 2v4l-3 2-1 3-2 4h-4l-2-3-3-1-4-2v-4l3-2 1-3Z"/>', arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>', plus: '<path d="M12 5v14M5 12h14"/>', check: '<path d="m5 12 4 4 10-10"/>', scan: '<path d="M3 8V3h5m8 0h5v5m0 8v5h-5M8 21H3v-5"/><circle cx="12" cy="12" r="4"/>', lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>', close: '<path d="m6 6 12 12M6 18 18 6"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>', refresh: '<path d="M20 8a8 8 0 1 0 0 8m0-13v5h-5"/>', sun: '<circle cx="12" cy="12" r="4"/><path d="M12 1v2m0 18v2M1 12h2m18 0h2M4 4l2 2m12 12 2 2M4 20l2-2M18 6l2-2"/>' }[name] || '<circle cx="12" cy="12" r="8"/>'}</svg>`;
export async function api(path, body, method) {
  const options = {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: { "x-confdeck": "1" },
  };
  if (body !== undefined) {
    if (body instanceof FormData) options.body = body;
    else {
      options.body = JSON.stringify(body);
      options.headers["content-type"] = "application/json";
    }
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(
      data.error?.title || (response.status === 404 && /\/(speed-test|preset-speed)$/.test(path)
        ? "Preset speed requires the updated backend. Restart ConfDeck and reload this page."
        : typeof data.detail === "string" ? data.detail : `This action could not finish (HTTP ${response.status})`),
    );
    error.detail = data.error;
    error.status = response.status;
    throw error;
  }
  return data;
}
export function toast(message, error = false) {
  const node = $("#toast");
  if (!node) return;
  node.textContent = message;
  node.className = error ? "show error" : "show";
  clearTimeout(node.timer);
  node.timer = setTimeout(() => (node.className = ""), 7000);
}
export function fail(error) {
  toast(
    [
      error.message,
      error.detail?.detail,
      error.detail?.cause,
      error.detail?.fix,
    ]
      .filter(Boolean)
      .join(" "),
    true,
  );
}
export function statusClass(state = "") {
  return /^(Online|Connected|Live|On|Listening)/.test(state)
    ? "good"
    : /^(Error|Wrong|Unsupported|Unavailable)/.test(state)
      ? "bad"
      : "warn";
}
export function badge(state) {
  return `<span class="badge ${statusClass(state)}"><span class="dot"></span>${esc(state)}</span>`;
}
export function bindTooltips(root = document) {
  $$("button,input,select,a,summary", root).forEach((el) => {
    if (!el.title)
      el.title =
        el.getAttribute("aria-label") ||
        el.textContent.trim() ||
        el.name ||
        "Edit this value";
  });
}
export function liveSocket(path, onState, onConnection) {
  let socket,
    timer,
    attempts = 0,
    closed = false;
  function connect() {
    if (closed) return;
    socket = new WebSocket(
      `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${path}`,
    );
    if(path === "/api/ws") room.socket = socket;
    socket.onopen = () => {
      attempts = 0;
      onConnection(true);
    };
    socket.onmessage = (e) => {
      try {
        onState(JSON.parse(e.data));
      } catch (error) {
        console.warn("Could not read a room update", error.name);
      }
    };
    socket.onclose = () => {
      onConnection(false);
      if (!closed)
        timer = setTimeout(
          connect,
          Math.min(15000, 500 * 2 ** attempts++) + Math.random() * 300,
        );
    };
    socket.onerror = () => socket.close();
  }
  connect();
  return () => {
    closed = true;
    clearTimeout(timer);
    socket?.close();
  };
}
export class MicRadar extends HTMLElement {
  constructor() {
    super();
    this.trail = [];
    this._data = null;
  }
  connectedCallback() {
    this.innerHTML =
      '<canvas width="640" height="580" role="img" aria-label="Live microphone direction radar"></canvas>';
    this.draw();
  }
  set data(value) {
    this._data = value;
    if (value?.position && !value.stale_fields?.length && value.age < 5) {
      const p = value.position;
      const last = this.trail.at(-1);
      if (
        !last ||
        last.azimuth !== p.azimuth ||
        last.elevation !== p.elevation
      ) {
        this.trail.push(p);
        if (this.trail.length > 25) this.trail.shift();
      }
    }
    this.draw();
  }
  draw() {
    const c = this.querySelector("canvas");
    if (!c) return;
    const ctx = c.getContext("2d"),
      x = 320,
      y = 285,
      r = 220;
    ctx.clearRect(0, 0, 640, 580);
    ctx.fillStyle = "#102a40";
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#31516b";
    ctx.lineWidth = 1;
    [0.25, 0.5, 0.75, 1].forEach((f) => {
      ctx.beginPath();
      ctx.arc(x, y, r * f, 0, Math.PI * 2);
      ctx.stroke();
    });
    ctx.setLineDash([4, 6]);
    for (let d = 0; d < 360; d += 30) {
      const a = (d * Math.PI) / 180;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x + Math.sin(a) * r, y - Math.cos(a) * r);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    ctx.fillStyle = "#a5bdcc";
    ctx.font = "18px Inter, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("0°", x, 35);
    ctx.fillText("180°", x, 550);
    ctx.fillText("270°", 46, y + 6);
    ctx.fillText("90°", 596, y + 6);
    const data = this._data;
    const fresh = data?.position && data.age < 5 && !data.stale_fields?.length;
    if (fresh) {
      const p = data.position,
        a = ((p.azimuth - 90) * Math.PI) / 180,
        bw = ((data.beam_width || 30) * Math.PI) / 360;
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.arc(x, y, r, a - bw, a + bw);
      ctx.closePath();
      const gradient = ctx.createRadialGradient(x, y, 0, x, y, r);
      gradient.addColorStop(0, "#ff8a3d55");
      gradient.addColorStop(1, "#ff8a3d08");
      ctx.fillStyle = gradient;
      ctx.fill();
      ctx.strokeStyle = "#ff8a3d55";
      ctx.stroke();
      this.trail.forEach((p, i) => {
        const a = (p.azimuth * Math.PI) / 180,
          rr = r * (0.25 + (0.6 * p.elevation) / 90);
        ctx.fillStyle = `rgba(58,160,255,${0.08 + (0.55 * i) / this.trail.length})`;
        ctx.beginPath();
        ctx.arc(x + Math.sin(a) * rr, y - Math.cos(a) * rr, 3, 0, 7);
        ctx.fill();
      });
      const rr = r * (0.25 + (0.6 * p.elevation) / 90),
        px = x + Math.cos(a) * rr,
        py = y + Math.sin(a) * rr;
      ctx.beginPath();
      ctx.arc(px, py, 15, 0, 7);
      ctx.fillStyle = "#ff8a3d20";
      ctx.fill();
      ctx.beginPath();
      ctx.arc(px, py, 7, 0, 7);
      ctx.fillStyle = "#ff9c5c";
      ctx.shadowColor = "#ff8a3d";
      ctx.shadowBlur = 20;
      ctx.fill();
      ctx.shadowBlur = 0;
    } else {
      ctx.fillStyle = "#a5bdcc";
      ctx.font = "19px Inter, sans-serif";
      ctx.fillText("Waiting for position", x, y + 48);
    }
    ctx.beginPath();
    ctx.arc(x, y, 7, 0, 7);
    ctx.fillStyle = "#edf4fc";
    ctx.fill();
    ctx.beginPath();
    ctx.arc(x, y, 14, 0, 7);
    ctx.strokeStyle = "#edf4fc40";
    ctx.stroke();
  }
}
customElements.define("mic-radar", MicRadar);
