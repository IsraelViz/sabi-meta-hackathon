// Sabi owner app: read-only view of the books. No build step, no framework.
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const view = $("#view");
const TOKEN_KEY = "sabi.token";

// ---- helpers -------------------------------------------------------------

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// Whole amounts on screen: kobo from average-cost arithmetic (₦4,610.92) is noise to a trader.
// The books keep exact kobo; the entry detail's debits and credits still balance to the kobo.
// Written in the shop's own money (/api/me currency): ₦12,500 in Lagos, 12 500 F CFA in Dakar.
const NAIRA = { symbol: "₦", after: false, group: ",", cents: true };
function naira(kobo, exact = false) {
  const c = ME?.currency || NAIRA;
  const n = Math.round(kobo || 0);
  const sign = n < 0 ? "−" : "";
  const abs = Math.abs(n);
  const cents = exact && c.cents;
  const whole = (cents ? Math.floor(abs / 100) : Math.round(abs / 100)).toLocaleString("en-US").replace(/,/g, c.group);
  const frac = cents && abs % 100 ? "." + String(abs % 100).padStart(2, "0") : "";
  return c.after ? `${sign}${whole}${frac} ${c.symbol}` : `${sign}${c.symbol}${whole}${frac}`;
}
function moneySign() { return (ME?.currency || NAIRA).symbol.trim(); }
function day(iso) {
  if (!iso) return "";
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00" : ""));
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const diff = Math.round((today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString("en-NG", { weekday: "short", day: "numeric", month: "short" });
}
function time(iso) {
  return iso ? new Date(iso).toLocaleString("en-NG", { dateStyle: "medium", timeStyle: "short" }) : "";
}

const KIND = {
  sale: ["Sale", "S"], purchase: ["Purchase", "P"], payment_in: ["Payment in", "₦"], payment_out: ["Paid supplier", "₦"],
  expense: ["Expense", "E"], drawing: ["You took", "D"], capital: ["You put in", "+"], stock_loss: ["Stock loss", "L"], saving: ["Saved", "₦"], reversal: ["Cancelled", "×"],
  opening: ["Opening stock", "O"],
};
const MONEY_IN = new Set(["sale", "payment_in", "capital"]);
let ME = null;   // /api/me: which shop, and whether she talks to Sabi in the Chat tab (pilot) or on WhatsApp
const where = () => (ME?.chat ? "in Chat" : "on WhatsApp");
const BY = { "agent:auto": "Recorded automatically", "agent:approved": "You confirmed it", app: "Entered in the app", seed: "Demo data" };

// ---- API -------------------------------------------------------------------

class AuthError extends Error {}

async function api(path, { blob = false } = {}) {
  const token = localStorage.getItem(TOKEN_KEY);
  const res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  $("#offline").hidden = res.headers.get("x-sabi-cache") !== "1";
  if (res.status === 401) throw new AuthError(await res.text());
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return blob ? res.blob() : res.json();
}

async function send(method, path, body) {
  const token = localStorage.getItem(TOKEN_KEY);
  const res = await fetch(path, { method, body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
  if (res.status === 401) throw new AuthError(await res.text());
  if (!res.ok) {
    let detail = await res.text();
    try { detail = JSON.parse(detail).detail || detail; } catch { /* plain text */ }
    throw new Error(typeof detail === "string" ? detail : `${res.status}`);
  }
  return res.json();
}

async function mediaUrl(path) {
  return URL.createObjectURL(await api(path, { blob: true }));   // fetched with the token, never a public link
}

// ---- screens -----------------------------------------------------------------

function entryRow(e) {
  const [label, letter] = KIND[e.kind] || [e.kind, "?"];
  const what = e.items?.length ? e.items.join(", ") : e.memo || label;
  const who = e.contact ? ` · ${e.contact}` : "";
  const cls = e.kind === "reversal" ? "out" : MONEY_IN.has(e.kind) ? "in" : "out";
  const struck = e.status === "reversed" ? " struck" : "";
  return `<button class="row" data-entry="${esc(e.id)}">
    <span class="badge ${esc(e.kind)}">${esc(letter)}</span>
    <span class="main"><div class="title${struck}">${esc(what)}</div>
      <div class="meta">${esc(label)}${esc(who)}${e.due_date && e.kind === "sale" ? ` · due ${esc(day(e.due_date))}` : ""}${e.status === "reversed" ? " · cancelled" : ""}</div></span>
    <span class="amt ${cls}${struck}">${naira(e.amount_kobo)}</span></button>`;
}

async function today() {
  const s = await api("/api/summary");
  setShop(s.shop);
  const t = s.today, w = s.week;
  view.innerHTML = `
    ${s.pending_questions ? `<div class="banner">💬 <span><b>${s.pending_questions}</b> question${s.pending_questions > 1 ? "s" : ""} waiting for you ${where()}</span></div>` : ""}
    <div class="grid">
      <div class="card kpi"><div class="label">Sales today</div><div class="value">${naira(t.sales_kobo)}</div><div class="hint">This week ${naira(w.sales_kobo)}</div></div>
      <div class="card kpi"><div class="label">Profit today</div><div class="value">${naira(t.net_profit_kobo)}</div><div class="hint">This week ${naira(w.net_profit_kobo)}</div></div>
      <div class="card kpi"><div class="label">Cash</div><div class="value">${naira(s.cash.cash_kobo)}</div></div>
      <div class="card kpi"><div class="label">Bank</div><div class="value">${naira(s.cash.bank_kobo)}</div></div>
      <a class="card kpi wide" href="#/owing" style="text-decoration:none;color:inherit">
        <div class="label">Customers owe you</div><div class="value">${naira(s.owed_total_kobo)}</div>
        <div class="hint">${s.debtors.length ? s.debtors.slice(0, 3).map(d => esc(d.name)).join(", ") : "Nobody owes you"}</div></a>
      ${s.sales ? `<a class="card kpi wide${s.sales.waiting_for_owner.length ? " waiting" : ""}" href="#/sales" style="text-decoration:none;color:inherit">
        <div class="label">Customer chats today</div><div class="value">${s.sales.chats}</div>
        <div class="hint">${s.sales.waiting_for_owner.length
          ? `<b style="color:var(--accent)">Waiting for you: ${s.sales.waiting_for_owner.map(esc).join(", ")}</b>`
          : `${s.sales.ordered + s.sales.paid + s.sales.lost} ordered · ${s.sales.paid} paid`}</div></a>` : ""}
    </div>
    ${s.low_stock.length ? `<h3>Running low</h3><div class="card list">${s.low_stock.map(l => `
      <div class="row" style="cursor:default"><span class="badge stock_loss">!</span>
      <span class="main"><div class="title">${esc(l.name)}</div><div class="meta">Reorder at ${esc(l.reorder_level)}</div></span>
      <span class="amt">${esc(l.qty)} ${esc(l.unit)}</span></div>`).join("")}</div>` : ""}
    <div id="tips"></div>
    <h3>Latest</h3>
    <div class="card list">${s.recent.length ? s.recent.map(entryRow).join("") : `<div class="empty">Nothing recorded yet. Tell Sabi what you sold ${where()}: type it or send a voice note.</div>`}</div>`;
  Promise.all([api("/api/coach"), api("/api/report/savings")]).then(([c, sv]) => {
    const box = $("#tips");
    if (!box) return;
    const tips = c.tips.slice(0, 2);
    box.innerHTML = `${tips.length ? `<h3>Sabi's tips</h3>${tips.map(t => `<div class="card tip">💡 ${esc(t.text)}</div>`).join("")}` : ""}
      ${sv.percent || sv.saved_kobo ? `<h3>${esc(sv.name)}</h3><div class="card">
        <div class="kpi"><div class="value">${naira(sv.saved_kobo)}</div>
        <div class="hint">${sv.goal_kobo ? `of ${naira(sv.goal_kobo)} goal` : "saved so far"}${sv.percent ? ` · putting aside ${sv.percent}% of profit` : ""}</div></div>
        ${sv.goal_kobo ? `<div class="bar"><i style="width:${Math.min(100, sv.progress_pct || 0)}%"></i></div>` : ""}</div>` : ""}`;
  }).catch(() => {});
}

const LEDGER_FILTERS = [["", "All"], ["sale", "Sales"], ["purchase", "Purchases"], ["payment_in", "Payments in"],
  ["expense", "Expenses"], ["stock_loss", "Stock loss"], ["reversal", "Cancelled"]];
let ledgerKind = "";

async function ledger() {
  view.innerHTML = `<h1>Ledger</h1>
    <div class="chips">${LEDGER_FILTERS.map(([k, l]) => `<button class="chip${k === ledgerKind ? " on" : ""}" data-kind="${k}">${l}</button>`).join("")}</div>
    <div id="ledger-list"></div>`;
  view.querySelectorAll("[data-kind]").forEach(b => b.onclick = () => { ledgerKind = b.dataset.kind; ledger(); });
  await loadLedger();
}

async function loadLedger(before) {
  const list = $("#ledger-list");
  const q = new URLSearchParams({ limit: "40" });
  if (ledgerKind) q.set("kind", ledgerKind);
  if (before) q.set("before_seq", before);
  const data = await api(`/api/entries?${q}`);
  $(".more", list)?.remove();
  if (!data.entries.length && !before) {
    list.innerHTML = `<div class="empty">No entries here yet.</div>`;
    return;
  }
  // Group by day; when paging, a day that continues from the last page gets no second heading
  let html = "", current = before ? list.dataset.lastDay || "" : "", open = false;
  if (!before) list.innerHTML = "";
  for (const e of data.entries) {
    const d = day(e.date);
    if (d !== current || !open) {
      html += (open ? "</div>" : "") + (d !== current ? `<h3>${esc(d)}</h3>` : "") + `<div class="card list">`;
      current = d; open = true;
    }
    html += entryRow(e);
  }
  if (open) html += "</div>";
  list.insertAdjacentHTML("beforeend", html);
  list.dataset.lastDay = current;
  if (data.next_before_seq) {
    list.insertAdjacentHTML("beforeend", `<button class="more">Load older</button>`);
    $(".more", list).onclick = () => loadLedger(data.next_before_seq);
  }
}

async function owing() {
  const rows = await api("/api/debtors");
  const total = rows.reduce((a, d) => a + d.owes_kobo, 0);
  view.innerHTML = `<h1>Owing you</h1>
    <div class="card kpi"><div class="label">Total</div><div class="value">${naira(total)}</div>
      <div class="hint">${rows.length} customer${rows.length === 1 ? "" : "s"}</div></div>
    <h3>Who owes</h3>
    <div class="card list">${rows.length ? rows.map(d => `
      <button class="row" data-debtor="${esc(d.contact_id)}">
        <span class="badge payment_in">${esc((d.name || "?")[0])}</span>
        <span class="main"><div class="title">${esc(d.name)}</div>
          <div class="meta">${d.due ? `Oldest due ${esc(day(d.due))}` : "No due date"}</div></span>
        <span style="text-align:right"><div class="amt">${naira(d.owes_kobo)}</div>
          ${d.overdue_days ? `<span class="tag late">${d.overdue_days}d late</span>` : ""}</span>
      </button>`).join("") : `<div class="empty">Nobody owes you money. 🎉</div>`}</div>`;
}

const STAGE = { asking: ["Asked", ""], ordered: ["Ordered, not paid", "warn"], paid: ["Paid", "ok"], lost: ["Didn't pay", "late"] };
const SALES_DAYS = [[1, "Today"], [7, "This week"], [30, "30 days"]];
let salesDays = 1;

async function sales() {
  const s = await api(`/api/sales?days=${salesDays}`);
  const ordered = s.ordered + s.paid + s.lost;
  const waiting = s.leads.filter(l => l.with_owner);
  const leadRow = l => {
    const [label, cls] = l.with_owner ? ["Needs you", "warn"] : STAGE[l.stage] || [l.stage, ""];
    return `<div class="row" style="cursor:default">
      <span class="badge lead">${esc((l.name || "?")[0])}</span>
      <span class="main"><div class="title">${esc(l.name)}</div>
        <div class="meta">${l.with_owner ? esc(l.handoff_reason || "Needs you") : l.interests.length ? esc(l.interests.join(", ")) : "Just chatting"}</div></span>
      <span style="text-align:right">${l.order_total_kobo ? `<div class="amt">${naira(l.order_total_kobo)}</div>` : ""}
        <span class="tag ${cls}">${esc(label)}</span></span></div>`;
  };
  view.innerHTML = `<h1>Sales</h1>
    <div class="chips">${SALES_DAYS.map(([d, l]) => `<button class="chip${d === salesDays ? " on" : ""}" data-days="${d}">${l}</button>`).join("")}</div>
    ${waiting.length ? `<h3>Waiting for you</h3>
      <div class="card list waiting">${waiting.map(leadRow).join("")}</div>
      <p class="hint" style="color:var(--muted);font-size:13px;margin:6px 2px 0">Sabi has passed these chats to you. Answer from Sabi's message ${esc(where())}, or tap “Let Sabi handle it”.</p>` : ""}
    <h3>Customer chats</h3>
    <div class="card">
      <div class="funnel">
        <div><b>${s.chats}</b><span>Chats</span></div>
        <div><b>${ordered}</b><span>Ordered</span></div>
        <div><b>${s.paid}</b><span>Paid</span></div>
        <div><b>${s.lost}</b><span>Didn't pay</span></div>
      </div>
      <div class="hint" style="color:var(--muted);font-size:13px;margin-top:10px">${s.paid
        ? `${naira(s.paid_kobo)} paid through Sabi${s.chats ? ` · ${Math.round((s.paid / s.chats) * 100)}% of chats` : ""}`
        : ME?.chat ? "Share your chat link (Shop ⚙) with customers. Sabi answers them with your prices and stock, and follows up once if they go quiet."
        : "Sabi answers customers on WhatsApp with your prices and stock, and follows up once if they go quiet."}</div>
    </div>
    ${s.leads.length ? `<div class="card list" style="margin-top:10px">${s.leads.filter(l => !l.with_owner).map(leadRow).join("")}</div>` : ""}
    ${s.missed.length ? `<h3>Asked for, not available</h3>
      <div class="card list">${s.missed.map(m => `<div class="row" style="cursor:default">
        <span class="badge stock_loss">?</span>
        <span class="main"><div class="title">${esc(m.item[0].toUpperCase() + m.item.slice(1))}</div><div class="meta">Customers asked for this</div></span>
        <span class="amt">×${esc(m.times)}</span></div>`).join("")}</div>` : ""}
    ${!s.chats ? `<div class="empty">No customer chats ${salesDays === 1 ? "today" : "in this period"} yet.</div>` : ""}`;
  view.querySelectorAll("[data-days]").forEach(b => b.onclick = () => { salesDays = Number(b.dataset.days); sales(); });
}

const LINKS = [["website", "Website", "mamankechi.com"], ["instagram", "Instagram", "@mamankechi"],
  ["facebook", "Facebook page", "facebook.com/mamankechi"], ["whatsapp", "WhatsApp business number", "Read once WhatsApp is connected"]];
let shopDocs = null;   // pages read by "Read my pages", saved with the shop info

async function shop() {
  const b = await api("/api/business");
  setShop(b.name);
  shopDocs = null;
  const link = ME?.chat ? await api("/api/chat/link") : null;
  view.innerHTML = `<h1>Your shop</h1>
    <p class="note" style="margin:-6px 2px 14px">This is everything Sabi tells customers about your shop. Prices and stock always come from your books.</p>
    ${link ? `<h3>Your customer chat link</h3>
    <div class="card">
      <p style="margin:0 0 8px">Customers who open this link chat with Sabi: prices, stock, orders and payments, in their own language.</p>
      <div class="linkbox">${esc(link.url)}</div>
      <div class="grid" style="margin-top:10px"><button class="btn" id="share-link">Share</button><button class="btn ghost" id="copy-link">Copy</button></div>
      <p class="note">Put it in your WhatsApp status, Instagram bio and on your price list. Messages that need you come to your Chat.</p>
    </div>` : ""}
    <h3>Your pages</h3>
    <div class="card">
      ${LINKS.map(([k, label, hint]) => `<label class="field"><span>${label}</span>
        <input id="link-${k}" value="${esc(b.links[k] || "")}" placeholder="${esc(hint)}" ${k === "whatsapp" ? "" : 'autocapitalize="off"'}></label>`).join("")}
      <button class="btn ghost" id="learn">Read my pages</button>
      <div id="learn-status"></div>
    </div>
    <h3>What Sabi tells customers</h3>
    <div class="card">
      <label class="field"><span>Shop info: address, hours, delivery, payment, returns, bulk terms</span>
        <textarea id="shop-info" placeholder="Shop 14, Oke-Arin Market, Lagos Island. Open Monday to Saturday, 7am to 7pm…">${esc(b.shop_info)}</textarea></label>
      <label class="field"><span>Payment details given with every order</span>
        <input id="pay" value="${esc(b.payment_details)}" placeholder="Transfer to … (account name). Send the screenshot here."></label>
      <button class="btn" id="save">Save</button>
      <p class="note" id="saved">${b.updated_at ? `Last updated ${esc(time(b.updated_at))}` : "Not set up yet."}
        ${b.sources.length ? ` · Sabi can also look things up on ${b.sources.length} page${b.sources.length > 1 ? "s" : ""} from your links.` : ""}</p>
    </div>
    <h3>Settings</h3>
    <div class="card" id="prefs"><div class="skeleton"></div></div>
    ${ME?.push ? `<h3>Notifications</h3>
    <div class="card">
      <p style="margin:0 0 10px">Get told on this phone when a customer needs you, a payment claim comes in, or your summary is ready, even when Sabi is closed.</p>
      <button class="btn ghost" id="push-btn">Turn on notifications</button>
      <p class="note" id="push-note"></p>
    </div>` : ""}
    ${ME?.chat ? `<h3>This device</h3>
    <div class="card">
      <p style="margin:0 0 10px">Signed in here. Signing out asks for your PIN or password next time; your other phones stay signed in.</p>
      <button class="btn ghost" id="sign-out">Sign out of this device</button>
    </div>` : ""}
    ${ME?.chat ? `<h3>Your data</h3>
    <div class="card">
      <p style="margin:0 0 10px">Your books, chats, voice notes and photos are kept for this test. Delete them any time: this can't be undone.</p>
      <button class="btn danger" id="delete-shop">Delete my shop and all its data</button>
    </div>` : ""}`;
  if (link) {
    $("#share-link").onclick = () => navigator.share
      ? navigator.share({ title: b.name, text: `Chat with ${b.name}: prices, orders and delivery`, url: link.url }).catch(() => {})
      : copy(link.url, $("#share-link"));
    $("#copy-link").onclick = () => copy(link.url, $("#copy-link"));
    $("#sign-out").onclick = async () => {
      await send("POST", "/api/logout", {}).catch(() => {});
      localStorage.removeItem(TOKEN_KEY);
      ME = null;
      history.replaceState(null, "", location.pathname + "#/");   // no reload, so the note stays
      login("You're signed out.");
    };
    $("#delete-shop").onclick = async () => {
      const typed = prompt(`This deletes ${b.name}'s books and chats for good. Type DELETE to confirm.`);
      if ((typed || "").trim().toUpperCase() !== "DELETE") return;
      await send("DELETE", "/api/shop", {});
      localStorage.removeItem(TOKEN_KEY);
      ME = null;
      location.hash = "#/today";
      login("Your shop and its data were deleted.");
    };
  }
  prefsCard();
  if (ME?.push) pushButton();
  const links = () => Object.fromEntries(LINKS.map(([k]) => [k, $(`#link-${k}`).value.trim() || null]));
  $("#learn").onclick = async () => {
    const btn = $("#learn"), out = $("#learn-status");
    btn.disabled = true; btn.textContent = "Reading your pages…";
    out.innerHTML = "";
    try {
      const d = await send("POST", "/api/business/learn", links());
      out.innerHTML = `<div class="status">${Object.entries(d.status).map(([k, v]) =>
        `<div>${esc(k)}: ${v === "ok" ? `<span class="ok">read ✓</span>` : `<span class="bad">${esc(v)}</span>`}</div>`).join("")}</div>
        ${d.shop_info ? `<p class="note">Sabi drafted the shop info below from your pages. Check it, fix anything wrong, then Save.</p>` : ""}
        ${d.missing?.length ? `<p class="note">Customers may ask, and your pages don't say: ${d.missing.map(esc).join("; ")}.</p>` : ""}
        ${d.products_seen?.length ? `<p class="note">Products on your pages: ${d.products_seen.map(p => esc(p.name)).join(", ")}.</p>` : ""}`;
      if (d.shop_info) {
        $("#shop-info").value = d.shop_info;
        shopDocs = d.docs;
      }
    } catch (e) {
      out.innerHTML = `<div class="err">${esc(e.message)}</div>`;
    } finally {
      btn.disabled = false; btn.textContent = "Read my pages";
    }
  };
  $("#save").onclick = async () => {
    const btn = $("#save");
    btn.disabled = true;
    try {
      const body = { shop_info: $("#shop-info").value, payment_details: $("#pay").value, links: links() };
      if (shopDocs) body.docs = shopDocs;
      const saved = await send("PUT", "/api/business", body);
      shopDocs = null;
      $("#saved").textContent = `Saved. Sabi uses this from the next customer message.${saved.sources.length ? ` It can also look things up on ${saved.sources.length} page${saved.sources.length > 1 ? "s" : ""} from your links.` : ""}`;
    } catch (e) {
      $("#saved").innerHTML = `<span class="err">${esc(e.message)}</span>`;
    } finally {
      btn.disabled = false;
    }
  };
}

// ---- her settings ----------------------------------------------------------------------

const HOURS = Array.from({ length: 19 }, (_, i) => i + 5);
const hourLabel = h => `${h % 12 || 12} ${h < 12 ? "am" : "pm"}`;
const TONE_LABEL = { gentle: "Gentle", firm: "Firm", pidgin: "Pidgin" };

async function prefsCard() {
  const box = $("#prefs");
  const p = await api("/api/prefs");
  const s = p.summary, sv = p.savings;
  box.innerHTML = `
    <label class="field"><span>Your summary</span>
      <select id="p-freq">${[["daily", "Every day"], ["weekly", "Once a week"], ["off", "Off"]].map(([v, l]) =>
        `<option value="${v}" ${s.frequency === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <div class="grid" id="p-when">
      <label class="field"><span>At</span><select id="p-hour">${HOURS.map(h =>
        `<option value="${h}" ${s.hour === h ? "selected" : ""}>${hourLabel(h)}</option>`).join("")}</select></label>
      <label class="field" id="p-day-f"><span>On</span><select id="p-day">${p.weekdays.map((d, i) =>
        `<option value="${i}" ${s.weekday === i ? "selected" : ""}>${d}</option>`).join("")}</select></label>
    </div>
    <label class="field"><span>Sabi's language</span><select id="p-lang">${[...new Set([...p.languages, p.language])].map(l =>
      `<option value="${esc(l)}" ${p.language === l ? "selected" : ""}>${l === "auto" ? "The language I write in" : esc(l)}</option>`).join("")}</select></label>
    <label class="field"><span>Money my books are kept in</span><select id="p-cur">${p.currencies.map(c =>
      `<option value="${c.code}" ${p.currency === c.code ? "selected" : ""}>${esc(c.label)}</option>`).join("")}</select></label>
    <label class="field"><span>Payment reminders sound</span><select id="p-tone">${p.tones.map(t =>
      `<option value="${t}" ${p.reminder_tone === t ? "selected" : ""}>${TONE_LABEL[t] || t}</option>`).join("")}</select></label>
    <div class="grid">
      <label class="field"><span>Put aside from profit</span><select id="p-save">${[0, 5, 10, 15, 20, 25, 30].map(v =>
        `<option value="${v}" ${Number(sv.percent) === v ? "selected" : ""}>${v ? v + "%" : "Nothing"}</option>`).join("")}</select></label>
      <label class="field"><span>Savings goal (${esc(moneySign())})</span><input id="p-goal" inputmode="numeric" value="${sv.goal_kobo ? sv.goal_kobo / 100 : ""}" placeholder="50000"></label>
    </div>
    <label class="field"><span>Saving for</span><input id="p-sname" value="${esc(sv.name)}" placeholder="Ajo, school fees, new freezer…"></label>
    <label class="toggle"><input type="checkbox" id="p-low" ${p.low_data ? "checked" : ""}> <span><b>Low-data mode</b><br>Photos load only when you tap them, and the chat checks for messages less often.</span></label>
    <label class="toggle"><input type="checkbox" id="p-score" ${p.share_score ? "checked" : ""}> <span><b>Show my Sabi Score on my statement</b><br>Lenders see your score and how it was worked out.</span></label>
    ${ME?.chat ? `<label class="toggle"><input type="checkbox" id="p-prices" ${p.share_prices ? "checked" : ""}> <span><b>Share my supplier prices</b><br>Anonymous: other Sabi shops see only the lowest, middle and highest price paid, never your name or supplier. You see theirs too.</span></label>` : ""}
    <button class="btn" id="p-save-btn">Save settings</button>
    <p class="note" id="p-note">${esc(p.summary_text)}${p.next_summary ? ` · next: ${esc(time(p.next_summary))}` : ""}. You can also just tell Sabi, e.g. “send my summary at 7am”.</p>`;
  const sync = () => {
    $("#p-when").hidden = $("#p-freq").value === "off";
    $("#p-day-f").hidden = $("#p-freq").value !== "weekly";
  };
  $("#p-freq").onchange = sync; sync();
  $("#p-save-btn").onclick = async () => {
    const btn = $("#p-save-btn");
    btn.disabled = true;
    const goal = Number(($("#p-goal").value || "0").replace(/[^0-9.]/g, ""));
    const body = {
      summary: { frequency: $("#p-freq").value, hour: Number($("#p-hour").value), weekday: Number($("#p-day").value) },
      language: $("#p-lang").value, currency: $("#p-cur").value, reminder_tone: $("#p-tone").value, low_data: $("#p-low").checked, share_score: $("#p-score").checked,
      savings: { percent: Number($("#p-save").value), goal_kobo: Math.round(goal * 100), ...($("#p-sname").value.trim() ? { name: $("#p-sname").value.trim() } : {}) },
    };
    if ($("#p-prices")) body.share_prices = $("#p-prices").checked;
    try {
      const saved = await send("PUT", "/api/prefs", body);
      if (ME) { ME.low_data = saved.low_data; if (saved.language !== "auto") ME.language = saved.language; }
      if (ME && ME.currency?.code !== saved.currency) ME = await api("/api/me");   // amounts now show in her money
      $("#p-note").textContent = `Saved. ${saved.summary_text}${saved.next_summary ? ` · next: ${time(saved.next_summary)}` : ""}.`;
    } catch (e) {
      $("#p-note").innerHTML = `<span class="err">${esc(e.message)}</span>`;
    } finally { btn.disabled = false; }
  };
}

// ---- phone notifications (pilot shops) -----------------------------------------------------

function b64ToBytes(s) {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const raw = atob((s + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, c => c.charCodeAt(0));
}

async function pushState() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) return "unsupported";
  const reg = await navigator.serviceWorker.ready;
  return (await reg.pushManager.getSubscription()) ? "on" : Notification.permission === "denied" ? "blocked" : "off";
}

async function pushButton() {
  const btn = $("#push-btn"), note = $("#push-note");
  const show = async () => {
    const st = await pushState();
    btn.hidden = st === "unsupported" || st === "blocked";
    btn.textContent = st === "on" ? "Turn off notifications" : "Turn on notifications";
    note.textContent = st === "unsupported"
      ? (/iphone|ipad/i.test(navigator.userAgent) ? "On iPhone, add Sabi to your home screen first, then open it from there to turn these on." : "This browser can't show notifications.")
      : st === "blocked" ? "Notifications are blocked for Sabi. Allow them in your browser's site settings." : st === "on" ? "On for this phone." : "";
  };
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        await send("DELETE", "/api/push", sub.toJSON());
        await sub.unsubscribe();
      } else {
        if (await Notification.requestPermission() !== "granted") { await show(); return; }
        const { key } = await api("/api/push/key");
        const fresh = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(key) });
        await send("POST", "/api/push", fresh.toJSON());
        await send("POST", "/api/push/test", {});
      }
    } catch (e) { note.textContent = `Couldn't change notifications: ${e.message}`; }
    finally { btn.disabled = false; await show(); }
  };
  await show();
}

async function statement() {
  const token = localStorage.getItem(TOKEN_KEY);
  const res = await fetch("/api/statement", { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (res.status === 401) throw new AuthError(await res.text());
  if (!res.ok) throw new Error(`${res.status}`);
  const html = await res.text();
  view.innerHTML = `<h1>Business statement</h1>
    <p class="note" style="margin:-6px 2px 12px">Made from your books. Share it with a bank, microfinance or supplier when you ask for credit.</p>
    <div class="grid" style="margin-bottom:12px">
      <button class="btn" id="share">Share link</button>
      <button class="btn ghost" id="print">Print / PDF</button>
    </div>
    <div id="share-out"></div>
    <div id="score"></div>
    <iframe class="statement" title="Business statement" sandbox="allow-modals allow-scripts allow-same-origin"></iframe>`;
  const frame = $("iframe.statement");
  frame.srcdoc = html;
  frame.onload = () => { frame.style.height = frame.contentDocument.documentElement.scrollHeight + "px"; };
  $("#print").onclick = () => frame.contentWindow.print();
  api("/api/score").then(s => {
    $("#score").innerHTML = `<div class="card score-card">
      <div class="score-head"><div><div class="label">Sabi Score</div>
        <div class="value">${s.score ?? "–"}<span>/100</span></div><div class="hint">${esc(s.band)}</div></div>
        <p>${esc(s.note || "How ready your records are to back a loan. It reads only your own books.")}</p></div>
      ${s.parts.map(p => `<div class="part"><div class="pl"><span>${esc(p.name)}</span><b>${p.value}</b></div>
        <div class="bar"><i style="width:${p.value}%"></i></div><div class="hint">${esc(p.measured)}</div></div>`).join("")}
      <div class="tip" style="margin-top:10px">➡️ <b>To raise it:</b> ${esc(s.next_step)}</div></div>`;
  }).catch(() => {});
  $("#share").onclick = async () => {
    try {
      const { url, days } = await send("POST", "/api/statement/link", {});
      if (navigator.share) {
        await navigator.share({ title: "Business statement", url }).catch(() => {});
      }
      $("#share-out").innerHTML = `<div class="card" style="margin-bottom:12px;font-size:14px;overflow-wrap:anywhere">
        This link works for ${days} days: <a href="${esc(url)}" target="_blank" rel="noopener">${esc(url)}</a></div>`;
    } catch (e) { $("#share-out").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  };
}

const PERIODS = [["today", "Today"], ["week", "This week"], ["month", "This month"], ["all", "All time"]];
let period = "week";

// ---- insights: business intelligence from her own books ----------------------------

async function insights() {
  const d = await api("/api/insights");
  const k = d.kpis;
  const change = k.sales_change_pct;
  const trend = change == null ? `<div class="hint">No sales in the 30 days before</div>`
    : `<div class="trend ${change >= 0 ? "up" : "down"}">${change >= 0 ? "▲" : "▼"} ${Math.abs(change)}% on the 30 days before</div>`;
  const maxBest = Math.max(1, ...d.best_sellers.map(r => r.sales_kobo));
  const list = (rows, render, empty) => rows.length ? `<div class="card">${rows.map(render).join("")}</div>` : `<div class="empty small">${empty}</div>`;
  const f = d.forecast || {};
  view.innerHTML = `<h1>Insights</h1>
    <p class="lead">Last 30 days, from the books Sabi keeps.</p>
    ${d.takeaways.length ? `<h3>What your books say</h3><ol class="card takeaways">${d.takeaways.map(t => `<li>${esc(t)}</li>`).join("")}</ol>` : ""}
    <div class="grid">
      <div class="card kpi wide"><div class="label">Sales, last 30 days</div><div class="value">${naira(k.sales_kobo)}</div>${trend}</div>
      <div class="card kpi"><div class="label">Gross profit</div><div class="value">${naira(k.gross_profit_kobo)}</div>
        <div class="hint">${k.margin_pct == null ? "costs not known yet" : `${k.margin_pct}% of sales`}</div></div>
      <div class="card kpi"><div class="label">Owed to you</div><div class="value">${naira(k.owed_to_you_kobo)}</div></div>
      <div class="card kpi"><div class="label">Stock value</div><div class="value">${naira(k.stock_value_kobo)}</div><div class="hint">at what you paid</div></div>
      <div class="card kpi"><div class="label">Cash and bank</div><div class="value">${naira(k.cash_kobo)}</div></div>
    </div>
    <h3>Sales, week by week</h3>
    <div class="card chart">${weekChart(d.weekly_sales)}<div class="chart-note" id="week-note">Tap a week to see its sales.</div></div>
    <h3>Best sellers</h3>
    ${list(d.best_sellers, r => `<div class="hbar" title="${esc(r.name)}: ${naira(r.sales_kobo)}, ${r.qty} sold">
        <div class="hbar-top"><span>${esc(r.name)}</span><b>${naira(r.sales_kobo)}</b></div>
        <div class="hbar-track"><i style="width:${Math.max(2, (r.sales_kobo / maxBest) * 100).toFixed(1)}%"></i></div>
        <div class="hint">${r.qty} sold · ${r.share_pct ?? 0}% of sales</div></div>`, "No sales in the last 30 days.")}
    <h3>What makes you the most profit</h3>
    ${list(d.top_earners, r => `<div class="line"><span>${esc(r.name)}</span><span>${naira(r.profit_kobo)} <small class="hint">${r.margin_pct}%</small></span></div>`,
      "Tell Sabi what you paid for your stock to see profit by product.")}
    ${f.items?.length ? `<h3>Coming up</h3><div class="card"><b>${esc(f.headline)}</b>${f.order_by ? `<div class="hint">Order by ${esc(f.order_by)}</div>` : ""}
      ${f.items.map(i => `<div class="line"><span>${esc(i.name)}</span><span>order ${i.order_qty}</span></div>`).join("")}
      ${f.clear?.length ? `<div class="hint" style="margin-top:6px">Sell first, don't reorder: ${f.clear.map(esc).join(", ")}</div>` : ""}</div>` : ""}
    <h3>Stock that isn't moving</h3>
    ${list(d.slow_stock, r => `<div class="line"><span>${esc(r.name)} <small class="hint">${r.stock} left</small></span><span>${naira(r.tied_up_kobo)}</span></div>`,
      "Everything in stock sold at least once in 30 days.")}
    <h3>Customers asked for, you didn't have</h3>
    ${list(d.customer_demand, r => `<div class="line"><span>${esc(r.item)}</span><span>${r.times}×</span></div>`, "Nothing missed. 👍")}
    ${d.price_watch.length ? `<h3>Prices to check</h3><div class="card">${d.price_watch.map(p => `<p class="tip">${esc(p.text)}</p>`).join("")}</div>` : ""}
    <a class="card statement-cta" href="#/profit"><span class="badge sale">▲</span>
      <span><b>Full profit and loss</b><br><span class="hint">Today, this week, this month</span></span></a>`;
  view.querySelectorAll(".week").forEach(g => g.onclick = () => {
    view.querySelectorAll(".week").forEach(x => x.classList.toggle("on", x === g));
    $("#week-note").textContent = `Week to ${g.dataset.label}: ${naira(Number(g.dataset.kobo))}`;
  });
}

// Eight weekly columns: one hue, rounded tops on a shared baseline, a label on the highest and the latest week
// only; every column can be tapped (or hovered) for its value.
function weekChart(weeks) {
  const W = 320, H = 150, top = 18, base = 122, gap = 10, n = weeks.length;
  const bw = (W - gap * (n + 1)) / n, max = Math.max(1, ...weeks.map(w => w.sales_kobo));
  const peak = weeks.findIndex(w => w.sales_kobo === max);
  const bars = weeks.map((w, i) => {
    const h = w.sales_kobo ? Math.max(3, ((base - top) * w.sales_kobo) / max) : 0;
    const x = gap + i * (bw + gap), y = base - h;
    const label = i === peak || i === n - 1;
    return `<g class="week" data-label="${esc(w.label)}" data-kobo="${w.sales_kobo}" tabindex="0">
      <title>Week to ${esc(w.label)}: ${naira(w.sales_kobo)}</title>
      <rect class="hit" x="${x - gap / 2}" y="${top - 14}" width="${bw + gap}" height="${base - top + 30}"></rect>
      ${h ? `<path class="col" d="M${x},${base} V${y + 4} Q${x},${y} ${x + 4},${y} H${x + bw - 4} Q${x + bw},${y} ${x + bw},${y + 4} V${base} Z"></path>` : ""}
      ${label && w.sales_kobo ? `<text class="val" x="${x + bw / 2}" y="${y - 5}">${naira(w.sales_kobo).replace(/,\d{3}$/, "k")}</text>` : ""}
      ${(n - 1 - i) % 2 === 0 ? `<text class="lab" x="${x + bw / 2}" y="${base + 16}">${esc(w.label)}</text>` : ""}
    </g>`;
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Sales by week for the last ${n} weeks">
    <line class="axis" x1="0" x2="${W}" y1="${base}" y2="${base}"></line>${bars}</svg>`;
}

async function profit() {
  const [p, cash] = await Promise.all([api(`/api/report/pnl?period=${period}`), api("/api/report/cash")]);
  const exp = Object.entries(p.expenses_kobo || {});
  const EXP = { stock_loss: "Stock loss", transport: "Transport", power: "Power / fuel", other_exp: "Other expenses" };
  const sales = Math.max(p.sales_kobo, 1);
  const pct = v => Math.max(0, Math.min(100, (v / sales) * 100)).toFixed(1);
  const expTotal = exp.reduce((a, [, v]) => a + v, 0);
  view.innerHTML = `<h1>Profit</h1>
    <div class="chips">${PERIODS.map(([k, l]) => `<button class="chip${k === period ? " on" : ""}" data-period="${k}">${l}</button>`).join("")}</div>
    <div class="card pl">
      <div class="line"><span>Sales</span><span>${naira(p.sales_kobo)}</span></div>
      <div class="line"><span>Cost of goods sold</span><span class="neg">−${naira(p.cogs_kobo).replace("−", "")}</span></div>
      <div class="line"><b>Gross profit</b><b>${naira(p.gross_profit_kobo)}</b></div>
      ${exp.map(([k, v]) => `<div class="line"><span>${esc(EXP[k] || k)}</span><span class="neg">−${naira(v).replace("−", "")}</span></div>`).join("")}
      <div class="line total"><span>Net profit</span><span>${naira(p.net_profit_kobo)}</span></div>
      ${p.sales_kobo ? `<div class="bar" aria-hidden="true">
          <span style="width:${pct(p.cogs_kobo)}%;background:var(--muted)"></span>
          <span style="width:${pct(expTotal)}%;background:var(--warn)"></span>
          <span style="width:${pct(Math.max(p.net_profit_kobo, 0))}%;background:var(--good)"></span></div>
        <div class="hint" style="color:var(--muted);font-size:12px">Of every ${naira(10000)} sold: ${naira(pct(p.cogs_kobo).split(".")[0] * 100)} goods, ${naira(pct(expTotal).split(".")[0] * 100)} expenses, ${naira(pct(Math.max(p.net_profit_kobo, 0)).split(".")[0] * 100)} profit</div>` : ""}
    </div>
    <a class="card statement-cta" href="#/statement">
      <span class="badge payment_in">₦</span>
      <span><b>Business statement for a loan</b><br><span class="hint">Your sales, profit and stock on one page a lender can read</span></span></a>
    <h3>Money now</h3>
    <div class="grid">
      <div class="card kpi"><div class="label">Cash</div><div class="value">${naira(cash.cash_kobo)}</div></div>
      <div class="card kpi"><div class="label">Bank</div><div class="value">${naira(cash.bank_kobo)}</div></div>
    </div>`;
  view.querySelectorAll("[data-period]").forEach(b => b.onclick = () => { period = b.dataset.period; profit(); });
}

const POST_STATUS = { draft: ["Waiting for you", "warn"], published: ["Posted", "ok"], skipped: ["Skipped", ""], failed: ["Failed", "late"] };

async function posts() {
  const rows = await api("/api/posts");
  view.innerHTML = `<div class="page-head"><h1>Posts</h1>${ME?.chat ? `<button class="btn small" id="gen-post">+ Generate post</button>` : ""}</div>${rows.length ? rows.map(p => {
    const [label, cls] = POST_STATUS[p.status] || [p.status, ""];
    const ids = p.platform_ids ? Object.entries(p.platform_ids).map(([k, v]) => `${esc(k)}: ${esc(v)}`).join(" · ") : "";
    return `<article class="card post">
      ${p.image ? `<img alt="Flyer for ${esc(p.product)}" data-src="${esc(p.image)}">` : ""}
      <div class="body">
        <span class="tag ${cls}">${esc(label)}</span> <b>${esc(p.product || "")}</b>
        <div class="caption">${esc(p.caption)}</div>
        <div class="why">Why this product: ${esc(p.reason)}</div>
        ${ids ? `<div class="why">${ids}</div>` : ""}
        ${p.error ? `<div class="err">${esc(p.error)}</div>` : ""}
        <div class="why">${esc(time(p.created_at))}</div>
        ${p.image ? `<button class="btn ghost share-post" data-img="${esc(p.image)}" data-caption="${esc(p.caption)}" style="margin-top:10px">Share to Status / Instagram</button>` : ""}
      </div></article>`;
  }).join("") : `<div class="empty">No posts yet. Tap “Make tomorrow’s post” in the 8 pm summary, or ask Sabi ${where()}.</div>`}`;
  for (const img of view.querySelectorAll("img[data-src]")) {
    mediaUrl(img.dataset.src).then(u => img.src = u).catch(() => img.remove());
  }
  view.querySelectorAll(".share-post").forEach(btn => btn.onclick = () => sharePicture(btn.dataset.img, btn.dataset.caption, btn));
  const gen = $("#gen-post");
  if (gen) gen.onclick = () => { chatAsk = "Make a post"; location.hash = "#/chat"; };   // Sabi picks the product and makes the flyer
}

// The flyer and its caption to Instagram, WhatsApp Status or Facebook in one tap: the phone's share sheet
// lists them. Instagram drops shared text, so the caption is copied for her to paste.
async function sharePicture(src, caption, btn) {
  try {
    const blob = await (await fetch(await mediaUrl(src))).blob();
    const file = new File([blob], "sabi-post.jpg", { type: blob.type || "image/jpeg" });
    if (navigator.canShare?.({ files: [file] })) {
      await navigator.clipboard?.writeText(caption || "").catch(() => {});
      await navigator.share({ files: [file], text: caption || "" });
      toast("Caption copied too: on Instagram, tap the caption box and paste.");
    } else {
      const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "sabi-post.jpg" });
      a.click();
      if (caption) copy(caption, btn);
      toast("Picture saved and caption copied. Open Instagram and post it from your gallery.");
    }
  } catch (e) { if (e.name !== "AbortError") toast("Couldn't share it. Press and hold the picture to save it."); }
}

// ---- sheets: entry detail with its evidence, debtor statement ---------------

function openSheet(title, html) {
  $("#sheet-title").textContent = title;
  $("#sheet-body").innerHTML = html;
  $("#sheet").hidden = $("#backdrop").hidden = false;
  $("#sheet").scrollTop = 0;
}
function closeSheet() { $("#sheet").hidden = $("#backdrop").hidden = true; }

async function showEntry(id) {
  openSheet("Loading…", `<div class="skeleton"></div><div class="skeleton"></div>`);
  const e = await api(`/api/entries/${encodeURIComponent(id)}`);
  const [label] = KIND[e.kind] || [e.kind];
  const ev = e.evidence;
  const notes = [];
  if (e.status === "reversed") notes.push(`<div class="banner">This entry was cancelled. <button class="chip" data-entry="${esc(e.reversed_by)}">See the cancellation</button></div>`);
  if (e.reverses_id) notes.push(`<div class="banner">This cancels an earlier entry. <button class="chip" data-entry="${esc(e.reverses_id)}">See the original</button></div>`);
  openSheet(`${label} · ${naira(e.amount_kobo)}`, `
    ${notes.join("")}
    <div class="card">
      <div><b>${esc(e.items?.join(", ") || e.memo || label)}</b></div>
      <div class="meta" style="color:var(--muted);font-size:14px">${esc(day(e.date))}${e.contact ? ` · ${esc(e.contact)}` : ""}${e.due_date ? ` · due ${esc(day(e.due_date))}` : ""}</div>
      <div style="font-size:13px;color:var(--muted);margin-top:4px">${esc(BY[e.created_by] || e.created_by)}</div>
    </div>
    ${e.why?.length ? `<h3>Why this is in your books</h3>
    <ol class="why-steps">${e.why.map(s => `<li><span class="wi">${esc(s.icon)}</span><div><b>${esc(s.title)}</b>
      <div class="wd">${esc(s.detail).replace(/\n/g, "<br>")}</div></div></li>`).join("")}</ol>` : ""}
    <h3>Where this came from</h3>
    <div class="card evidence" id="evidence">${ev ? `
      ${ev.media ? `<div id="media"></div>` : ""}
      ${ev.original_text ? `<blockquote>“${esc(ev.original_text)}”</blockquote>` : ""}
      ${ev.normalized_text && ev.normalized_text !== ev.original_text ? `<div style="font-size:13px;color:var(--muted)">Read as: ${esc(ev.normalized_text)}</div>` : ""}
      <dl class="kv">
        <dt>Received</dt><dd>${esc(time(ev.created_at))} via ${esc(ev.channel)}</dd>
        ${ev.model ? `<dt>Model</dt><dd>${esc(ev.model)}</dd>` : ""}
        ${ev.prompt_version ? `<dt>Prompt</dt><dd>${esc(ev.prompt_version)}</dd>` : ""}
      </dl>
      ${ev.extracted_json ? `<details><summary>What Sabi extracted</summary><pre>${esc(JSON.stringify(ev.extracted_json, null, 2))}</pre></details>` : ""}`
      : `<div class="empty" style="padding:12px">No message behind this entry (opening stock or seed data).</div>`}
    </div>
    <h3>Double entry</h3>
    <div class="card"><table class="lines">
      <thead><tr><th>Account</th><th class="num">Debit</th><th class="num">Credit</th></tr></thead>
      <tbody>${e.lines.map(l => `<tr><td>${esc(l.account_name)}<small>${esc([l.qty ? `${l.qty} × ${l.product || ""}` : l.product, l.contact].filter(Boolean).join(" · "))}</small></td>
        <td class="num">${l.debit_kobo ? naira(l.debit_kobo, true) : ""}</td><td class="num">${l.credit_kobo ? naira(l.credit_kobo, true) : ""}</td></tr>`).join("")}</tbody>
      <tfoot><tr><td><b>Total</b></td><td class="num"><b>${naira(e.lines.reduce((a, l) => a + l.debit_kobo, 0), true)}</b></td>
        <td class="num"><b>${naira(e.lines.reduce((a, l) => a + l.credit_kobo, 0), true)}</b></td></tr></tfoot>
    </table></div>`);
  if (ev?.media) {
    const box = $("#media");
    try {
      const url = await mediaUrl(ev.media.url);
      box.innerHTML = ev.media.kind === "audio" ? `<audio controls src="${url}"></audio>`
        : ev.media.kind === "image" ? `<img alt="Photo she sent" src="${url}">` : `<a href="${url}" download>Download file</a>`;
    } catch { box.innerHTML = `<div class="err">Couldn't load the original media.</div>`; }
  }
}

async function showDebtor(id) {
  openSheet("Loading…", `<div class="skeleton"></div>`);
  const s = await api(`/api/debtors/${encodeURIComponent(id)}`);
  openSheet(`${s.contact.name} owes ${naira(s.owes_kobo)}`, `
    <div class="card list">${s.rows.slice().reverse().map(r => `
      <button class="row" data-entry="${esc(r.entry_id)}">
        <span class="badge ${esc(r.kind)}">${esc((KIND[r.kind] || ["", "?"])[1])}</span>
        <span class="main"><div class="title${r.status === "reversed" ? " struck" : ""}">${esc(r.memo || (KIND[r.kind] || [r.kind])[0])}</div>
          <div class="meta">${esc(day(r.date))}${r.due_date ? ` · due ${esc(day(r.due_date))}` : ""} · balance ${naira(r.balance_kobo)}</div></span>
        <span class="amt ${r.change_kobo < 0 ? "in" : "out"}">${r.change_kobo < 0 ? "paid " : "+"}${naira(Math.abs(r.change_kobo))}</span>
      </button>`).join("")}</div>`);
}

// ---- chat with Sabi (pilot shops) -------------------------------------------------

let chatLast = 0, chatTimer = null, chatBusy = false;
let chatAsk = null;   // a request another page hands to Chat, sent once Chat opens
const ANSWERED = "sabi.answered";

function copy(text, btn) {
  navigator.clipboard?.writeText(text).then(() => { const t = btn.textContent; btn.textContent = "Copied ✓"; setTimeout(() => (btn.textContent = t), 1500); });
}
// WhatsApp's own markup, as WhatsApp shows it: *bold*, _italic_, ~strike~. Escaped text in; links are left alone.
function waFormat(html) {
  const links = [];
  const safe = html.replace(/https?:\/\/[^\s<]+/g, u => `\u0000${links.push(u) - 1}\u0000`);
  const mark = ch => {          // ch is *, _ or ~: escaped, so it is matched as itself
    const c = "\\" + ch, R = String.raw;
    return new RegExp(R`(^|[\s(•\-–—:])` + c + R`(?=\S)([^` + c + R`\n]*?\S)` + c + R`(?=$|[\s).,!?:;])`, "gm");
  };
  return safe.replace(mark("*"), "$1<b>$2</b>").replace(mark("_"), "$1<i>$2</i>").replace(mark("~"), "$1<s>$2</s>")
    .replace(/\u0000(\d+)\u0000/g, (_, i) => links[i]);
}

function linkify(html) {
  // Escaped text in; only real links become anchors. "/s/..." statement links are made absolute so she can share them.
  return html.replace(/(https?:\/\/[^\s<]+|(?<![\w/])\/s\/[\w.-]+)/g, url => {
    const clean = url.replace(/[).,;:!?]+$/, "");
    const href = clean.startsWith("/") ? location.origin + clean : clean;
    return `<a href="${href}" target="_blank" rel="noopener">${href}</a>${url.slice(clean.length)}`;
  });
}
function answered() { try { return new Set(JSON.parse(localStorage.getItem(ANSWERED) || "[]")); } catch { return new Set(); } }
function markAnswered(id) {
  const s = answered(); s.add(id);
  try { localStorage.setItem(ANSWERED, JSON.stringify([...s].slice(-300))); } catch { /* private mode */ }
}

const VOICE = {};   // message id -> her recording, kept on this phone so she can play it back

function voiceNote(m) {
  const heard = /^🎙\s*“([\s\S]*)”$/.exec(m.body || "");
  if (m.role !== "in" || !(heard || m.audio)) return null;
  const url = m.audio || VOICE[m.id];
  return `<div class="voice">${url ? `<button class="play" type="button" data-play aria-label="Play">${SabiIcon("play")}</button>`
    : SabiIcon("mic")}<span>Voice note</span></div>
    <div class="body heard">${heard ? `“${esc(heard[1])}”` : "Listening…"}</div>`.replace(/\n\s*/g, "");
}

function bubble(m) {
  const done = answered().has(m.id);
  const el = document.createElement("div");
  el.className = `msg ${m.role === "in" ? "me" : m.role === "note" ? "note" : "them"}`;
  el.dataset.id = m.id;
  const photo = m.image ? (ME?.low_data && m.id ? `<button class="load-photo" data-src="${esc(m.image)}">📷 Tap to load photo</button>`
    : `<img alt="" src="${esc(m.image)}">`) : "";
  const voice = voiceNote(m);
  el.innerHTML = `${photo}${voice || (m.body ? `<div class="body">${linkify(waFormat(esc(m.body)))}</div>` : "")}
    ${m.buttons?.length ? `<div class="btns">${m.buttons.map(b =>
      `<button data-bid="${esc(b.id)}" ${done ? "disabled" : ""}>${esc(b.title)}</button>`).join("")}</div>` : ""}
    <div class="when">${m.at ? new Date(m.at).toLocaleTimeString("en-NG", { hour: "numeric", minute: "2-digit" }) : ""}
      ${m.role === "out" && m.body && "speechSynthesis" in window ? `<button class="flag" data-say title="Read it out">🔊</button>` : ""}
      ${m.role === "out" ? `<button class="flag" data-flag="${m.id}" title="Something wrong with this reply?">👎</button>` : ""}</div>
    ${m.role === "out" && m.image ? `<button class="btn ghost share-pic" type="button">Share to Instagram / Status ↗</button>` : ""}`;
  el.querySelector("[data-say]")?.addEventListener("click", () => say(m.body));
  el.querySelector("[data-play]")?.addEventListener("click", ev => {
    const btn = ev.currentTarget, audio = btn.audio || (btn.audio = new Audio(m.audio || VOICE[m.id]));
    audio.onended = audio.onpause = () => (btn.innerHTML = SabiIcon("play"));
    if (audio.paused) { audio.play(); btn.innerHTML = SabiIcon("stop"); } else { audio.pause(); audio.currentTime = 0; }
  });
  el.querySelector(".share-pic")?.addEventListener("click", ev => sharePicture(m.image, captionOf(m.body), ev.currentTarget));
  el.querySelector(".load-photo")?.addEventListener("click", ev => {
    ev.currentTarget.outerHTML = `<img alt="" src="${esc(ev.currentTarget.dataset.src)}">`;
  });
  el.querySelectorAll("[data-bid]").forEach(btn => btn.onclick = () => {
    el.querySelectorAll("[data-bid]").forEach(x => (x.disabled = true));
    markAnswered(m.id);
    chatSend({ button_id: btn.dataset.bid, button_title: btn.textContent }, btn.textContent);
  });
  return el;
}

// Read a reply aloud with the phone's own voice: no data used, works for people who prefer listening.
// In her language when the phone has a voice for it (most have French, Portuguese, Spanish, Arabic...).
const VOICE_LANG = { French: "fr", Portuguese: "pt", Spanish: "es", Swahili: "sw", Arabic: "ar", Amharic: "am",
  Hindi: "hi", Chinese: "zh", Zulu: "zu", Yoruba: "yo", Igbo: "ig", Hausa: "ha", Russian: "ru" };
function say(text) {
  const synth = window.speechSynthesis;
  if (synth.speaking) { synth.cancel(); return; }
  const clean = text.replace(/https?:\/\/\S+/g, "the link").replace(/[*_•]/g, "").replace(/₦\s?([\d,]+)/g, "$1 naira");
  const u = new SpeechSynthesisUtterance(clean);
  const voices = synth.getVoices();
  const code = VOICE_LANG[ME?.language] || "en";
  u.voice = (code !== "en" && voices.find(v => v.lang.startsWith(code))) || voices.find(v => v.lang === "en-NG")
    || voices.find(v => /en[-_](NG|GB|ZA|KE)/.test(v.lang)) || voices.find(v => v.lang.startsWith("en")) || null;
  if (u.voice) u.lang = u.voice.lang;
  u.rate = 0.95;
  synth.speak(u);
}

// The flyer message holds the caption between the "Why:" line and the "Post to …?" question.
function captionOf(body) {
  const text = body || "";
  const m = text.match(/Why:[^\n]*\n\n([\s\S]*?)\n\nPost to /);
  return (m ? m[1] : text).trim();
}

function showMessages(list) {
  const log = $("#log");
  if (!log) return;
  let added = false;
  for (const m of list) {
    if (m.id <= chatLast) continue;
    log.appendChild(bubble(m));
    chatLast = m.id;
    added = true;
  }
  if (added) {
    $(".chat-empty", log)?.remove();
    window.scrollTo(0, document.body.scrollHeight);
  }
  try { localStorage.setItem("sabi.seen", String(chatLast)); } catch { /* private mode */ }
}

async function chatSend(payload, shown, image, audio) {
  if (chatBusy) { toast("One moment, Sabi is still answering."); return; }
  chatBusy = true;
  const log = $("#log");
  const pending = bubble({ id: 0, role: "in", body: shown, image, audio, buttons: [] });
  pending.classList.add("pending");
  log.appendChild(pending);
  log.insertAdjacentHTML("beforeend", `<div class="typing">Sabi is typing…</div>`);
  window.scrollTo(0, document.body.scrollHeight);
  try {
    const r = await send("POST", "/api/chat", payload);
    pending.remove(); $(".typing", log)?.remove();
    const mine = audio && r.messages.find(x => x.role === "in");
    if (mine) VOICE[mine.id] = audio;
    showMessages(r.messages);
    return r.messages;
  } catch (e) {
    pending.remove(); $(".typing", log)?.remove();
    log.appendChild(bubble({ id: 0, role: "note", body: `Not sent: ${e.message}` }));
    if (payload.text) $("#chat-text").value = payload.text;
  } finally {
    chatBusy = false;
  }
}

function toast(text) {
  let t = $("#toast");
  if (!t) { document.body.insertAdjacentHTML("beforeend", `<div id="toast" class="toast"></div>`); t = $("#toast"); }
  t.textContent = text; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), 4000);
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
const blobToDataUrl = blob => new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(blob); });

const STARTERS = { books: ["I sell Things Fall Apart ₦5,000 a copy", "Sold 2 copies of Purple Hibiscus, transfer"],
  online: ["I sell Ankara gown ₦15,000", "Sold 1 gown to Bisi, she paid transfer"],
  shop: ["I sell rice 50kg bag ₦78,000", "Sold 2 bags of rice to Mama Tunde, she go pay Friday"] };

async function chat() {
  document.body.classList.add("chatting");
  chatLast = 0;
  view.innerHTML = `<div class="chat-log" id="log"></div>
    <form class="composer" id="composer">
      <label class="cbtn" title="Send a photo" aria-label="Send a photo">${SabiIcon("camera")}<input type="file" accept="image/*" hidden id="chat-photo"></label>
      <input id="chat-text" class="ctext" placeholder="Tell Sabi what happened…" autocomplete="off" enterkeyhint="send">
      <button type="button" class="cbtn" id="chat-mic" title="Voice note" aria-label="Record a voice note">${SabiIcon("mic")}</button>
      <button type="button" class="cbtn talk" id="chat-talk" title="Talk to Sabi (hands-free)" aria-label="Talk to Sabi">${SabiIcon("talk")}</button>
      <button class="cbtn send" title="Send" aria-label="Send">${SabiIcon("send")}</button>
    </form>`;
  const { messages } = await api("/api/chat");
  const products = messages.length ? [] : await api("/api/products").catch(() => []);
  if (!messages.length && products.length) {
    // A shop that already has its books (the demo shop, or one set up at sign-up): try it, don't set it up.
    const first = products.find(p => p.stock > 0) || products[0];
    const tries = [`I sold 2 ${first.name}, cash`, "Who owes me?", "How much profit this week?",
      ...(ME?.kind === "books" ? ["What should I order before resumption?"] : ["What is running low?"])];
    $("#log").innerHTML = `<div class="chat-empty">
      <div class="msg them"><div class="body">Hello 👋 I'm Sabi, the business intelligence system for ${esc(ME?.shop || "your shop")}.
Your ${products.length} products are in the books already. Ask "how is my business doing?", or see the Insights tab. Tell me what you sold or bought, ask how the business is doing, or tap the sound-wave button and just talk to me.</div></div>
      <div class="starters">${tries.map(s => `<button type="button">${esc(s)}</button>`).join("")}</div></div>`;
    $("#log").querySelectorAll(".starters button").forEach(b => b.onclick = () => { $("#chat-text").value = b.textContent; $("#chat-text").focus(); });
  } else if (!messages.length) {
    $("#log").innerHTML = `<div class="chat-empty">
      <div class="msg them"><div class="body">Hello 👋 I'm Sabi, the business intelligence system for ${esc(ME?.shop || "your shop")}.
I keep your books from what you tell me, then show you what is selling, what makes money and what to do next.
Tell me what you sell, sold or bought, the way you'd say it: type it or send a voice note 🎙, in English, Pidgin, Yoruba, Igbo, Hausa, French or any language you speak.

To start, send a photo 📷 of your price list, or type your products and prices.</div></div>
      <div class="starters">${[...(STARTERS[ME?.kind] || STARTERS.shop), "Who owes me?", "How much profit this week?"].map(s =>
        `<button type="button">${esc(s)}</button>`).join("")}</div></div>`;
    $("#log").querySelectorAll(".starters button").forEach(b => b.onclick = () => { $("#chat-text").value = b.textContent; $("#chat-text").focus(); });
  }
  showMessages(messages);
  window.scrollTo(0, document.body.scrollHeight);
  if (chatAsk) { const text = chatAsk; chatAsk = null; chatSend({ text }, text); }
  $("#composer").onsubmit = e => {
    e.preventDefault();
    const text = $("#chat-text").value.trim();
    if (!text) return;
    $("#chat-text").value = "";
    chatSend({ text }, text);
  };
  $("#chat-photo").onchange = async e => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const image = await shrinkImage(file).catch(() => null);
    if (!image) { toast("Couldn't read that photo."); return; }
    const text = $("#chat-text").value.trim();
    $("#chat-text").value = "";
    chatSend({ image, text: text || null }, text, image);
  };
  if (window.SabiTalk) {
    new SabiTalk({
      button: $("#chat-talk"),
      send: async blob => ((await chatSend({ audio: await blobToDataUrl(blob) }, "🗣 (talking)")) || [])
        .filter(m => m.role === "out" && m.body).map(m => m.body),
      speak: async text => {
        const token = localStorage.getItem(TOKEN_KEY);
        const res = await fetch("/api/speak", { method: "POST", body: JSON.stringify({ text: text.slice(0, 4000) }),
          headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
        return res.ok ? res.blob() : null;
      },
      onState: s => {
        if (s === "unsupported") toast("This browser can't record. Please type.");
        if (s === "denied") toast("Allow the microphone to talk to Sabi.");
        if (s === "idle") toast("Talk mode stopped: nobody spoke for a while.");
      },
    });
  }
  const mic = $("#chat-mic");
  let rec = null, chunks = [];
  mic.onclick = async () => {
    if (rec) { rec.stop(); return; }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") { toast("This browser can't record voice notes. Please type."); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      rec = new MediaRecorder(stream); chunks = [];
      rec.ondataavailable = ev => chunks.push(ev.data);
      rec.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        mic.classList.remove("rec"); mic.innerHTML = SabiIcon("mic");
        const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        rec = null;
        if (blob.size < 1500) { toast("That was too short. Tap the microphone, talk, then tap it again."); return; }
        chatSend({ audio: await blobToDataUrl(blob) }, "", null, URL.createObjectURL(blob));
      };
      rec.start();
      mic.classList.add("rec"); mic.innerHTML = SabiIcon("stop");
      toast("Recording… tap ■ when you finish.");
      setTimeout(() => rec?.state === "recording" && rec.stop(), 120000);
    } catch { toast("Allow the microphone to send voice notes."); }
  };
  clearInterval(chatTimer);
  chatTimer = setInterval(async () => {
    if (!$("#log") || chatBusy || document.hidden) return;
    try { showMessages((await api(`/api/chat?after=${chatLast}`)).messages); } catch { /* offline: try again next time */ }
  }, ME?.low_data ? 15000 : 4000);
}

function feedbackSheet(messageId) {
  openSheet(messageId ? "What was wrong with this reply?" : "Tell us how Sabi is doing", `
    <label class="field"><span>${messageId ? "What should Sabi have said or done?" : "What worked, what didn't, what you wish it did"}</span>
      <textarea id="fb" style="min-height:120px"></textarea></label>
    <button class="btn" id="fb-send">Send</button><p class="note">The Sabi team reads every one.</p>`);
  $("#fb-send").onclick = async () => {
    await send("POST", "/api/feedback", { note: $("#fb").value, rating: messageId ? -1 : null, message_id: messageId || null });
    $("#sheet-body").innerHTML = `<p>Thank you! 🙏</p>`;
    setTimeout(closeSheet, 1200);
  };
}

// New messages while she's on another tab: a dot on Chat.
async function checkUnread() {
  if (!ME?.chat || location.hash.startsWith("#/chat") || document.hidden) return;
  try {
    const seen = Number(localStorage.getItem("sabi.seen") || 0);
    const { messages } = await api(`/api/chat?after=${seen}`);
    $("#chat-dot").hidden = !messages.some(m => m.role !== "in");
  } catch { /* offline */ }
}

function welcome() {
  const key = `sabi.welcomed.${ME.shop}`;
  try { if (localStorage.getItem(key)) return; } catch { return; }
  const ios = /iphone|ipad/i.test(navigator.userAgent);
  openSheet("Welcome to Sabi 👋", `
    <p>Sabi is business intelligence for your shop. It keeps your books from what you tell it, answers your customers, and shows you what to do next: what's selling, what makes money, what's stuck, and what to order.</p>
    <ul class="plain">
      <li>💬 <b>Chat</b>: tell Sabi what you sold or bought. Type, send a voice note, or a photo of a receipt or price list.</li>
      <li>✅ Sabi asks before anything unusual goes in your books. Check what it records: it can make mistakes.</li>
      <li>🛍 <b>Shop ⚙</b>: your customer chat link, and what Sabi tells customers.</li>
    </ul>
    <h3>Your data</h3>
    <p class="note" style="margin-top:0">Sabi keeps your messages, voice notes, photos and books, and your customers' messages on your chat link. During this test the Sabi team can read the chats to fix problems. Nobody else sees them. You can delete everything in Shop ⚙ at any time.</p>
    <h3>Put Sabi on your home screen</h3>
    <p class="note" style="margin-top:0">${ios ? "Tap the Share button ⬆ in Safari, then “Add to Home Screen”." : "Tap ⋮ in Chrome, then “Add to Home screen” or “Install app”."}</p>
    <button class="btn" id="agree">OK, let's start</button>`);
  $("#agree").onclick = async () => {
    try { localStorage.setItem(key, "1"); } catch { /* private mode */ }
    send("POST", "/api/consent", {}).catch(() => {});
    closeSheet();
  };
}

let installPrompt = null;
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); installPrompt = e; $("#install").hidden = false; });

