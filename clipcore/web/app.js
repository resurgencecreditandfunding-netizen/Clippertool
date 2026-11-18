/* ClipCore web app — vanilla JS, no build step. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const hms = (s) => {
  s = Math.round(s || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${m}:${String(x).padStart(2, "0")}`;
};
const ICON = {
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l13-7.5z"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 20h14"/></svg>',
  back: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>',
  fire: "🔥",
};

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
    throw new Error(typeof msg === "string" ? msg : "Request failed");
  }
  return res.json();
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "show" + (err ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = ""), 3200);
}

let me = null;
let pollTimer = null;
function poll(fn, ms) { clearTimeout(pollTimer); pollTimer = setTimeout(fn, ms); }

/* ------------------------------------------------------------ router */
function route() {
  clearTimeout(pollTimer);
  const h = location.hash;
  const job = h.match(/^#job\/(\d+)/);
  (job ? viewJob(+job[1]) : h === "#admin" && me?.is_admin ? viewAdmin() : viewHome()).catch((e) => toast(e.message, true));
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);

/* ------------------------------------------------------------ home */
async function viewHome() {
  const v = $("#view");
  if (!$("#paste-form")) {
    v.innerHTML = `
      <section class="card hero">
        <h1>Drop a VOD. Get the viral clips.</h1>
        <p>We scan the whole stream, rank the moments that went off, and cut them into captioned 9:16 Shorts.</p>
        <form class="paste" id="paste-form">
          <div class="url">${ICON.link}<input id="url" placeholder="Paste a Twitch, Kick or YouTube VOD link" autocomplete="off" spellcheck="false"></div>
          <select id="count" aria-label="Number of clips">
            <option value="0">Auto clips</option><option value="5">5 clips</option><option value="10">10 clips</option>
            <option value="15">15 clips</option><option value="20">20 clips</option>
          </select>
          <button class="btn primary" id="go">Get clips</button>
        </form>
        <div class="sources muted">Works with
          <span class="pill">twitch.tv/videos/…</span><span class="pill">kick.com/…/videos/…</span><span class="pill">youtube.com/watch?v=…</span>
        </div>
      </section>
      <div class="section-head"><h2>Your videos</h2></div>
      <div class="projects" id="projects"></div>`;
    $("#paste-form").addEventListener("submit", submitLink);
  }
  await refreshProjects();
}

async function submitLink(e) {
  e.preventDefault();
  const url = $("#url").value.trim();
  if (!url) return $("#url").focus();
  $("#go").disabled = true;
  try {
    await api("/api/jobs", { method: "POST", body: { url, clip_count: +$("#count").value } });
    $("#url").value = "";
    toast("Got it. Finding the best moments now.");
    await refreshProjects();
  } catch (err) { toast(err.message, true); }
  $("#go").disabled = false;
}

async function refreshProjects() {
  const box = $("#projects");
  if (!box) return;
  const jobs = await api("/api/jobs");
  if (!jobs.length) {
    box.innerHTML = `<div class="card empty"><b>No videos yet</b>Paste a VOD link above. A 4-hour stream usually takes a few minutes.</div>`;
    return;
  }
  box.innerHTML = jobs.map(projectCard).join("");
  $$(".project.clickable", box).forEach((el) => el.addEventListener("click", (e) => {
    if (!e.target.closest("button")) location.hash = `#job/${el.dataset.id}`;
  }));
  $$("[data-retry]", box).forEach((b) => b.onclick = async () => {
    await api(`/api/jobs/${b.dataset.retry}/retry`, { method: "POST" }).catch((e) => toast(e.message, true));
    refreshProjects();
  });
  $$("[data-del]", box).forEach((b) => b.onclick = async () => {
    if (!confirm("Delete this video and its clips?")) return;
    await api(`/api/jobs/${b.dataset.del}`, { method: "DELETE" }).catch((e) => toast(e.message, true));
    refreshProjects();
  });
  if (jobs.some((j) => j.status === "queued" || j.status === "running")) poll(refreshProjects, 2500);
}

function projectCard(j) {
  const title = j.title || j.url;
  const meta = [j.channel, j.platform, j.duration ? hms(j.duration) : ""].filter(Boolean).join(" · ");
  let status = "";
  if (j.status === "queued") {
    status = `<div class="progress"><div class="bar"><i style="width:2%"></i></div>
      <div class="lbl"><span>In line${j.position > 1 ? ` · ${j.position - 1} ahead of you` : " · you're next"}</span><span></span></div></div>`;
  } else if (j.status === "running") {
    status = `<div class="progress"><div class="bar"><i style="width:${Math.max(3, j.progress)}%"></i></div>
      <div class="lbl"><span>${esc(j.stage)}…</span><span>${Math.round(j.progress)}%</span></div></div>`;
  } else if (j.status === "failed") {
    status = `<div class="err" style="margin-top:8px">${esc(j.error)}</div>`;
  }
  const side = j.status === "done"
    ? `<span class="pill ok">${j.shorts} clips ready</span><button class="btn ghost" data-del="${j.id}" title="Delete">✕</button>`
    : j.status === "failed"
      ? `<button class="btn" data-retry="${j.id}">Retry</button><button class="btn ghost" data-del="${j.id}" title="Delete">✕</button>`
      : j.status === "queued" ? `<button class="btn ghost" data-del="${j.id}" title="Cancel">✕</button>` : "";
  return `<div class="card project ${j.status === "done" ? "clickable" : ""}" data-id="${j.id}">
    <div class="thumb" style="background-image:url('${esc(j.thumbnail)}')">${j.duration ? `<span class="dur">${hms(j.duration)}</span>` : ""}</div>
    <div><h3>${esc(title)}</h3><div class="meta">${esc(meta) || (j.status === "failed" ? "" : "Opening link…")}</div>${status}</div>
    <div class="side">${side}</div></div>`;
}

/* ------------------------------------------------------------ job / clips */
async function viewJob(id) {
  const j = await api(`/api/jobs/${id}`);
  const v = $("#view");
  if (j.status !== "done") { location.hash = ""; return; }
  v.innerHTML = `
    <div class="jobhead">
      <a class="btn ghost" href="#">${ICON.back} Back</a>
      <div class="grow">
        <h1>${esc(j.title)}</h1>
        <div class="muted">${esc([j.channel, j.platform, hms(j.duration)].filter(Boolean).join(" · "))} · ${j.clips.length} clips, best first</div>
      </div>
      <a class="btn primary" href="/api/jobs/${j.id}/zip">${ICON.down} Download all</a>
    </div>
    ${j.clips.length ? `<div class="grid">${j.clips.map(clipCard).join("")}</div>`
      : `<div class="card empty"><b>No clips came out of this one.</b>Try a longer or livelier stream.</div>`}`;

  $$(".clip .vid").forEach((el) => {
    const video = $("video", el);
    el.addEventListener("mouseenter", () => { video.src ||= video.dataset.src; video.currentTime = 0; video.play().then(() => el.classList.add("playing")).catch(() => {}); });
    el.addEventListener("mouseleave", () => { video.pause(); el.classList.remove("playing"); });
    el.addEventListener("click", () => openPlayer(j.clips.find((c) => c.id === +el.dataset.id)));
  });
}

function clipCard(c) {
  return `<article class="card clip">
    <div class="vid" data-id="${c.id}">
      <img src="${c.thumb_url}" alt="" loading="lazy">
      <video muted playsinline loop preload="none" data-src="${c.video_url}"></video>
      <div class="badges"><span class="rank">#${c.rank}</span><span class="score">${ICON.fire} ${Math.round(c.score)}</span></div>
      <div class="play"><span>${ICON.play}</span></div>
    </div>
    <div class="body">
      <div class="title">${esc(c.title)}</div>
      <div class="row"><span class="muted" style="font-size:13px">${Math.round(c.duration)}s · at ${hms(c.start)}</span>
        <a class="btn" href="${c.download_url}" title="Download">${ICON.down}</a></div>
    </div>
  </article>`;
}

function openPlayer(c) {
  const d = $("#modal");
  d.innerHTML = `
    <button class="btn close" id="close">Close ✕</button>
    <div class="player">
      <video src="${c.video_url}" controls autoplay playsinline></video>
      <div class="info">
        <div><span class="pill hot">${ICON.fire} Viral score ${Math.round(c.score)}</span></div>
        <h3>${esc(c.title)}</h3>
        <div class="muted" style="font-size:14px">${Math.round(c.duration)} seconds · from ${hms(c.start)} in the stream</div>
        ${c.transcript ? `<div class="quote">“${esc(c.transcript)}”</div>` : ""}
        <a class="btn primary" href="${c.download_url}">${ICON.down} Download MP4</a>
      </div>
    </div>`;
  $("#close", d).onclick = () => d.close();
  d.onclose = () => { d.innerHTML = ""; };
  d.onclick = (e) => { if (e.target === d) d.close(); };
  d.showModal();
}

/* ------------------------------------------------------------ admin */
async function viewAdmin() {
  const a = await api("/api/admin/overview");
  const s = a.settings;
  const q = a.queue;
  $("#view").innerHTML = `
    <div class="section-head"><h2>Admin</h2><span class="muted">${(q.running || 0)} processing · ${(q.queued || 0)} waiting · ${a.disk.free_gb} GB free</span></div>
    <div class="admin">
      <div class="card">
        <h2>Access</h2>
        <p class="note">Leave the access code blank for open sign-ups. Set one to make the app invite-only: sell access through your checkout and send buyers the code.</p>
        <div class="field"><label>Access code for new sign-ups</label><input id="invite" value="${esc(s.invite_code)}" placeholder="Blank = anyone can sign up" autocomplete="off"></div>
        <div class="two">
          <div class="field"><label>Videos processing at once (server-wide)</label><input id="mpj" type="number" min="1" max="16" value="${s.max_parallel_jobs}"></div>
          <div class="field"><label>Videos in progress per user</label><input id="jpu" type="number" min="1" max="20" value="${s.jobs_per_user}"></div>
        </div>
        <div class="two">
          <div class="field"><label>Keep finished clips for (days)</label><input id="ret" type="number" min="1" max="90" value="${s.retention_days}"></div>
          <div class="field"><label>Longest VOD allowed (hours)</label><input id="mvh" type="number" min="0.5" max="48" step="0.5" value="${s.max_vod_hours}"></div>
        </div>
        <button class="btn primary" id="save-access">Save</button>
      </div>
      <div class="card">
        <h2>Twitch connection <span class="pill ${s.twitch_client_secret_set ? "ok" : ""}" style="margin-left:6px">${s.twitch_client_secret_set ? "keys saved" : "optional"}</span></h2>
        <p class="note">With your Twitch app keys, Twitch links also rank moments by the clips viewers already made. That's the strongest viral signal there is. From dev.twitch.tv/console → Applications → Manage.</p>
        <div class="field"><label>Client ID</label><input id="cid" value="${esc(s.twitch_client_id)}" autocomplete="off" spellcheck="false"></div>
        <div class="field"><label>Client Secret</label><input id="csec" type="password" autocomplete="new-password"
          placeholder="${s.twitch_client_secret_set ? "Saved. Leave blank to keep it" : "Paste your Client Secret"}"></div>
        <div style="display:flex;gap:8px"><button class="btn primary" id="save-tw">Save</button><button class="btn" id="test-tw">Test connection</button></div>
        <div id="tw-out" style="margin-top:12px;font-size:14px"></div>
      </div>
      <div class="card">
        <h2>AI clip judge <span class="pill ${s.anthropic_api_key_set ? "ok" : ""}" style="margin-left:6px">${s.anthropic_api_key_set ? "on" : "off"}</span></h2>
        <p class="note">Recommended. An AI model reads the top candidate moments like a clipper would, picks the ones that will actually go viral, and writes a hook title for each. Costs a few cents per video. Get a key at console.anthropic.com → API Keys.</p>
        <div class="field"><label>Anthropic API key</label><input id="akey" type="password" autocomplete="new-password"
          placeholder="${s.anthropic_api_key_set ? "Saved. Leave blank to keep it" : "sk-ant-…"}"></div>
        <div style="display:flex;gap:8px"><button class="btn primary" id="save-ai">Save</button><button class="btn" id="test-ai">Test connection</button></div>
        <div id="ai-out" style="margin-top:12px;font-size:14px"></div>
      </div>
      <div class="card" style="grid-column:1/-1">
        <h2>Users (${a.users.length})</h2>
        <div style="overflow-x:auto"><table>
          <tr><th>Email</th><th>Joined</th><th class="num">Videos</th><th class="num">Clips</th><th>Status</th><th></th></tr>
          ${a.users.map((u) => `<tr>
            <td>${esc(u.email)} ${u.is_admin ? '<span class="pill ok">admin</span>' : ""}</td>
            <td class="muted">${new Date(u.created_at).toLocaleDateString()}</td>
            <td class="num">${u.videos}</td><td class="num">${u.clips}</td>
            <td>${u.disabled ? '<span class="pill bad">disabled</span>' : '<span class="muted">active</span>'}</td>
            <td style="text-align:right;white-space:nowrap">${u.is_admin ? "" : `
              <button class="btn ghost" data-reset="${u.id}">Reset password</button>
              <button class="btn ghost ${u.disabled ? "" : "danger"}" data-toggle="${u.id}" data-off="${u.disabled}">${u.disabled ? "Enable" : "Disable"}</button>`}</td>
          </tr>`).join("")}
        </table></div>
      </div>
    </div>`;
  const save = async (body) => {
    try { await api("/api/admin/settings", { method: "POST", body }); toast("Saved"); viewAdmin(); }
    catch (e) { toast(e.message, true); }
  };
  $("#save-access").onclick = () => save({ invite_code: $("#invite").value, max_parallel_jobs: +$("#mpj").value,
    jobs_per_user: +$("#jpu").value, retention_days: +$("#ret").value, max_vod_hours: +$("#mvh").value });
  $("#save-tw").onclick = () => save({ twitch_client_id: $("#cid").value, twitch_client_secret: $("#csec").value });
  $("#save-ai").onclick = () => save({ anthropic_api_key: $("#akey").value });
  $("#test-ai").onclick = async () => {
    $("#ai-out").innerHTML = '<span class="muted">Testing…</span>';
    try { const r = await api("/api/admin/test-ai", { method: "POST" }); $("#ai-out").innerHTML = `<span style="color:var(--accent)">✓ ${esc(r.message)}</span>`; }
    catch (e) { $("#ai-out").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $("#test-tw").onclick = async () => {
    $("#tw-out").innerHTML = '<span class="muted">Testing…</span>';
    try { const r = await api("/api/admin/test-twitch", { method: "POST" }); $("#tw-out").innerHTML = `<span style="color:var(--accent)">✓ ${esc(r.message)}</span>`; }
    catch (e) { $("#tw-out").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  };
  $$("[data-toggle]").forEach((b) => b.onclick = async () => {
    await api(`/api/admin/users/${b.dataset.toggle}`, { method: "PATCH", body: { disabled: b.dataset.off !== "true" } }).catch((e) => toast(e.message, true));
    viewAdmin();
  });
  $$("[data-reset]").forEach((b) => b.onclick = async () => {
    const pw = prompt("New password for this user (8+ characters). Send it to them privately.");
    if (!pw) return;
    try { await api(`/api/admin/users/${b.dataset.reset}`, { method: "PATCH", body: { password: pw } }); toast("Password reset"); }
    catch (e) { toast(e.message, true); }
  });
}

/* ------------------------------------------------------------ account menu */
$("#menu-btn").onclick = (e) => { e.stopPropagation(); $("#menu").classList.toggle("open"); };
document.addEventListener("click", () => $("#menu").classList.remove("open"));
$("#logout").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); location.replace("/login.html"); };
$("#pw-open").onclick = () => {
  const d = $("#modal");
  d.innerHTML = `<form class="card" style="padding:24px;width:min(380px,92vw)" id="pwf">
    <h3 style="margin:0 0 16px">Change password</h3>
    <div class="field"><label>Current password</label><input id="pc" type="password" autocomplete="current-password"></div>
    <div class="field"><label>New password (8+ characters)</label><input id="pn" type="password" autocomplete="new-password"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end"><button type="button" class="btn ghost" id="pcancel">Cancel</button><button class="btn primary">Save</button></div></form>`;
  $("#pcancel").onclick = () => d.close();
  $("#pwf").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("/api/auth/password", { method: "POST", body: { current: $("#pc").value, new: $("#pn").value } }); toast("Password changed"); d.close(); }
    catch (err) { toast(err.message, true); }
  };
  d.showModal();
};

/* ------------------------------------------------------------ boot */
api("/api/me").then((u) => {
  me = u;
  $("#who").textContent = u.email;
  $("#admin-link").hidden = !u.is_admin;
  route();
}).catch(() => {});
