const ICONS = { api: "fa-solid fa-server", ui: "fa-solid fa-window-restore", discord: "fa-brands fa-discord" };
const LABELS = { api: "API", ui: "Fern", discord: "Discord" };
const STATUS_TEXT = { up: "Operational", degraded: "Degraded", down: "Down" };

function esc(s) { return String(s ?? "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

async function loadStatus() {
  try {
    const d = await fetch("/api/status").then(r => r.json());
    const svc = d.services || {};

    document.getElementById("services").innerHTML = Object.keys(svc).map(key => {
      const s = svc[key] || {};
      const st = s.status || "down";
      return `<div class="card">
        <div class="ico"><i class="${ICONS[key] || ICONS.api}"></i></div>
        <div class="body">
          <div class="name">${esc(s.label || LABELS[key] || key)} <span class="pill ${st}">${STATUS_TEXT[st] || esc(st)}</span></div>
          ${s.note ? `<div class="note">${esc(s.note)}</div>` : ""}
        </div>
        <div class="latency">${s.latency != null ? `<b>${esc(s.latency)}ms</b><br>response` : ""}</div>
      </div>`;
    }).join("");

    const banner = d.banner || {};
    const bEl = document.getElementById("banner");
    if (banner.text) {
      const level = banner.level || "info";
      bEl.className = "banner show " + level;
      bEl.innerHTML = `<i class="fa-solid ${level === "critical" ? "fa-triangle-exclamation" : level === "warn" ? "fa-triangle-exclamation" : "fa-circle-info"}"></i><div>${esc(banner.text)}</div>`;
    } else {
      bEl.className = "banner";
    }

    document.getElementById("updated").textContent = "Last checked: " + new Date(d.updated_at).toLocaleString();
  } catch (e) {
    document.getElementById("services").innerHTML = '<div class="banner show critical"><i class="fa-solid fa-triangle-exclamation"></i><div>Could not reach the status service.</div></div>';
  }
}

loadStatus();
setInterval(loadStatus, 30000);