// StandOut owner app: three panels (Content, Funnels, Leads) + Setup.
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const view = $("#view");
let ME = null, TAB = "content";

const PILLAR_COLOR = {"Teach": "var(--teach)", "Behind the Scenes": "var(--bts)", "Proof": "var(--proof)",
  "Mistake / Myth": "var(--myth)", "Offer": "var(--offer)", "Local": "var(--local)", "Story": "var(--story)"};
const FUNNEL_NAME = {estimate: "Free Estimate", special: "Seasonal Special", emergency: "Same-Day Help", referral: "Review & Refer",
  manual: "Added by you", import: "Imported", "referral-friend": "Referred friend"};
const STAGES = [["new", "New"], ["contacted", "Contacted"], ["quoted", "Quoted"], ["won", "Won"], ["lost", "Lost"]];

async function api(path, opts = {}) {
  const init = {method: opts.method || "GET", headers: {}};
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = typeof opts.body === "string" ? "text/plain" : "application/json";
    init.body = typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body);
  }
  const r = await fetch(path, init);
  if (r.status === 401) { location.href = "/login"; throw new Error("logged out"); }
  const data = await r.json().catch(() => ({}));
  if (r.status === 402) { showLocked(data.detail); throw new Error(data.detail); }
  if (!r.ok) throw new Error(data.detail || "Something went wrong");
  return data;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => t.hidden = true, 2600);
}
async function copy(text, what = "Copied") {
  try { await navigator.clipboard.writeText(text); toast(what + " ✓"); } catch { toast("Couldn't copy. Select and copy by hand."); }
}
function openDrawer(html) { $("#drawerBody").innerHTML = html; $("#drawer").hidden = false; return $("#drawerBody"); }
function closeDrawer() { $("#drawer").hidden = true; }
$("#drawer").addEventListener("click", e => { if (e.target.id === "drawer") closeDrawer(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });
const isoLocal = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const fmtDate = iso => new Date(iso + "T12:00").toLocaleDateString(undefined, {weekday: "short", month: "short", day: "numeric"});
const ago = ts => {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago"; return Math.floor(s / 86400) + "d ago";
};
const delayText = s => s === 0 ? "Right away" : s < 3600 ? `After ${Math.round(s / 60)} min` : s < 86400 ? `After ${Math.round(s / 3600)} hr` : `After ${Math.round(s / 86400)} day${s >= 172800 ? "s" : ""}`;

// ------------------------------------------------------------------ shell
function showLocked(msg) {
  document.querySelector(".top nav").style.visibility = "hidden";
  view.innerHTML = `<div class="card empty" style="max-width:560px;margin:40px auto"><h2>Your account is paused</h2>
    <p class="muted">${esc(msg || "")}</p><p class="muted">Your contacts and settings are saved. Reach out and we'll turn everything back on.</p></div>`;
}
async function loadMe() {
  ME = await api("/api/me");
  const cl = $("#communityLink");
  if (ME.community_url && ME.plan_status !== "canceled" && !cl) {
    $(".top-right").insertAdjacentHTML("afterbegin", `<a class="btn small" id="communityLink" target="_blank" rel="noopener" href="${esc(ME.community_url)}">💬 Community &amp; support</a>`);
  }
  const b = $("#banner");
  const pct = ME.contacts_used / ME.contact_limit;
  if (ME.acting_as) {
    b.innerHTML = `You're working inside <b>${esc(ME.profile.business || ME.email)}</b>'s account as an admin. <a href="#" id="backAdmin">← Back to admin</a>`;
    b.hidden = false;
    $("#backAdmin").onclick = async e => { e.preventDefault(); await api("/api/admin/return", {method: "POST"}); location.href = "/admin"; };
  } else if (ME.plan_status === "canceled") {
    b.hidden = true; showLocked("Your subscription is canceled."); throw new Error("canceled");
  } else if (ME.plan_status === "past_due") {
    b.textContent = "Your last payment didn't go through. Update your payment so your funnels and follow-up keep running."; b.hidden = false;
  } else if (ME.missing.length) {
    b.innerHTML = `Finish your setup so your funnels and scripts use your real business info. <a href="#" data-go="setup">Finish setup →</a>`;
    b.hidden = false;
  } else if (pct >= .9) {
    b.textContent = `You're using ${ME.contacts_used} of ${ME.contact_limit} contacts.`;
    b.hidden = false;
  } else b.hidden = true;
}
document.addEventListener("click", e => {
  const go = e.target.closest("[data-go]");
  if (go) { e.preventDefault(); show(go.dataset.go); }
});
document.querySelectorAll("[data-tab]").forEach(b => b.onclick = () => show(b.dataset.tab));
$("#logout").onclick = async () => { await api("/api/logout", {method: "POST"}); location.href = "/login"; };

async function show(tab) {
  TAB = tab;
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  try { localStorage.setItem("tab", tab); } catch {}
  view.innerHTML = `<p class="muted">Loading…</p>`;
  try { await ({content: renderContent, funnels: renderFunnels, leads: renderLeads, setup: renderSetup})[tab](); }
  catch (e) { view.innerHTML = `<div class="card err">${esc(e.message)}</div>`; }
  refreshBadge();
}
async function refreshBadge() {
  const s = await api("/api/summary").catch(() => null);
  if (!s) return;
  const b = $("#newBadge"); b.textContent = s.new_leads; b.hidden = !s.new_leads;
}

// ------------------------------------------------------------------ CONTENT
async function renderContent() {
  const [{posts, rhythm, arc}, s] = await Promise.all([api("/api/posts"), api("/api/summary")]);
  const today = isoLocal(new Date());
  const legend = rhythm.map((r, i) => `<span class="chip" title="${esc(r.why)}"><i style="background:${PILLAR_COLOR[r.pillar]}"></i>${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][i]}: ${esc(r.pillar)}</span>`).join("");
  const aiOpt = ME.ai ? `<label class="row" style="font-weight:600"><input type="checkbox" id="useAi" style="width:auto" checked> Write with AI</label>` : "";
  const tp = s.today_post;
  let html = `
  <div class="head"><div class="grow"><h1>Content</h1>
    <p class="muted">What to say, and when to say it. Every post has a script you can film in 5 minutes.</p></div>
    ${aiOpt}<button class="btn primary" id="gen">✨ ${posts.length ? "Make a new 30-day plan" : "Make my 30-day plan"}</button></div>
  ${tp ? `<div class="card" style="margin-bottom:16px;border-left:4px solid ${PILLAR_COLOR[tp.pillar]}">
      <div class="row"><div style="flex:1"><small class="muted">TODAY'S POST · ${esc(tp.pillar)} · ${esc(tp.format)}</small>
      <h2 style="margin-top:4px">${esc(tp.title)}</h2><p style="margin:6px 0 0">“${esc(tp.hook)}”</p></div>
      <button class="btn primary" data-post="${tp.id}">Open script</button></div></div>` : ""}
  <div class="legend">${legend}</div>`;
  if (!posts.length) {
    html += `<div class="card empty"><h2>No plan yet</h2><p class="muted">One click builds 30 days of posts: a hook, a script, shooting notes and a caption for each day,<br>following the weekly rhythm above and pointing each post to one of your funnels.</p></div>`;
  } else {
    // group into Monday-start weeks
    const weeks = {};
    posts.forEach(p => {
      const d = new Date(p.day + "T12:00"); const mon = new Date(d); mon.setDate(d.getDate() - ((d.getDay() + 6) % 7));
      (weeks[isoLocal(mon)] ||= []).push(p);
    });
    const first = Object.keys(weeks).sort()[0];
    Object.keys(weeks).sort().forEach(wk => {
      const n = Math.round((new Date(wk + "T12:00") - new Date(first + "T12:00")) / 6048e5);
      const a = arc[Math.min(n, arc.length - 1)];
      const cells = Array.from({length: 7}, (_, i) => {
        const d = new Date(wk + "T12:00"); d.setDate(d.getDate() + i); const iso = isoLocal(d);
        const p = weeks[wk].find(x => x.day === iso);
        if (!p) return `<div class="day" style="opacity:.35;cursor:default"><div class="d">${fmtDate(iso)}</div></div>`;
        return `<div class="day ${iso === today ? "today" : ""}" data-post="${p.id}" style="--c:${PILLAR_COLOR[p.pillar]}">
          <div class="d">${fmtDate(iso)}<span class="status ${p.status}">${p.status}</span></div>
          <div class="p">${esc(p.pillar)}</div><div class="t">${esc(p.title)}</div>
          <small class="muted">${esc(p.format)} · ${esc(p.time)}</small></div>`;
      }).join("");
      html += `<section class="week"><h3>Week ${n + 1}: ${esc(a.theme)}<small>${esc(a.goal)}</small></h3><div class="days">${cells}</div></section>`;
    });
  }
  view.innerHTML = html;
  view.querySelectorAll("[data-post]").forEach(el => el.onclick = () => openPost(+el.dataset.post, posts.concat(tp ? [tp] : [])));
  $("#gen").onclick = async () => {
    if (posts.length && !confirm("Replace the planned posts (not the ones already posted) with a fresh 30-day plan?")) return;
    const btn = $("#gen"); btn.disabled = true;
    const ai = $("#useAi")?.checked;
    btn.textContent = ai ? "AI is writing your month… (about a minute)" : "Building…";
    try { const r = await api("/api/content/generate", {method: "POST", body: {ai, days: 30}}); toast(`${r.created} posts ready`); renderContent(); }
    catch (e) { toast(e.message); btn.disabled = false; }
  };
}

function openPost(id, posts) {
  const p = posts.find(x => x.id === id);
  const link = `${location.origin}/f/${ME.slug}/${p.funnel}`;
  const d = openDrawer(`
    <div class="row"><span class="chip"><i style="background:${PILLAR_COLOR[p.pillar]}"></i>${esc(p.pillar)}</span>
      <span class="chip">${esc(p.format)}</span><span class="chip">→ ${esc(FUNNEL_NAME[p.funnel] || p.funnel)}</span>
      <span style="flex:1"></span><button class="icon" onclick="closeDrawer()">✕</button></div>
    <h2>${esc(p.title)}</h2>
    <div class="grid2"><label>Day<input type="date" id="pDay" value="${p.day}"></label><label>Time<input type="time" id="pTime" value="${p.time}"></label></div>
    <label>Hook (first 3 seconds)<textarea id="pHook" rows="2">${esc(p.hook)}</textarea></label>
    <label>Script<textarea id="pScript" rows="14">${esc(p.script)}</textarea></label>
    <div class="row"><button class="btn small" id="cScript">Copy script</button></div>
    <label>Caption<textarea id="pCap" rows="7">${esc(p.caption)}</textarea></label>
    <div class="row"><button class="btn small" id="cCap">Copy caption</button><a class="btn small" href="${esc(link)}" target="_blank">Preview funnel ↗</a></div>
    <div class="row" style="border-top:1px solid var(--line);padding-top:14px">
      <button class="btn primary" id="pSched">${p.status === "scheduled" ? "✓ Scheduled" : "Schedule it"}</button>
      <button class="btn" id="pPosted">Mark as posted</button><button class="btn" id="pSave">Save edits</button>
      <span style="flex:1"></span><button class="btn small danger" id="pDel">Delete</button></div>
    <p class="muted" style="font-size:13px">${ME.profile.fb_connected ? "Scheduled posts go to your Facebook Page automatically at the time above."
      : "When it's time, we email you the caption and script so you can post in one tap. Connect your Facebook Page in Setup to post automatically."}</p>`);
  const vals = () => ({day: $("#pDay", d).value, time: $("#pTime", d).value, hook: $("#pHook", d).value, script: $("#pScript", d).value, caption: $("#pCap", d).value});
  const save = async extra => { await api(`/api/posts/${id}`, {method: "PATCH", body: {...vals(), ...extra}}); closeDrawer(); renderContent(); };
  $("#cScript", d).onclick = () => copy($("#pScript", d).value, "Script copied");
  $("#cCap", d).onclick = () => copy($("#pCap", d).value, "Caption copied");
  $("#pSched", d).onclick = () => save({status: "scheduled"}).then(() => toast("Scheduled"));
  $("#pPosted", d).onclick = () => save({status: "posted"}).then(() => toast("Nice work. Marked as posted"));
  $("#pSave", d).onclick = () => save({}).then(() => toast("Saved"));
  $("#pDel", d).onclick = async () => { if (confirm("Delete this post?")) { await api(`/api/posts/${id}`, {method: "DELETE"}); closeDrawer(); renderContent(); } };
}

// ------------------------------------------------------------------ FUNNELS
let FUNNELS = [];
async function renderFunnels() {
  const [list, tr] = await Promise.all([api("/api/funnels"), api("/api/tracking")]);
  FUNNELS = list;
  const t = FUNNELS.reduce((a, f) => ({v7: a.v7 + f.stats.views_7, l7: a.l7 + f.stats.leads_7, v30: a.v30 + f.stats.views_30, l30: a.l30 + f.stats.leads_30}), {v7: 0, l7: 0, v30: 0, l30: 0});
  const lastErr = tr.log.find(x => x.status === "error");
  const health = !tr.pixel_id ? `<span class="chip" style="color:var(--warn)">● Facebook tracking not connected</span> <a href="#" data-go="setup">Connect →</a>`
    : !tr.connected ? `<span class="chip" style="color:var(--warn)">● Pixel on, Conversions API token missing</span> <a href="#" data-go="setup">Finish →</a>`
    : lastErr && (!tr.log[0] || tr.log[0].status === "error") ? `<span class="chip" style="color:var(--bad)">● Meta rejected the last event: ${esc(lastErr.detail)}</span>`
    : `<span class="chip" style="color:var(--ok)">● Pixel + Conversions API live</span>`;
  view.innerHTML = `
  <div class="head"><div class="grow"><h1>Funnels</h1>
    <p class="muted">Four pages that turn clicks into leads, built from your brand and business info. Turn them on, put the link in your ads.</p></div>
    <div>${health}</div></div>
  <div class="stats">
    <div class="stat"><b>${t.v7}</b><span>Visits, last 7 days</span></div>
    <div class="stat"><b>${t.l7}</b><span>Leads, last 7 days</span></div>
    <div class="stat"><b>${t.v30}</b><span>Visits, last 30 days</span></div>
    <div class="stat"><b>${t.l30}</b><span>Leads, last 30 days</span></div>
    <div class="stat"><b>${t.v30 ? (100 * t.l30 / t.v30).toFixed(1) : 0}%</b><span>Visit → lead, 30 days</span></div>
  </div>
  <div class="funnels">${FUNNELS.map(f => `
    <div class="card funnel">
      <div class="row"><h3 style="flex:1">${esc(f.name)}</h3>
        <label class="switch" title="Turn page on/off"><input type="checkbox" data-toggle="${f.key}" ${f.enabled ? "checked" : ""}><span></span></label></div>
      <p class="muted" style="margin:6px 0 0">${esc(f.purpose)}</p>
      <div class="nums"><div><b>${f.stats.views_7}</b><span>Visits (7d)</span></div><div><b>${f.stats.leads_7}</b><span>Leads (7d)</span></div>
        <div><b>${f.stats.leads_30}</b><span>Leads (30d)</span></div><div><b>${f.stats.rate_30}%</b><span>Convert (30d)</span></div></div>
      <div class="link"><input readonly value="${esc(f.link)}"><button class="btn small" data-copy="${esc(f.link)}">Copy</button></div>
      <div class="row" style="margin-top:10px"><a class="btn small" target="_blank" href="${esc(f.preview)}">View page ↗</a>
        <button class="btn small primary" data-open="${f.key}">Edit · Follow-up · Ad</button></div>
    </div>`).join("")}</div>`;
  view.querySelectorAll("[data-copy]").forEach(b => b.onclick = () => copy(b.dataset.copy, "Link copied"));
  view.querySelectorAll("[data-toggle]").forEach(c => c.onchange = async () => {
    await api(`/api/funnels/${c.dataset.toggle}`, {method: "PUT", body: {enabled: c.checked}}); toast(c.checked ? "Page is live" : "Page turned off");
  });
  view.querySelectorAll("[data-open]").forEach(b => b.onclick = () => openFunnel(b.dataset.open, "followup"));
}

function openFunnel(key, sub) {
  const f = FUNNELS.find(x => x.key === key);
  const d = openDrawer(`
    <div class="row"><h2 style="flex:1">${esc(f.name)}</h2><button class="icon" onclick="closeDrawer()">✕</button></div>
    <div class="tabs2"><button data-sub="followup">Follow-up</button><button data-sub="page">Page words</button><button data-sub="ad">Facebook ad</button></div>
    <div id="fsub"></div>`);
  const subs = {
    followup() {
      const steps = f.sequence;
      return `<p class="muted">Every lead from this page gets these messages automatically. Follow-up stops on its own when you move the lead out of <b>New</b> or they text back.</p>
        <p class="muted" style="font-size:13px">Use {first}, {business}, {owner}, {phone}, {offer} and the message fills itself in.</p>
        <div id="steps">${steps.map((s, i) => `
          <div class="step ${s.on === false ? "off" : ""}" data-i="${i}">
            <div class="row"><b style="flex:1">${s.channel === "sms" ? "💬 Text" : "✉️ Email"} · ${delayText(s.delay)}</b>
              <label class="row" style="font-weight:600"><input type="checkbox" class="on" style="width:auto" ${s.on === false ? "" : "checked"}> On</label></div>
            ${s.channel === "email" ? `<input class="subject" value="${esc(s.subject)}" placeholder="Subject">` : ""}
            <textarea class="body" rows="${s.channel === "sms" ? 3 : 5}">${esc(s.body)}</textarea>
          </div>`).join("")}</div>
        <div class="row"><button class="btn primary" id="saveSeq">Save follow-up</button><button class="btn small" id="resetSeq">Reset to recommended</button></div>`;
    },
    page() {
      const c = f.copy;
      return `<p class="muted">Already written for you. Change words only if you want to.</p>
        <label>Headline<input id="cH" value="${esc(c.headline)}"></label>
        <label>Under the headline<input id="cS" value="${esc(c.sub)}"></label>
        <label>Bullet points (one per line)<textarea id="cB" rows="4">${esc(c.bullets.join("\n"))}</textarea></label>
        <label>Button<input id="cBtn" value="${esc(c.button)}"></label>
        <label>Thank-you message<input id="cT" value="${esc(c.thanks)}"></label>
        <div class="row"><button class="btn primary" id="saveCopy">Save page</button><a class="btn" target="_blank" href="${esc(f.preview)}">Preview ↗</a></div>`;
    },
    ad() {
      const a = f.ad;
      return `<p class="muted">Ad copy that matches this page, so the ad and the page say the same thing. Paste it into Ads Manager and use the link below as the website URL.</p>
        <label>Primary text</label><div class="adbox">${esc(a.primary)}</div><button class="btn small" id="cP">Copy primary text</button>
        <label>Headline</label><div class="adbox">${esc(a.headline)}</div><button class="btn small" id="cHd">Copy headline</button>
        <label>Website URL</label><div class="adbox">${esc(a.link)}</div><button class="btn small" id="cL">Copy link</button>
        <div class="card" style="box-shadow:none"><b>Angle:</b> ${esc(a.angle)}</div>
        ${f.gap_ads.length ? `<h3 style="margin-top:8px">Competitor-gap ads</h3><p class="muted">Built from what local customers complain about most, answered with your promises. No competitor is ever named.</p>
          ${f.gap_ads.map((g, i) => `<div class="card" style="box-shadow:none;display:grid;gap:6px"><b>${esc(g.label)}</b><small class="muted">${esc(g.angle)}</small>
            <div class="adbox">${esc(g.primary)}</div><div class="row"><button class="btn small" data-gp="${i}">Copy primary text</button><button class="btn small" data-gh="${i}">Copy headline</button></div></div>`).join("")}`
          : `<p class="muted">Tip: confirm your promises in Setup → Your edge to get ads built from your competitors' 1-2 star reviews.</p>`}`;
    },
  };
  const wire = s => {
    d.querySelectorAll("[data-sub]").forEach(b => b.classList.toggle("on", b.dataset.sub === s));
    $("#fsub", d).innerHTML = subs[s]();
    if (s === "followup") {
      d.querySelectorAll(".step .on").forEach(c => c.onchange = () => c.closest(".step").classList.toggle("off", !c.checked));
      $("#saveSeq", d).onclick = async () => {
        const seq = [...d.querySelectorAll(".step")].map(el => {
          const o = f.sequence[+el.dataset.i];
          return {...o, body: $(".body", el).value, subject: $(".subject", el)?.value || "", on: $(".on", el).checked};
        });
        try { await api(`/api/funnels/${key}`, {method: "PUT", body: {sequence: seq}}); f.sequence = seq; toast("Follow-up saved"); } catch (e) { toast(e.message); }
      };
      $("#resetSeq", d).onclick = async () => {
        if (!confirm("Reset this page's words and follow-up to the recommended version?")) return;
        await api(`/api/funnels/${key}/reset`, {method: "POST"}); FUNNELS = await api("/api/funnels"); closeDrawer(); openFunnel(key, "followup"); toast("Reset");
      };
    }
    if (s === "page") $("#saveCopy", d).onclick = async () => {
      const ov = {headline: $("#cH", d).value, sub: $("#cS", d).value, button: $("#cBtn", d).value, thanks: $("#cT", d).value,
        bullets: $("#cB", d).value.split("\n").filter(x => x.trim())};
      await api(`/api/funnels/${key}`, {method: "PUT", body: {overrides: ov}}); Object.assign(f.copy, ov); toast("Page saved");
    };
    if (s === "ad") {
      $("#cP", d).onclick = () => copy(f.ad.primary); $("#cHd", d).onclick = () => copy(f.ad.headline); $("#cL", d).onclick = () => copy(f.ad.link);
      d.querySelectorAll("[data-gp]").forEach(b => b.onclick = () => copy(f.gap_ads[+b.dataset.gp].primary));
      d.querySelectorAll("[data-gh]").forEach(b => b.onclick = () => copy(f.gap_ads[+b.dataset.gh].headline));
    }
  };
  d.querySelectorAll("[data-sub]").forEach(b => b.onclick = () => wire(b.dataset.sub));
  wire(sub);
}

// ------------------------------------------------------------------ LEADS
async function renderLeads() {
  const [contacts, s] = await Promise.all([api("/api/contacts"), api("/api/summary")]);
  await loadMe();
  const pct = Math.min(100, 100 * ME.contacts_used / ME.contact_limit);
  view.innerHTML = `
  <div class="head"><div class="grow"><h1>Leads</h1><p class="muted">Every lead from every funnel lands here and gets followed up automatically.</p></div>
    <div><small class="muted">${ME.contacts_used} / ${ME.contact_limit} contacts</small><div class="meter"><i style="width:${pct}%"></i></div></div>
    <button class="btn primary" id="addLead">+ Add lead</button>
    <button class="btn" id="imp">Import CSV</button><a class="btn" href="/api/contacts.csv">Export</a>
    <input type="file" id="impFile" accept=".csv" hidden></div>
  <div class="stats">
    <div class="stat"><b>${s.new_leads}</b><span>New leads waiting</span></div>
    <div class="stat"><b>${s.leads_7d}</b><span>Leads this week</span></div>
    <div class="stat"><b>${s.following_up}</b><span>Being followed up automatically</span></div>
    <div class="stat"><b>${s.messages_7d}</b><span>Texts/emails sent for you (7 days)</span></div>
    <div class="stat"><b>$${Math.round(s.won_value).toLocaleString()}</b><span>Won jobs</span></div>
  </div>
  <input id="search" placeholder="Search name, phone or email" style="max-width:360px;margin-bottom:12px">
  <div class="board">${STAGES.map(([k, label]) => `<div class="col" data-stage="${k}"><h3>${label}<span>${contacts.filter(c => c.stage === k).length}</span></h3>
    ${contacts.filter(c => c.stage === k).map(leadCard).join("") || `<p class="muted" style="font-size:13px;margin:4px">Nothing here yet</p>`}</div>`).join("")}</div>`;
  view.querySelectorAll(".lead").forEach(el => {
    el.onclick = () => openLead(+el.dataset.id);
    el.ondragstart = e => e.dataTransfer.setData("text/plain", el.dataset.id);
  });
  view.querySelectorAll(".col").forEach(col => {
    col.ondragover = e => { e.preventDefault(); col.classList.add("drop"); };
    col.ondragleave = () => col.classList.remove("drop");
    col.ondrop = async e => {
      e.preventDefault(); col.classList.remove("drop");
      await api(`/api/contacts/${e.dataTransfer.getData("text/plain")}`, {method: "PATCH", body: {stage: col.dataset.stage}}); renderLeads();
    };
  });
  $("#search").oninput = e => {
    const q = e.target.value.toLowerCase();
    view.querySelectorAll(".lead").forEach(el => el.hidden = q && !el.textContent.toLowerCase().includes(q));
  };
  $("#addLead").onclick = addLead;
  $("#imp").onclick = () => $("#impFile").click();
  $("#impFile").onchange = async e => {
    const file = e.target.files[0]; if (!file) return;
    const r = await api("/api/contacts/import", {method: "POST", body: await file.text()});
    toast(`Imported ${r.added}${r.skipped ? `, skipped ${r.skipped}` : ""}`); renderLeads();
  };
}
function leadCard(c) {
  return `<div class="lead" draggable="true" data-id="${c.id}"><b>${esc(c.name)}</b>
    <small>${esc(c.phone || c.email)}</small><small>${esc(FUNNEL_NAME[c.source] || c.source)} · ${ago(c.created)}</small>
    ${c.seq_status === "active" ? `<small class="auto">● Auto follow-up on</small>` : ""}
    ${c.value ? `<small>$${Math.round(c.value).toLocaleString()}</small>` : ""}</div>`;
}
function addLead() {
  const d = openDrawer(`
    <div class="row"><h2 style="flex:1">Add a lead</h2><button class="icon" onclick="closeDrawer()">✕</button></div>
    <label>Name<input id="nName"></label><div class="grid2"><label>Phone<input id="nPhone" type="tel"></label><label>Email<input id="nEmail" type="email"></label></div>
    <label>What do they need?<textarea id="nDet" rows="3"></textarea></label>
    <label>Start automatic follow-up<select id="nFun"><option value="">No, I'll handle it</option>
      ${Object.entries(FUNNEL_NAME).slice(0, 4).map(([k, v]) => `<option value="${k}">Yes, use the ${v} follow-up</option>`).join("")}</select></label>
    <button class="btn primary" id="nSave">Add lead</button>`);
  $("#nSave", d).onclick = async () => {
    try {
      await api("/api/contacts", {method: "POST", body: {name: $("#nName", d).value, phone: $("#nPhone", d).value, email: $("#nEmail", d).value,
        details: $("#nDet", d).value, funnel: $("#nFun", d).value}});
      closeDrawer(); toast("Lead added"); renderLeads();
    } catch (e) { toast(e.message); }
  };
}
async function openLead(id) {
  const c = await api(`/api/contacts/${id}`);
  const auto = c.seq_status === "active" ? `<span class="chip" style="color:var(--ok)">● Auto follow-up on</span> <button class="btn small" id="lStop">Stop follow-up</button>`
    : c.seq_status.startsWith("stopped") ? `<span class="chip">Follow-up ${esc(c.seq_status)}</span>` : c.seq_status === "finished" ? `<span class="chip">Follow-up finished</span>` : "";
  const d = openDrawer(`
    <div class="row"><h2 style="flex:1">${esc(c.name)}</h2><button class="icon" onclick="closeDrawer()">✕</button></div>
    <div class="row">${c.phone ? `<a class="btn small" href="tel:${esc(c.phone)}">📞 ${esc(c.phone)}</a>` : ""}${c.email ? `<a class="btn small" href="mailto:${esc(c.email)}">✉️ ${esc(c.email)}</a>` : ""}</div>
    <div class="row">${auto}</div>
    <div class="grid2"><label>Stage<select id="lStage">${STAGES.map(([k, l]) => `<option value="${k}" ${k === c.stage ? "selected" : ""}>${l}</option>`).join("")}</select></label>
      <label>Job value ($)<input id="lVal" type="number" min="0" value="${c.value || ""}"></label></div>
    <label>From: ${esc(FUNNEL_NAME[c.source] || c.source)}<pre>${esc(c.details || "No details")}</pre></label>
    <label>Notes<textarea id="lNotes" rows="3">${esc(c.notes)}</textarea></label>
    <div class="row"><button class="btn primary" id="lSave">Save</button><span style="flex:1"></span><button class="btn small danger" id="lDel">Delete</button></div>
    <h3>Conversation</h3>
    <div class="msgs">${c.messages.map(m => `<div class="msg ${m.direction}">${esc(m.body)}<small>${m.channel === "sms" ? "Text" : "Email"} · ${ago(m.created)} · ${esc(m.status)}</small></div>`).join("") || `<p class="muted">No messages yet.</p>`}</div>
    <div class="card" style="box-shadow:none;display:grid;gap:8px">
      <div class="row"><select id="mCh" style="width:auto"><option value="sms">💬 Text</option><option value="email">✉️ Email</option></select></div>
      <textarea id="mBody" rows="3" placeholder="Write a message…"></textarea><button class="btn" id="mSend">Send</button></div>`);
  $("#lSave", d).onclick = async () => {
    await api(`/api/contacts/${id}`, {method: "PATCH", body: {stage: $("#lStage", d).value, value: +$("#lVal", d).value || 0, notes: $("#lNotes", d).value}});
    closeDrawer(); toast("Saved"); renderLeads();
  };
  $("#lDel", d).onclick = async () => { if (confirm(`Delete ${c.name}?`)) { await api(`/api/contacts/${id}`, {method: "DELETE"}); closeDrawer(); renderLeads(); } };
  $("#lStop", d) && ($("#lStop", d).onclick = async () => { await api(`/api/contacts/${id}`, {method: "PATCH", body: {followup: "stop"}}); openLead(id); });
  $("#mSend", d).onclick = async () => {
    try { const r = await api(`/api/contacts/${id}/message`, {method: "POST", body: {channel: $("#mCh", d).value, body: $("#mBody", d).value}}); toast(`Message ${r.status}`); openLead(id); }
    catch (e) { toast(e.message); }
  };
}

// ------------------------------------------------------------------ SETUP + ONBOARDING
// Each section renders its fields and wires its own buttons. Onboarding walks them as steps;
// Setup shows them all on one page for small adjustments later.
const TONES = [["friendly", "Friendly neighbor", "Warm, casual, first names, a little personality."],
  ["straight", "Straight shooter", "Short and direct. No fluff."],
  ["professional", "Polished professional", "Courteous, clear and reassuring."]];
const fieldInput = (k, type = "text", hint = "") => {
  const p = ME.profile, req = ME.missing.includes(k) ? " *" : "";
  return `<label>${esc(ME.fields[k] || k)}${req}<input name="${k}" type="${type}" value="${esc(p[k] || "")}" ${hint ? `placeholder="${esc(hint)}"` : ""}></label>`;
};
const fieldArea = (k, rows = 2, hint = "") => `<label>${esc(ME.fields[k] || k)}${ME.missing.includes(k) ? " *" : ""}<textarea name="${k}" rows="${rows}" ${hint ? `placeholder="${esc(hint)}"` : ""}>${esc(ME.profile[k] || "")}</textarea></label>`;
async function saveSection(el) {
  const body = {};
  el.querySelectorAll("[name]").forEach(i => { if (i.type === "radio") { if (i.checked) body[i.name] = i.value; } else body[i.name] = i.value; });
  const r = await api("/api/profile", {method: "PUT", body});
  await loadMe();
  return r;
}

const SECTIONS = {
  brand: {
    title: "Your brand", intro: "Paste your website and we'll pull your logo, colors and the way you describe your business. Your funnels will look like your site.",
    html: () => {
      const p = ME.profile;
      return `<div class="row"><input id="siteUrl" placeholder="yourbusiness.com" value="${esc(p.website || "")}" style="flex:1;min-width:220px">
          <button class="btn primary" id="doImport" type="button">Import my brand</button></div>
        <p id="impMsg" class="muted" style="margin:0"></p>
        <div class="brandrow">
          <div class="logobox">${p.logo ? `<img src="${esc(p.logo)}" alt="Logo">` : `<span class="muted">No logo yet</span>`}</div>
          <div style="display:grid;gap:8px"><label>Upload a logo (PNG, JPG, SVG · under 2 MB)<input type="file" id="logoFile" accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml"></label></div>
        </div>
        <div class="grid2"><label>Main color<input name="color" type="color" value="${esc(p.color || "#1f6feb")}"></label>
          <label>Accent color<input name="color2" type="color" value="${esc(p.color2 || p.color || "#1f6feb")}"></label></div>
        ${fieldInput("font", "text", "e.g. Montserrat (any Google Font)")}`;
    },
    wire: el => {
      $("#doImport", el).onclick = async () => {
        const btn = $("#doImport", el); btn.disabled = true; btn.textContent = "Reading your site…";
        try {
          const r = await api("/api/brand/import", {method: "POST", body: {url: $("#siteUrl", el).value}});
          await loadMe();
          $("#impMsg", el).textContent = `Found ${r.colors.length} brand color${r.colors.length === 1 ? "" : "s"}${r.font ? `, the ${r.font} font` : ""}${r.logo ? ", your logo" : ""} and ${r.words} words about your business.`;
          rerender(el, "brand");
        } catch (e) { $("#impMsg", el).textContent = e.message; }
        btn.disabled = false; btn.textContent = "Import my brand";
      };
      $("#logoFile", el).onchange = async e => {
        const fd = new FormData(); fd.append("file", e.target.files[0]);
        const r = await fetch("/api/brand/logo", {method: "POST", body: fd});
        const d = await r.json().catch(() => ({}));
        if (!r.ok) return toast(d.detail || "Upload failed");
        await loadMe(); toast("Logo saved"); rerender(el, "brand");
      };
    },
  },
  business: {
    title: "Your business", intro: "Used in every funnel, ad, script and follow-up message.",
    html: () => `<div class="grid2">${fieldInput("business")}${fieldInput("owner")}${fieldInput("trade", "text", "plumbing, HVAC, roofing…")}${fieldInput("city")}
        ${fieldInput("service_area")}${fieldInput("phone", "tel")}${fieldInput("years")}${fieldInput("review_link", "url")}</div>
      ${fieldArea("services", 2, "water heaters, drain cleaning, leak repair")}${fieldArea("problems", 2, "What do people call you about?")}
      ${fieldArea("ideal_customer")}${fieldInput("offer", "text", "$50 off your first service")}${fieldArea("guarantee")}${fieldArea("difference")}`,
  },
  edge: {
    title: "Your edge",
    intro: "We read your local competitors' 1 and 2 star reviews and find what customers hate most. Then you pick the promises you can keep. They go into your funnels, ads, scripts and follow-up.",
    html: () => `<div id="edgeBox" class="secbody"><p class="muted">Loading…</p></div>`,
    wire: el => drawEdge(el),
  },
  voice: {
    title: "Your voice", intro: "Your follow-up texts and emails should sound like you, not a robot. Tell us how you talk and we'll rewrite every message in your voice.",
    html: () => {
      const p = ME.profile;
      return `<div class="tones">${TONES.map(([k, n, d]) => `<label class="tone"><input type="radio" name="tone" value="${k}" ${p.tone === k ? "checked" : ""}><b>${n}</b><span>${d}</span></label>`).join("")}</div>
        ${fieldInput("voice_words", "text", "e.g. no-brainer, folks, we'll take care of you")}
        ${fieldInput("voice_avoid", "text", "e.g. synergy, cheap, ASAP")}
        ${fieldArea("voice_sample", 4, "Paste a text or email you've actually sent a customer. This is the best clue to how you sound.")}
        ${fieldInput("signoff", "text", "e.g. Talk soon, / Thanks again,")}
        <p class="muted" style="margin:0;font-size:13px">${p.has_site_text ? "✓ We'll also use the words from your website." : "Tip: import your website in the Brand step so we can learn from it too."}</p>
        <div class="row"><button class="btn primary" id="doVoice" type="button">✍️ Write my follow-up in my voice</button><span id="voiceMsg" class="muted"></span></div>
        ${p.voice_guide ? `<div class="card" style="box-shadow:none"><b>Your brand voice</b><pre style="margin-top:6px">${esc(p.voice_guide)}</pre></div>` : ""}`;
    },
    wire: el => {
      $("#doVoice", el).onclick = async () => {
        const btn = $("#doVoice", el); btn.disabled = true; btn.textContent = ME.ai ? "Writing in your voice… (about a minute)" : "Updating…";
        try {
          await saveSection(el);
          const r = await api("/api/voice/generate", {method: "POST"});
          await loadMe();
          toast(r.used_ai ? `Rewrote ${r.steps} messages in your voice` : `Updated ${r.steps} messages`);
          rerender(el, "voice");
          if (!r.used_ai) $("#voiceMsg", el).textContent = "Applied your greeting and sign-off. A full rewrite in your voice turns on when AI is enabled for the platform.";
        } catch (e) { toast(e.message); btn.disabled = false; }
      };
    },
  },
  tracking: {
    title: "Facebook tracking", intro: "Connect your Pixel and the Conversions API so Facebook sees every lead, even with ad blockers and iPhones. Facebook then finds more people like your real customers, and your cost per lead drops.",
    html: () => {
      const p = ME.profile;
      return `<ol class="howto">
          <li>Open <b>Meta Events Manager</b>, then pick your dataset (Pixel). Copy its <b>ID</b>.</li>
          <li>Go to <b>Settings → Conversions API → Generate access token</b>. Copy the token.</li>
          <li>Paste both below and press <b>Send test event</b>.</li></ol>
        <div class="grid2">${fieldInput("pixel_id", "text", "15-16 digits")}
          <label>Conversions API access token<input name="capi_token" type="password" placeholder="${p.capi_connected ? "Saved (leave blank to keep)" : "Paste token"}"></label>
          ${fieldInput("lead_value", "text", "e.g. 40")}${fieldInput("capi_test_code", "text", "TEST12345 (remove when live)")}</div>
        ${fieldInput("fb_verify", "text", "From Business Settings → Brand Safety → Domains")}
        <div class="row"><button class="btn primary" id="doTest" type="button">Save and send test event</button><span id="testMsg" class="muted"></span></div>
        <details><summary style="cursor:pointer;font-weight:600">What gets sent to Facebook</summary><div id="evMap" class="muted">Loading…</div></details>`;
    },
    wire: async el => {
      $("#doTest", el).onclick = async () => {
        try { await saveSection(el); const r = await api("/api/tracking/test", {method: "POST"}); $("#testMsg", el).textContent = "✓ " + r.detail; }
        catch (e) { $("#testMsg", el).textContent = e.message; }
      };
      const tr = await api("/api/tracking");
      $("#evMap", el).innerHTML = `<table class="tbl">${tr.events.map(x => `<tr><td>${esc(x.when)}</td><td><b>${esc(x.sends)}</b></td></tr>`).join("")}</table>
        <p>Every event goes from the browser (Pixel) and from our server (Conversions API) with the same ID, so Facebook counts it once. Customer details are hashed before they leave our server.</p>
        ${tr.log.length ? `<b>Recent sends</b><table class="tbl">${tr.log.slice(0, 8).map(x => `<tr><td>${esc(x.event)}</td><td style="color:${x.status === "ok" ? "var(--ok)" : "var(--bad)"}">${esc(x.status)}</td><td>${esc(x.detail)}</td><td>${ago(x.created)}</td></tr>`).join("")}</table>` : ""}`;
    },
  },
  domain: {
    title: "Your domain", intro: "Run your funnels on your own web address, like go.yourbusiness.com. Optional; your funnels already work without it.",
    html: () => `<div class="row"><input id="domIn" placeholder="go.yourbusiness.com" value="${esc(ME.funnel_domain)}" style="flex:1;min-width:220px">
        <button class="btn" id="doDom" type="button">Save domain</button><button class="btn" id="chkDom" type="button">Check</button></div>
      <p id="domMsg" class="muted" style="margin:0"></p>
      <div class="card" style="box-shadow:none"><b>One DNS record, wherever you bought your domain:</b>
        <table class="tbl"><tr><td>Type</td><td><b>CNAME</b></td></tr><tr><td>Name / Host</td><td><b>${esc((ME.funnel_domain || "go.yourbusiness.com").split(".")[0])}</b></td></tr>
        <tr><td>Points to</td><td><b>${esc(ME.cname_target)}</b></td></tr></table></div>`,
    wire: el => {
      $("#doDom", el).onclick = async () => {
        try { const r = await api("/api/domain", {method: "PUT", body: {domain: $("#domIn", el).value}}); await loadMe(); toast(r.domain ? "Domain saved" : "Domain removed"); rerender(el, "domain"); }
        catch (e) { $("#domMsg", el).textContent = e.message; }
      };
      $("#chkDom", el).onclick = async () => { const r = await api("/api/domain/check"); $("#domMsg", el).textContent = (r.ok ? "✓ " : "") + r.detail; };
    },
  },
  alerts: {
    title: "Alerts and posting", intro: "Where we tell you about new leads, and how scheduled posts go out.",
    html: () => {
      const p = ME.profile;
      return `<div class="grid2">${fieldInput("owner_mobile", "tel")}<label>${esc(ME.fields.timezone)}<select name="timezone">${["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu"]
        .map(z => `<option ${z === p.timezone ? "selected" : ""}>${z}</option>`).join("")}</select></label></div>
        <details><summary style="cursor:pointer;font-weight:600">Auto-post to your Facebook Page (optional) ${p.fb_connected ? "✓ connected" : ""}</summary>
        <div class="grid2" style="margin-top:10px"><label>Page ID<input name="fb_page_id" value="${esc(p.fb_page_id || "")}"></label>
        <label>Page access token<input name="fb_page_token" type="password" placeholder="${p.fb_connected ? "Saved (leave blank to keep)" : ""}"></label></div></details>
        <small class="muted">Texting: ${ME.sms_live ? "live" : "demo mode (logged, not sent)"} · Email: ${ME.email_live ? "live" : "demo mode (logged, not sent)"} · AI writing: ${ME.ai ? "on" : "off (built-in templates)"}</small>`;
    },
  },
};
// ---- competitor edge
async function drawEdge(el) {
  const box = $("#edgeBox", el);
  const e = await api("/api/edge");
  const r = e.report;
  const runUi = `${e.available ? `<div class="row"><button class="btn primary" id="edgeRun" type="button">🔎 Find what customers hate about my competitors</button>
      <small class="muted">Looks up ${esc(ME.profile.trade || "your trade")} companies near ${esc(ME.profile.city || "you")} on Google.</small></div>` : ""}
    <details ${e.available ? "" : "open"}><summary style="cursor:pointer;font-weight:600">${e.available ? "Or paste" : "Paste"} competitor reviews yourself</summary>
      <textarea id="edgePaste" rows="5" placeholder="Copy 1 and 2 star reviews from your competitors' Google or Yelp pages. Leave a blank line between reviews." style="margin-top:8px"></textarea>
      <button class="btn" id="edgePasteGo" type="button" style="margin-top:8px">Analyze pasted reviews</button></details>
    <p id="edgeMsg" class="muted" style="margin:0"></p>`;
  if (!r) { box.innerHTML = runUi; return wireEdgeRun(el); }
  const max = Math.max(...r.themes.map(t => t.share), 1);
  box.innerHTML = `
    <div class="card" style="box-shadow:none"><b>🔒 Private to you.</b> <span class="muted">We analyzed ${r.reviews_analyzed} bad reviews${r.competitors.length ? ` from ${r.competitors.length} local competitors (${r.competitors.map(c => esc(c.name)).join(", ")})` : ""}.
      Competitor names and reviews never appear in your funnels, ads or posts.</span></div>
    <h3>What local customers complain about</h3>
    <div class="themes">${r.themes.map(t => `<div class="theme"><div class="row"><b style="flex:1">${esc(t.label)}</b><span>${t.share}%</span></div>
      <div class="tbar"><i style="width:${100 * t.share / max}%"></i></div><small class="muted">${esc(t.summary)}</small>
      ${t.examples.length ? `<small class="muted"><i>“${esc(t.examples[0])}”</i></small>` : ""}</div>`).join("")}</div>
    <h3>Your promises: tick only what you really do</h3>
    <div id="dlist">${e.distinguishers.map((d, i) => `<div class="step ${d.on ? "" : "off"}" data-d="${i}">
      <label class="row" style="color:var(--ink)"><input type="checkbox" class="don" style="width:auto" ${d.on ? "checked" : ""}> <b>${esc(d.label)}</b> <span class="muted">(${d.share}% of bad reviews)</span></label>
      <label>Your promise<input class="dpromise" value="${esc(d.promise)}"></label>
      <div class="grid2"><label>How you prove it<input class="dproof" value="${esc(d.proof)}"></label><label>Ad / video hook<input class="dhook" value="${esc(d.hook)}"></label></div></div>`).join("")}</div>
    <div class="row"><button class="btn primary" id="edgeSave" type="button">Save my promises</button></div>
    <details><summary style="cursor:pointer;font-weight:600">Run it again</summary>${runUi}</details>`;
  box.querySelectorAll(".don").forEach(c => c.onchange = () => c.closest(".step").classList.toggle("off", !c.checked));
  $("#edgeSave", box).onclick = async () => { const n = await saveEdge(el); toast(`${n} promise${n === 1 ? "" : "s"} saved. Your funnels and ads now use them`); };
  box._dist = e.distinguishers;
  wireEdgeRun(el);
}
function wireEdgeRun(el) {
  const go = async (body, btn) => {
    btn.disabled = true; const label = btn.textContent; btn.textContent = "Reading reviews… (up to a minute)";
    try { await api("/api/edge/run", {method: "POST", body}); await drawEdge(el); toast("Done. Pick your promises"); }
    catch (err) { const m = $("#edgeMsg", el); if (m) m.textContent = err.message; btn.disabled = false; btn.textContent = label; }
  };
  $("#edgeRun", el) && ($("#edgeRun", el).onclick = e => go({}, e.target));
  $("#edgePasteGo", el) && ($("#edgePasteGo", el).onclick = e => go({pasted: $("#edgePaste", el).value}, e.target));
}
async function saveEdge(el) {
  const box = $("#edgeBox", el);
  if (!box || !box._dist) return 0;
  const ds = [...box.querySelectorAll("[data-d]")].map(s => ({...box._dist[+s.dataset.d], on: $(".don", s).checked,
    promise: $(".dpromise", s).value, proof: $(".dproof", s).value, hook: $(".dhook", s).value}));
  const r = await api("/api/edge", {method: "PUT", body: {distinguishers: ds}});
  await loadMe();
  return r.confirmed;
}

function sectionCard(key, withSave = true) {
  const s = SECTIONS[key];
  return `<section class="card setup" data-sec="${key}"><h2>${s.title}</h2><p class="muted">${s.intro}</p><div class="secbody">${s.html()}</div>
    ${withSave ? `<div class="row"><button class="btn primary" data-save="${key}" type="button">Save</button></div>` : ""}</section>`;
}
function rerender(el, key) {
  const card = el.closest("[data-sec]") || el;
  $(".secbody", card).innerHTML = SECTIONS[key].html();
  SECTIONS[key].wire?.(card);
}

async function renderSetup() {
  await loadMe();
  view.innerHTML = `<div class="head"><div class="grow"><h1>Setup</h1><p class="muted">Change anything here and your funnels, scripts and messages update with it.</p></div>
    <a class="btn" href="#" id="rerun">Run the setup guide again</a></div>
    <div style="display:grid;gap:16px">${Object.keys(SECTIONS).map(k => sectionCard(k, k !== "edge")).join("")}</div>`;
  view.querySelectorAll("[data-sec]").forEach(el => SECTIONS[el.dataset.sec].wire?.(el));
  view.querySelectorAll("[data-save]").forEach(b => b.onclick = b.dataset.save === "edge" ? async () => {
    const n = await saveEdge(b.closest("[data-sec]")); toast(`${n} promise${n === 1 ? "" : "s"} saved`);
  } : async () => {
    try { const r = await saveSection(b.closest("[data-sec]")); toast(r.missing.length ? "Saved. A few required fields are still empty (*)" : "Saved"); }
    catch (e) { toast(e.message); }
  });
  $("#rerun").onclick = e => { e.preventDefault(); runOnboarding(0); };
}

const STEPS = ["brand", "business", "edge", "voice", "tracking", "domain"];
async function runOnboarding(i = 0) {
  await loadMe();
  document.querySelector(".top nav").style.visibility = "hidden";
  const key = STEPS[i];
  view.innerHTML = `<div class="wizard">
    <div class="dots">${STEPS.map((k, j) => `<span class="${j < i ? "done" : j === i ? "on" : ""}">${j + 1}. ${SECTIONS[k].title}</span>`).join("")}</div>
    ${sectionCard(key, false)}
    <div class="row"><button class="btn" id="back" ${i === 0 ? "hidden" : ""}>← Back</button><span style="flex:1"></span>
      ${["edge", "tracking", "domain"].includes(key) ? `<button class="btn" id="skip">Skip for now</button>` : ""}
      <button class="btn primary" id="next">${i === STEPS.length - 1 ? "Finish and build my month →" : "Save and continue →"}</button></div></div>`;
  const el = $("[data-sec]", view);
  SECTIONS[key].wire?.(el);
  const advance = async () => {
    if (i < STEPS.length - 1) return runOnboarding(i + 1);
    $("#next").disabled = true; $("#next").textContent = "Building your first month…";
    await api("/api/onboarding/done", {method: "POST"});
    await api("/api/content/generate", {method: "POST", body: {ai: ME.ai, days: 30}}).catch(() => null);
    document.querySelector(".top nav").style.visibility = "";
    await loadMe(); toast("You're all set"); show("content");
  };
  $("#next").onclick = async () => {
    try {
      if (key === "edge") await saveEdge(el);
      else if (key !== "domain") { const r = await saveSection(el); if (key === "business" && r.missing.length) return toast("Fill in the fields marked *"); }
      advance();
    } catch (e) { toast(e.message); }
  };
  $("#skip") && ($("#skip").onclick = advance);
  $("#back").onclick = () => runOnboarding(i - 1);
}

// ------------------------------------------------------------------ boot
(async () => {
  await loadMe();
  if (!ME.onboarded && !ME.acting_as) return runOnboarding(0);
  let start = "content";
  try { start = localStorage.getItem("tab") || start; } catch {}
  show(start);
})();
