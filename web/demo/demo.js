// Try Sabi: two WhatsApp-style phones and the live books, talking to the real agent.
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const phones = { owner: $("#owner"), customer: $("#customer") };
let session = null;
let busy = false;
let lastSeq = 0;

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function naira(kobo) {
  const n = Math.round((kobo || 0) / 100);
  return (n < 0 ? "−" : "") + "₦" + Math.abs(n).toLocaleString("en-NG");
}
function toast(text) {
  const t = $("#toast");
  t.textContent = text; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), 5000);
}

// ---- chat rendering ---------------------------------------------------------

function addBubble(who, cls, { body = "", image = null, buttons = [], meta = "" } = {}) {
  const chat = $(".chat", phones[who]);
  const el = document.createElement("div");
  el.className = `msg ${cls}`;
  el.innerHTML = `${image ? `<img alt="" src="${esc(image)}">` : ""}${esc(body)}
    ${meta ? `<div class="meta">${esc(meta)}</div>` : ""}`;
  if (buttons.length) {
    const box = document.createElement("div");
    box.className = "btns";
    for (const b of buttons) {
      const btn = document.createElement("button");
      btn.textContent = b.title;
      btn.onclick = () => {
        $$("button", box).forEach(x => (x.disabled = true));   // one tap per question, like WhatsApp
        addBubble(who, "me", { body: b.title });
        send(who, { button_id: b.id, button_title: b.title });
      };
      box.appendChild(btn);
    }
    el.appendChild(box);
  }
  chat.appendChild(el);
  chat.scrollTop = chat.scrollHeight;
  if (who !== currentPanel()) mark(who);
}

function typing(who, on) {
  const chat = $(".chat", phones[who]);
  $(".typing", chat)?.remove();
  if (on) {
    chat.insertAdjacentHTML("beforeend", `<div class="typing">Sabi is typing…</div>`);
    chat.scrollTop = chat.scrollHeight;
  }
}

function showReplies(data, from) {
  const tools = data.tools?.length ? `${session.model || "model"} · ${data.tools.join(" → ")}` : "";
  let first = true;
  for (const m of data.messages || []) {
    const meta = m.template ? "WhatsApp template (customer hasn't messaged in 24h)"
      : first && m.to === from ? tools : "";
    addBubble(m.to, "them", { body: m.body, image: m.image, buttons: m.buttons || [], meta });
    if (m.to === from) first = false;
  }
  if (data.books) renderBooks(data.books);
  if (typeof data.left === "number" && data.left <= 5) toast(`${data.left} messages left in this demo session.`);
}

// ---- talking to the server --------------------------------------------------------

async function post(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Error ${res.status}`);
  return data;
}

async function send(who, payload) {
  if (!session) return;
  if (busy) { toast("One moment, Sabi is still answering."); return; }
  busy = true;
  typing(who, true);
  try {
    const data = await post(`/demo/api/${session.id}/message`, { who, ...payload });
    typing(who, false);
    if (data.heard) addBubble(who, "note", { body: `🎙 Sabi heard: “${data.heard}”` });
    showReplies(data, who);
  } catch (e) {
    typing(who, false);
    addBubble(who, "note", { body: e.message });
    if (/expired/.test(e.message)) start();
  } finally {
    busy = false;
  }
}

async function start() {
  for (const p of Object.values(phones)) $(".chat", p).innerHTML = "";
  lastSeq = 0;
  try {
    session = await post("/demo/api/session");
  } catch (e) {
    toast(`Couldn't start the demo: ${e.message}`);
    return;
  }
  $("#shop").textContent = `${session.shop} · demo shop with a week of history`;
  $("#model").textContent = session.model ? `Running on ${session.model}` : "AI model not connected: buttons only";
  renderBooks(session.books, true);
  for (const m of session.intro) addBubble(m.to, "note", { body: m.body });
}

// ---- books panel ----------------------------------------------------------------------

const KIND = { sale: "Sale", purchase: "Purchase", payment_in: "Paid in", payment_out: "Paid out", expense: "Expense",
  drawing: "Took", stock_loss: "Loss", reversal: "Cancel" };

