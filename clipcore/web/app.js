/* ClipCore OS dashboard — vanilla JS, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => Number(n || 0).toLocaleString();
const money = (n) => "$" + Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const today = () => new Date().toLocaleDateString("en-CA");

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body,
  });
  if (res.status === 401) { location.replace("/login.html"); throw new Error("Signed out"); }
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json();
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (err ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = ""), 2600);
}

const state = { view: "review", clips: [], idx: 0, campaigns: [], stats: {} };

/* ------------------------------------------------------------ chrome */
async function refreshStats() {
  try {
    const s = (state.stats = await api("/api/overview/stats"));
    const pill = $("#nav-pending");
    pill.textContent = s.pending;
    pill.toggleAttribute("data-zero", !s.pending);
    $("#kpis").innerHTML = [
      ["To review", fmt(s.pending)],
      ["Ready to schedule", fmt(s.approved_unscheduled)],
      ["Posts today", fmt(s.scheduled_today)],
      ["Views", fmt(s.views)],
      ["Earned", money(s.earnings)],
    ].map(([k, v]) => `<div class="kpi"><small>${k}</small><b>${v}</b></div>`).join("");
    const jobs = s.live_jobs.length ? `${s.live_jobs.length} job(s) running` : "idle";
    $("#status-foot").innerHTML = `
      <div><span class="dot ${s.twitch ? "on" : "off"}"></span>${s.twitch ? "Twitch keys set" : '<a href="#settings" onclick="go(\'settings\')">Add Twitch keys →</a>'}</div>
      <div><span class="dot ${s.transcription ? "on" : "off"}"></span>Captions ${s.transcription ? "on" : "off"}</div>
      <div><span class="dot ${s.live_jobs.length ? "on" : ""}"></span>${jobs}</div>
      <div>${fmt(s.sources)} active sources</div>
      ${s.low_disk ? '<div style="color:var(--bad)">⚠ Disk almost full: recording paused. <a href="#settings" onclick="go(\'settings\')">Free space</a></div>' : ""}`;
  } catch (e) { /* server restarting */ }
}

function go(view) {
  state.view = view;
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  $("#view-title").textContent = $(`nav a[data-view="${view}"]`).childNodes[0].textContent.trim();
  location.hash = view;
  VIEWS[view]().catch((e) => toast(e.message, true));
}

document.querySelectorAll("nav a").forEach((a) => a.addEventListener("click", () => go(a.dataset.view)));

/* ------------------------------------------------------------ review */
async function viewReview(keepIdx = false) {
  state.clips = await api("/api/clips/list?status=pending");
  if (!keepIdx) state.idx = 0;
  state.idx = Math.min(state.idx, Math.max(0, state.clips.length - 1));
  renderReview();
}

