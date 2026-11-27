import { $ } from "./common.js";
const navigationPlayers = new Map();
export function cameraIsPlaying(id) {
  return [...document.querySelectorAll('camera-player')].some(p =>
    p.getAttribute('camera') === id && p.running && !p.video?.paused &&
    p.video?.readyState >= 2 && performance.now() - (p.lastFrameAt ?? -Infinity) < 4000);
}
export function retainNavigationPlayers(root) {
  root.querySelectorAll('camera-player').forEach(player => {
    const id = player.getAttribute('camera');
    if (navigationPlayers.has(id)) return;
    player.retained = true;
    navigationPlayers.set(id, player);
    player.remove();
  });
}
export function finishNavigationPlayers() {
  for (const player of navigationPlayers.values()) {
    player.retained = false;
    player.disconnectedCallback();
  }
  navigationPlayers.clear();
}
export class CameraPlayer extends HTMLElement {
  constructor() {
    super();
    this.running = false;
    this.hlsOnly = false;
    this.attempt = 0;
    this.generation = 0;
    this.metrics = { fps: null, latency_ms: null, transport: "" };
    this.visibility = () => (document.hidden ? this.stop() : this.start());
  }
  connectedCallback() {
    const existing = navigationPlayers.get(this.getAttribute('camera'));
    if (existing && existing !== this) {
      navigationPlayers.delete(this.getAttribute('camera'));
      existing.retained = false;
      this.replaceWith(existing);
      return;
    }
    if (this.video) {
      document.addEventListener("visibilitychange", this.visibility);
      this.start();
      return;
    }
    this.innerHTML = `<video autoplay muted playsinline></video><div class="video-slate"><span class="camera-glyph">◉</span><span>Waiting for camera</span><small>Connecting to the room…</small></div><span class="video-transport">CONNECTING</span><div class="video-bottom"><span class="video-name"></span><span class="video-metric">—</span></div>`;
    this.video = $("video", this);
    this.video.ontimeupdate = () => {
      if (this.video.currentTime !== this.lastVideoTime) {
        this.lastVideoTime = this.video.currentTime;
        this.lastFrameAt = performance.now();
      }
    };
    $(".video-name", this).textContent = this.getAttribute("name") || "Camera";
    this.video.onplaying = () => {
      this.slate(false);
      this.attempt = 0;
    };
    document.addEventListener("visibilitychange", this.visibility);
    this.start();
  }
  disconnectedCallback() {
    if (this.retained) return;
    document.removeEventListener("visibilitychange", this.visibility);
    this.stop();
  }
  slate(
    show,
    message = "Waiting for camera",
    detail = "Check the camera cable. Reconnecting automatically.",
  ) {
    const node = $(".video-slate", this);
    node.hidden = !show;
    $("span:nth-child(2)", node).textContent = message;
    $("small", node).textContent = detail;
  }
  async start() {
    if (this.running || document.hidden || !this.isConnected) return;
    this.running = true;
    this.startedAt = performance.now();
    clearTimeout(this.firstFrameTimer);
    this.firstFrameTimer=setTimeout(()=>{if(this.video.readyState<2){this.slate(true,"Waiting for keyframe","No first frame in 8 seconds. Diagnose video; check RTP streaming and a 1–2 second I-frame interval.");if(document.querySelector("#app")&&Date.now()-(this.lastDiagnosis||0)>60000){this.lastDiagnosis=Date.now();fetch(`/api/cameras/${encodeURIComponent(this.getAttribute("camera"))}/diagnose`,{method:"POST",headers:{"x-confdeck":"1"}}).catch(()=>{});}}},8000);
    const generation = ++this.generation;
    try {
      if (this.hlsOnly || !window.RTCPeerConnection) await this.hls(generation);
      else await this.whep(generation);
    } catch (error) {
      if (generation !== this.generation) return;
      this.cleanup();
      this.running = false;
      this.slate(true);
      if (!this.hlsOnly) {
        this.hlsOnly = true;
        this.retryTimer = setTimeout(() => this.start(), 500);
      } else
        this.retryTimer = setTimeout(
          () => this.start(),
          Math.min(15000, 700 * 2 ** this.attempt++),
        );
    }
  }
  async whep(generation) {
    const pc = (this.pc = new RTCPeerConnection({ iceServers: [] }));
    this.metrics.transport = "WebRTC";
    $(".video-transport", this).textContent = "WEBRTC";
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.ontrack = (event) => {
      if (generation === this.generation)
        this.video.srcObject =
          event.streams[0] || new MediaStream([event.track]);
    };
    pc.onconnectionstatechange = () => {
      if (generation !== this.generation) return;
      if (["failed", "disconnected"].includes(pc.connectionState)) {
        this.slate(true);
        this.stop();
        this.retryTimer = setTimeout(() => this.start(), 2000);
      }
    };
    await pc.setLocalDescription(await pc.createOffer());
    if (pc.iceGatheringState !== "complete")
      await Promise.race([
        new Promise((resolve) =>
          pc.addEventListener(
            "icegatheringstatechange",
            () => pc.iceGatheringState === "complete" && resolve(),
          ),
        ),
        new Promise((resolve) => setTimeout(resolve, 1500)),
      ]);
    if (generation !== this.generation) return;
    const response = await fetch(
      `/media/${encodeURIComponent(this.getAttribute("camera"))}/whep`,
      {
        method: "POST",
        headers: { "Content-Type": "application/sdp", "x-confdeck": "1" },
        body: pc.localDescription.sdp,
      },
    );
    if (!response.ok) throw new Error("Camera unavailable");
    const session = response.headers.get("location"),
      answer = await response.text();
    if (generation !== this.generation) {
      if (session)
        fetch(session, {
          method: "DELETE",
          headers: { "x-confdeck": "1" },
        }).catch(() => {});
      return;
    }
    this.session = session;
    await pc.setRemoteDescription({ type: "answer", sdp: answer });
    this.fallbackTimer = setTimeout(() => {
      if (generation === this.generation && this.video.readyState < 3) {
        this.stop();
        this.hlsOnly = true;
        this.start();
      }
    }, 8000);
    this.statsTimer = setInterval(() => this.readStats(), 2000);
  }
  async hls(generation) {
    this.metrics.transport = "HLS";
    $(".video-transport", this).textContent = "HLS · FALLBACK";
    const url = `/media/${encodeURIComponent(this.getAttribute("camera"))}/index.m3u8`;
    if (
      !window.Hls?.isSupported() &&
      this.video.canPlayType("application/vnd.apple.mpegurl")
    ) {
      this.video.src = url;
      this.video.play().catch(() => {});
    } else if (window.Hls?.isSupported()) {
      this.hlsInstance = new Hls({
        lowLatencyMode: true,
        backBufferLength: 5,
        maxBufferLength: 5,
        maxMaxBufferLength: 10,
        enableWorker: true,
      });
      this.hlsInstance.loadSource(url);
      this.hlsInstance.attachMedia(this.video);
      this.hlsInstance.on(Hls.Events.MANIFEST_PARSED, () =>
        this.video.play().catch(() => {}),
      );
      this.hlsInstance.on(Hls.Events.ERROR, (_, data) => {
        if (data.fatal && generation === this.generation) {
          this.slate(true);
          this.stop();
          this.retryTimer = setTimeout(
            () => this.start(),
            Math.min(15000, 1000 * 2 ** this.attempt++),
          );
        }
      });
    } else {
      this.slate(
        true,
        "This browser cannot play live video",
        "Open this page in current Edge, Chrome, Firefox or Safari.",
      );
      return;
    }
    this.statsTimer = setInterval(() => this.readStats(), 2000);
  }
  async readStats() {
    let received = false;
    try {
      if (this.pc) {
        const stats = await this.pc.getStats();
        stats.forEach((s) => {
          if (s.type === "inbound-rtp" && s.kind === "video") {
            this.metrics.fps = s.framesPerSecond ?? null;
            this.metrics.latency_ms = s.jitterBufferEmittedCount
              ? Math.round(
                  (1000 * s.jitterBufferDelay) / s.jitterBufferEmittedCount,
                )
              : null;
            if (
              this.lastFrames !== undefined &&
              s.framesDecoded === this.lastFrames
            ) {
              this.stalls = (this.stalls || 0) + 1;
            } else {
              this.stalls = 0;
              received = s.framesDecoded > (this.lastFrames || 0);
            }
            this.lastFrames = s.framesDecoded;
          }
        });
      } else {
        const quality = this.video.getVideoPlaybackQuality?.();
        if (quality) {
          const now = performance.now();
          this.metrics.fps = this.lastQuality
            ? Math.round(
                (quality.totalVideoFrames - this.lastQuality.frames) /
                  ((now - this.lastQuality.time) / 1000),
              )
            : null;
          received = quality.totalVideoFrames > (this.lastQuality?.frames || 0);
          this.lastQuality = { frames: quality.totalVideoFrames, time: now };
          this.stalls = received ? 0 : (this.stalls || 0) + 1;
        }
        if (this.video.seekable.length)
          this.metrics.latency_ms = Math.max(
            0,
            Math.round(
              (this.video.seekable.end(this.video.seekable.length - 1) -
                this.video.currentTime) *
                1000,
            ),
          );
      }
      this.metrics.frames_received = received;
      if (received) this.slate(false);
      if (this.stalls >= 3 && performance.now() - this.startedAt > 20000) {
        this.slate(true);
        this.stop();
        this.retryTimer = setTimeout(() => this.start(), 1500);
        return;
      }
      $(".video-metric", this).textContent =
        [
          this.metrics.fps != null
            ? `${Math.round(this.metrics.fps)} fps`
            : null,
          this.metrics.latency_ms != null
            ? `~${this.metrics.latency_ms} ms buffer`
            : null,
        ]
          .filter(Boolean)
          .join(" · ") || "Waiting for frames";
      this.dispatchEvent(
        new CustomEvent("metrics", {
          bubbles: true,
          detail: { ...this.metrics, camera: this.getAttribute("camera") },
        }),
      );
    } catch {}
  }
  cleanup() {
    clearTimeout(this.fallbackTimer);
    clearInterval(this.statsTimer);
    this.pc?.close();
    this.pc = null;
    this.hlsInstance?.destroy();
    this.hlsInstance = null;
    if (this.video) {
      this.video.pause();
      this.video.srcObject = null;
      this.video.removeAttribute("src");
      this.video.load();
    }
    if (this.session) {
      fetch(this.session, {
        method: "DELETE",
        headers: { "x-confdeck": "1" },
        keepalive: true,
      }).catch(() => {});
      this.session = null;
    }
  }
  stop() {
    clearTimeout(this.firstFrameTimer);
    this.generation++;
    this.running = false;
    clearTimeout(this.retryTimer);
    this.cleanup();
  }
  useHLS(value) {
    this.stop();
    this.hlsOnly = value;
    this.start();
  }
}
customElements.define("camera-player", CameraPlayer);
export function syncPlayers(container, cameras) {
  if (!container) return;
  const wanted = new Set(cameras.map((c) => c.id));
  [...container.querySelectorAll("camera-player")].forEach((p) => {
    if (!wanted.has(p.getAttribute("camera"))) p.remove();
  });
  for (const camera of cameras) {
    let player = [...container.querySelectorAll("camera-player")].find(
      (p) => p.getAttribute("camera") === camera.id,
    );
    if (!player) {
      player = document.createElement("camera-player");
      player.setAttribute("camera", camera.id);
      player.setAttribute("name", camera.name);
      container.append(player);
    } else $(".video-name", player).textContent = camera.name;
  }
  const empty = $(".empty", container);
  if (cameras.length) empty?.remove();
  else if (!empty)
    container.insertAdjacentHTML(
      "beforeend",
      '<div class="empty"><span>◉</span><h3>No cameras connected</h3><p>Add a real camera by IP or scan the local network.</p><a href="#cameras">Set up cameras →</a></div>',
    );
}

