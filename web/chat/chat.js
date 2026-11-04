// A shop's customer chat link (/c/<slug>): the customer talks to Sabi, the shop's assistant.
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const slug = decodeURIComponent(location.pathname.split("/").filter(Boolean)[1] || "");
const KEY = `sabi.chat.${slug}`;
let me = load();
let last = 0, busy = false, shop = null;

function load() {
  try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch { return null; }
}
function save() {
  try { localStorage.setItem(KEY, JSON.stringify(me)); } catch { /* private mode: this visit only */ }
}
function newId() {
  const a = new Uint8Array(16);
  crypto.getRandomValues(a);
  return Array.from(a, b => b.toString(16).padStart(2, "0")).join("");
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// WhatsApp's own markup, as WhatsApp shows it: *bold*, _italic_, ~strike~. Escaped text in; links are left alone.
function waFormat(html) {
  const links = [];
  const safe = html.replace(/https?:\/\/[^\s<]+/g, u => `\u0000${links.push(u) - 1}\u0000`);
  const mark = (ch, tag) => new RegExp(`(^|[\s(•\-–—:])\${ch}(?=\S)([^${ch}\n]*?\S)\${ch}(?=$|[\s).,!?:;])`, "gm");
  return safe.replace(mark("*", "b"), "$1<b>$2</b>").replace(mark("_", "i"), "$1<i>$2</i>").replace(mark("~", "s"), "$1<s>$2</s>")
    .replace(/\u0000(\d+)\u0000/g, (_, i) => links[i]);
}

function linkify(html) {
  return html.replace(/https?:\/\/[^\s<]+/g, url => {
    const clean = url.replace(/[).,;:!?]+$/, "");
    return `<a href="${clean}" target="_blank" rel="noopener">${clean}</a>${url.slice(clean.length)}`;
  });
}
function toast(text) {
  const t = $("#toast");
  t.textContent = text; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), 4000);
}

function bubble(m) {
  const el = document.createElement("div");
  el.className = `msg ${m.role === "in" ? "me" : m.role === "note" ? "note" : "them"}`;
  el.innerHTML = `${m.image ? `<img alt="" src="${esc(m.image)}">` : ""}${m.body ? linkify(waFormat(esc(m.body))) : ""}
    <div class="when">${m.at ? new Date(m.at).toLocaleTimeString("en-NG", { hour: "numeric", minute: "2-digit" }) : ""}</div>`;
  return el;
}

function show(list) {
  let added = false;
  for (const m of list) {
    if (m.id <= last) continue;
    $("#log").appendChild(bubble(m));
    last = m.id;
    added = true;
  }
  if (added) window.scrollTo(0, document.body.scrollHeight);
}

async function call(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Error ${res.status}`);
  return data;
}

async function say(payload, shown, image) {
  if (busy) { toast("One moment, still answering."); return; }
  busy = true;
  const pending = bubble({ role: "in", body: shown, image });
  pending.classList.add("pending");
  $("#log").appendChild(pending);
  $("#log").insertAdjacentHTML("beforeend", `<div class="typing">typing…</div>`);
  window.scrollTo(0, document.body.scrollHeight);
  try {
    const r = await call(`/c/api/${encodeURIComponent(slug)}/message`, { cid: me.cid, name: me.name, ...payload });
    pending.remove(); $(".typing")?.remove();
    show(r.messages);
  } catch (e) {
    pending.remove(); $(".typing")?.remove();
    $("#log").appendChild(bubble({ role: "note", body: `Not sent: ${e.message}` }));
    if (payload.text) $("#text").value = payload.text;
  } finally {
    busy = false;
  }
}

function shrinkImage(file, max = 1600) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(img.src);
      resolve(c.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = reject;
    img.src = URL.createObjectURL(file);
  });
}

async function open() {
  $("#hello").hidden = true;
  $("#composer").hidden = false;
  const { messages } = await call(`/c/api/${encodeURIComponent(slug)}/messages?cid=${me.cid}`);
  if (!messages.length) {
    $("#log").appendChild(bubble({ role: "out", body: `Hello ${me.name} 👋 Welcome to ${shop.name}. Ask me about prices or what's in stock, or tell me what you'd like to order.` }));
  }
  show(messages);
  setInterval(async () => {
    if (busy || document.hidden) return;
    try { show((await call(`/c/api/${encodeURIComponent(slug)}/messages?cid=${me.cid}&after=${last}`)).messages); } catch { /* try again */ }
  }, 4000);
}

$("#composer").onsubmit = e => {
  e.preventDefault();
  const text = $("#text").value.trim();
  if (!text) return;
  $("#text").value = "";
  say({ text }, text);
};
$("#photo").onchange = async e => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  const image = await shrinkImage(file).catch(() => null);
  if (!image) { toast("Couldn't read that photo."); return; }
  const text = $("#text").value.trim();
  $("#text").value = "";
  say({ image, text: text || null }, text, image);
};
let rec = null, chunks = [];
$("#mic").onclick = async () => {
  const mic = $("#mic");
  if (rec) { rec.stop(); return; }
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") { toast("This browser can't record. Please type."); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    rec = new MediaRecorder(stream); chunks = [];
    rec.ondataavailable = ev => chunks.push(ev.data);
    rec.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      mic.classList.remove("rec"); mic.textContent = "🎙";
      const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
      rec = null;
      if (blob.size < 1500) { toast("That was too short."); return; }
      const audio = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(blob); });
      say({ audio }, "🎙 Voice note…");
    };
    rec.start();
    mic.classList.add("rec"); mic.textContent = "■";
    toast("Recording… tap ■ when you finish.");
    setTimeout(() => rec?.state === "recording" && rec.stop(), 120000);
  } catch { toast("Allow the microphone to send voice notes."); }
};

$("#start").onclick = () => {
  const name = $("#your-name").value.trim();
  if (!name) { $("#your-name").focus(); return; }
  me = { cid: newId(), name };
  save();
  open();
};
$("#your-name").addEventListener("keydown", e => { if (e.key === "Enter") $("#start").click(); });

(async () => {
  try {
    shop = await call(`/c/api/${encodeURIComponent(slug)}`);
  } catch (e) {
    $("#name").textContent = "Shop not found";
    $("#sub").textContent = "";
    $("#log").appendChild(bubble({ role: "note", body: "This chat link doesn't work. Ask the shop for the right one." }));
    return;
  }
  document.title = `Chat with ${shop.name}`;
  $("#name").textContent = shop.name;
  $("#avatar").textContent = shop.name.trim()[0]?.toUpperCase() || "S";
  $("#mic").hidden = !shop.voice;
  if (me?.cid) return open();
  $("#hello-title").textContent = `Chat with ${shop.name}`;
  $("#hello-about").textContent = shop.about || `${shop.kind}. Ask about prices, stock, orders and delivery.`;
  $("#hello").hidden = false;
})();