function renderReview() {
  const v = $("#view");
  const c = state.clips[state.idx];
  if (!c) {
    const cand = state.stats.candidates || 0;
    v.innerHTML = `<div class="card empty"><b>Queue clear.</b>
      ${cand ? `${cand} lower-scoring moments are detected but not rendered yet.` : "New clips land here as sessions are processed."}
      <div class="row" style="justify-content:center;margin-top:16px">
        ${cand ? `<button class="btn primary" id="more">Render next 5</button>` : ""}
        <button class="btn" onclick="go('schedule')">Go to schedule</button>
      </div></div>`;
    $("#more")?.addEventListener("click", renderMore);
    return;
  }
  const f = c.features || {};
  const z = (x) => (x ?? 0) >= 20 ? "20+" : (x ?? 0).toFixed(1);
  const chip = (label, val, hot) => `<span class="chip ${hot ? "hot" : ""}">${label} ${val}</span>`;
  v.innerHTML = `
  <div class="review">
    <div>
      <div class="player"><video id="vid" src="${c.video_url}" controls autoplay playsinline loop></video></div>
    </div>
    <div class="detail">
      <div class="card">
        <div class="score">
          <div class="ring" style="--p:${c.score}"><b>${Math.round(c.score)}</b></div>
          <div>
            <div style="font-size:16px;font-weight:700">${esc(c.streamer)}</div>
            <div class="muted">${esc(c.campaign || "No campaign")} · ${c.duration.toFixed(0)}s · ${esc(c.layout)} · ${money(c.payout_per_1k)}/1k</div>
          </div>
          <div class="spacer"></div>
          <div class="muted">${state.idx + 1} / ${state.clips.length}</div>
        </div>
        <div class="chips">
          ${chip("Chat ×", z(f.chat_z), f.chat_z > 3)}
          ${chip("Hype ×", z(f.hype_z), f.hype_z > 3)}
          ${chip("Audio ×", z(f.audio_z), f.audio_z > 3)}
          ${chip("Viewer clips", (f.viewer_clips ?? 0).toFixed(1), f.viewer_clips >= 1)}
          ${f.hook !== undefined ? chip("Hook", Math.round(f.hook * 100) + "%", f.hook >= 0.5) : ""}
        </div>
        ${c.issues.length ? `<div class="issues">${c.issues.map((i) => `<div>${esc(i)}</div>`).join("")}</div>` : ""}
        <div class="field"><label>Hook title <kbd>E</kbd></label>
          <input id="title" value="${esc(c.title)}" maxlength="100"></div>
        <div class="field"><label>Caption (auto-built with credit + required tags)</label>
          <div class="caption">${esc(c.caption)}</div></div>
        <div class="field"><label>Trim &amp; layout (re-renders)</label>
          <div class="trim">
            <button class="btn" data-trim="-3,0">Start −3s</button>
            <button class="btn" data-trim="3,0">Start +3s</button>
            <button class="btn" data-trim="0,-3">End −3s</button>
            <button class="btn" data-trim="0,3">End +3s</button>
          </div>
          <div class="row" style="margin-top:6px">
            <button class="btn" id="layout">Layout: ${c.layout === "stacked" ? "stacked → blur-fill" : "blur-fill → stacked"}</button>
            ${c.transcript ? `<span class="muted" style="font-size:12px">“${esc(c.transcript.slice(0, 90))}${c.transcript.length > 90 ? "…" : ""}”</span>` : ""}
          </div>
        </div>
        <div class="actions">
          <button class="btn big danger" id="reject">Reject <kbd>X</kbd></button>
          <button class="btn big primary" id="approve">Approve <kbd>A</kbd></button>
        </div>
        <div class="row muted" style="margin-top:10px;font-size:12px">
          <span><kbd>←</kbd><kbd>→</kbd> browse</span><span><kbd>Space</kbd> play/pause</span>
          <div class="spacer"></div><a href="${c.video_url}" download="clip${c.id}.mp4">Download mp4</a>
        </div>
      </div>
      <div class="queue">${state.clips.map((q, i) =>
        `<img src="${q.thumb_url}" data-i="${i}" class="${i === state.idx ? "cur" : ""}" title="${esc(q.streamer)} · ${Math.round(q.score)}" loading="lazy">`).join("")}</div>
    </div>
  </div>`;

  $("#approve").onclick = () => decide("approved");
  $("#reject").onclick = () => decide("rejected");
  $("#layout").onclick = () => doRecut(0, 0, c.layout === "stacked" ? "blurfill" : "stacked");
  v.querySelectorAll("[data-trim]").forEach((b) => (b.onclick = () => {
    const [s, e] = b.dataset.trim.split(",").map(Number);
    doRecut(s, e);
  }));
  v.querySelectorAll(".queue img").forEach((img) => (img.onclick = () => { state.idx = +img.dataset.i; renderReview(); }));
  $("#title").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); saveTitle().then(() => e.target.blur()); } });
  $("#title").addEventListener("blur", saveTitle);
  $(".queue img.cur")?.scrollIntoView({ block: "nearest", inline: "center" });
}

async function saveTitle() {
  const c = state.clips[state.idx];
  const t = $("#title")?.value.trim();
  if (!c || t === undefined || t === c.title) return;
  await api(`/api/clips/${c.id}`, { method: "PATCH", body: { title: t } });
  // Title is burned into the video, so re-render it.
  await doRecut(0, 0, null, t);
}

async function decide(status) {
  const c = state.clips[state.idx];
  if (!c) return;
  await api(`/api/clips/${c.id}`, { method: "PATCH", body: { status } });
  state.clips.splice(state.idx, 1);
  state.idx = Math.min(state.idx, state.clips.length - 1);
  toast(status === "approved" ? "Approved ✓" : "Rejected");
  refreshStats();
  renderReview();
}

async function doRecut(s, e, layout = null, title = null) {
  const c = state.clips[state.idx];
  $(".review")?.classList.add("busy");
  toast("Re-rendering…");
  try {
    const updated = await api(`/api/clips/${c.id}/recut`, { method: "POST", body: { start_delta: s, end_delta: e, layout, title } });
    // Replace by id: the queue may have moved on (e.g. approved) while rendering.
    const i = state.clips.findIndex((x) => x.id === updated.id);
    if (i >= 0 && updated.status === "pending") state.clips[i] = updated;
    toast("Updated");
  } catch (err) { toast(err.message, true); }
  renderReview();
}

async function renderMore() {
  const r = await api("/api/clips/render-more?count=5", { method: "POST" });
  toast(`Rendering ${r.queued} more — they'll appear shortly`);
  setTimeout(() => state.view === "review" && viewReview(), 8000);
}

