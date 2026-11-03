// Talk mode: a hands-free conversation with Sabi. Tap once and just talk. Sabi hears when you
// stop (the microphone level drops for a moment), answers out loud, then listens again. Tap
// again to stop. Used by the owner app (/app/) and the public demo (/demo/).
//
//   const talk = new SabiTalk({ button, send: blob => Promise<string[]>, speak: text => Promise<Blob|null> });
//
// send() posts the recording as a voice note and resolves to the texts Sabi replied with;
// speak() returns Sabi's voice for a text (Meta's MMS voice from the server), or null to use the
// phone's own voice.
(function () {
  const QUIET_MS = 1100;      // this much quiet after speech = she finished
  const MAX_MS = 25000;       // one turn is never longer than this
  const IDLE_MS = 15000;      // nobody spoke: talk mode stops by itself
  const LABEL = { off: "🗣", listening: "●", thinking: "…", speaking: "🔊" };
  const TITLE = { off: "Talk to Sabi (hands-free)", listening: "Listening… tap to stop", thinking: "Sabi is thinking…",
    speaking: "Sabi is talking… tap to stop" };

  class SabiTalk {
    constructor({ button, send, speak, onState }) {
      this.button = button; this.send = send; this.speak = speak; this.onState = onState || (() => {});
      this.on = false; this.audio = null;
      this.setState("off");
      button.addEventListener("click", () => (this.on ? this.stop() : this.start()));
    }

    setState(s) {
      this.state = s;
      this.button.textContent = LABEL[s];
      this.button.title = TITLE[s];
      this.button.classList.toggle("talking", s !== "off");
      this.button.dataset.state = s;
      this.onState(s);
    }

    async start() {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
        this.onState("unsupported"); return;
      }
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      } catch { this.onState("denied"); return; }
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();   // made on the tap, so phones allow sound
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      this.ctx.createMediaStreamSource(this.stream).connect(this.analyser);
      this.on = true;
      this.loop();
    }

    stop() {
      this.on = false;
      this.rec?.state === "recording" && this.rec.stop();
      this.audio?.pause();
      window.speechSynthesis?.cancel();
      this.stream?.getTracks().forEach(t => t.stop());
      this.ctx?.close().catch(() => {});
      this.setState("off");
    }

    level() {
      const buf = new Float32Array(this.analyser.fftSize);
      this.analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) sum += v * v;
      return Math.sqrt(sum / buf.length);
    }

    // One turn of listening: resolves to the recording, or null if nobody spoke.
    listen() {
      return new Promise(resolve => {
        this.setState("listening");
        const chunks = [];
        this.rec = new MediaRecorder(this.stream);
        this.rec.ondataavailable = e => chunks.push(e.data);
        let spoke = false, began = performance.now(), quietSince = 0, floor = 0, samples = 0;
        this.rec.onstop = () => {
          clearInterval(timer);
          resolve(spoke ? new Blob(chunks, { type: this.rec.mimeType || "audio/webm" }) : null);
        };
        this.rec.start();
        const timer = setInterval(() => {
          if (!this.on) { this.rec.state === "recording" && this.rec.stop(); return; }
          const now = performance.now(), v = this.level();
          if (now - began < 400) { floor += v; samples++; return; }        // the room's own noise
          const threshold = Math.max(0.015, (floor / Math.max(samples, 1)) * 3);
          if (v > threshold) { spoke = true; quietSince = 0; }
          else if (spoke && !quietSince) quietSince = now;
          const done = (spoke && quietSince && now - quietSince > QUIET_MS) || now - began > MAX_MS
            || (!spoke && now - began > IDLE_MS);
          if (done && this.rec.state === "recording") this.rec.stop();
        }, 50);
      });
    }

    async say(text) {
      this.setState("speaking");
      let blob = null;
      try { blob = await this.speak(text); } catch { blob = null; }
      if (!this.on) return;
      if (blob) {
        const url = URL.createObjectURL(blob);
        this.audio = new Audio(url);
        await new Promise(r => { this.audio.onended = this.audio.onerror = r; this.audio.play().catch(r); });
        URL.revokeObjectURL(url);
        return;
      }
      if (!window.speechSynthesis) return;
      await new Promise(r => {
        const u = new SpeechSynthesisUtterance(text.replace(/[*_•]/g, "").replace(/₦\s?([\d,]+)/g, "$1 naira"));
        u.onend = u.onerror = r;
        speechSynthesis.speak(u);
      });
    }

    async loop() {
      while (this.on) {
        const blob = await this.listen();
        if (!this.on) break;
        if (!blob) { this.stop(); this.onState("idle"); break; }          // nobody spoke for a while
        if (blob.size < 1500) continue;
        this.setState("thinking");
        let replies = [];
        try { replies = await this.send(blob); } catch { replies = []; }
        if (!this.on) break;
        for (const text of replies) {
          if (!this.on) break;
          await this.say(text);
        }
      }
    }
  }

  window.SabiTalk = SabiTalk;
})();
