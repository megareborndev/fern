const titles = { overview: "Overview", licenses: "Licenses", referrals: "Referrals", trials: "Trials", changelog: "Changelog", accounts: "Accounts", status: "Status" };

document.querySelectorAll("[data-nav]").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("[data-nav]").forEach(b => b.classList.toggle("active", b === btn));
    const key = btn.dataset.nav;
    document.querySelectorAll("[data-pane]").forEach(p => p.classList.toggle("active", p.dataset.pane === key));
    document.querySelector("[data-pane-title]").textContent = titles[key];
  });
});

async function j(url, opts) {
  const r = await fetch(url, opts);
  let d = null;
  try { d = await r.json(); } catch (e) {}
  if (!r.ok) throw (d && d.error ? new Error(d.error) : new Error("HTTP " + r.status));
  return d;
}

function esc(s) { return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

function fmtDate(iso) {
  if (!iso) return "&mdash;";
  const d = new Date(iso);
  if (isNaN(d)) return esc(iso);
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

async function loadAll() {
  try {
    const data = await j("/api/admin/data");
    const s = data.stats || {};
    document.getElementById("stats").innerHTML =
      stat(s.total, "Total Licenses") +
      stat(s.active, "Active") +
      stat(s.bound, "Bound") +
      stat(s.trials, "Trial Keys") +
      stat(data.referrals ? data.referrals.length : 0, "Referrals") +
      stat(data.trials ? data.trials.length : 0, "Trials Claimed");

    document.getElementById("lic-rows").innerHTML = (data.licenses || [])
      .sort((a, b) => (b.created || "").localeCompare(a.created || ""))
      .map(l => `<tr>
        <td class="mono">${esc(l.key)}</td>
        <td>${l.active ? '<span class="badge active">Active</span>' : '<span class="badge inactive">Inactive</span>'}${l.trials ? ' <span class="badge trial">Trial</span>' : ''}</td>
        <td>${l.expires ? fmtDate(l.expires) : 'Lifetime'}</td>
        <td class="mono">${esc(l.bound_to || "&mdash;")}</td>
        <td>${fmtDate(l.created)}</td>
      </tr>`).join("") || '<tr><td class="empty" colspan="5">No licenses yet</td></tr>';

    document.getElementById("ref-rows").innerHTML = (data.referrals || [])
      .slice().reverse()
      .map(r => `<tr>
        <td class="mono">${esc(r.inviter_id)}</td>
        <td class="mono">${esc(r.invitee_id)}</td>
        <td>${esc(r.status)}</td>
        <td>${fmtDate(r.created_at)}</td>
      </tr>`).join("") || '<tr><td class="empty" colspan="4">No referrals yet</td></tr>';

    document.getElementById("trial-rows").innerHTML = (data.trials || [])
      .map(t => `<tr>
        <td class="mono">${esc(t.user_id)}</td>
        <td>${fmtDate(t.claimed_at)}</td>
        <td class="mono">${esc(t.license_key || "&mdash;")}</td>
      </tr>`).join("") || '<tr><td class="empty" colspan="3">No trials yet</td></tr>';
  } catch (e) {
    document.getElementById("stats").innerHTML = '<div class="msg err">' + esc(e.message) + '</div>';
  }
  loadLive();
  loadChangelog();
}

async function loadLive() {
  try {
    const d = await j("/api/admin/live");
    const peakTxt = d.peak_at
      ? new Date(d.peak_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
      : "&mdash;";
    document.getElementById("live-stats").innerHTML =
      stat(d.active, "Active Now") +
      stat(d.peak, "Peak (24h)") +
      stat(peakTxt, "Peak At");
    const entries = Object.entries(d.hourly || {}).sort((a, b) => a[0].localeCompare(b[0]));
    const max = Math.max(1, ...entries.map(e => e[1]));
    document.getElementById("live-hourly").innerHTML = entries.map(([hour, count]) =>
      `<div class="bar-wrap" title="${esc(hour)} — ${count}">
         <div class="bar ${count === d.peak ? 'hi' : ''}" style="height:${Math.round((count / max) * 100)}%"></div>
         <div class="bar-lbl">${hour.slice(11, 16)}</div>
       </div>`).join("") || '<div class="msg">No activity recorded yet</div>';
  } catch (e) {
    document.getElementById("live-stats").innerHTML = '<div class="msg err">' + esc(e.message) + '</div>';
  }
}

function stat(num, lbl) {
  return '<div class="stat"><div class="num">' + esc(num) + '</div><div class="lbl">' + esc(lbl) + '</div></div>';
}

async function createLicense() {
  const btn = document.getElementById("create-btn");
  const msg = document.getElementById("create-msg");
  const days = parseInt(document.getElementById("days").value || "0", 10);
  btn.disabled = true; msg.className = "msg";
  try {
    const d = await j("/api/admin/license", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ days }) });
    msg.className = "msg ok";
    msg.innerHTML = 'Created: <span class="mono">' + esc(d.license_key) + '</span> (' + days + 'd)';
    document.getElementById("days").value = "";
    loadAll();
  } catch (e) {
    msg.className = "msg err"; msg.textContent = e.message;
  } finally { btn.disabled = false; }
}