document.addEventListener("keydown", (e) => {
  if (state.view !== "review" || e.target.matches("input, textarea, select") || $("#modal").open) return;
  const k = e.key.toLowerCase();
  if (k === "a") decide("approved");
  else if (k === "x" || k === "r") decide("rejected");
  else if (k === "arrowright") { state.idx = Math.min(state.idx + 1, state.clips.length - 1); renderReview(); }
  else if (k === "arrowleft") { state.idx = Math.max(state.idx - 1, 0); renderReview(); }
  else if (k === "e") { e.preventDefault(); $("#title")?.focus(); }
  else if (k === " ") { e.preventDefault(); const v = $("#vid"); if (v) v.paused ? v.play() : v.pause(); }
});

/* ------------------------------------------------------------ schedule */
async function viewSchedule() {
  const day = state.day || today();
  const pubs = await api(`/api/publications?day=${day}`);
  const v = $("#view");
  v.innerHTML = `
    <div class="card">
      <div class="row" style="margin-bottom:14px">
        <input type="date" id="day" value="${day}" style="width:auto">
        <button class="btn primary" id="plan">Route approved clips</button>
        <a class="btn" href="/api/export?day=${day}">Download day pack (.zip)</a>
        <div class="spacer"></div>
        <span class="muted">${pubs.length} posts</span>
      </div>
      <p class="note">Routing gives each clip one account per platform, keeps each account under its daily cap and posting gap, and avoids back-to-back posts of the same streamer. The day pack has a folder per account with each mp4 and its caption.</p>
      ${pubs.length ? `<table>
        <tr><th>Time</th><th></th><th>Clip</th><th>Account</th><th>Status</th><th class="num">Views</th><th>Post URL</th><th></th></tr>
        ${pubs.map((p) => `<tr data-id="${p.id}">
          <td>${new Date(p.scheduled_for).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</td>
          <td><a href="${p.video_url}" target="_blank"><img class="thumb" src="${p.thumb_url}"></a></td>
          <td><b>${esc(p.title)}</b><div class="muted">${esc(p.streamer)} · score ${Math.round(p.score)}</div></td>
          <td><span class="tag ${esc(p.platform)}">${esc(p.platform)}</span> ${esc(p.account)}</td>
          <td><span class="tag ${esc(p.status)}">${esc(p.status)}</span></td>
          <td class="num"><input type="number" min="0" value="${p.views}" data-f="views" style="width:100px;text-align:right"></td>
          <td><input value="${esc(p.url)}" placeholder="paste link once posted" data-f="url"></td>
          <td class="acts"><div class="row" style="flex-wrap:nowrap;justify-content:flex-end">
            <button class="btn" data-copy title="Copy caption">Copy</button>
            ${p.status !== "posted" ? `<button class="btn" data-posted>Mark posted</button>` : ""}
            <button class="btn danger" data-del title="Unschedule">✕</button></div></td>
        </tr>`).join("")}
      </table>` : `<div class="empty"><b>Nothing scheduled for this day.</b>Approve clips in Review, add accounts, then route.</div>`}
    </div>`;
  $("#day").onchange = (e) => { state.day = e.target.value; viewSchedule(); };
  $("#plan").onclick = async () => {
    const r = await api(`/api/schedule?day=${day}`, { method: "POST" });
    toast(r.scheduled ? `Scheduled ${r.scheduled} posts` : "Nothing to route. Check approved clips, accounts and caps.");
    refreshStats(); viewSchedule();
  };
  v.querySelectorAll("tr[data-id]").forEach((tr) => {
    const id = tr.dataset.id;
    const p = pubs.find((x) => String(x.id) === id);
    tr.querySelectorAll("input[data-f]").forEach((inp) => inp.addEventListener("change", async () => {
      const body = { [inp.dataset.f]: inp.dataset.f === "views" ? Number(inp.value) : inp.value };
      await api(`/api/publications/${id}`, { method: "PATCH", body });
      toast("Saved"); refreshStats();
    }));
    tr.querySelector("[data-copy]").onclick = async () => { await navigator.clipboard.writeText(p.caption); toast("Caption copied"); };
    tr.querySelector("[data-posted]")?.addEventListener("click", async () => {
      await api(`/api/publications/${id}`, { method: "PATCH", body: { status: "posted" } }); viewSchedule();
    });
    tr.querySelector("[data-del]").onclick = async () => { await api(`/api/publications/${id}`, { method: "DELETE" }); viewSchedule(); refreshStats(); };
  });
}

