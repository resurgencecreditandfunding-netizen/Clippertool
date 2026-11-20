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
async function loadMe() {
  ME = await api("/api/me");
  const b = $("#banner");
  const pct = ME.contacts_used / ME.contact_limit;
  if (ME.missing.length) {
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
  FUNNELS = await api("/api/funnels");
  view.innerHTML = `
  <div class="head"><div class="grow"><h1>Funnels</h1>
    <p class="muted">Four pages that turn clicks into leads. They're already built from your business info; just turn them on and share the link.</p></div></div>
  <div class="funnels">${FUNNELS.map(f => `
    <div class="card funnel">
      <div class="row"><h3 style="flex:1">${esc(f.name)}</h3>
        <label class="switch" title="Turn page on/off"><input type="checkbox" data-toggle="${f.key}" ${f.enabled ? "checked" : ""}><span></span></label></div>
      <p class="muted" style="margin:6px 0 0">${esc(f.purpose)}</p>
      <div class="nums"><div><b>${f.views}</b><span>Visits</span></div><div><b>${f.leads}</b><span>Leads</span></div><div><b>${f.rate}%</b><span>Convert</span></div></div>
      <div class="link"><input readonly value="${esc(f.link)}"><button class="btn small" data-copy="${esc(f.link)}">Copy</button></div>
      <div class="row" style="margin-top:10px"><a class="btn small" target="_blank" href="${esc(f.link)}?preview=1">View page ↗</a>
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
        <div class="row"><button class="btn primary" id="saveCopy">Save page</button><a class="btn" target="_blank" href="${esc(f.link)}?preview=1">Preview ↗</a></div>`;
    },
    ad() {
      const a = f.ad;
      return `<p class="muted">Ad copy that matches this page, so the ad and the page say the same thing. Paste it into Ads Manager and use the link below as the website URL.</p>
        <label>Primary text</label><div class="adbox">${esc(a.primary)}</div><button class="btn small" id="cP">Copy primary text</button>
        <label>Headline</label><div class="adbox">${esc(a.headline)}</div><button class="btn small" id="cHd">Copy headline</button>
        <label>Website URL</label><div class="adbox">${esc(a.link)}</div><button class="btn small" id="cL">Copy link</button>
        <div class="card" style="box-shadow:none"><b>Angle:</b> ${esc(a.angle)}</div>`;
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

// ------------------------------------------------------------------ SETUP
async function renderSetup() {
  await loadMe();
  const p = ME.profile, f = ME.fields;
  const input = (k, type = "text") => `<label>${esc(f[k] || k)}${ME.missing.includes(k) ? " *" : ""}<input name="${k}" type="${type}" value="${esc(p[k] || (type === "color" ? "#1f6feb" : ""))}"></label>`;
  const area = k => `<label>${esc(f[k])}${ME.missing.includes(k) ? " *" : ""}<textarea name="${k}" rows="2">${esc(p[k] || "")}</textarea></label>`;
  view.innerHTML = `
  <div class="head"><div class="grow"><h1>Setup</h1><p class="muted">Fill this in once. Your funnels, scripts, ads and follow-up messages all use it.</p></div></div>
  <form id="prof" class="card" style="display:grid;gap:12px">
    <h3>Your business</h3>
    <div class="grid2">${input("business")}${input("owner")}${input("trade")}${input("city")}${input("service_area")}${input("phone", "tel")}${input("years")}${input("color", "color")}</div>
    ${area("services")}${area("problems")}${area("ideal_customer")}
    <h3>Your offer and promise</h3>
    ${input("offer")}${area("guarantee")}${area("difference")}${input("review_link", "url")}
    <h3>Alerts and posting</h3>
    <div class="grid2">${input("owner_mobile", "tel")}<label>${esc(f.timezone)}<select name="timezone">${["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu"]
      .map(z => `<option ${z === p.timezone ? "selected" : ""}>${z}</option>`).join("")}</select></label></div>
    <details><summary style="cursor:pointer;font-weight:600">Connect your Facebook Page (optional) ${p.fb_connected ? "✓ connected" : ""}</summary>
      <div class="grid2" style="margin-top:10px"><label>Page ID<input name="fb_page_id" value="${esc(p.fb_page_id || "")}"></label>
      <label>Page access token<input name="fb_page_token" type="password" placeholder="${p.fb_connected ? "Saved (leave blank to keep)" : ""}"></label></div></details>
    <div class="row"><button class="btn primary">Save</button>
      <small class="muted">Texting: ${ME.sms_live ? "live" : "demo mode (logged, not sent)"} · Email: ${ME.email_live ? "live" : "demo mode (logged, not sent)"} · AI scripts: ${ME.ai ? "on" : "templates"}</small></div>
  </form>`;
  $("#prof").onsubmit = async e => {
    e.preventDefault();
    const r = await api("/api/profile", {method: "PUT", body: Object.fromEntries(new FormData(e.target))});
    await loadMe(); toast(r.missing.length ? "Saved. A few fields are still missing (*)" : "Saved. Everything is up to date");
  };
}

// ------------------------------------------------------------------ boot
(async () => {
  await loadMe();
  let start = "content";
  try { start = localStorage.getItem("tab") || start; } catch {}
  show(ME.missing.length > 3 ? "setup" : start);
})();