async function postChangelog() {
  const btn = document.getElementById("cl-btn");
  const msg = document.getElementById("cl-msg");
  const body = {
    version: document.getElementById("cl-version").value.trim(),
    title: document.getElementById("cl-title").value.trim(),
    body: document.getElementById("cl-body").value.trim(),
  };
  if (!body.title || !body.body) { msg.className = "msg err"; msg.textContent = "Title and body are required."; return; }
  btn.disabled = true; msg.className = "msg";
  try {
    await j("/api/admin/changelog", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) });
    msg.className = "msg ok"; msg.textContent = "Published.";
    document.getElementById("cl-version").value = "";
    document.getElementById("cl-title").value = "";
    document.getElementById("cl-body").value = "";
    loadChangelog();
  } catch (e) {
    msg.className = "msg err"; msg.textContent = e.message;
  } finally { btn.disabled = false; }
}

async function loadChangelog() {
  try {
    const data = await j("/api/changelog");
    const entries = (data.entries || []).slice().reverse();
    document.getElementById("cl-list").innerHTML = entries.map(e => `
      <div class="entry">
        <div class="head">
          <span class="title">${esc(e.title)}</span>
          ${e.version ? `<span class="ver">v${esc(e.version)}</span>` : ''}
          <span class="date">${fmtDate(e.created_at)}</span>
          <button class="del" onclick="deleteEntry('${esc(e.id)}')"><i class="fa-solid fa-trash"></i></button>
        </div>
        <div class="body">${esc(e.body)}</div>
      </div>`).join("") || '<p class="empty">No changelog entries yet.</p>';
  } catch (e) {
    document.getElementById("cl-list").innerHTML = '<p class="msg err">' + esc(e.message) + '</p>';
  }
}

async function deleteEntry(id) {
  if (!confirm("Delete this changelog entry?")) return;
  try { await j("/api/admin/changelog/" + encodeURIComponent(id), { method: "DELETE" }); loadChangelog(); }
  catch (e) { alert(e.message); }
}

const SERVICES = ["api", "ui", "discord"];
const SVC_LABELS = { api: "API", ui: "Fern", discord: "Discord" };

async function loadStatus() {
  try {
    const d = await j("/api/admin/status");
    const banner = d.banner || {};
    document.getElementById("st-banner-level").value = banner.level || "info";
    document.getElementById("st-banner-text").value = banner.text || "";
    const svc = d.services || {};
    document.getElementById("st-rows").innerHTML = SERVICES.map(key => {
      const s = svc[key] || {};
      const opts = ["", "up", "degraded", "down"].map(v =>
        `<option value="${v}" ${(s.override || "") === v ? "selected" : ""}>${v === "" ? "Auto" : v[0].toUpperCase() + v.slice(1)}</option>`
      ).join("");
      return `<tr>
        <td>${esc(SVC_LABELS[key] || key)}</td>
        <td><select data-svc="${key}" class="st-override">${opts}</select></td>
        <td><input data-note="${key}" class="st-note" type="text" value="${esc(s.note || "")}" placeholder="Optional note"></td>
      </tr>`;
    }).join("");
  } catch (e) {
    document.getElementById("st-rows").innerHTML = '<tr><td class="empty" colspan="3">' + esc(e.message) + '</td></tr>';
  }
}

async function saveStatus() {
  const btn = event.target;
  const msg = document.getElementById("st-msg");
  btn.disabled = true; msg.className = "msg";
  const services = {};
  SERVICES.forEach(key => {
    services[key] = {
      override: document.querySelector('.st-override[data-svc="' + key + '"]')?.value || "",
      note: document.querySelector('.st-note[data-note="' + key + '"]')?.value || "",
    };
  });
  try {
    await j("/api/admin/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        banner: {
          level: document.getElementById("st-banner-level").value,
          text: document.getElementById("st-banner-text").value,
        },
        services,
      }),
    });
    msg.className = "msg ok"; msg.textContent = "Status saved.";
  } catch (e) {
    msg.className = "msg err"; msg.textContent = e.message;
  } finally { btn.disabled = false; }
}

async function loadAccounts() {
  try {
    const d = await j("/api/admin/accounts");
    document.getElementById("acc-stats").innerHTML =
      stat(d.count || 0, "Accounts") + stat(d.accounts ? d.accounts.length : 0, "Linked Users");
    document.getElementById("acc-rows").innerHTML = (d.accounts || []).flatMap(a =>
      (a.licenses || []).map(l => `<tr>
        <td class="mono">${esc(a.user_id)}</td>
        <td class="mono">${esc(l.key)}</td>
        <td>${l.type === "trial" ? '<span class="badge trial">Trial</span>' : l.type === "lifetime" ? '<span class="badge active">Lifetime</span>' : '<span class="badge">Monthly</span>'}</td>
        <td>${l.expires ? fmtDate(l.expires) : 'Lifetime'}</td>
        <td>${l.active ? '<span class="badge active">Active</span>' : '<span class="badge inactive">Inactive</span>'}</td>
      </tr>`)
    ).join("") || '<tr><td class="empty" colspan="5">No bound accounts yet</td></tr>';
  } catch (e) {
    document.getElementById("acc-rows").innerHTML = '<tr><td class="empty" colspan="5">' + esc(e.message) + '</td></tr>';
  }
}

loadAll();
loadStatus();
loadAccounts();

window.loadAll = loadAll;
window.createLicense = createLicense;
window.postChangelog = postChangelog;
window.saveStatus = saveStatus;
window.deleteEntry = deleteEntry;