/* ------------------------------------------------------------ generic CRUD views */
const FORMS = {
  campaigns: [
    ["name", "Campaign name", "text"],
    ["platforms", "Allowed platforms (comma-separated)", "text", "youtube,tiktok"],
    ["payout_per_1k", "Payout per 1,000 views ($)", "number", 1],
    ["budget_total", "Total budget ($, 0 = unknown)", "number", 0],
    ["min_seconds", "Min clip length (s)", "number", 15],
    ["max_seconds", "Max clip length (s)", "number", 60],
    ["required_tags", "Required tags / mentions (space-separated)", "text", ""],
    ["notes", "Rules & notes", "textarea", ""],
  ],
  sources: [
    ["handle", "Twitch handle", "text"],
    ["display_name", "Display name (optional)", "text", ""],
    ["campaign_id", "Campaign (your permission to clip)", "campaign"],
    ["facecam_box", 'Facecam box "x,y,w,h" as fractions (blank = blur-fill)', "text", ""],
    ["auto_record", "Auto-record when live", "bool", true],
  ],
  destinations: [
    ["platform", "Platform", "select:youtube,tiktok,instagram"],
    ["account_name", "Account name", "text"],
    ["campaign_ids", "Campaign IDs this account posts (blank = all)", "text", ""],
    ["daily_cap", "Max posts per day", "number", 4],
    ["min_gap_minutes", "Min minutes between posts", "number", 90],
  ],
};