function renderBooks(b, first = false) {
  const kpis = [["Sales today", naira(b.sales_today_kobo)], ["Profit today", naira(b.profit_today_kobo)],
    ["Cash", naira(b.cash.cash_kobo)], ["Bank", naira(b.cash.bank_kobo)]];
  const old = $$(".kpi b").map(x => x.textContent);
  $(".kpis").innerHTML = kpis.map(([l, v], i) =>
    `<div class="kpi${!first && old[i] && old[i] !== v ? " flash" : ""}"><span>${l}</span><b>${v}</b></div>`).join("");
  $(".entries").innerHTML = b.recent.map(e => `<li class="${!first && e.seq > lastSeq ? "new" : ""}">
      <span class="${e.status === "reversed" ? "struck" : ""}"><span class="k">${esc(KIND[e.kind] || e.kind)}</span>${esc(e.memo)}</span>
      <span class="num">${naira(e.total_kobo)}</span></li>`).join("") || "<li>No entries yet</li>";
  const newest = Math.max(0, ...b.recent.map(e => e.seq));
  if (!first && newest > lastSeq) mark("books");
  lastSeq = Math.max(lastSeq, newest);
  $(".owing").innerHTML = b.debtors.map(d => `<li><span>${esc(d.name)}
      ${d.overdue_days ? `<span class="late">${d.overdue_days}d late</span>` : ""}</span><span class="num">${naira(d.owes_kobo)}</span></li>`).join("")
    || "<li>Nobody owes you 🎉</li>";
  $(".stock tbody").innerHTML = b.stock.map(s => `<tr><td>${esc(s.name)}</td><td>${s.qty} ${esc(s.unit)}</td></tr>`).join("");
}

// ---- composers: text, photo, voice ------------------------------------------------------

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

function blobToDataUrl(blob) {
  return new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(blob); });
}

for (const [who, phone] of Object.entries(phones)) {
  const input = $(".text", phone);
  $(".composer", phone).onsubmit = e => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    addBubble(who, "me", { body: text });
    send(who, { text });
  };
  $$(".chips button", phone).forEach(b => b.onclick = () => { input.value = b.textContent; input.focus(); });

  $("input[type=file]", phone).onchange = async e => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const image = await shrinkImage(file).catch(() => null);
    if (!image) { toast("Couldn't read that image."); return; }
    const text = input.value.trim();
    input.value = "";
    addBubble(who, "me", { body: text, image });
    send(who, { image, text: text || null });
  };

  const mic = $(".mic", phone);
  let rec = null, chunks = [];
  mic.onclick = async () => {
    if (rec) { rec.stop(); return; }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      toast("Voice notes need a browser with microphone recording."); return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      rec = new MediaRecorder(stream);
      chunks = [];
      rec.ondataavailable = ev => chunks.push(ev.data);
      rec.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        mic.classList.remove("rec"); mic.textContent = "🎙";
        const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        rec = null;
        if (blob.size < 1500) { toast("That was too short."); return; }
        addBubble(who, "me", { body: "🎙 Voice note" });
        send(who, { audio: await blobToDataUrl(blob) });
      };
      rec.start();
      mic.classList.add("rec"); mic.textContent = "■";
      setTimeout(() => rec?.state === "recording" && rec.stop(), 60000);
    } catch {
      toast("Microphone permission was refused.");
    }
  };
}

// ---- panels on small screens ------------------------------------------------------------------

function currentPanel() {
  return window.matchMedia("(max-width: 900px)").matches ? $(".switch .on").dataset.panel : null;
}
function mark(panel) {
  if (currentPanel() && currentPanel() !== panel) $(`.switch [data-panel=${panel}] i`).hidden = false;
}
$$(".switch button").forEach(b => b.onclick = () => {
  $$(".switch button").forEach(x => x.classList.toggle("on", x === b));
  $$(".stage > section").forEach(s => s.classList.toggle("on", s.id === b.dataset.panel));
  $("i", b).hidden = true;
});

$("#reset").onclick = start;
$("#summary").onclick = async () => {
  if (!session) return;
  try {
    const data = await post(`/demo/api/${session.id}/summary`);
    showReplies(data, "owner");
  } catch (e) { toast(e.message); }
};

start();
