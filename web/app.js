// Sabi owner app: read-only view of the books. No build step, no framework.
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const view = $("#view");
const TOKEN_KEY = "sabi.token";

// ---- helpers -------------------------------------------------------------

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
// Whole naira on screen: kobo from average-cost arithmetic (₦4,610.92) is noise to a trader.
// The books keep exact kobo; the entry detail's debits and credits still balance to the kobo.
function naira(kobo, exact = false) {
  const n = Math.round(kobo || 0);
  const sign = n < 0 ? "−" : "";
  const abs = Math.abs(n);
  if (!exact) return `${sign}₦${Math.round(abs / 100).toLocaleString("en-NG")}`;
  const frac = abs % 100 ? "." + String(abs % 100).padStart(2, "0") : "";
  return `${sign}₦${Math.floor(abs / 100).toLocaleString("en-NG")}${frac}`;
}
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
  expense: ["Expense", "E"], drawing: ["You took", "D"], stock_loss: ["Stock loss", "L"], reversal: ["Cancelled", "×"],
  opening: ["Opening stock", "O"],
};
const MONEY_IN = new Set(["sale", "payment_in"]);
const BY = { "agent:auto": "Recorded automatically", "agent:approved": "You confirmed it on WhatsApp", app: "Entered in the app", seed: "Demo data" };

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
    ${s.pending_questions ? `<div class="banner">💬 <span><b>${s.pending_questions}</b> question${s.pending_questions > 1 ? "s" : ""} waiting for you on WhatsApp</span></div>` : ""}
    <div class="grid">
      <div class="card kpi"><div class="label">Sales today</div><div class="value">${naira(t.sales_kobo)}</div><div class="hint">This week ${naira(w.sales_kobo)}</div></div>
      <div class="card kpi"><div class="label">Profit today</div><div class="value">${naira(t.net_profit_kobo)}</div><div class="hint">This week ${naira(w.net_profit_kobo)}</div></div>
      <div class="card kpi"><div class="label">Cash</div><div class="value">${naira(s.cash.cash_kobo)}</div></div>
      <div class="card kpi"><div class="label">Bank</div><div class="value">${naira(s.cash.bank_kobo)}</div></div>
      <a class="card kpi wide" href="#/owing" style="text-decoration:none;color:inherit">
        <div class="label">Customers owe you</div><div class="value">${naira(s.owed_total_kobo)}</div>
        <div class="hint">${s.debtors.length ? s.debtors.slice(0, 3).map(d => esc(d.name)).join(", ") : "Nobody owes you"}</div></a>
    </div>
    ${s.low_stock.length ? `<h3>Running low</h3><div class="card list">${s.low_stock.map(l => `
      <div class="row" style="cursor:default"><span class="badge stock_loss">!</span>
      <span class="main"><div class="title">${esc(l.name)}</div><div class="meta">Reorder at ${esc(l.reorder_level)}</div></span>
      <span class="amt">${esc(l.qty)} ${esc(l.unit)}</span></div>`).join("")}</div>` : ""}
    <h3>Latest</h3>
    <div class="card list">${s.recent.length ? s.recent.map(entryRow).join("") : `<div class="empty">Nothing recorded yet. Send Sabi a voice note on WhatsApp.</div>`}</div>`;
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

const PERIODS = [["today", "Today"], ["week", "This week"], ["month", "This month"], ["all", "All time"]];
let period = "week";

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
        <div class="hint" style="color:var(--muted);font-size:12px">Of every ₦100 sold: ₦${pct(p.cogs_kobo).split(".")[0]} goods, ₦${pct(expTotal).split(".")[0]} expenses, ₦${pct(Math.max(p.net_profit_kobo, 0)).split(".")[0]} profit</div>` : ""}
    </div>
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
  view.innerHTML = `<h1>Posts</h1>${rows.length ? rows.map(p => {
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
      </div></article>`;
  }).join("") : `<div class="empty">No posts yet. Tap “Make tomorrow’s post” in the 8 pm summary, or ask Sabi on WhatsApp.</div>`}`;
  for (const img of view.querySelectorAll("img[data-src]")) {
    mediaUrl(img.dataset.src).then(u => img.src = u).catch(() => img.remove());
  }
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

// ---- login ------------------------------------------------------------------------

function login(message) {
  setShop("Sabi");
  view.innerHTML = `<div class="login card">
    <h1>Open your books</h1>
    <p style="color:var(--muted)">Paste the app key Sabi gave you. It stays on this phone.</p>
    <label for="key">App key</label>
    <input id="key" type="password" autocomplete="off" autocapitalize="off">
    ${message ? `<div class="err">${esc(message)}</div>` : ""}
    <button class="btn" id="go">Open</button></div>`;
  $("#go").onclick = () => { localStorage.setItem(TOKEN_KEY, $("#key").value.trim()); route(); };
}

// ---- router ---------------------------------------------------------------------

const ROUTES = { today, ledger, owing, profit, posts };

function setShop(name) {
  $("#shop").textContent = name || "Sabi";
  document.title = `${name || "Sabi"} · Books`;
}

async function route() {
  // A link like /app/#key=XYZ (sent by Sabi on WhatsApp) stores the key once and clears it from the address bar.
  const keyMatch = location.hash.match(/key=([^&]+)/);
  if (keyMatch) {
    localStorage.setItem(TOKEN_KEY, decodeURIComponent(keyMatch[1]));
    history.replaceState(null, "", location.pathname + "#/today");
  }
  const name = (location.hash.match(/^#\/(\w+)/) || [])[1] || "today";
  const screen = ROUTES[name] || today;
  document.querySelectorAll(".tabs a").forEach(a => a.classList.toggle("on", a.dataset.tab === name));
  $("#sub").textContent = { today: "Today", ledger: "Every entry, with its receipt", owing: "Customers who owe", profit: "Profit and loss", posts: "Promotions" }[name] || "";
  view.innerHTML = `<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>`;
  try {
    await screen();
  } catch (err) {
    if (err instanceof AuthError) return login(localStorage.getItem(TOKEN_KEY) ? "That key didn't work." : "");
    view.innerHTML = `<div class="empty">Couldn't load this page.<br><small>${esc(err.message)}</small></div>`;
  }
}

document.addEventListener("click", ev => {
  const entry = ev.target.closest("[data-entry]");
  if (entry && entry.dataset.entry) { ev.preventDefault(); showEntry(entry.dataset.entry).catch(e => openSheet("Error", esc(e.message))); return; }
  const debtor = ev.target.closest("[data-debtor]");
  if (debtor) { ev.preventDefault(); showDebtor(debtor.dataset.debtor).catch(e => openSheet("Error", esc(e.message))); }
});
$("#sheet-close").onclick = closeSheet;
$("#backdrop").onclick = closeSheet;
document.addEventListener("keydown", e => { if (e.key === "Escape") closeSheet(); });
$("#refresh").onclick = route;
window.addEventListener("hashchange", () => { closeSheet(); route(); });

if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
route();
