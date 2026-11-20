// Admin: enroll customers, watch onboarding and results, customize on their behalf.
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const view = $("#view");
async function api(path, opts = {}) {
  const init = {method: opts.method || "GET", headers: {}};
  if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  const r = await fetch(path, init);
  if (r.status === 401) { location.href = "/login"; throw new Error("logged out"); }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || "Something went wrong");
  return d;
}
function toast(m) { const t = $("#toast"); t.textContent = m; t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => t.hidden = true, 2600); }
async function copy(t, w = "Copied") { try { await navigator.clipboard.writeText(t); toast(w + " ✓"); } catch { toast("Copy it by hand"); } }
function openDrawer(h) { $("#drawerBody").innerHTML = h; $("#drawer").hidden = false; return $("#drawerBody"); }
function closeDrawer() { $("#drawer").hidden = true; }
$("#drawer").addEventListener("click", e => { if (e.target.id === "drawer") closeDrawer(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });
$("#logout").onclick = async () => { await api("/api/logout", {method: "POST"}); location.href = "/login"; };
const ago = ts => { if (!ts) return "never"; const s = Date.now() / 1000 - ts; return s < 3600 ? Math.max(1, Math.floor(s / 60)) + "m ago" : s < 86400 ? Math.floor(s / 3600) + "h ago" : Math.floor(s / 86400) + "d ago"; };

const planChip = s => ({active: `<span style="color:var(--ok)">● Active</span>`, past_due: `<span style="color:var(--warn)">● Past due</span>`,
  canceled: `<span style="color:var(--bad)">● Canceled</span>`})[s] || esc(s);

async function render() {
  let list;
  try { list = await api("/api/admin/customers"); }
  catch (e) { view.innerHTML = `<div class="card err">${esc(e.message)} <a href="/">Go to your app</a></div>`; return; }
  const tot = list.reduce((a, c) => ({leads: a.leads + c.leads_30, views: a.views + c.views_30, done: a.done + (c.pct === 100)}), {leads: 0, views: 0, done: 0});
  view.innerHTML = `
  <div class="head"><div class="grow"><h1>Customers</h1><p class="muted">Enroll members, see who's stuck in setup, and jump into any account to customize it.</p></div>
    <button class="btn primary" id="enroll">+ Enroll a customer</button></div>
  <div class="stats">
    <div class="stat"><b>${list.filter(c => c.plan_status === "active").length}</b><span>Active · $${(197 * list.filter(c => c.plan_status !== "canceled").length).toLocaleString()}/mo</span></div>
    <div class="stat"><b>${tot.done}</b><span>Fully set up</span></div>
    <div class="stat"><b>${list.length - tot.done}</b><span>Still onboarding</span></div>
    <div class="stat"><b>${tot.views}</b><span>Funnel visits (30d, all)</span></div>
    <div class="stat"><b>${tot.leads}</b><span>Leads generated (30d, all)</span></div>
  </div>
  <input id="q" placeholder="Search customers" style="max-width:320px;margin-bottom:12px">
  <div class="tablewrap"><table class="atable"><thead><tr><th></th><th>Business</th><th>Plan</th><th>Setup</th><th>Leads (30d)</th><th>Contacts</th><th>Tracking</th><th>Domain</th><th>Last seen</th></tr></thead>
  <tbody>${list.map(c => `<tr class="click" data-id="${c.id}">
    <td>${c.logo ? `<img src="${esc(c.logo)}" alt="">` : ""}</td>
    <td><b>${esc(c.business || "(no name)")}</b><br><small class="muted">${esc(c.email)}${c.trade ? " · " + esc(c.trade) : ""}${c.city ? " · " + esc(c.city) : ""}</small></td>
    <td>${planChip(c.plan_status)}<br><small class="muted">${c.setup_paid ? "Setup paid" : "Setup unpaid"}</small></td>
    <td><span class="bar"><i style="width:${c.pct}%"></i></span>${c.pct}%<br><small class="muted">${esc(Object.entries(c.steps).filter(([, v]) => !v).map(([k]) => k).join(", ") || "Done")}</small></td>
    <td>${c.leads_30} <small class="muted">/ ${c.views_30} visits</small></td>
    <td>${c.contacts} / ${c.contact_limit}</td>
    <td>${c.steps["Pixel + CAPI"] ? (c.capi_errors ? `<span style="color:var(--bad)">● ${c.capi_errors} errors (24h)</span>` : `<span style="color:var(--ok)">● Live</span>`) : `<span class="muted">Not connected</span>`}</td>
    <td>${esc(c.domain) || `<span class="muted">-</span>`}</td><td>${ago(c.last_seen)}</td></tr>`).join("") ||
    `<tr><td colspan="9" class="muted" style="text-align:center;padding:30px">No customers yet. Enroll your first one.</td></tr>`}</tbody></table></div>`;
  $("#enroll").onclick = enroll;
  view.querySelectorAll("tr[data-id]").forEach(tr => tr.onclick = () => openCustomer(+tr.dataset.id));
  $("#q").oninput = e => { const q = e.target.value.toLowerCase(); view.querySelectorAll("tr[data-id]").forEach(tr => tr.hidden = q && !tr.textContent.toLowerCase().includes(q)); };
}

function enroll() {
  const d = openDrawer(`<div class="row"><h2 style="flex:1">Enroll a customer</h2><button class="icon" onclick="closeDrawer()">✕</button></div>
    <p class="muted">Creates their account and gives you a one-time link to send them. If you add their website, we import their brand right away.</p>
    <label>Business name<input id="eBiz"></label><label>Owner email<input id="eEmail" type="email"></label>
    <label>Their website (optional)<input id="eSite" placeholder="theirbusiness.com"></label>
    <label>Contact limit<input id="eLim" type="number" value="500" min="1"></label>
    <button class="btn primary" id="eGo">Create account</button><div id="eOut"></div>`);
  $("#eGo", d).onclick = async () => {
    const btn = $("#eGo", d); btn.disabled = true; btn.textContent = "Creating…";
    try {
      const r = await api("/api/admin/customers", {method: "POST", body: {business: $("#eBiz", d).value, email: $("#eEmail", d).value,
        website: $("#eSite", d).value, contact_limit: +$("#eLim", d).value || null}});
      const b = r.brand && !r.brand.error ? `✓ Brand imported: ${r.brand.colors.length} colors${r.brand.logo ? ", logo" : ""}.` : r.brand?.error ? `Brand import failed: ${esc(r.brand.error)}` : "";
      $("#eOut", d).innerHTML = `<div class="card" style="box-shadow:none;display:grid;gap:8px"><b>Account created.</b> Send them this setup link (valid 7 days, works once):
        <div class="link"><input readonly value="${esc(r.setup_link)}"><button class="btn small" id="cp">Copy</button></div><small class="muted">${b}</small></div>`;
      $("#cp", d).onclick = () => copy(r.setup_link, "Setup link copied");
      btn.hidden = true; render();
    } catch (e) { toast(e.message); btn.disabled = false; btn.textContent = "Create account"; }
  };
}

async function openCustomer(id) {
  const c = await api(`/api/admin/customers/${id}`);
  const p = c.profile;
  const st = Object.entries(c.stats);
  const d = openDrawer(`<div class="row">${p.logo ? `<img src="${esc(p.logo)}" style="height:40px;max-width:120px;object-fit:contain">` : ""}
      <h2 style="flex:1">${esc(p.business || c.email)}</h2><button class="icon" onclick="closeDrawer()">✕</button></div>
    <div class="row"><button class="btn primary" id="actAs">Open their app and customize →</button><button class="btn" id="link">New setup link</button></div>
    <table class="tbl"><tr><td>Email</td><td>${esc(c.email)}</td></tr><tr><td>Website</td><td>${esc(p.website || "-")}</td></tr>
      <tr><td>Funnel domain</td><td>${esc(c.funnel_domain || "-")}</td></tr><tr><td>Brand voice</td><td>${p.voice_guide ? "✓ written" : p.tone ? "tone picked" : "-"}</td></tr>
      <tr><td>Pixel + CAPI</td><td>${p.capi_connected ? "✓ connected" : p.pixel_id ? "pixel only" : "-"}</td></tr></table>
    <h3>Funnels, last 30 days</h3>
    <table class="tbl"><tr><td></td><td><b>Visits</b></td><td><b>Leads</b></td><td><b>Convert</b></td></tr>
      ${st.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${v.views_30}</td><td>${v.leads_30}</td><td>${v.rate_30}%</td></tr>`).join("")}</table>
    ${c.capi_log.length ? `<h3>Recent Facebook events</h3><table class="tbl">${c.capi_log.map(x => `<tr><td>${esc(x.event)}</td><td style="color:${x.status === "ok" ? "var(--ok)" : "var(--bad)"}">${esc(x.status)}</td><td>${esc(x.detail)}</td><td>${ago(x.created)}</td></tr>`).join("")}</table>` : ""}
    <h3>Quick customize</h3>
    <div class="row"><input id="site" placeholder="theirbusiness.com" value="${esc(p.website || "")}" style="flex:1"><button class="btn" id="imp">Import brand</button></div>
    <div class="grid2"><label>Main color<input type="color" id="c1" value="${esc(p.color || "#1f6feb")}"></label><label>Accent<input type="color" id="c2" value="${esc(p.color2 || p.color || "#1f6feb")}"></label></div>
    <label>Funnel domain<input id="dom" value="${esc(c.funnel_domain)}" placeholder="go.theirbusiness.com"></label>
    <label>Contact limit<input id="lim" type="number" value="${c.contact_limit}" min="1"></label>
    <div class="grid2"><label>Subscription<select id="plan">${[["active", "Active"], ["past_due", "Past due (still works, shows warning)"], ["canceled", "Canceled (locks app, funnels offline)"]]
      .map(([k, l]) => `<option value="${k}" ${c.plan_status === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label class="row" style="align-self:end;color:var(--ink)"><input type="checkbox" id="paid" style="width:auto" ${c.setup_paid ? "checked" : ""}> $497 setup fee paid</label></div>
    <div class="row"><button class="btn primary" id="save">Save</button><button class="btn" id="voice">Rewrite follow-up in their voice</button>
      <button class="btn" id="edge">Find their competitor edge</button></div>
    ${c.edge_report ? `<h3>Competitor edge</h3><table class="tbl">${c.edge_report.themes.slice(0, 5).map(t => `<tr><td>${esc(t.label)}</td><td>${t.share}%</td></tr>`).join("")}</table>
      <small class="muted">${c.distinguishers.filter(x => x.on).length} promise(s) confirmed by the customer</small>` : ""}`);
  $("#actAs", d).onclick = async () => { await api(`/api/admin/customers/${id}/act-as`, {method: "POST"}); location.href = "/"; };
  $("#link", d).onclick = async () => { const r = await api(`/api/admin/customers/${id}/setup-link`, {method: "POST"}); copy(r.setup_link, "Setup link copied"); };
  $("#imp", d).onclick = async () => {
    try { const r = await api(`/api/admin/customers/${id}/import`, {method: "POST", body: {url: $("#site", d).value}}); toast(`Imported ${r.colors.length} colors${r.logo ? " + logo" : ""}`); openCustomer(id); }
    catch (e) { toast(e.message); }
  };
  $("#save", d).onclick = async () => {
    try {
      await api(`/api/admin/customers/${id}`, {method: "PATCH", body: {contact_limit: +$("#lim", d).value, domain: $("#dom", d).value,
        plan_status: $("#plan", d).value, setup_paid: $("#paid", d).checked,
        profile: {color: $("#c1", d).value, color2: $("#c2", d).value}}});
      toast("Saved"); render();
    } catch (e) { toast(e.message); }
  };
  $("#edge", d).onclick = async () => {
    const b = $("#edge", d); b.disabled = true; b.textContent = "Reading reviews…";
    try { const r = await api(`/api/admin/customers/${id}/edge`, {method: "POST", body: {}}); toast(`${r.report.reviews_analyzed} bad reviews analyzed`); openCustomer(id); }
    catch (e) { toast(e.message); b.disabled = false; b.textContent = "Find their competitor edge"; }
  };
  $("#voice", d).onclick = async () => {
    const b = $("#voice", d); b.disabled = true; b.textContent = "Writing…";
    try { const r = await api(`/api/admin/customers/${id}/voice`, {method: "POST"}); toast(r.used_ai ? "Rewritten in their voice" : "Greeting and sign-off applied (AI is off)"); }
    catch (e) { toast(e.message); }
    b.disabled = false; b.textContent = "Rewrite follow-up in their voice";
  };
}
render();