// Preserve the outgoing composition until the destination has decoded video.
export function syncOutput(container, cameras, layout, ordered, blank=false) {
  if (!container) return;
  const signature=JSON.stringify([layout,ordered,cameras.map(c=>c.id)]);
  if(blank){
    cancelAnimationFrame(container.handoffTick);
    container.handoff?.remove();container.handoff=null;
    container.outputSignature=null;
    syncPlayers(container,[]);
    container.querySelector('.empty')?.remove();
    return;
  }
  if(signature!==container.outputSignature && !container.handoff){
    const rect=container.getBoundingClientRect();
    const sources=[...container.querySelectorAll('camera-player')];
    if(rect.width && rect.height && sources.some(p=>p.video?.readyState>=2)){
      const canvas=document.createElement('canvas');
      canvas.width=Math.round(rect.width);canvas.height=Math.round(rect.height);
      canvas.className='output-handoff';canvas.setAttribute('aria-label','Previous output held while the next camera connects');
      const ctx=canvas.getContext('2d');ctx.fillStyle='#000';ctx.fillRect(0,0,canvas.width,canvas.height);
      let copied=false;
      for(const p of sources){
        const v=p.video;if(!v?.videoWidth || v.readyState<2)continue;
        const r=v.getBoundingClientRect();
        const scale=Math.min(r.width/v.videoWidth,r.height/v.videoHeight);
        const w=v.videoWidth*scale,h=v.videoHeight*scale;
        try{ctx.drawImage(v,r.left-rect.left+(r.width-w)/2,r.top-rect.top+(r.height-h)/2,w,h);copied=true;}catch{}
      }
      if(copied){container.handoff=canvas;container.append(canvas);}
    }
  }
  container.outputSignature=signature;
  container.className=`display-grid ${layout}`;
  syncPlayers(container,cameras);
  container.querySelectorAll('camera-player').forEach(p=>{
    const i=Math.max(0,ordered.indexOf(p.getAttribute('camera')));
    p.className=`slot-${i}`;p.style.order=i;
  });
  cancelAnimationFrame(container.handoffTick);
  if(container.handoff){
    container.querySelector('.empty')?.remove();
    const reveal=()=>{
      if(!container.isConnected)return;
      const visible={single:1,two:2,three:3,pip:2,grid:4}[layout]||4;
      const players=[...container.querySelectorAll('camera-player')].filter(p=>
        ordered.indexOf(p.getAttribute('camera')) < visible);
      if(players.length && players.every(p=>p.video?.readyState>=2 && !p.video.paused && performance.now()-(p.lastFrameAt??-Infinity)<4000)){
        container.handoff?.remove();container.handoff=null;
      }else container.handoffTick=requestAnimationFrame(reveal);
    };
    container.handoffTick=requestAnimationFrame(reveal);
  }
}