function openForm(kind, item = null, preset = {}) {
  const f = $("#modal-form");
  const vals = { ...Object.fromEntries(FORMS[kind].map(([k, , , d]) => [k, d ?? ""])), ...preset, ...(item || {}) };
  f.innerHTML = `<h2>${item ? "Edit" : "New"} ${kind.slice(0, -1)}</h2>
    ${FORMS[kind].map(([k, label, type]) => {
      const val = vals[k];
      let input;
      if (type === "textarea") input = `<textarea name="${k}">${esc(val)}</textarea>`;
      else if (type === "bool") input = `<select name="${k}"><option value="true" ${val ? "selected" : ""}>Yes</option><option value="false" ${!val ? "selected" : ""}>No</option></select>`;
      else if (type === "campaign") input = `<select name="${k}" required><option value="">Select…</option>${state.campaigns.map((c) => `<option value="${c.id}" ${c.id === val ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select>`;
      else if (type.startsWith("select:")) input = `<select name="${k}">${type.slice(7).split(",").map((o) => `<option ${o === val ? "selected" : ""}>${o}</option>`).join("")}</select>`;
      else input = `<input name="${k}" type="${type}" step="any" value="${esc(val)}">`;
      return `<div class="field"><label>${label}</label>${input}</div>`;
    }).join("")}
    <div class="row" style="justify-content:flex-end;margin-top:6px">
      <button class="btn" value="cancel" formnovalidate>Cancel</button>
      <button class="btn primary" value="save">Save</button>
    </div>`;
  const dlg = $("#modal");
  dlg.onclose = async () => {
    if (dlg.returnValue !== "save") return;
    const body = {};
    FORMS[kind].forEach(([k, , type]) => {
      let v = f.elements[k].value;
      if (type === "number") v = Number(v);
      else if (type === "bool") v = v === "true";
      else if (type === "campaign") v = v ? Number(v) : null;
      body[k] = v;
    });
    try {
      await api(item ? `/api/${kind}/${item.id}` : `/api/${kind}`, { method: item ? "PATCH" : "POST", body });
      toast("Saved"); refreshStats(); VIEWS[state.view]();
    } catch (e) { toast(e.message, true); }
  };
  dlg.returnValue = "";
  dlg.showModal();
}

async function viewCampaigns() {
  const rows = (state.campaigns = await api("/api/campaigns"));
  $("#view").innerHTML = `<div class="card">
    <div class="row" style="margin-bottom:12px"><p class="note" style="margin:0">A campaign is your permission to clip. Every source must belong to one, and every clip is checked against its rules before it can be scheduled.</p>
    <div class="spacer"></div><button class="btn primary" id="new">New campaign</button></div>
    ${rows.length ? `<table><tr><th>ID</th><th>Name</th><th>Platforms</th><th class="num">$/1k</th><th>Budget</th><th>Length</th><th>Required tags</th><th>Active</th><th></th></tr>
    ${rows.map((c) => {
      const pct = c.budget_total ? Math.min(100, (c.budget_spent / c.budget_total) * 100) : 0;
      return `<tr><td class="muted">${c.id}</td><td><b>${esc(c.name)}</b></td><td>${esc(c.platforms)}</td><td class="num">${money(c.payout_per_1k)}</td>
      <td>${c.budget_total ? `<div class="bar"><i style="width:${pct}%"></i></div><small class="muted">${money(c.budget_spent)} / ${money(c.budget_total)}</small>` : '<span class="muted">—</span>'}</td>
      <td>${c.min_seconds}–${c.max_seconds}s</td><td>${esc(c.required_tags) || '<span class="muted">—</span>'}</td>
      <td>${c.active ? "Yes" : "No"}</td>
      <td class="acts"><div class="row" style="flex-wrap:nowrap;justify-content:flex-end"><button class="btn" data-edit="${c.id}">Edit</button>
      <button class="btn" data-toggle="${c.id}">${c.active ? "Pause" : "Resume"}</button></div></td></tr>`;
    }).join("")}</table>` : `<div class="empty"><b>No campaigns yet.</b>Add the clipping campaigns you've joined.</div>`}</div>`;
  $("#new").onclick = () => openForm("campaigns");
  document.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => openForm("campaigns", rows.find((r) => r.id == b.dataset.edit))));
  document.querySelectorAll("[data-toggle]").forEach((b) => (b.onclick = async () => {
    const c = rows.find((r) => r.id == b.dataset.toggle);
    await api(`/api/campaigns/${c.id}`, { method: "PATCH", body: { active: !c.active } }); viewCampaigns();
  }));
}

async function viewSources() {
  const [rows, camps] = await Promise.all([api("/api/sources"), api("/api/campaigns")]);
  state.campaigns = camps;
  const cname = (id) => camps.find((c) => c.id === id)?.name || "—";
  const active = rows.filter((r) => r.active);
  $("#view").innerHTML = `
  <div class="grid">
    <div class="card">
      <div class="row" style="margin-bottom:12px"><h2 style="margin:0">Tracked sources</h2><div class="spacer"></div>
      <button class="btn primary" id="new">Add source</button></div>
      ${active.length ? `<table><tr><th>Streamer</th><th>Campaign</th><th>Layout</th><th>Auto-record</th><th></th></tr>
      ${active.map((s) => `<tr><td><b>${esc(s.display_name || s.handle)}</b> <span class="muted">twitch.tv/${esc(s.handle)}</span></td>
        <td>${esc(cname(s.campaign_id))}</td><td>${s.facecam_box ? "stacked (facecam)" : "blur-fill"}</td>
        <td>${s.auto_record ? "On" : "Off"}</td>
        <td class="acts"><div class="row" style="flex-wrap:nowrap;justify-content:flex-end"><button class="btn" data-edit="${s.id}">Edit</button><button class="btn danger" data-del="${s.id}">Remove</button></div></td></tr>`).join("")}
      </table>` : `<div class="empty"><b>No sources.</b>Add streamers whose campaigns you're in.</div>`}
    </div>
    <div class="card">
      <div class="row" style="margin-bottom:12px"><h2 style="margin:0">Discover: live GTA 6 streams</h2><div class="spacer"></div>
      <button class="btn" id="scan">Scan Twitch</button></div>
      <p class="note">Discovery is for scouting. Only add a streamer once you're in their clipping campaign or have their written OK.</p>
      <div id="disc" class="muted">Click Scan to list the top live streams.</div>
    </div>
  </div>`;
  $("#new").onclick = () => (camps.length ? openForm("sources") : toast("Create a campaign first", true));
  document.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => openForm("sources", rows.find((r) => r.id == b.dataset.edit))));
  document.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => { await api(`/api/sources/${b.dataset.del}`, { method: "DELETE" }); viewSources(); refreshStats(); }));
  $("#scan").onclick = async () => {
    $("#disc").textContent = "Scanning…";
    try {
      const live = await api("/api/discover/live?limit=25");
      $("#disc").innerHTML = live.length ? `<table><tr><th></th><th>Streamer</th><th class="num">Viewers</th><th>Title</th><th></th></tr>
        ${live.map((s) => `<tr><td><img src="${esc(s.thumbnail)}" style="width:96px;border-radius:6px"></td>
        <td><b>${esc(s.display_name)}</b><div class="muted">${esc(s.language)}</div></td><td class="num">${fmt(s.viewers)}</td>
        <td>${esc(s.title)}</td><td>${s.tracked ? '<span class="tag approved">tracked</span>' :
          `<button class="btn" data-add="${esc(s.handle)}" data-name="${esc(s.display_name)}">Add…</button>`}</td></tr>`).join("")}</table>` : "No live streams found.";
      document.querySelectorAll("[data-add]").forEach((b) => (b.onclick = () => camps.length
        ? openForm("sources", null, { handle: b.dataset.add, display_name: b.dataset.name })
        : toast("Create a campaign first", true)));
    } catch (e) { $("#disc").textContent = e.message; }
  };
}

async function viewAccounts() {
  const [rows, camps] = await Promise.all([api("/api/destinations"), api("/api/campaigns")]);
  state.campaigns = camps;
  $("#view").innerHTML = `<div class="card">
    <div class="row" style="margin-bottom:12px"><p class="note" style="margin:0">Spread output across 3–4 niche accounts rather than one firehose. One account posting 30+ Shorts a day from the same streamers looks like a content farm.</p>
    <div class="spacer"></div><button class="btn primary" id="new">Add account</button></div>
    ${rows.length ? `<table><tr><th>Platform</th><th>Account</th><th>Campaigns</th><th class="num">Daily cap</th><th class="num">Gap</th><th></th></tr>
    ${rows.map((d) => `<tr><td><span class="tag ${esc(d.platform)}">${esc(d.platform)}</span></td><td><b>${esc(d.account_name)}</b></td>
      <td>${d.campaign_ids ? d.campaign_ids.split(",").map((id) => esc(camps.find((c) => c.id == id)?.name || "#" + id)).join(", ") : '<span class="muted">all</span>'}</td>
      <td class="num">${d.daily_cap}</td><td class="num">${d.min_gap_minutes}m</td>
      <td class="acts"><div class="row" style="flex-wrap:nowrap;justify-content:flex-end"><button class="btn" data-edit="${d.id}">Edit</button><button class="btn danger" data-del="${d.id}">Remove</button></div></td></tr>`).join("")}
    </table>` : `<div class="empty"><b>No accounts.</b>Add the channels you post to.</div>`}</div>`;
  $("#new").onclick = () => openForm("destinations");
  document.querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => openForm("destinations", rows.find((r) => r.id == b.dataset.edit))));
  document.querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => { await api(`/api/destinations/${b.dataset.del}`, { method: "DELETE" }); viewAccounts(); }));
}

async function viewSessions() {
  const [rows, sources] = await Promise.all([api("/api/sessions/all"), api("/api/sources")]);
  $("#view").innerHTML = `
  <div class="grid">
    <div class="card">
      <h2>Import a VOD or file</h2>
      <div class="row">
        <select id="imp-src" style="width:auto">${sources.filter((s) => s.active).map((s) => `<option value="${s.id}">${esc(s.display_name || s.handle)}</option>`).join("")}</select>
        <input id="imp-url" placeholder="https://www.twitch.tv/videos/…  or  /path/to/file.mp4" style="flex:1;min-width:240px">
        <button class="btn primary" id="imp">Import &amp; process</button>
      </div>
      <p class="muted" style="margin:10px 0 0;font-size:12px">Live recording runs automatically with <code>clipcore serve --watch</code>. VOD imports have no chat log, so detection uses audio and viewer clips only.</p>
    </div>
    <div class="card">
      ${rows.length ? `<table><tr><th>ID</th><th>Source</th><th>Title</th><th>Started</th><th class="num">Length</th><th class="num">Clips</th><th>Status</th><th></th></tr>
      ${rows.map((s) => `<tr><td class="muted">${s.id}</td><td>${esc(s.handle)}</td><td>${esc(s.title)}</td>
        <td>${new Date(s.started_at).toLocaleString()}</td><td class="num">${(s.duration / 60).toFixed(0)}m</td><td class="num">${s.clips}</td>
        <td><span class="tag ${esc(s.status)}" title="${esc(s.error)}">${esc(s.status)}</span></td>
        <td>${["recorded", "processed", "failed"].includes(s.status) ? `<button class="btn" data-proc="${s.id}">Re-process</button>` : ""}</td></tr>`).join("")}
      </table>` : `<div class="empty"><b>No sessions yet.</b></div>`}
    </div>
  </div>`;
  $("#imp").onclick = async () => {
    const url = $("#imp-url").value.trim();
    if (!url) return;
    await api("/api/sessions/import", { method: "POST", body: { source_id: Number($("#imp-src").value), path_or_url: url } });
    toast("Importing. Clips will appear in Review.");
    setTimeout(viewSessions, 1500);
  };
  document.querySelectorAll("[data-proc]").forEach((b) => (b.onclick = async () => {
    await api(`/api/sessions/${b.dataset.proc}/process`, { method: "POST" }); toast("Processing…"); setTimeout(viewSessions, 1000);
  }));
}

async function viewAnalytics() {
  const a = await api("/api/overview/analytics");
  const table = (title, rows) => `<div class="card"><h2>${title}</h2>${rows.length ? `<table>
    <tr><th>Name</th><th class="num">Posts</th><th class="num">Views</th><th class="num">Avg views</th><th class="num">Earned</th></tr>
    ${rows.map((r) => `<tr><td>${esc(r.name)}</td><td class="num">${r.posted}/${r.clips}</td><td class="num">${fmt(r.views)}</td>
      <td class="num">${fmt(r.avg_views)}</td><td class="num"><b>${money(r.earnings)}</b></td></tr>`).join("")}</table>`
    : `<div class="muted">No posted clips yet. Enter view counts in Schedule.</div>`}</div>`;
  const w = a.weights;
  const wmax = Math.max(...Object.values(w));
  $("#view").innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(min(460px,100%),1fr))">
    ${table("By campaign", a.by_campaign)}
    ${table("By streamer", a.by_source)}
    ${table("By account", a.by_account)}
    <div class="card"><h2>Campaign budgets</h2>${a.budgets.length ? `<table>${a.budgets.map((b) => `<tr><td>${esc(b.name)}</td>
      <td style="width:40%">${b.total ? `<div class="bar"><i style="width:${Math.min(100, (b.spent / b.total) * 100)}%"></i></div>` : ""}</td>
      <td class="num">${b.remaining !== null ? money(b.remaining) + " left" : "uncapped"}</td></tr>`).join("")}</table>` : '<div class="muted">No active campaigns.</div>'}
      <p class="muted" style="font-size:12px;margin-bottom:0">Budgets drain as views are logged. Steer output toward campaigns with budget left.</p></div>
    <div class="card"><h2>Detection model</h2>
      <p class="muted" style="margin-top:0">These weights decide what counts as a moment. "Learn" re-fits them to what actually got views on your accounts. This is your GTA 6-specific edge.</p>
      <table>${Object.entries(w).map(([k, v]) => `<tr><td>${esc(k.replace("_", " "))}</td><td style="width:55%"><div class="bar"><i style="width:${(v / wmax) * 100}%"></i></div></td><td class="num">${v.toFixed(2)}</td></tr>`).join("")}</table>
      <div class="row" style="margin-top:12px"><button class="btn primary" id="learn">Learn from results</button>
      <span class="muted">${a.learn_samples}/${a.learn_needed} posts with views${a.approval_rate !== null ? ` · ${a.approval_rate}% approval rate` : ""}</span></div>
    </div>
  </div>`;
  $("#learn").onclick = async () => {
    const r = await api("/api/overview/learn", { method: "POST" });
    toast(r.updated ? "Weights updated from your results" : `Need ${r.needed} posts with views (have ${r.samples})`, !r.updated);
    viewAnalytics();
  };
}