// ---- login ------------------------------------------------------------------------

// A saved key that no longer works (signed out elsewhere, expired, shop deleted): forget it and ask again.
function signedOut() {
  const had = localStorage.getItem(TOKEN_KEY);
  localStorage.removeItem(TOKEN_KEY);
  return login(had ? "Please sign in again." : "");
}

function login(message) {
  setShop("Sabi");
  view.innerHTML = `<div class="login card">
    <h1>Open your shop</h1>
    <p style="color:var(--muted)">Sign in with your phone number and your PIN or password.</p>
    <label for="in-phone">Phone number</label>
    <input id="in-phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="0803 123 4567">
    <label for="in-secret">PIN or password</label>
    <input id="in-secret" type="password" autocomplete="current-password">
    <label class="remember"><input type="checkbox" id="in-remember" checked> Keep me signed in on this device</label>
    <div class="err" id="in-err">${message ? esc(message) : ""}</div>
    <button class="btn" id="go">Sign in</button>
    <p class="note" style="text-align:center">New to Sabi? <a href="/join/">Create your shop</a> · <a href="#" id="use-key">I have an app key</a></p>
    <div class="judge-box" id="judge-box" hidden>
      <b>Judging Sabi? Try the demo shop</b>
      <span>Phone <code id="judge-phone"></code> · PIN <code id="judge-pin"></code></span>
      <button class="btn ghost" id="judge-go">Open the demo shop</button>
    </div>
    <div id="key-box" hidden>
      <label for="key">App key</label>
      <input id="key" type="password" autocomplete="off" autocapitalize="off">
      <button class="btn ghost" id="go-key">Open with key</button>
    </div></div>`;
  const signIn = async () => {
    $("#in-err").textContent = ""; $("#go").disabled = true;
    try {
      const res = await fetch("/join/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone: $("#in-phone").value.trim(), secret: $("#in-secret").value, remember: $("#in-remember").checked }) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Couldn't sign in. Try again.");
      localStorage.setItem(TOKEN_KEY, data.token); ME = null; route();
    } catch (e) { $("#in-err").textContent = e.message; $("#go").disabled = false; }
  };
  $("#go").onclick = signIn;
  $("#in-secret").onkeydown = e => e.key === "Enter" && signIn();
  $("#use-key").onclick = e => { e.preventDefault(); $("#key-box").hidden = false; $("#key").focus(); };
  fetch("/join/api/demo").then(r => r.json()).then(d => {
    if (!d.available) return;
    $("#judge-box").hidden = false;
    $("#judge-phone").textContent = d.phone; $("#judge-pin").textContent = d.pin;
  }).catch(() => {});
  $("#judge-go").onclick = async () => {
    $("#judge-go").disabled = true;
    try {
      const res = await fetch("/join/api/demo", { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Couldn't open the demo shop.");
      localStorage.setItem(TOKEN_KEY, data.token); ME = null; location.hash = "#/chat"; route();
    } catch (e) { $("#in-err").textContent = e.message; $("#judge-go").disabled = false; }
  };
  $("#go-key").onclick = () => { localStorage.setItem(TOKEN_KEY, $("#key").value.trim()); ME = null; route(); };
}

// ---- router ---------------------------------------------------------------------

const ROUTES = { chat, today, ledger, sales, owing, insights, profit, posts, shop, statement };

function setShop(name) {
  $("#shop").textContent = name || "Sabi";
  document.title = name ? `${name} · Sabi` : "Sabi";
}

async function route() {
  // A link like /app/#key=XYZ (sent by Sabi on WhatsApp) stores the key once and clears it from the address bar.
  const keyMatch = location.hash.match(/key=([^&]+)/);
  if (keyMatch) {
    localStorage.setItem(TOKEN_KEY, decodeURIComponent(keyMatch[1]));
    history.replaceState(null, "", location.pathname + "#/");
    ME = null;
  }
  if (!ME) {
    try {
      ME = await api("/api/me");
    } catch (err) {
      if (err instanceof AuthError) return signedOut();
    }
    $("#chat-tab").hidden = !ME?.chat;
    // Every shop sees its posts; shops without Instagram connected share them from there in one tap.
    if (ME?.shop) setShop(ME.shop);
    if (ME?.chat) welcome();
  }
  const name = (location.hash.match(/^#\/(\w+)/) || [])[1] || (ME?.chat ? "chat" : "today");
  const screen = ROUTES[name] || today;
  document.body.classList.remove("chatting");
  if (name !== "chat") clearInterval(chatTimer);
  if (name === "chat") $("#chat-dot").hidden = true;
  document.querySelectorAll(".tabs a").forEach(a => a.classList.toggle("on", a.dataset.tab === name));
  $("#sub").textContent = { chat: "Talk to Sabi", today: "Today", ledger: "Every entry, with its receipt", sales: "Customer chats and orders", shop: "What Sabi tells customers", statement: "For a loan or supplier credit", owing: "Customers who owe", insights: "What your books say", profit: "Profit and loss", posts: "Promotions" }[name] || "";
  view.innerHTML = `<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>`;
  try {
    await screen();
  } catch (err) {
    if (err instanceof AuthError) return signedOut();
    view.innerHTML = `<div class="empty">Couldn't load this page.<br><small>${esc(err.message)}</small></div>`;
  }
}

document.addEventListener("click", ev => {
  const entry = ev.target.closest("[data-entry]");
  if (entry && entry.dataset.entry) { ev.preventDefault(); showEntry(entry.dataset.entry).catch(e => openSheet("Error", esc(e.message))); return; }
  const flag = ev.target.closest("[data-flag]");
  if (flag) { ev.preventDefault(); feedbackSheet(Number(flag.dataset.flag)); return; }
  const debtor = ev.target.closest("[data-debtor]");
  if (debtor) { ev.preventDefault(); showDebtor(debtor.dataset.debtor).catch(e => openSheet("Error", esc(e.message))); }
});
$("#sheet-close").onclick = closeSheet;
$("#backdrop").onclick = closeSheet;
document.addEventListener("keydown", e => { if (e.key === "Escape") closeSheet(); });
$("#refresh").onclick = route;
$("#feedback").onclick = () => feedbackSheet(null);
$("#install").onclick = async () => { if (!installPrompt) return; installPrompt.prompt(); await installPrompt.userChoice; installPrompt = null; $("#install").hidden = true; };
setInterval(checkUnread, 20000);
window.addEventListener("hashchange", () => { closeSheet(); route(); });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
route();