/* ------------------------------------------------------------ settings */
const TIMEZONES = ["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles",
  "America/Anchorage", "Pacific/Honolulu", "America/Toronto", "Europe/London", "Europe/Berlin", "Australia/Sydney", "UTC"];

async function viewSettings() {
  const s = await api("/api/settings");
  const d = s.disk;
  const tzs = TIMEZONES.includes(s.timezone) ? TIMEZONES : [s.timezone, ...TIMEZONES];
  const hours = (sel) => Array.from({ length: 24 }, (_, h) =>
    `<option value="${h}" ${h === sel ? "selected" : ""}>${new Date(2000, 0, 1, h).toLocaleTimeString([], { hour: "numeric" })}</option>`).join("");
  $("#view").innerHTML = `
  <div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(min(440px,100%),1fr))">
    <div class="card">
      <h2>1 · Twitch connection</h2>
      <p class="note">From <b>dev.twitch.tv/console</b> → Applications → Manage your app. Paste both values and click Save. The secret is stored on this server only and is never shown again.</p>
      <div class="field"><label>Client ID</label><input id="cid" value="${esc(s.twitch_client_id)}" autocomplete="off" spellcheck="false"></div>
      <div class="field"><label>Client Secret ${s.twitch_client_secret_set ? '<span class="tag approved" style="margin-left:6px">saved</span>' : ""}</label>
        <input id="csec" type="password" autocomplete="new-password" spellcheck="false"
          placeholder="${s.twitch_client_secret_set ? "Saved. Leave blank to keep it, or paste a new one" : "Paste your Client Secret"}"></div>
      <div class="field"><label>Game category to track</label><input id="game" value="${esc(s.game_name)}">
        <small class="muted">Before launch day, use <b>Grand Theft Auto V</b> to test. Switch to <b>Grand Theft Auto VI</b> once the category exists.</small></div>
      <div class="row"><button class="btn primary" id="save-tw">Save</button><button class="btn" id="test-tw">Test connection</button></div>
      <div id="tw-result" style="margin-top:12px"></div>
    </div>

    <div class="card">
      <h2>2 · Posting schedule</h2>
      <div class="field"><label>Your timezone (posting times use this)</label>
        <select id="tz">${tzs.map((t) => `<option ${t === s.timezone ? "selected" : ""}>${t}</option>`).join("")}</select></div>
      <div class="row" style="flex-wrap:nowrap">
        <div class="field" style="flex:1"><label>First post no earlier than</label><select id="pws">${hours(s.post_window_start)}</select></div>
        <div class="field" style="flex:1"><label>Last post no later than</label><select id="pwe">${hours(s.post_window_end)}</select></div>
      </div>
      <div class="field"><label>Clips to render per hour of stream</label><input id="cph" type="number" step="0.5" min="0.5" max="10" value="${s.clips_per_hour}">
        <small class="muted">1.5 is a good start. Higher means more to review, and more lower-quality clips.</small></div>
      <button class="btn primary" id="save-sched">Save</button>
    </div>

    <div class="card">
      <h2>3 · Storage</h2>
      <p class="note">Recordings are huge (3–6 GB per stream hour). After a stream is processed, its recording is deleted once this many days have passed. Your rendered clips are kept.</p>
      <table>
        <tr><td>Recordings</td><td class="num">${d.recordings_gb} GB</td></tr>
        <tr><td>Clips</td><td class="num">${d.clips_gb} GB</td></tr>
        <tr><td>Free space on server</td><td class="num"><b style="color:${d.free_gb < 50 ? "var(--bad)" : "inherit"}">${d.free_gb} GB</b> of ${d.total_gb} GB</td></tr>
      </table>
      <div class="field" style="margin-top:12px"><label>Recording quality</label>
        <select id="rq">${Object.entries(s.quality_options).map(([k, v]) => `<option value="${esc(k)}" ${k === s.record_quality ? "selected" : ""}>${esc(v)}</option>`).join("")}</select></div>
      <div class="row" style="margin-top:12px">
        <label class="muted">Keep recordings for</label>
        <input id="ret" type="number" min="1" max="60" value="${s.retention_days}" style="width:80px"><span class="muted">days</span>
        <button class="btn primary" id="save-ret">Save</button>
        <button class="btn" id="clean">Clean up now</button>
      </div>
    </div>

    <div class="card">
      <h2>4 · Account</h2>
      <div class="field"><label>Current password</label><input id="pw-cur" type="password" autocomplete="current-password"></div>
      <div class="field"><label>New password (10+ characters)</label><input id="pw-new" type="password" autocomplete="new-password"></div>
      <div class="row"><button class="btn" id="pw-save">Change password</button><div class="spacer"></div>
        <button class="btn danger" id="logout">Sign out</button></div>
    </div>
  </div>`;

  const save = async (body, msg = "Saved") => {
    try { await api("/api/settings", { method: "POST", body }); toast(msg); refreshStats(); return true; }
    catch (e) { toast(e.message, true); return false; }
  };
  $("#save-tw").onclick = async () => {
    if (await save({ twitch_client_id: $("#cid").value, twitch_client_secret: $("#csec").value, game_name: $("#game").value })) viewSettings();
  };
  $("#test-tw").onclick = async () => {
    const box = $("#tw-result");
    box.innerHTML = '<span class="muted">Testing…</span>';
    try {
      const r = await api("/api/settings/test-twitch", { method: "POST" });
      box.innerHTML = `<div class="caption" style="border-color:var(--accent);color:var(--accent)">✓ ${esc(r.message)}</div>`;
    } catch (e) { box.innerHTML = `<div class="issues"><div>${esc(e.message)}</div></div>`; }
  };
  $("#save-sched").onclick = () => save({ timezone: $("#tz").value, post_window_start: +$("#pws").value,
    post_window_end: +$("#pwe").value, clips_per_hour: +$("#cph").value });
  $("#save-ret").onclick = () => save({ retention_days: +$("#ret").value, record_quality: $("#rq").value });
  $("#clean").onclick = async () => {
    const r = await api("/api/settings/cleanup", { method: "POST" });
    toast(`Freed ${r.freed_gb} GB`); viewSettings();
  };
  $("#pw-save").onclick = async () => {
    try {
      await api("/api/auth/change", { method: "POST", body: { current: $("#pw-cur").value, new: $("#pw-new").value } });
      toast("Password changed"); $("#pw-cur").value = $("#pw-new").value = "";
    } catch (e) { toast(e.message, true); }
  };
  $("#logout").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); location.replace("/login.html"); };
}

const VIEWS = {
  review: viewReview, schedule: viewSchedule, sources: viewSources, campaigns: viewCampaigns,
  accounts: viewAccounts, sessions: viewSessions, analytics: viewAnalytics, settings: viewSettings,
};

refreshStats();
setInterval(refreshStats, 15000);
api("/api/campaigns").then((c) => (state.campaigns = c)).catch(() => {});
go(VIEWS[location.hash.slice(1)] ? location.hash.slice(1) : "review");
