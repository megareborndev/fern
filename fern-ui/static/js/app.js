import { api, ApiError } from "./api.js";
import { AppLayout, LoginError, SkeletonLayout, MessageListHtml, escapeHtml, initials, renderMarkdown, dotClass, authorName } from "./components.js?v=120";
import { addDmMessage, clearReply, selectDm, selectGuild, selectGuildChannel, setActivePanel, setDmMessages, setGuildChannels, setReadCursor, setState, state, subscribe, toggleFolder, addGuildToFolder, removeGuildFromFolder, createFolder, deleteFolder, renameFolder, getFolderForGuild, checkDeleteCooldown, updateMessageReaction } from "./state.js";
import { initContextMenuListeners, hideReplyBar } from "./context-menu.js";
import { joinVoiceCall, endVoiceCall } from "./voice.js";
import { parseTwemoji } from "./twemoji-util.js";

window.addEventListener('unhandledrejection', (event) => {
  console.error('[INIT] Unhandled rejection:', event.reason);
});

const root = document.querySelector("#app");
let elements = {};
let prevSelectedDmId = null;
let _pendingDmScroll = false;
let _lastMsgFingerprint = new Map();
let _prevLastMsgId = new Map();
let _lastMsgSent = 0;
let _suppressReadScrollUntil = 0;

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function closeMobileOverlays() {
  document.querySelectorAll('[data-mobile-sidebar-parent], [data-mobile-members-parent], [data-mobile-dms-parent]').forEach(el => { el.style.display = 'none'; });
}

function togglePricingSidebar() {
  const sidebar = document.querySelector('.pricing-page-sidebar');
  const main = document.querySelector('.pricing-page > .pricing-hero');
  const toggleBtn = document.querySelector('#pricing-mobile-toggle i');
  const page = document.querySelector('.pricing-page');
  
  if (!sidebar || !page) return;
  
  sidebar.classList.toggle('open');
  
  if (sidebar.classList.contains('open')) {
    page.classList.add('sidebar-open');
    if (toggleBtn) {
      toggleBtn.classList.remove('fa-bars');
      toggleBtn.classList.add('fa-xmark');
    }
    main.style.display = 'none';
  } else {
    page.classList.remove('sidebar-open');
    if (toggleBtn) {
      toggleBtn.classList.remove('fa-xmark');
      toggleBtn.classList.add('fa-bars');
    }
    main.style.display = 'block';
  }
}

const lottieScript = document.createElement("script");
lottieScript.src = "https://unpkg.com/@lottiefiles/lottie-player@latest/dist/lottie-player.js";
document.head.appendChild(lottieScript);

const socket = io();

function showRateLimitPopup(retryAfter = null) {
  document.querySelector("[data-rate-limit-modal]")?.remove();
  const seconds = Number.isFinite(Number(retryAfter)) ? Math.max(1, Math.ceil(Number(retryAfter))) : null;

  const overlay = document.createElement("div");
  overlay.dataset.rateLimitModal = "";
  overlay.innerHTML = `
    <style>
      @keyframes discordOverlayIn { from { opacity: 0; } to { opacity: 1; } }
      @keyframes discordModalIn { from { opacity: 0; transform: scale(.88) translateY(12px); } to { opacity: 1; transform: scale(1) translateY(0); } }
      @keyframes discordOverlayOut { from { opacity: 1; } to { opacity: 0; } }
      @keyframes discordModalOut { from { opacity: 1; transform: scale(1) translateY(0); } to { opacity: 0; transform: scale(.92) translateY(8px); } }
      [data-rate-limit-backdrop] { animation: discordOverlayIn 160ms ease-out forwards; }
      [data-rate-limit-dialog] { animation: discordModalIn 220ms cubic-bezier(.2, .9, .3, 1.15) forwards; transform-origin: center; }
      [data-rate-limit-backdrop].closing { animation: discordOverlayOut 140ms ease-in forwards; }
      [data-rate-limit-backdrop].closing [data-rate-limit-dialog] { animation: discordModalOut 140ms ease-in forwards; }
      [data-close-rate-limit] { transition: background-color 120ms ease, transform 100ms ease, box-shadow 120ms ease; }
      [data-close-rate-limit]:hover { background: var(--accent-hover) !important; box-shadow: 0 4px 12px rgba(0, 0, 0, .25); }
      [data-close-rate-limit]:active { transform: scale(.96); }
      [data-close-rate-limit]:focus-visible { outline: 2px solid #ffffff; outline-offset: 2px; }
      @media (prefers-reduced-motion: reduce) {
        [data-rate-limit-backdrop], [data-rate-limit-dialog],
        [data-rate-limit-backdrop].closing, [data-rate-limit-backdrop].closing [data-rate-limit-dialog] { animation: none; }
      }
    </style>
    <div data-rate-limit-backdrop style="
      position: fixed; inset: 0; z-index: 999999;
      display: flex; align-items: center; justify-content: center; padding: 24px;
      background: rgba(0, 0, 0, .72); backdrop-filter: blur(2px);
    ">
      <div data-rate-limit-dialog role="dialog" aria-modal="true" aria-labelledby="rate-limit-title" style="
        width: 440px; max-width: 92vw; overflow: hidden; color: var(--text-normal);
        background: var(--bg-1); border: 1px solid rgba(255, 255, 255, .05);
        border-radius: 8px;
        box-shadow: 0 18px 48px rgba(0, 0, 0, .5), 0 0 0 1px rgba(0, 0, 0, .2);
        font-family: 'gg sans', Whitney, 'Helvetica Neue', Helvetica, Arial, sans-serif;
      ">
        <div style="display: flex; gap: 16px; align-items: flex-start; padding: 24px;">
          <div style="
            display: flex; flex: 0 0 42px; align-items: center; justify-content: center;
            width: 42px; height: 42px; color: #fff; font-size: 22px; font-weight: 700;
            background: #f23f42; border-radius: 50%; box-shadow: 0 4px 12px rgba(242, 63, 66, .3);
          ">!</div>
          <div>
            <h2 id="rate-limit-title" style="margin: 0 0 8px; color: var(--text-primary); font-size: 20px; line-height: 1.2;">
              You are getting rate limited
            </h2>
            <p style="margin: 0; color: var(--text-secondary); font-size: 14px; line-height: 1.5;">
              ${seconds ? `Please wait ${seconds} second${seconds === 1 ? "" : "s"} before sending another message.` : "Please wait a moment before sending another message."}
            </p>
          </div>
        </div>
        <div style="display: flex; justify-content: flex-end; padding: 16px; background: var(--bg-2);">
          <button type="button" data-close-rate-limit style="
            min-width: 96px; padding: 10px 18px; color: #fff; font: inherit; font-weight: 500;
            cursor: pointer; background: var(--accent); border: 0; border-radius: 4px;
          ">Okay</button>
        </div>
      </div>
    </div>
  `;
  closeMobileOverlays();
  document.body.appendChild(overlay);

  const backdrop = overlay.querySelector("[data-rate-limit-backdrop]");
  const dialog = overlay.querySelector("[data-rate-limit-dialog]");
  const closeButton = overlay.querySelector("[data-close-rate-limit]");
  let closing = false;

  const close = () => {
    if (closing) return;
    closing = true;
    backdrop.classList.add("closing");
    document.removeEventListener("keydown", handleKeydown);
    setTimeout(() => { overlay.remove(); }, 150);
  };

  const handleKeydown = event => {
    if (event.key === "Escape") close();
  };

  closeButton.addEventListener("click", close);
  backdrop.addEventListener("click", event => {
    if (event.target === backdrop) close();
  });
  dialog.addEventListener("click", event => { event.stopPropagation(); });
  document.addEventListener("keydown", handleKeydown);
  requestAnimationFrame(() => { closeButton.focus(); });
}

function isCaptchaError(err) {
  if (!err) return false;
  if (err.status === 428) return true;
  if (err.data?.captcha_required || err.data?.error === "captcha_required" || err.data?.error === "first_message_required") return true;
  const msg = String(err.data?.message || err.message || "").toLowerCase();
  if (msg.includes("captcha") || msg.includes("verification") || msg.includes("original client") || msg.includes("precondition")) return true;
  return false;
}

function cacheElements() {
  elements = {
    serverRail: root.querySelector("aside"),
    sidebarChat: root.querySelector(".sidebar-chat"),
    rightsideArea: root.querySelector(".rightside-area"),
    messageList: root.querySelector(".message-list"),
    messageComposer: root.querySelector(".message-composer"),
  };
}

let fullRendered = false;

function routeFromPath(pathname = window.location.pathname) {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] === "settings") return { type: "settings" };
  if (parts[0] === "pricing") return { type: "pricing" };
  if (parts[0] !== "channels") return { type: "home" };
  if (parts[1] === "@me" && parts[2]) {
    const dmId = parts[2];
    if (/^\d{17,20}$/.test(dmId)) {
      return { type: "dm", id: dmId };
    }
    return { type: "home" };
  }
  if (parts[1] === "@me") return { type: "home" };
  if (parts[1]) return { type: "guild", id: parts[1], channelId: parts[2] || parts[1] };
  return { type: "home" };
}

async function loadGuildChannels(guildId) {
  if (!guildId) return;
  if (state.channelsByGuild[guildId]) return;
  setState({ channelsLoading: true });
  try {
    const payload = await api.guildChannels(guildId);
    setGuildChannels(guildId, payload);
    if (payload.roles && payload.roles.length > 0) {
      setState({ rolesByGuild: { ...(state.rolesByGuild || {}), [guildId]: payload.roles } });
    }
  } catch {
  } finally {
    setState({ channelsLoading: false });
  }
}

function isUserMentioned(msg, user) {
  if (!msg || !user) return false;
  const myId = String(user.id || "");
  const authorId = String(msg.author_id || (typeof msg.author === "object" ? msg.author?.id : "") || "");
  if (myId && authorId === myId) return false;

  if (Array.isArray(msg.mentions)) {
    const hasId = msg.mentions.some(m => {
      const id = typeof m === "object" ? String(m.id || "") : String(m);
      return id === myId;
    });
    if (hasId) return true;
  }

  const text = String(msg.content || "").toLowerCase();
  if (myId && (text.includes(`<@${myId}>`) || text.includes(`<@!${myId}>`))) return true;
  if (text.includes("@everyone") || text.includes("@here")) return true;

  const uname = (user.username || "").toLowerCase();
  const dname = (user.displayName || "").toLowerCase();
  const aname = (localStorage.getItem("fern_alias_name") || "").toLowerCase();

  if (uname && uname.length >= 2 && text.includes(`@${uname}`)) return true;
  if (dname && dname.length >= 2 && dname !== uname && text.includes(`@${dname}`)) return true;
  if (aname && aname.length >= 2 && text.includes(`@${aname}`)) return true;

  return false;
}

function applyRoute() {
  const route = routeFromPath();
  if (route.type === "settings") {
    setActivePanel("settings");
    history.replaceState({}, "", "/settings");
    return;
  }
  if (route.type === "pricing") {
    setActivePanel("pricing");
    return;
  }
  if (route.type === "guild") {
    selectGuild(route.id);
    loadGuildChannels(route.id);
    if (route.channelId && route.channelId !== route.id) {
      selectGuildChannel(route.channelId);
      loadMessages(route.channelId);
      const newChanMentions = { ...state.channelMentions, [route.channelId]: 0 };
      const newChanUnreads = { ...state.channelUnreads, [route.channelId]: false };
      const gChs = state.channelsByGuild[route.id]?.text || [];
      const gHasUnread = gChs.some(c => c.id !== route.channelId && newChanUnreads[c.id]);
      let gMentionCount = 0;
      for (const c of gChs) {
        if (c.id !== route.channelId) gMentionCount += (newChanMentions[c.id] || 0);
      }
      setState({
        channelMentions: newChanMentions,
        channelUnreads: newChanUnreads,
        guildUnreads: { ...state.guildUnreads, [route.id]: gHasUnread },
        guildMentions: { ...state.guildMentions, [route.id]: gMentionCount },
      });
    } else {
      setState({
        guildUnreads: { ...state.guildUnreads, [route.id]: false },
        guildMentions: { ...state.guildMentions, [route.id]: 0 },
      });
    }
  }
  if (route.type === "dm") {
    selectDm(route.id);
    loadMessages(route.id);
    refreshFriends();
    const updated = (state.dms || []).map(d => d.id === route.id ? { ...d, unread: false } : d);
    const newDmMentions = { ...state.dmMentions, [route.id]: 0 };
    setState({ dms: updated, dmMentions: newDmMentions });
    const dm = (state.dms || []).find(d => d.id === route.id);
    if (dm && dm.lastMessageId) {
      api.markRead(route.id, dm.lastMessageId);
      setReadCursor(route.id, dm.lastMessageId);
    }
  }
  if (route.type === "home") setState({ selectedGuildId: null, selectedDmId: null, selectedChannelId: null, activePanel: null });
}

let dmPollTimer = null;
let isPolling = false;
let pendingDmId = null;
let lastUnreadDmIds = new Set();

function stopDmPolling() {
  if (dmPollTimer) {
    clearInterval(dmPollTimer);
    dmPollTimer = null;
  }
}

let licenseCheckTimer = null;
let licenseKickTriggered = false;

function startLicenseExpiryCheck() {
  if (licenseCheckTimer) return;
  const check = async () => {
    if (licenseKickTriggered) return;
    try {
      const res = await api.licenseStatus();
      if (!res?.is_fern_user || !res.expires) return;
      const expiryTime = new Date(res.expires).getTime();
      if (isNaN(expiryTime)) return;
      if (Date.now() > expiryTime) {
        licenseKickTriggered = true;
        clearInterval(licenseCheckTimer);
        window.location.assign("/logout?error=" + encodeURIComponent(res.trial ? "Your trial license has expired. Please purchase a license key." : "Your Fern license has expired."));
      }
    } catch {
    }
  };
  check();
  licenseCheckTimer = setInterval(check, 10000);
}

function startDmPolling() {
  if (dmPollTimer) return;
  dmPollTimer = setInterval(refreshActiveDmMessages, 2000);
}

async function refreshActiveDmMessages() {
  const activeId = state.selectedDmId || state.selectedChannelId;
  if (!activeId || !/^\d{17,20}$/.test(activeId)) return;
  if (isPolling) return;
  isPolling = true;
  try {
    const payload = await api.messages(activeId);
    if ((state.selectedDmId || state.selectedChannelId) !== activeId) return;
    const newMessages = payload.items || [];
    const existing = state.messagesByDm[activeId] || [];
    const existingSig = existing.map(m => m.id + ":" + m.content).join("|");
    const existingIds = new Set(existing.map((m) => m.id));
    const myId = state.user?.id;
    const locallyDeletedId = checkDeleteCooldown(activeId);
    const merged = [];
    let mentionMsg = null;
    for (const msg of newMessages) {
      if (msg.id === locallyDeletedId) continue;
      merged.push(msg);
      if (!existingIds.has(msg.id) && isUserMentioned(msg, state.user)) {
        mentionMsg = msg;
      }
    }
    merged.sort((a, b) => (a.createdAt || "").localeCompare(b.createdAt || ""));
    const mergedSig = merged.map(m => m.id + ":" + m.content).join("|");
    const hasChanges = existingSig !== mergedSig || existing.length !== merged.length;
    const hasNewMessages = merged.length > existing.length;
    if (hasChanges) {
      for (const msg of merged) {
        if (!existingIds.has(msg.id)) {
          const aId = msg.author_id || (typeof msg.author === "object" ? msg.author?.id : null);
          clearTypingUser(activeId, aId);
          if (msg.author_id !== myId && msg.author !== "me") {
            clearTypingUser(activeId);
          }
        }
      }
      setDmMessages(activeId, merged);
      updateTypingUI();
    }
    if (hasNewMessages) {
      promoteActiveDm();
      if (mentionMsg) {
        playPingSound();
        const route = state.selectedGuildId ? `/channels/${state.selectedGuildId}/${activeId}` : `/channels/@me/${activeId}`;
        pushNotification({
          title: `${mentionMsg.authorDisplay || mentionMsg.author || "someone"} mentioned you`,
          description: mentionMsg.content || "New message",
          avatarUrl: mentionMsg.authorAvatar,
          actionLabel: "View",
          onClick: () => goToRoute(route),
        });
      }
    }
  } catch (err) {
    if (err.status === 401) {
      console.warn('[POLL] Session expired during poll');
    }
  } finally {
    isPolling = false;
  }
}

async function refreshFriends() {
  try {
    const friends = await api.friends();
    setState({ friends });
  } catch {
    // ignore
  }
}

function promoteActiveDm() {
  if (!state.selectedDmId) return;
  var dms = state.dms.slice();
  var idx = dms.findIndex(function(d) { return d.id === state.selectedDmId; });
  if (idx > 0) {
    var dm = dms.splice(idx, 1)[0];
    dms.unshift(dm);
    setState({ dms: dms });
  }
}

async function refreshDms() {
  try {
    const payload = await api.dms();
    setState({ dms: payload.items || [], dmReason: payload.reason || "" });
  } catch {
    // ignore
  }
}

function updateServerRail(appState) {
  if (!elements.serverRail) return;
  const railHTML = AppLayout.serverRail(appState);
  if (elements.serverRail.innerHTML !== railHTML) {
    const temp = document.createElement("div");
    temp.innerHTML = railHTML;
    const oldNavGroup = elements.serverRail.querySelector(".rail-nav-group");
    const newNavGroup = temp.querySelector(".rail-nav-group");
    const oldBottom = elements.serverRail.querySelector(".rail-bottom-group");
    const newBottom = temp.querySelector(".rail-bottom-group");
    if (oldNavGroup && newNavGroup && oldBottom && newBottom) {
      if (oldNavGroup.innerHTML !== newNavGroup.innerHTML) {
        oldNavGroup.innerHTML = newNavGroup.innerHTML;
      }
      const oldAvatar = oldBottom.querySelector(".rail-user-avatar");
      const newAvatar = newBottom.querySelector(".rail-user-avatar");
      if (oldAvatar && newAvatar && oldAvatar.innerHTML !== newAvatar.innerHTML) {
        oldAvatar.innerHTML = newAvatar.innerHTML;
      }
      const oldEnc = oldBottom.querySelector("#enc-toggle");
      const newEnc = newBottom.querySelector("#enc-toggle");
      if (oldEnc && newEnc && oldEnc.innerHTML !== newEnc.innerHTML) {
        oldEnc.innerHTML = newEnc.innerHTML;
      }
    } else {
      elements.serverRail.innerHTML = railHTML;
    }
  }
  updateEncToggleIcons();
  setupRailInteractions();
}

function setupRailInteractions() {
  const rail = elements.serverRail;
  if (!rail) return;

  rail.querySelectorAll(".server-icon").forEach(icon => {
    icon.draggable = true;
    icon.addEventListener("dragstart", (e) => {
      const guildId = icon.dataset.guildId;
      if (guildId) {
        e.dataTransfer.setData("text/plain", guildId);
        e.dataTransfer.effectAllowed = "move";
        icon.classList.add("dragging");
      }
    });
    icon.addEventListener("dragend", () => {
      icon.classList.remove("dragging");
      rail.querySelectorAll(".drag-over").forEach(el => el.classList.remove("drag-over"));
    });
  });

  rail.querySelectorAll(".server-icon, .rail-folder.collapsed").forEach(el => {
    el.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      el.classList.add("drag-over");
    });
    el.addEventListener("dragleave", () => {
      el.classList.remove("drag-over");
    });
    el.addEventListener("drop", (e) => {
      e.preventDefault();
      el.classList.remove("drag-over");
      const draggedGuildId = e.dataTransfer.getData("text/plain");
      if (!draggedGuildId) return;

      if (el.classList.contains("rail-folder") && el.classList.contains("collapsed")) {
        const folderId = el.dataset.folderToggle;
        if (folderId) addGuildToFolder(draggedGuildId, folderId);
      } else if (el.classList.contains("server-icon")) {
        const targetGuildId = el.dataset.guildId;
        if (targetGuildId && targetGuildId !== draggedGuildId) {
          removeGuildFromFolder(draggedGuildId);
          removeGuildFromFolder(targetGuildId);
          const existing = state.folders.find(f => f.guildIds.length === 0);
          if (existing) {
            addGuildToFolder(draggedGuildId, existing.id);
            addGuildToFolder(targetGuildId, existing.id);
          } else {
            createFolder("New Folder");
            const newFolder = state.folders[state.folders.length - 1];
            if (newFolder) {
              addGuildToFolder(draggedGuildId, newFolder.id);
              addGuildToFolder(targetGuildId, newFolder.id);
            }
          }
        }
      }
    });
  });
}

let prevSelectionKey = "";

function selectionKey(appState) {
  return `${appState.selectedGuildId || ""}|${appState.selectedDmId || ""}|${appState.selectedChannelId || ""}|${appState.activePanel || ""}`;
}

function updateSidebar(appState) {
  if (!elements.sidebarChat) return;
  const focused = document.activeElement;
  if (focused && focused.closest('[data-friend-form]')) return;
  const sidebarHTML = AppLayout.sidebar(appState);
  if (elements.sidebarChat.innerHTML !== sidebarHTML) {
    const key = selectionKey(appState);
    const viewChanged = key !== prevSelectionKey;
    elements.sidebarChat.innerHTML = sidebarHTML;
    if (viewChanged) {
      elements.sidebarChat.classList.remove('sidebar-enter');
      void elements.sidebarChat.offsetWidth;
      elements.sidebarChat.classList.add('sidebar-enter');
    }
    prevSelectionKey = key;
  }
}

function formatTypingText(names) {
  if (!names || names.length === 0) return '';
  if (names.length === 1) return `<strong>${escapeHtml(names[0])}</strong> is typing...`;
  if (names.length === 2) return `<strong>${escapeHtml(names[0])}</strong> and <strong>${escapeHtml(names[1])}</strong> are typing...`;
  if (names.length === 3) return `<strong>${escapeHtml(names[0])}</strong>, <strong>${escapeHtml(names[1])}</strong>, and <strong>${escapeHtml(names[2])}</strong> are typing...`;
  return 'Several people are typing...';
}

function updateTypingUI() {
  const indicator = document.getElementById('typing-indicator');
  if (!indicator) return;
  const activeId = state.selectedDmId || state.selectedChannelId;
  const channelTyping = (activeId && state.typingByChannel?.[activeId]) || {};
  const names = Object.values(channelTyping).map(u => u.username).filter(Boolean);
  const textEl = indicator.querySelector('.typing-text');
  if (names.length > 0) {
    if (textEl) textEl.innerHTML = formatTypingText(names);
    indicator.classList.add('active');
  } else {
    indicator.classList.remove('active');
    if (textEl) textEl.innerHTML = '';
  }
}

function clearTypingUser(channelId, userId) {
  if (!channelId || !state.typingByChannel?.[channelId]) return;
  let cur = { ...state.typingByChannel[channelId] };
  let changed = false;
  if (userId) {
    const uIdStr = String(userId);
    for (const k of Object.keys(cur)) {
      if (k === uIdStr || cur[k]?.username === userId || cur[k]?.userId === uIdStr) {
        if (cur[k]?.timeoutId) clearTimeout(cur[k].timeoutId);
        delete cur[k];
        changed = true;
      }
    }
  } else {
    for (const k of Object.keys(cur)) {
      if (cur[k]?.timeoutId) clearTimeout(cur[k].timeoutId);
    }
    cur = {};
    changed = true;
  }
  if (changed) {
    const next = { ...state.typingByChannel };
    if (Object.keys(cur).length === 0) {
      delete next[channelId];
    } else {
      next[channelId] = cur;
    }
    setState({ typingByChannel: next });
    updateTypingUI();
  }
}

function updateChatArea(appState) {
  if (!elements.rightsideArea) return;
  const focused = document.activeElement;
  if (focused && focused.closest('[data-friend-form]')) return;
  const key = selectionKey(appState);
  const viewChanged = key !== prevSelectionKey;
  if (viewChanged) {
    elements.rightsideArea.innerHTML = AppLayout.chatArea(appState);
    elements.rightsideArea.classList.remove('view-enter');
    void elements.rightsideArea.offsetWidth;
    elements.rightsideArea.classList.add('view-enter');
    prevSelectionKey = key;
    _lastMsgFingerprint.delete(key);
  } else if (elements.rightsideArea.querySelector('.message-list')) {
    updateMessageList(appState);
  } else {
    const chatHTML = AppLayout.chatArea(appState);
    if (elements.rightsideArea.innerHTML !== chatHTML) {
      elements.rightsideArea.innerHTML = chatHTML;
    }
  }
  updateTypingUI();
}

function render(appState) {
  window._CURRENT_STATE = appState;
  updateServerRail(appState);
  updateSidebar(appState);
  updateChatArea(appState);
  updateTypingUI();
  hydrateInviteCards();
  const listEl = document.querySelector('.message-list');
  if (listEl) {
    const nearBottom = listEl.scrollTop + listEl.clientHeight >= listEl.scrollHeight - 200;
    if (nearBottom) {
      listEl.scrollTop = listEl.scrollHeight;
    }
  }
}

async function loadMessages(channelId) {
  if (!channelId || !/^\d{17,20}$/.test(channelId)) return;
  const loadId = channelId;
  pendingDmId = loadId;
  setState({ messagesLoading: true, messageError: "" });
  try {
    const payload = await api.messages(channelId);
    if (pendingDmId !== loadId) return;
    const items = (payload.items || []).reverse();
    setDmMessages(channelId, items);
    if (items.length > 0) {
      const lastMsg = items[items.length - 1];
      if (lastMsg && lastMsg.id) {
        api.markRead(channelId, lastMsg.id);
        setReadCursor(channelId, lastMsg.id);
      }
    } else {
      syncReadCursor(channelId);
    }
    const updated = (state.dms || []).map(d => d.id === channelId ? { ...d, unread: false } : d);
    setState({ dms: updated });
  } catch (error) {
    if (pendingDmId === loadId) {
      const msg = error instanceof ApiError && error.status === 403
        ? "You cannot send messages in this channel."
        : "Messages could not be loaded.";
      setState({ messageError: msg });
    }
  } finally {
    if (pendingDmId === loadId) {
      pendingDmId = null;
      setState({ messagesLoading: false });
    }
  }
}

async function syncReadCursor(channelId) {
  if (!channelId || !/^\d{17,20}$/.test(channelId)) return;
  try {
    const res = await api.readCursor(channelId);
    if ((state.selectedDmId || state.selectedChannelId) === channelId) {
      setReadCursor(channelId, res?.last_message_id || "");
    }
  } catch {
    // ignore
  }
}

async function markActiveChannelRead(channelId) {
  if (!channelId || !/^\d{17,20}$/.test(channelId)) return;
  const messages = state.messagesByDm[channelId] || [];
  const lastMsg = [...messages].reverse().find(m => m && m.id && m.type !== 6 && m.type !== 2 && m.type !== 1 && m.type !== 67);
  if (!lastMsg) return;
  setReadCursor(channelId, lastMsg.id);
  try {
    await api.markRead(channelId, lastMsg.id);
  } catch {
    // ignore
  }
}

let _lastTypingSent = 0;
document.addEventListener("input", (e) => {
  const textarea = e.target.closest(".message-composer textarea");
  if (!textarea) return;
  const form = textarea.closest("form[data-message-form]");
  const channelId = form?.dataset?.dmId;
  if (!channelId) return;
  const now = Date.now();
  if (now - _lastTypingSent > 7000) {
    _lastTypingSent = now;
    api.typing(channelId);
  }
});

document.addEventListener("keydown", (e) => {
  const textarea = e.target.closest(".message-composer textarea");
  if (!textarea) return;
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const form = textarea.closest("form[data-message-form]");
    if (form) form.requestSubmit();
  }
});

document.addEventListener("scroll", (e) => {
  if (Date.now() < _suppressReadScrollUntil) return;
  const listEl = e.target && e.target.classList && e.target.classList.contains("message-list") ? e.target : null;
  if (!listEl) return;
  const activeId = state.selectedDmId || state.selectedChannelId;
  if (!activeId || !/^\d{17,20}$/.test(activeId)) return;
  if (listEl.scrollTop + listEl.clientHeight >= listEl.scrollHeight - 200) {
    markActiveChannelRead(activeId);
  }
}, true);

function initialRender(appState) {
  console.log('[INIT] Rendering UI');
  if (appState.loading) {
    root.innerHTML = SkeletonLayout();
    cacheElements();
    return;
  }
  if (appState.error) {
    root.innerHTML = LoginError(appState.error);
    cacheElements();
    return;
  }
  root.innerHTML = AppLayout.full(appState);
  cacheElements();
  console.log('[INIT] Cached DOM elements');
}

async function loadApplicationData() {
  console.log('[INIT] Starting application');
  setState({ loading: true, error: "" });
  try {
    console.log('[INIT] Loading application data');
    const [user, guilds, dmsPayload, friends] = await Promise.all([
      api.me(),
      api.guilds(),
      api.dms(),
      api.friends(),
    ]);
    setState({
      user,
      guilds,
      dms: dmsPayload.items || [],
      dmReason: dmsPayload.reason || "",
      friends,
      presences: user?.status ? { ...state.presences, [user.id]: user.status } : {},
      loading: false,
    });
    console.log('[INIT] State initialized');
    applyRoute();
  } catch (error) {
    console.error('[INIT] Load failed:', error);
    setState({
      loading: false,
      error: `Discord data could not be loaded: ${error.message || "Unknown error"}`,
    });
  }
}

document.addEventListener("click", async (event) => {
  if (event.target.closest("#fern-voice-panel")) {
    return;
  }
  const logoutBtn = event.target.closest('a[href="/logout"]');
  if (logoutBtn) {
    event.preventDefault();
    showConfirmModal("Log Out", "Are you sure you want to log out?", "Log Out")
      .then(function(confirmed) {
        if (confirmed) navigateToLogout();
      });
    return;
  }
  const settingsBtn = event.target.closest('[data-open-discord="settings"]');
  if (settingsBtn) {
    event.preventDefault();
    showSettingsModal();
    return;
  }
  const pricingBtn = event.target.closest('[data-open-fern-pricing]');
  if (pricingBtn) {
    event.preventDefault();
    history.pushState({}, "", "/pricing");
    applyRoute();
    return;
  }
  const shopSoonBtn = event.target.closest('[data-shop-soon]');
  if (shopSoonBtn) {
    event.preventDefault();
    showToast("Shop coming soon");
    return;
  }
  const homepageBtn = event.target.closest('[data-homepage]');
  if (homepageBtn) {
    event.preventDefault();
    const fade = document.getElementById("page-fade");
    if (fade) {
      fade.classList.add("active");
      setTimeout(() => { window.location.href = "/"; }, 320);
    } else {
      window.location.href = "/";
    }
    return;
  }
  const firstMessageBtn = event.target.closest("[data-first-message-discord]");
  if (firstMessageBtn) {
    event.preventDefault();
    window.open("https://discord.com/app", "_blank");
    return;
  }
  const discordBtn = event.target.closest("[data-open-discord]");
  if (discordBtn) {
    event.preventDefault();
    showConfirmModal("Are you sure you want to leave?", "This will open Discord\u2019s original client.", "Leave")
      .then(function(confirmed) {
        if (!confirmed) return;
        var opened = false;
        function tryOpen() {
          if (opened) return;
          opened = true;
          window.open("https://discord.com/app", "_blank");
        }
        var win = window.open("discord://channels/@me", "_blank");
        if (!win || win.closed) {
          tryOpen();
        } else {
          setTimeout(tryOpen, 2000);
        }
      });
    return;
  }
  const msgLink = event.target.closest("a.msg-link");
  if (msgLink) {
    event.preventDefault();
    showConfirmModal("Leaving Fern", "This will take you away from Fern to an external site. Do you want to continue?", "Continue")
      .then(function(confirmed) {
        if (confirmed) window.open(msgLink.href, "_blank");
      });
    return;
  }
  const voiceBtn = event.target.closest("[data-voice-channel]");
  if (voiceBtn) {
    event.preventDefault();
    const chId = voiceBtn.getAttribute("data-voice-channel");
    if (chId) {
      voiceBtn.style.color = "var(--accent)";
      const chName = voiceBtn.querySelector("p")?.textContent || voiceBtn.querySelector(".channel-name")?.textContent || "Voice Channel";
      (window.FernVoice?.joinVoiceCall || joinVoiceCall)(chId, chName);
      showToast("Connecting to voice call...");
    }
    return;
  }
  const ringBtn = event.target.closest("[data-ring-channel]");
  const phoneIcon = event.target.closest(".fa-phone, .fa-phone-flip, .fa-phone-volume");
  if (ringBtn || phoneIcon) {
    event.preventDefault();
    const targetEl = ringBtn || phoneIcon.closest("button, div, svg, a") || phoneIcon;
    const channelId = targetEl?.getAttribute("data-ring-channel") || targetEl?.getAttribute("data-voice-channel") || targetEl?.getAttribute("data-channel-id") || state.selectedDmId || state.selectedChannelId;
    if (channelId) {
      if (targetEl && targetEl.style) targetEl.style.color = "var(--accent)";
      const dm = state.dms?.find(d => d.id === channelId);
      const ch = state.channelsByGuild?.[state.selectedGuildId]?.text?.find(c => c.id === channelId) || state.channelsByGuild?.[state.selectedGuildId]?.voice?.find(c => c.id === channelId);
      const title = dm?.displayName || dm?.username || ch?.name || "Voice Call";
      (window.FernVoice?.joinVoiceCall || joinVoiceCall)(channelId, title);
      showToast("Starting voice call...");
    }
    return;
  }
  const folderToggle = event.target.closest("[data-folder-toggle]");
  if (folderToggle) {
    event.preventDefault();
    toggleFolder(folderToggle.dataset.folderToggle);
    return;
  }
  const joinBtn = event.target.closest("[data-join-guild]");
  if (joinBtn) {
    event.preventDefault();
    showJoinGuildModal();
    return;
  }
  const joinInviteBtn = event.target.closest("[data-join-invite]");
  if (joinInviteBtn) {
    event.preventDefault();
    const code = joinInviteBtn.getAttribute("data-join-invite");
    if (!code || joinInviteBtn.disabled) return;
    joinInviteBtn.disabled = true;
    joinInviteBtn.textContent = "Joining...";
    try {
      await api.joinGuild(code);
      const guilds = await api.guilds();
      setState({ guilds });
      joinInviteBtn.textContent = "Joined";
      joinInviteBtn.classList.add("joined");
      showToast("Server joined");
    } catch (err) {
      joinInviteBtn.disabled = false;
      joinInviteBtn.textContent = "Join";
      if (isCaptchaError(err)) {
        showCaptchaFallback("Complete this action on the original Discord client.");
      } else {
        showToast(err.data?.error || err.message || "Failed to join");
      }
    }
    return;
  }
  const encBtn = event.target.closest("#enc-toggle");
  if (encBtn) {
    const next = !state.encryptionEnabled;
    try { localStorage.setItem("fern_encryption_enabled", next ? "1" : "0"); } catch {}
    setState({ encryptionEnabled: next });
    document.querySelectorAll('#enc-toggle').forEach(el => {
      el.classList.toggle("active", next);
    });
    showToast(next ? "Encryption enabled" : "Encryption disabled");
    return;
  }
  const createGroupBtn = event.target.closest("[data-create-group]");
  if (createGroupBtn) {
    event.preventDefault();
    showCreateGroupModal();
    return;
  }
  const addMemberBtn = event.target.closest("[data-add-group-member]");
  if (addMemberBtn) {
    event.preventDefault();
    showAddGroupMemberModal(addMemberBtn.dataset.addGroupMember);
    return;
  }
  const leaveGroupBtn = event.target.closest("[data-leave-group]");
  if (leaveGroupBtn) {
    event.preventDefault();
    const groupId = leaveGroupBtn.dataset.leaveGroup;
    if (groupId) leaveGroup(groupId);
    return;
  }
  const kickBtn = event.target.closest("[data-kick-group-member]");
  if (kickBtn) {
    event.preventDefault();
    event.stopPropagation();
    const userId = kickBtn.dataset.kickGroupMember;
    const member = (state.membersByChannel[state.selectedDmId] || []).find(m => m.id === userId);
    const name = member?.displayName || member?.username || "this member";
    showConfirmModal("Kick Member", `Are you sure you want to remove ${escapeHtml(name)} from the group?`, "Kick Member")
      .then(function(confirmed) {
        if (!confirmed) return;
        api.removeGroupMember(state.selectedDmId, userId)
          .then(function() {
            showToast(`${name} removed from the group`);
            loadMembers(state.selectedDmId, state);
          })
          .catch(function(e) {
            showToast(e.data?.error || e.message || "Failed to remove member");
          });
      });
    return;
  }
  const profileBtn = event.target.closest("[data-author-id], .member-item");
  if (profileBtn) {
    if (event.target.closest(".group-member-kick")) return;
    const userId = profileBtn.getAttribute("data-author-id") || profileBtn.getAttribute("data-user-id") || profileBtn.closest("[data-user-id]")?.getAttribute("data-user-id");
    if (userId) showProfilePopup(userId, event);
    return;
  }
});

function showConfirmModal(title, text, confirmLabel) {
  return new Promise(function(resolve) {
    var modal = document.getElementById("confirm-modal");
    if (!modal) { resolve(false); return; }
    document.getElementById("modal-title").textContent = title;
    document.getElementById("modal-text").textContent = text;
    document.getElementById("modal-confirm").textContent = confirmLabel || "Confirm";
    closeMobileOverlays();
    modal.style.display = "flex";
    function closeModal(result) {
      if (modal.dataset.closing) return;
      modal.dataset.closing = "1";
      modal.classList.add("modal-closing");
      setTimeout(() => {
        modal.style.display = "none";
        modal.classList.remove("modal-closing");
        modal.dataset.closing = "";
      }, 160);
      resolve(result);
    }
    document.getElementById("modal-cancel").onclick = function() { closeModal(false); };
    document.getElementById("modal-confirm").onclick = function() { closeModal(true); };
    modal.onclick = function(e) { if (e.target === modal) closeModal(false); };
  });
}

function showFolderPicker(guildId) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.style.cssText = "display:flex;align-items:center;justify-content:center;";
  const folderItems = state.folders.map(f =>
    `<div class="list-item" data-folder-pick="${escapeHtml(f.id)}" style="padding:10px 14px;cursor:pointer;gap:8px;border-radius:8px;">${escapeHtml(f.name)}</div>`
  ).join("") || '<p style="color:var(--text-faint);padding:10px 14px;">No folders yet</p>';
  overlay.innerHTML = `<div class="modal-box" style="background:var(--bg-2);border-radius:12px;padding:20px;width:340px;max-width:90vw;box-shadow:0 8px 32px rgba(0,0,0,0.5);"><h2 style="color:var(--text-primary);font-size:18px;font-weight:700;margin-bottom:12px;">Add to Folder</h2><div style="display:flex;flex-direction:column;gap:4px;max-height:300px;overflow-y:auto;">${folderItems}</div><div style="display:flex;gap:12px;justify-content:flex-end;margin-top:16px;"><button class="modal-btn modal-cancel" style="border-radius:6px;">Cancel</button></div></div>`;
  document.body.appendChild(overlay);
  const close = () => { if (!closing) { closing = true; overlay.classList.add("modal-closing"); setTimeout(() => overlay.remove(), 160); } };
  let closing = false;
  overlay.querySelector(".modal-cancel").onclick = close;
  overlay.onclick = (e) => { if (e.target === overlay) close(); };
  overlay.querySelectorAll("[data-folder-pick]").forEach(el => {
    el.onclick = () => {
      addGuildToFolder(guildId, el.dataset.folderPick);
      close();
    };
  });
}

function friendPickerRow(friend, selected) {
  const avatar = friend.avatarUrl ? `<img src="${escapeHtml(friend.avatarUrl)}" alt="">` : `<div class="avatar-placeholder-sm">${escapeHtml(initials(friend.displayName || friend.username))}</div>`;
  return `<div class="fp-row" data-friend-id="${escapeHtml(friend.id)}">
    <div class="fp-avatar-wrap">${avatar}</div>
    <span>${escapeHtml(friend.displayName || friend.username)}</span>
  </div>`;
}

function friendPickerOverlay({ title, confirmLabel, emptyText, friends }) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.dataset.friendPicker = "";
  overlay.style.cssText = "display:flex;align-items:center;justify-content:center;";
  overlay.innerHTML = `<div class="modal-box" data-fp-box style="background:var(--bg-2);border-radius:12px;padding:20px;width:440px;max-width:90vw;box-shadow:0 8px 32px rgba(0,0,0,0.5);max-height:80vh;display:flex;flex-direction:column;">
    <h2 style="color:var(--text-primary);font-size:18px;font-weight:700;margin-bottom:4px;">${escapeHtml(title)}</h2>
    <p style="color:var(--text-muted);font-size:13px;margin-bottom:12px;">Select friends to add to this group DM.</p>
    <input type="text" data-fp-search placeholder="Search friends..." style="background:var(--bg-mid);border:1px solid var(--bg-hover);border-radius:6px;color:var(--text-normal);font-size:14px;padding:9px 12px;outline:none;margin-bottom:10px;width:100%;box-sizing:border-box;">
    <div style="display:flex;flex-direction:column;gap:2px;overflow-y:auto;flex:1;min-height:0;" data-fp-list>
    </div>
    <div style="display:flex;gap:12px;justify-content:flex-end;margin-top:16px;">
      <button class="modal-btn modal-cancel" style="border-radius:6px;">Cancel</button>
      <button class="modal-btn" style="border-radius:6px;background:var(--accent);color:#fff;" data-fp-confirm disabled>${escapeHtml(confirmLabel)}</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  const listEl = overlay.querySelector("[data-fp-list]");
  const confirmBtn = overlay.querySelector("[data-fp-confirm]");
  const searchEl = overlay.querySelector("[data-fp-search]");
  const selected = new Set();
  let closing = false;
  const close = () => { if (closing) return; closing = true; overlay.classList.add("modal-closing"); setTimeout(() => overlay.remove(), 160); };
  overlay.querySelector(".modal-cancel").onclick = close;
  overlay.onclick = (e) => { if (e.target === overlay) close(); };

  const renderList = () => {
    const q = (searchEl.value || "").trim().toLowerCase();
    const filtered = q
      ? friends.filter(f => (f.displayName || "").toLowerCase().includes(q) || (f.username || "").toLowerCase().includes(q))
      : friends;
    listEl.innerHTML = filtered.length
      ? filtered.map(f => friendPickerRow(f, selected.has(f.id))).join("")
      : `<p style="color:var(--text-muted);padding:10px 14px;">${q ? "No friends match your search." : escapeHtml(emptyText)}</p>`;
    listEl.querySelectorAll(".fp-row").forEach(row => {
      row.onclick = () => {
        const id = row.dataset.friendId;
        if (selected.has(id)) selected.delete(id);
        else selected.add(id);
        row.classList.toggle("selected", selected.has(id));
        confirmBtn.disabled = selected.size === 0;
      };
    });
  };
  searchEl.oninput = renderList;
  renderList();
  const getSelection = () => [...selected];
  overlay.getSelection = getSelection;
  return overlay;
}

function showCreateGroupModal() {
  const friends = (state.friends?.friends || []).filter(f => f.id !== state.user?.id);
  const overlay = friendPickerOverlay({
    title: "Create Group DM",
    confirmLabel: "Create Group",
    emptyText: "You need friends to start a group. Add some friends first.",
    friends,
  });
  const btn = overlay.querySelector("[data-fp-confirm]");
  btn.onclick = async () => {
    const ids = overlay.getSelection();
    if (ids.length < 1) return;
    btn.disabled = true;
    btn.textContent = "Creating...";
    try {
      const group = await api.createGroup(ids);
      overlay.classList.add("modal-closing");
      setTimeout(() => overlay.remove(), 160);
      showToast("Group created");
      await refreshDms();
      const dm = (state.dms || []).find(d => d.id === group.id);
      if (dm) goToRoute(`/channels/@me/${dm.id}`);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Create Group";
      showToast(e.data?.error || e.message || "Failed to create group");
    }
  };
}

function showAddGroupMemberModal(groupId) {
  const existing = state.membersByChannel[groupId] || [];
  const existingIds = new Set(existing.map(m => m.id));
  const friends = (state.friends?.friends || []).filter(f => f.id !== state.user?.id && !existingIds.has(f.id));
  const overlay = friendPickerOverlay({
    title: "Add Friends",
    confirmLabel: "Add",
    emptyText: "All of your friends are already in this group.",
    friends,
  });
  const btn = overlay.querySelector("[data-fp-confirm]");
  btn.onclick = async () => {
    const ids = overlay.getSelection();
    if (ids.length < 1) return;
    btn.disabled = true;
    btn.textContent = "Adding...";
    try {
      for (const id of ids) await api.addGroupMember(groupId, id);
      overlay.classList.add("modal-closing");
      setTimeout(() => overlay.remove(), 160);
      showToast("Friends added");
      await refreshDms();
      loadMembers(groupId, state);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Add";
      showToast(e.data?.error || e.message || "Failed to add friends");
    }
  };
}

async function leaveGroup(groupId) {
  const dm = (state.dms || []).find(d => d.id === groupId);
  const name = dm?.displayName || "this group";
  const confirmed = await showConfirmModal("Leave Group", `Are you sure you want to leave ${escapeHtml(name)}? You won't be able to see it again unless someone reinvites you.`, "Leave Group");
  if (!confirmed) return;
  try {
    await api.removeGroupMember(groupId, state.user?.id);
    const updated = (state.dms || []).filter(d => d.id !== groupId);
    setState({ dms: updated });
    if (state.selectedDmId === groupId) {
      setState({ selectedDmId: null, selectedChannelId: null });
      history.pushState({}, "", "/channels/@me");
      applyRoute();
    }
    showToast("Left the group");
  } catch (e) {
    showToast(e.data?.error || e.message || "Failed to leave group");
  }
}

function showJoinGuildModal() {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.style.cssText = 'display:flex;align-items:center;justify-content:center;';
  overlay.innerHTML = `<div class="modal-box" style="background:var(--bg-2);border-radius:12px;padding:24px;width:440px;max-width:90vw;box-shadow:0 8px 32px rgba(0,0,0,0.5);">
    <h2 style="color:var(--text-primary);font-size:20px;font-weight:700;margin-bottom:8px;">Join a Server</h2>
    <p style="color:var(--text-secondary);font-size:15px;margin-bottom:20px;">Enter an invite link or code to join a server.</p>
    <label style="color:var(--text-secondary);font-size:12px;font-weight:700;text-transform:uppercase;display:block;margin-bottom:6px;">Invite Link or Code</label>
    <input type="text" id="join-invite-input" placeholder="discord.gg/randomshi" style="width:100%;padding:10px;background:var(--bg-3);border:1px solid var(--bg-hover);border-radius:3px;color:var(--text-normal);font-size:15px;outline:none;box-sizing:border-box;">
    <div style="display:flex;gap:12px;justify-content:flex-end;margin-top:24px;">
      <button id="join-cancel" class="modal-btn modal-cancel" style="border-radius:6px;">Cancel</button>
      <button id="join-confirm" class="modal-btn" style="border-radius:6px;background:var(--accent);color:#fff;">Join Server</button>
    </div>
    <div id="join-error" style="color:#f23f43;font-size:13px;margin-top:12px;display:none;"></div>
  </div>`;
  closeMobileOverlays();
  document.body.appendChild(overlay);
  const input = overlay.querySelector('#join-invite-input');
  const errorEl = overlay.querySelector('#join-error');
  let closing = false;
  const close = () => {
    if (closing) return;
    closing = true;
    overlay.classList.add('modal-closing');
    setTimeout(() => overlay.remove(), 160);
  };
  const parseCode = (val) => {
    val = val.trim();
    if (!val) return '';
    const m = val.match(/(?:discord\.gg\/|discord\.com\/invite\/)?([a-zA-Z0-9_-]+)/);
    return m ? m[1] : val;
  };
  const join = async () => {
    const code = parseCode(input.value);
    if (!code) { errorEl.textContent = 'Please enter a valid invite code.'; errorEl.style.display = ''; return; }
    errorEl.style.display = 'none';
    const btn = overlay.querySelector('#join-confirm');
    btn.textContent = 'Joining...'; btn.disabled = true;
    try {
      await api.joinGuild(code);
      close();
      const guilds = await api.guilds();
      setState({ guilds });
      showToast('Server joined');
    } catch (e) {
      close();
      if (isCaptchaError(e)) {
        showCaptchaFallback("Complete this action on the original Discord client.");
      } else {
        const msg = e.data?.error || e.message || 'Failed to join';
        showToast(msg);
      }
    }
  };
  overlay.querySelector('#join-cancel').onclick = close;
  overlay.querySelector('#join-confirm').onclick = join;
  input.onkeydown = (e) => { if (e.key === 'Enter') join(); };
  overlay.onclick = (e) => { if (e.target === overlay) close(); };
  setTimeout(() => input.focus(), 100);
}

function showInviteModal(userId) {
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;inset:0;z-index:1000;background:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;';
  overlay.innerHTML = `<div class="modal-box" style="background:var(--bg-2);border-radius:8px;padding:24px;width:440px;max-width:90vw;box-shadow:0 8px 32px rgba(0,0,0,0.5);">
    <h2 style="color:var(--text-primary);font-size:20px;font-weight:700;margin-bottom:12px;">Invite to Fern</h2>
    <div id="invite-desc" style="color:var(--text-secondary);font-size:14px;line-height:1.55;margin-bottom:24px;"></div>
    <div style="display:flex;gap:12px;justify-content:flex-end;">
      <button id="invite-cancel" style="padding:9px 20px;border:none;border-radius:4px;font-size:14px;font-weight:500;cursor:pointer;background:var(--bg-hover);color:var(--text-normal);">Cancel</button>
      <button id="invite-confirm" style="padding:9px 20px;border:none;border-radius:4px;font-size:14px;font-weight:500;cursor:pointer;background:var(--accent);color:#fff;">Send Invite</button>
    </div>
    <div id="invite-msg" style="font-size:13px;margin-top:12px;line-height:1.4;"></div>
  </div>`;
  document.body.appendChild(overlay);
  const descEl = overlay.querySelector('#invite-desc');
  const msgEl = overlay.querySelector('#invite-msg');
  descEl.innerHTML = `You're about to invite this user to Fern. They'll receive a <strong style="color:var(--text-primary)">24-hour free trial</strong> when they sign up. If they decide to purchase a license, you'll get <strong style="color:var(--text-primary)">+3 days</strong> free as a reward.`;
  const close = () => overlay.remove();
  const send = async () => {
    msgEl.textContent = '';
    const btn = overlay.querySelector('#invite-confirm');
    btn.textContent = 'Sending...'; btn.disabled = true;
    try {
      await api.inviteUser(userId);
      close();
      showToast('Invite sent');
    } catch (e) {
      btn.textContent = 'Send Invite'; btn.disabled = false;
      const msg = e.data?.error === 'trial_user'
        ? 'Trial accounts cannot send referrals.'
        : e.data?.error === 'already_invited'
          ? 'That user has already been invited.'
          : e.data?.error === 'cannot_invite_yourself'
            ? 'You cannot invite yourself.'
            : e.data?.error === 'invitee_not_found'
              ? 'Discord user not found.'
              : (e.data?.message || e.message || 'Invite failed');
      msgEl.textContent = msg;
      msgEl.style.color = '#ed4245';
    }
  };
  overlay.querySelector('#invite-cancel').onclick = close;
  overlay.querySelector('#invite-confirm').onclick = send;
  overlay.onclick = (e) => { if (e.target === overlay) close(); };
}

function buyFernLicense(plan) {
  if (!window.sellAuth) {
    window.location.assign("/#pricing");
    return;
  }
  sellAuth.open({
    shopId: 258907,
    shopUrl: "https://fern.mysellauth.com",
    cart: plan === "month"
      ? [{ productId: 824850, variantId: 1415901, quantity: 1 }]
      : [{ productId: 824852, variantId: 1415903, quantity: 1 }],
    theme: "dark",
    returnUrl: window.location.origin + "/confirm"
  });
}

function showSettingsModal() {
  closeMobileOverlays();
  const existing = document.querySelector('[data-settings-modal]');
  if (existing) {
    existing.remove();
  }

  const user = state.user || {};
  const userStatus = state.presences?.[user.id] || user.status || "online";
  const dotColor = userStatus === "idle" ? "#f0b232" : userStatus === "dnd" ? "#f23f43" : (userStatus === "offline" || userStatus === "invisible") ? "#80848e" : "#23a55a";
  const displayName = user.displayName || user.username || "User";
  const username = user.username || "";
  const initial = initials(displayName);

  const overlay = document.createElement('div');
  overlay.dataset.settingsModal = '';
  overlay.className = 'modal-overlay';
  overlay.style.cssText = 'display:flex;align-items:center;justify-content:center;';
  overlay.innerHTML = `
    <div class="settings-modal">
      <div class="settings-head">
        <div class="settings-head-left">
          <h2>Settings</h2>
          <div class="settings-tabs">
            <button type="button" class="settings-tab-btn active" data-settings-nav="account"><i class="fa-solid fa-user"></i> <span>Account</span></button>
            <button type="button" class="settings-tab-btn" data-settings-nav="misc"><i class="fa-solid fa-sliders"></i> <span>Misc</span></button>
            <button type="button" class="settings-tab-btn" data-settings-nav="feedback"><i class="fa-solid fa-bug"></i> <span>Feedback</span></button>
          </div>
        </div>
        <button type="button" class="settings-close" data-settings-close aria-label="Close"><i class="fa-solid fa-xmark"></i></button>
      </div>
      <div class="settings-content">
        <section class="settings-pane" data-settings-pane="account">
          <div class="settings-card">
            <div class="settings-profile-compact">
              <div class="settings-avatar">
                ${user.avatarUrl ? `<img src="${escapeHtml(user.avatarUrl)}" alt="">${user.avatarDecorationUrl ? `<img src="${escapeHtml(user.avatarDecorationUrl)}" alt="" class="settings-avatar-deco">` : ""}` : `<div class="settings-avatar-ph">${escapeHtml(initial)}</div>`}
              </div>
              <div class="settings-profile-txt">
                <p class="settings-display">${escapeHtml(displayName)} <span class="settings-fern-badge">Fern User</span></p>
                <p class="settings-username">${escapeHtml(username)}</p>
              </div>
            </div>
          </div>
          <div class="settings-card">
            <div class="settings-card-header">
              <span>Fern Alias</span>
              <span class="alias-active-badge" data-alias-badge style="display:none">Active</span>
            </div>
            <div class="settings-row" style="padding:0;background:transparent;border:none;">
              <div class="settings-row-txt">
                <p class="settings-label">Use Alias</p>
              </div>
              <label class="settings-switch">
                <input type="checkbox" data-alias-toggle>
                <span class="switch-slider"></span>
              </label>
            </div>
            <div class="alias-input-group">
              <div class="alias-input-wrap">
                <span class="alias-prefix">@</span>
                <input type="text" class="alias-input" data-alias-input placeholder="Enter alias..." maxlength="32" autocomplete="off" spellcheck="false">
                <button type="button" class="alias-claim-btn" data-alias-claim-btn>Claim</button>
              </div>
              <p class="alias-feedback" data-alias-feedback></p>
            </div>
          </div>
          <div class="settings-card">
            <div class="settings-card-header">License</div>
            <div class="license-card blurred" data-license-card role="button" tabindex="0">
              <div class="license-veil"><i class="fa-solid fa-lock"></i> Click to reveal</div>
              <div class="license-inner">
                <div class="license-row"><span class="license-label">Key</span><span class="license-value license-key" data-license-key>—</span></div>
                <div class="license-row"><span class="license-label">Status</span><span class="license-status" data-license-status>Active</span></div>
                <div class="license-row"><span class="license-label">Expires</span><span class="license-value" data-license-expires>—</span></div>
                <div class="license-row" data-license-countdown-row style="display:none"><span class="license-label">Time left</span><span class="license-value license-countdown" data-license-countdown>—</span></div>
              </div>
            </div>
          </div>
          <div class="settings-card">
            <div class="settings-card-header">
              <span>Referrals</span>
              <span class="referral-reward-badge"><i class="fa-solid fa-gift"></i> +3 Days</span>
            </div>
            <div class="referral-stats" data-referral-stats>
              <div class="ref-stat"><span class="ref-num">0</span><span class="ref-label">Total</span></div>
              <div class="ref-stat"><span class="ref-num">0</span><span class="ref-label">Completed</span></div>
              <div class="ref-stat"><span class="ref-num">0</span><span class="ref-label">Pending</span></div>
            </div>
          </div>
          <button type="button" class="settings-logout-btn" data-settings-logout>
            <i class="fa-solid fa-arrow-right-from-bracket"></i>
            <span>Log Out</span>
          </button>
        </section>
        <section class="settings-pane" data-settings-pane="misc" style="display:none">
          <div class="settings-row">
            <div class="settings-row-txt">
              <p class="settings-label">Show all channels</p>
            </div>
            <label class="settings-switch">
              <input type="checkbox" data-show-all-channels ${state.settings.showAllChannels ? "checked" : ""}>
              <span class="switch-slider"></span>
            </label>
          </div>
          <div class="settings-row">
            <div class="settings-row-txt">
              <p class="settings-label">Ping sound</p>
            </div>
            <label class="settings-switch">
              <input type="checkbox" data-ping-sound-toggle ${state.settings.pingSound ? "checked" : ""}>
              <span class="switch-slider"></span>
            </label>
          </div>
          <div class="settings-card" style="padding:12px 14px;">
            <div class="settings-row" style="padding:0;background:transparent;border:none;">
              <div class="settings-row-txt">
                <p class="settings-label">Obscure Traffic</p>
              </div>
              <label class="settings-switch">
                <input type="checkbox" data-obscure-toggle ${state.settings.obscure ? "checked" : ""}>
                <span class="switch-slider"></span>
              </label>
            </div>
            <div class="obscure-route-box" data-obscure-box style="${state.settings.obscure ? '' : 'display:none'};margin-top:10px;padding:12px;">
              <div class="obscure-header" style="margin-bottom:8px;">
                <span class="obscure-title"><i class="fa-solid fa-route"></i> Multi-Hop Route</span>
                <button type="button" class="obscure-refresh-btn" data-obscure-refresh title="Reroute proxy chain"><i class="fa-solid fa-arrows-rotate"></i> Reroute</button>
              </div>
              <div class="obscure-chain" data-obscure-chain>
                <div class="obscure-hop">
                  <span class="obscure-flag">💻</span>
                  <div class="obscure-hop-info">
                    <span class="obscure-hop-name">Origin</span>
                    <span class="obscure-hop-sub">Local</span>
                  </div>
                </div>
              </div>
              <div class="obscure-status-bar" style="padding-top:8px;">
                <span class="obscure-status-text" data-obscure-status-text>Active • Encrypted</span>
              </div>
            </div>
          </div>
          <div class="settings-card">
            <div class="settings-card-header">Client Health</div>
            <div class="health-meter" data-health-meter>
              <div class="health-gauge-wrap">
                <svg class="health-gauge" viewBox="0 0 120 120">
                  <circle class="health-gauge-track" cx="60" cy="60" r="52"></circle>
                  <circle class="health-gauge-fill" cx="60" cy="60" r="52" data-health-fill></circle>
                </svg>
                <div class="health-gauge-center">
                  <span class="health-gauge-value" data-health-value>100</span>
                </div>
              </div>
              <div class="health-info-wrap">
                <div class="health-gauge-status" data-health-status>Optimal</div>
                <div class="health-flags" data-health-flags></div>
              </div>
            </div>
          </div>
        </section>
        <section class="settings-pane" data-settings-pane="feedback" style="display:none">
          <div class="settings-card">
            <div class="settings-card-header">Community</div>
            <a class="settings-feedback-btn" href="/invite" target="_blank" rel="noopener" style="margin-top:2px;width:fit-content;"><i class="fa-brands fa-discord"></i> Join Discord</a>
          </div>
          <div class="settings-row" data-changelog-row role="button" tabindex="0">
            <div class="settings-row-txt">
              <p class="settings-label">Changelog</p>
            </div>
            <i class="fa-solid fa-chevron-right settings-row-caret"></i>
          </div>
          <div class="settings-card">
            <div class="settings-card-header">Client Info</div>
            <div class="license-row">
              <span class="license-label">Version</span>
              <span class="license-value" data-version style="font-weight:600;">—</span>
            </div>
            <div class="license-row">
              <span class="license-label">Security</span>
              <span class="license-value" style="color:var(--accent);font-weight:600;"><i class="fa-solid fa-shield-halved"></i> Fernet E2EE</span>
            </div>
          </div>
        </section>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const close = () => {
    if (overlay.dataset.closing) return;
    overlay.dataset.closing = "1";
    overlay.classList.add("modal-closing");
    setTimeout(() => overlay.remove(), 160);
  };

  overlay.querySelector("[data-settings-close]").addEventListener("click", close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });

  const settingsLogoutBtn = overlay.querySelector("[data-settings-logout]");
  settingsLogoutBtn.addEventListener("click", () => {
    close();
    showConfirmModal("Log Out", "Are you sure you want to log out?", "Log Out")
      .then(function(confirmed) {
        if (confirmed) navigateToLogout();
      });
  });

  const licenseCard = overlay.querySelector("[data-license-card]");
  licenseCard.addEventListener("click", () => {
    const revealed = licenseCard.classList.toggle("revealed");
    if (revealed) licenseCard.classList.remove("blurred");
    else licenseCard.classList.add("blurred");
    showToast(revealed ? "License revealed" : "License hidden again");
  });
  licenseCard.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); licenseCard.click(); }
  });

  const toggle = overlay.querySelector("[data-show-all-channels]");
  toggle.addEventListener("change", () => {
    const next = toggle.checked;
    localStorage.setItem("fern_settings_show_all", next ? "1" : "0");
    setState({ settings: { ...state.settings, showAllChannels: next } });
    showToast(next ? "Showing all channels" : "Hiding channels you can't access");
  });

  const pingSoundToggle = overlay.querySelector("[data-ping-sound-toggle]");
  pingSoundToggle.addEventListener("change", () => {
    const next = pingSoundToggle.checked;
    localStorage.setItem("fern_settings_ping_sound", next ? "1" : "0");
    setState({ settings: { ...state.settings, pingSound: next } });
    showToast(next ? "Ping sound enabled" : "Ping sound disabled");
  });

  const obscureToggle = overlay.querySelector("[data-obscure-toggle]");
  const obscureBox = overlay.querySelector("[data-obscure-box]");
  const obscureChainEl = overlay.querySelector("[data-obscure-chain]");
  const obscureStatusText = overlay.querySelector("[data-obscure-status-text]");
  const obscureRefreshBtn = overlay.querySelector("[data-obscure-refresh]");

  async function renderObscureChain(refresh = false) {
    if (!obscureChainEl) return;
    try {
      if (obscureRefreshBtn) obscureRefreshBtn.classList.add("spinning");
      const res = await api.obscureRoute(refresh);
      const hops = res?.hops || [];
      let hopsHtml = `
        <div class="obscure-hop">
          <span class="obscure-flag">💻</span>
          <div class="obscure-hop-info">
            <span class="obscure-hop-name">Origin</span>
            <span class="obscure-hop-sub">Local</span>
          </div>
        </div>
      `;
      for (const hop of hops) {
        hopsHtml += `
          <div class="obscure-arrow"><i class="fa-solid fa-arrow-right"></i></div>
          <div class="obscure-hop">
            <span class="obscure-flag">${escapeHtml(hop.flag || "🌐")}</span>
            <div class="obscure-hop-info">
              <span class="obscure-hop-name">${escapeHtml(hop.country || "Proxy")}</span>
              <span class="obscure-hop-sub">${escapeHtml(hop.city ? `${hop.city} (${hop.latency})` : hop.latency || "")}</span>
            </div>
          </div>
        `;
      }
      hopsHtml += `
        <div class="obscure-arrow"><i class="fa-solid fa-arrow-right"></i></div>
        <div class="obscure-hop target">
          <span class="obscure-flag"><i class="fa-brands fa-discord"></i></span>
          <div class="obscure-hop-info">
            <span class="obscure-hop-name">Discord</span>
            <span class="obscure-hop-sub">Gateway</span>
          </div>
        </div>
      `;
      obscureChainEl.innerHTML = hopsHtml;
      if (obscureStatusText) {
        obscureStatusText.textContent = `${hops.length} hops active • Routed through ${hops.map(h => h.country).join(" → ")}`;
      }
    } catch {
      // ignore
    } finally {
      if (obscureRefreshBtn) obscureRefreshBtn.classList.remove("spinning");
    }
  }

  if (obscureToggle) {
    obscureToggle.addEventListener("change", () => {
      const next = obscureToggle.checked;
      localStorage.setItem("fern_settings_obscure", next ? "1" : "0");
      setState({ settings: { ...state.settings, obscure: next } });
      if (obscureBox) obscureBox.style.display = next ? "" : "none";
      if (next) renderObscureChain();
      showToast(next ? "Obscure routing enabled" : "Obscure routing disabled");
    });
  }

  if (obscureRefreshBtn) {
    obscureRefreshBtn.addEventListener("click", () => {
      renderObscureChain(true);
      showToast("Proxy chain rerouted");
    });
  }

  if (state.settings.obscure) {
    renderObscureChain();
  }

  const aliasToggle = overlay.querySelector("[data-alias-toggle]");
  const aliasInput = overlay.querySelector("[data-alias-input]");
  const aliasClaimBtn = overlay.querySelector("[data-alias-claim-btn]");
  const aliasBadge = overlay.querySelector("[data-alias-badge]");
  const aliasFeedback = overlay.querySelector("[data-alias-feedback]");

  function setAliasFeedback(msg, type = "info") {
    if (!aliasFeedback) return;
    aliasFeedback.textContent = msg || "";
    aliasFeedback.className = `alias-feedback ${type}`;
  }

  function updateSettingsProfileDisplay(userObj) {
    if (!userObj) return;
    const disp = overlay.querySelector(".settings-display");
    if (disp) {
      disp.innerHTML = `${escapeHtml(userObj.displayName)} <span class="settings-fern-badge">Fern User</span>`;
    }
  }

  const cachedAlias = localStorage.getItem("fern_alias_name");
  const cachedEnabled = localStorage.getItem("fern_alias_enabled");
  if (cachedAlias && aliasInput) aliasInput.value = cachedAlias;
  if (aliasToggle && cachedAlias) {
    aliasToggle.disabled = false;
    aliasToggle.checked = cachedEnabled !== "0";
  }
  if (aliasBadge && cachedAlias) {
    aliasBadge.style.display = cachedEnabled !== "0" ? "" : "none";
  }

  async function loadAliasData() {
    try {
      const myId = state.user?.id;
      const res = await api.getAlias(myId);
      if (res) {
        if (res.alias) {
          localStorage.setItem("fern_alias_name", res.alias);
          localStorage.setItem("fern_alias_enabled", res.enabled ? "1" : "0");
        }
        if (aliasInput) aliasInput.value = res.alias || "";
        if (aliasToggle) {
          aliasToggle.disabled = !res.alias;
          aliasToggle.checked = !!res.enabled;
        }
        if (aliasBadge) {
          aliasBadge.style.display = res.enabled && res.alias ? "" : "none";
        }
        if (res.alias) {
          aliasClaimBtn.textContent = "Change";
          setAliasFeedback(res.enabled ? `@${res.alias}` : `@${res.alias} (disabled)`, "info");
        } else {
          aliasClaimBtn.textContent = "Claim";
          setAliasFeedback("", "info");
        }
      }
    } catch {
      // ignore
    }
  }

  if (aliasClaimBtn && aliasInput) {
    aliasClaimBtn.addEventListener("click", async () => {
      const val = aliasInput.value.trim();
      if (!val) {
        setAliasFeedback("Please enter an alias", "error");
        return;
      }
      aliasClaimBtn.disabled = true;
      aliasClaimBtn.textContent = "Checking...";
      try {
        const myId = state.user?.id;
        const res = await api.claimAlias(val, myId);
        const claimedName = res.data?.alias || val;
        localStorage.setItem("fern_alias_name", claimedName);
        localStorage.setItem("fern_alias_enabled", "1");
        aliasClaimBtn.textContent = "Change";
        if (aliasToggle) {
          aliasToggle.disabled = false;
          aliasToggle.checked = true;
        }
        if (aliasBadge) aliasBadge.style.display = "";
        setAliasFeedback(`@${claimedName}`, "success");
        showToast(`Fern alias set to @${claimedName}`);
        const meRes = await api.me().catch(() => null);
        if (meRes) {
          setState({ user: meRes });
          updateSettingsProfileDisplay(meRes);
        }
      } catch (err) {
        const errMsg = err?.data?.error || err?.message || "Failed to claim alias";
        setAliasFeedback(errMsg, "error");
      } finally {
        aliasClaimBtn.disabled = false;
        if (aliasInput.value.trim()) aliasClaimBtn.textContent = "Change";
      }
    });

    aliasInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        aliasClaimBtn.click();
      }
    });
  }

  if (aliasToggle) {
    aliasToggle.addEventListener("change", async () => {
      const next = aliasToggle.checked;
      const myId = state.user?.id;
      try {
        await api.toggleAlias(next, myId);
        localStorage.setItem("fern_alias_enabled", next ? "1" : "0");
        if (aliasBadge) aliasBadge.style.display = next ? "" : "none";
        const currentAlias = aliasInput?.value?.trim() || localStorage.getItem("fern_alias_name") || "";
        setAliasFeedback(next ? `@${currentAlias}` : `@${currentAlias} (disabled)`, "info");
        showToast(next ? "Now using Fern alias" : "Reverted to Discord display name");
        const meRes = await api.me().catch(() => null);
        if (meRes) {
          setState({ user: meRes });
          updateSettingsProfileDisplay(meRes);
        }
      } catch {
        aliasToggle.checked = !next;
        showToast("Failed to update alias setting");
      }
    });
  }

  loadAliasData();

  const changelogRow = overlay.querySelector("[data-changelog-row]");
  changelogRow.addEventListener("click", () => showChangelogModal());
  changelogRow.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); changelogRow.click(); }
  });

  overlay.querySelectorAll("[data-settings-nav]").forEach(btn => {
    btn.addEventListener("click", () => {
      overlay.querySelectorAll("[data-settings-nav]").forEach(b => b.classList.toggle("active", b === btn));
      const target = btn.dataset.settingsNav;
      overlay.querySelectorAll("[data-settings-pane]").forEach(p => {
        p.style.display = p.dataset.settingsPane === target ? "" : "none";
      });
    });
  });

  loadSettingsData(overlay);
}

function showChangelogModal() {
  const existing = document.querySelector('[data-changelog-modal]');
  if (existing) {
    existing.classList.remove('modal-closing');
    existing.style.display = 'flex';
    return;
  }

  const overlay = document.createElement('div');
  overlay.dataset.changelogModal = '';
  overlay.className = 'modal-overlay';
  overlay.style.cssText = 'display:flex;align-items:center;justify-content:center;';
  overlay.innerHTML = `
    <div class="changelog-modal">
      <div class="settings-head">
        <h2>Changelog</h2>
        <button type="button" class="settings-close" data-changelog-close aria-label="Close"><i class="fa-solid fa-xmark"></i></button>
      </div>
      <div class="changelog-list" data-changelog-list>
        <div class="changelog-empty">Loading…</div>
      </div>
    </div>`;
  document.body.appendChild(overlay);

  const close = () => {
    if (overlay.dataset.closing) return;
    overlay.dataset.closing = "1";
    overlay.classList.add("modal-closing");
    setTimeout(() => overlay.remove(), 160);
  };

  overlay.querySelector("[data-changelog-close]").addEventListener("click", close);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });

  loadChangelogEntries(overlay);
}

async function loadChangelogEntries(overlay) {
  const listEl = overlay.querySelector("[data-changelog-list]");
  const res = await api.changelog().catch(() => null);
  const entries = res?.entries || [];
  if (!entries.length) {
    listEl.innerHTML = '<div class="changelog-empty">No changelog entries yet.</div>';
    return;
  }
  listEl.innerHTML = entries.map(e => `
    <div class="changelog-entry">
      <div class="changelog-entry-head">
        <span class="changelog-version">${escapeHtml(e.version)}</span>
        <span class="changelog-date">${escapeHtml(formatExpiry(e.created_at))}</span>
      </div>
      <div class="changelog-title">${escapeHtml(e.title)}</div>
      <div class="changelog-body">${escapeHtml(e.body)}</div>
    </div>`).join("");
}

function formatExpiry(iso) {
  if (!iso) return "Lifetime";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

async function loadSettingsData(overlay) {
  const versionEl = overlay.querySelector("[data-version]");
  const keyEl = overlay.querySelector("[data-license-key]");
  const statusEl = overlay.querySelector("[data-license-status]");
  const expiresEl = overlay.querySelector("[data-license-expires]");
  const countdownRow = overlay.querySelector("[data-license-countdown-row]");
  const countdownEl = overlay.querySelector("[data-license-countdown]");
  const statsEl = overlay.querySelector("[data-referral-stats]");

  if (overlay._countdownInterval) clearInterval(overlay._countdownInterval);
  if (countdownRow) countdownRow.style.display = "none";

  loadHealthMeter(overlay);

  try {
    const verRes = await api.version().catch(() => null);
    if (versionEl) versionEl.textContent = verRes?.version || "1.0.0";
  } catch (e) {}

  try {
    const res = await api.licenseStatus().catch(() => null);
    const hasFern = res?.is_fern_user === true || !!res?.license_key;
    if (hasFern) {
      if (keyEl) keyEl.textContent = res.license_key || "Active";
      const expiryTime = res.expires ? new Date(res.expires).getTime() : null;
      const isExpired = !isNaN(expiryTime) && Date.now() > expiryTime;
      if (statusEl) {
        if (isExpired) {
          statusEl.textContent = res.trial ? "Trial license expired" : "Fern license expired";
          statusEl.className = "license-status expired";
        } else {
          statusEl.textContent = res.trial ? "Trial license active" : "Fern license active";
          statusEl.className = "license-status fern";
        }
      }
      if (expiresEl) expiresEl.textContent = formatExpiry(res.expires);

      if (res.trial && res.expires && countdownRow && countdownEl) {
        countdownRow.style.display = "";
        const startCountdown = () => {
          const now = Date.now();
          const end = new Date(res.expires).getTime();
          if (isNaN(end)) {
            countdownEl.textContent = "—";
            countdownEl.className = "license-value license-countdown";
            return;
          }
          const diff = end - now;
          if (diff <= 0) {
            countdownEl.textContent = "Expired";
            countdownEl.className = "license-value license-countdown urgent";
            return;
          }
          const h = Math.floor(diff / 3600000);
          const m = Math.floor((diff % 3600000) / 60000);
          const s = Math.floor((diff % 60000) / 1000);
          countdownEl.textContent = `${h}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
          countdownEl.className = diff < 3600000
            ? "license-value license-countdown urgent"
            : "license-value license-countdown";
        };
        startCountdown();
        overlay._countdownInterval = setInterval(startCountdown, 1000);
      }
    } else if (res?.is_fern_user === false) {
      if (keyEl) keyEl.textContent = "—";
      if (statusEl) { statusEl.textContent = "No Fern license bound"; statusEl.className = "license-status none"; }
      if (expiresEl) expiresEl.textContent = "—";
    } else {
      if (keyEl) keyEl.textContent = "—";
      if (statusEl) { statusEl.textContent = "No license data"; statusEl.className = "license-status none"; }
      if (expiresEl) expiresEl.textContent = "—";
    }
  } catch (e) {}

  try {
    const refRes = await api.referralStats().catch(() => null);
    if (statsEl) {
      statsEl.innerHTML = refRes?.total_invites !== undefined
        ? `
          <div class="ref-stat"><span class="ref-num">${refRes.total_invites}</span><span class="ref-label">Total</span></div>
          <div class="ref-stat"><span class="ref-num">${refRes.completed}</span><span class="ref-label">Completed</span></div>
          <div class="ref-stat"><span class="ref-num">${refRes.pending}</span><span class="ref-label">Pending</span></div>`
        : `
          <div class="ref-stat"><span class="ref-num">0</span><span class="ref-label">Total</span></div>
          <div class="ref-stat"><span class="ref-num">0</span><span class="ref-label">Completed</span></div>
          <div class="ref-stat"><span class="ref-num">0</span><span class="ref-label">Pending</span></div>`;
    }
  } catch (e) {}
}

async function loadHealthMeter(overlay) {
  const meter = overlay.querySelector("[data-health-meter]");
  const fill = overlay.querySelector("[data-health-fill]");
  const valueEl = overlay.querySelector("[data-health-value]");
  const statusEl = overlay.querySelector("[data-health-status]");
  const flagsEl = overlay.querySelector("[data-health-flags]");
  if (!meter || !fill) return;

  const res = await api.safetyScore().catch(() => null);
  if (!res || typeof res.score !== "number") {
    if (valueEl) valueEl.textContent = "—";
    if (statusEl) statusEl.textContent = "Health data unavailable";
    if (flagsEl) flagsEl.innerHTML = '<div class="health-flags-clean"><i class="fa-solid fa-circle-xmark"></i> Could not reach health service</div>';
    return;
  }

  const score = Math.max(0, Math.min(100, res.score));
  const level = res.level || (score >= 85 ? "great" : score >= 65 ? "good" : score >= 45 ? "warn" : score >= 25 ? "risk" : "bad");
  meter.classList.remove("great", "good", "warn", "risk", "bad");
  meter.classList.add(level);

  const circumference = 2 * Math.PI * 52;
  fill.style.strokeDasharray = String(circumference);
  fill.style.strokeDashoffset = String(circumference * (1 - score / 100));
  if (valueEl) valueEl.textContent = String(Math.round(score));

  const labels = {
    great: "Optimal",
    good: "Good",
    warn: "Issues detected",
    risk: "At risk",
    bad: "Critical",
  };
  if (statusEl) statusEl.textContent = labels[level] || "Optimal";

  const endpoints = res.endpoints || [];
  if (!flagsEl) return;
  if (!endpoints.length) {
    flagsEl.innerHTML = '<div class="health-flags-clean"><i class="fa-solid fa-circle-check"></i> No flagged endpoints</div>';
    return;
  }

  flagsEl.innerHTML = `
    <div class="health-flags-title"><i class="fa-solid fa-triangle-exclamation"></i> Flagged endpoints</div>
    ${endpoints.map(ep => {
      const badges = (ep.flags || []).map(f => `<span class="health-badge health-badge-${escapeHtml(f)}">${escapeHtml(f)}</span>`).join("");
      const scoreColor = ep.score >= 60 ? "var(--accent)" : ep.score >= 35 ? "#f0b232" : "#f23f43";
      return `
        <div class="health-flag">
          <div class="health-flag-top">
            <span class="health-flag-path" title="${escapeHtml(ep.path)}">${escapeHtml(ep.path)}</span>
            <span class="health-flag-score" style="color:${scoreColor}">${Math.round(ep.score)}%</span>
          </div>
          <div class="health-flag-badges">${badges}</div>
          <div class="health-flag-stats">${ep.ok} ok · ${ep.bans} banned · ${ep.captchas} captcha</div>
        </div>`;
    }).join("")}`;
}

function setupMobileNav() {
  if (window.innerWidth > 600) return;
  const nav = document.querySelector('.rightside-area nav');
  if (!nav) return;
  if (nav.querySelector('[data-mobile-sidebar]')) return;
  const leftSide = nav.querySelector('.left-side');
  const iconsEnd = nav.querySelector('.icons-end');
  if (!leftSide) return;
  const sidebarBtn = document.createElement('i');
  sidebarBtn.className = 'fa-solid fa-bars';
  sidebarBtn.dataset.mobileSidebar = '';
  sidebarBtn.title = 'Servers & DMs';
  sidebarBtn.style.cssText = 'cursor:pointer;font-size:16px;margin-right:8px;';
  const membersPanel = (() => {
    const heading = document.querySelector('.rightside-area .bottom .right h1');
    return !!heading && heading.textContent.trim() === 'Members';
    })();
  let membersBtn = null;
  let membersOverlay = null;
  let dmsBtn = null;
  let dmsOverlay = null;
  const dmView = !!nav.querySelector('.left-side .one .dm-avatar-mini');
  leftSide.prepend(sidebarBtn);
  const sidebarOverlay = document.createElement('div');
  sidebarOverlay.dataset.mobileSidebarParent = '';
  sidebarOverlay.style.cssText = 'position:fixed;inset:0;z-index:1500;background:rgba(0,0,0,0.5);display:none;overflow-y:auto;';
  sidebarOverlay.innerHTML = '<div style="background:var(--bg-2);min-height:100%;display:flex;position:relative;"><button class="close-overlay" style="position:absolute;top:8px;right:8px;z-index:2;width:28px;height:28px;display:flex;align-items:center;justify-content:center;border-radius:4px;background:var(--bg-mid);color:var(--text-primary);cursor:pointer;border:none;font-size:16px;">&times;</button><div class="overlay-content" style="display:flex;width:100%;"></div></div>';
  if (membersPanel) {
    membersBtn = document.createElement('i');
    membersBtn.className = 'fa-solid fa-users';
    membersBtn.dataset.mobileMembers = '';
    membersBtn.title = 'Members';
    membersBtn.style.cssText = 'cursor:pointer;font-size:16px;';
    iconsEnd.appendChild(membersBtn);
    membersOverlay = document.createElement('div');
    membersOverlay.dataset.mobileMembersParent = '';
    membersOverlay.style.cssText = 'position:fixed;inset:0;z-index:1500;background:rgba(0,0,0,0.5);display:none;overflow-y:auto;';
    membersOverlay.innerHTML = '<div style="background:var(--bg-2);min-height:100%;position:relative;width:100%;"><button class="close-overlay" style="position:absolute;top:8px;right:8px;z-index:2;width:28px;height:28px;display:flex;align-items:center;justify-content:center;border-radius:4px;background:var(--bg-mid);color:var(--text-primary);cursor:pointer;border:none;font-size:16px;">&times;</button><div class="overlay-content" style="width:100%;"></div></div>';
            document.body.appendChild(membersOverlay);
  }
  if (dmView) {
    dmsBtn = document.createElement('i');
    dmsBtn.className = 'fa-solid fa-message-left';
    dmsBtn.dataset.mobileDms = '';
    dmsBtn.title = 'Direct Messages';
    dmsBtn.style.cssText = 'cursor:pointer;font-size:16px;';
    iconsEnd.appendChild(dmsBtn);
    dmsOverlay = document.createElement('div');
    dmsOverlay.dataset.mobileDmsParent = '';
    dmsOverlay.style.cssText = 'position:fixed;inset:0;z-index:1500;background:rgba(0,0,0,0.5);display:none;overflow-y:auto;';
    dmsOverlay.innerHTML = '<div style="background:var(--bg-2);min-height:100%;position:relative;width:100%;"><button class="close-overlay" style="position:absolute;top:8px;right:8px;z-index:2;width:28px;height:28px;display:flex;align-items:center;justify-content:center;border-radius:4px;background:var(--bg-mid);color:var(--text-primary);cursor:pointer;border:none;font-size:16px;">&times;</button><div class="overlay-content" style="width:100%;"></div></div>';
    document.body.appendChild(dmsOverlay);
  }
  document.body.appendChild(sidebarOverlay);
    const showOverlay = (type) => {
    let overlay;
    if (type === 'sidebar') overlay = sidebarOverlay;
    else if (type === 'members') overlay = membersOverlay;
    else if (type === 'dms') overlay = dmsOverlay;
    if (!overlay) return;
    const content = overlay.querySelector('.overlay-content');
    content.innerHTML = '';
    if (type === 'sidebar') {
      const aside = document.querySelector('aside');
      const sidebar = document.querySelector('.sidebar-chat');
      if (aside && sidebar) {
        const railClone = aside.cloneNode(true);
        railClone.style.cssText = 'display:flex!important;flex:0 0 auto;width:72px;min-width:72px;height:100vh;overflow-y:auto;overflow-x:hidden;flex-direction:column;align-items:center;padding:12px 6px;gap:8px;background:var(--bg-3);box-sizing:border-box;';
        railClone.querySelectorAll('.rail-nav-group').forEach(el => {
          el.style.cssText = 'display:flex!important;flex-direction:column!important;align-items:center!important;gap:8px!important;padding:0!important;width:100%!important;';
        });
        railClone.querySelectorAll('.rail-nav-btn').forEach(el => {
          el.style.cssText = 'display:flex!important;';
        });
        railClone.querySelectorAll('.rail-bottom-group').forEach(el => {
          el.style.cssText = 'display:flex!important;flex-direction:column!important;align-items:center!important;gap:10px!important;margin-top:auto!important;padding:10px 0 4px!important;border-top:1px solid rgba(255,255,255,0.05)!important;width:100%!important;';
        });
        const cloneEnc = railClone.querySelector('#enc-toggle');
        if (cloneEnc) {
          cloneEnc.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const next = !state.encryptionEnabled;
            try { localStorage.setItem("fern_encryption_enabled", next ? "1" : "0"); } catch {}
            setState({ encryptionEnabled: next });
            document.querySelectorAll('#enc-toggle').forEach(el => {
              el.classList.toggle("active", next);
            });
            cloneEnc.classList.toggle("active", next);
            showToast(next ? "Encryption enabled" : "Encryption disabled");
          });
        }
        const cloneAvatar = railClone.querySelector('[data-open-discord="settings"]');
        if (cloneAvatar) {
          cloneAvatar.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            overlay.style.display = 'none';
            showSettingsModal();
          });
        }
        const sidebarClone = sidebar.cloneNode(true);
        sidebarClone.style.cssText = 'display:flex!important;flex:1;min-width:0;height:100vh;flex-direction:column;background:var(--bg-2);overflow:hidden;';
        sidebarClone.querySelectorAll('[data-open-discord]:not([data-open-discord="settings"])').forEach(el => el.remove());
        content.appendChild(railClone);
        content.appendChild(sidebarClone);
      } else if (sidebar) {
        const clone = sidebar.cloneNode(true);
        clone.style.cssText = 'display:flex!important;width:100%!important;height:100vh;flex-direction:column;background:var(--bg-2);overflow:hidden;';
        clone.querySelectorAll('[data-open-discord]:not([data-open-discord="settings"])').forEach(el => el.remove());
        content.appendChild(clone);
      }
    } else if (type === 'dms') {
      const sidebar = document.querySelector('.sidebar-chat');
      if (sidebar) {
        const clone = sidebar.cloneNode(true);
        clone.style.cssText = 'display:block!important;width:100%!important;height:100vh;flex-direction:column;background:var(--bg-2);overflow-y:auto;overflow-x:hidden;';
        clone.querySelectorAll('[data-open-discord]:not([data-open-discord="settings"])').forEach(el => el.remove());
        content.appendChild(clone);
      }
    } else {
      const right = document.querySelector('.rightside-area .bottom .right');
      if (right) {
        const clone = right.cloneNode(true);
        clone.style.cssText = 'display:block!important;padding:24px 20px;width:100%;box-sizing:border-box;';
        const heading = clone.querySelector('h1');
        if (heading) heading.style.fontSize = '16px';
        content.appendChild(clone);
      }
    }
    overlay.style.display = 'block';
    overlay.onclick = (e) => {
      if (e.target === overlay) { overlay.style.display = 'none'; return; }
      const routeEl = e.target.closest('[data-route]');
      if (routeEl) {
        const route = routeEl.getAttribute('data-route');
        if (route && route !== window.location.pathname) { history.pushState({}, '', route); applyRoute(); }
        overlay.style.display = 'none';
        return;
      }
      const pricingEl = e.target.closest('[data-open-fern-pricing]');
      if (pricingEl) {
        history.pushState({}, '', '/pricing');
        applyRoute();
        overlay.style.display = 'none';
        return;
      }
      const joinEl = e.target.closest('[data-join-guild]');
      if (joinEl) {
        overlay.style.display = 'none';
        return;
      }
    };
  };
  sidebarBtn.onclick = () => showOverlay('sidebar');
  sidebarOverlay.querySelector('.close-overlay').onclick = () => sidebarOverlay.style.display = 'none';
    if (membersBtn && membersOverlay) {
    membersBtn.onclick = () => showOverlay('members');
    membersOverlay.querySelector('.close-overlay').onclick = () => membersOverlay.style.display = 'none';
  }
  if (dmsBtn && dmsOverlay) {
    dmsBtn.onclick = () => showOverlay('dms');
    dmsOverlay.querySelector('.close-overlay').onclick = () => dmsOverlay.style.display = 'none';
  }
  const pricingMobileToggle = document.querySelector('#pricing-mobile-toggle');
  if (pricingMobileToggle) {
    pricingMobileToggle.onclick = () => {
      const pricingPage = document.querySelector('.pricing-page');
      if (pricingPage) {
        pricingPage.classList.toggle('sidebar-open');
        const icon = pricingMobileToggle.querySelector('i');
        if (icon) {
          if (pricingPage.classList.contains('sidebar-open')) {
            icon.classList.remove('fa-bars');
            icon.classList.add('fa-xmark');
          } else {
            icon.classList.remove('fa-xmark');
            icon.classList.add('fa-bars');
          }
        }
      }
    };
  }
}


window.addEventListener("resize", () => { if (window.innerWidth > 600) { const overlay1 = document.querySelector('[data-mobile-sidebar-parent]'); const overlay2 = document.querySelector('[data-mobile-members-parent]'); if (overlay1) overlay1.style.display = 'none'; if (overlay2) overlay2.style.display = 'none'; } });

function customEmojiImageHtml(emoji, size = 18) {
  if (!emoji || !emoji.id) return "";
  const ext = emoji.animated ? "gif" : "png";
  const src = `https://cdn.discordapp.com/emojis/${encodeURIComponent(emoji.id)}.${ext}?size=64&quality=lossless`;
  return `<img src="${src}" alt=":${escapeHtml(emoji.name || "emoji")}:" style="width:${size}px;height:${size}px;vertical-align:middle;display:inline-block;">`;
}

function renderDiscordText(value) {
  const text = String(value || "");
  const pattern = /<a?:([A-Za-z0-9_]+):(\d{17,21})>/g;
  let result = "";
  let last = 0;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    const raw = text.slice(last, match.index);
    result += parseTwemoji(escapeHtml(raw)).replace(/\n/g, "<br>");
    const animated = match[0].startsWith("<a:");
    const emoji = { id: match[2], name: match[1], animated };
    result += customEmojiImageHtml(emoji, 18);
    last = match.index + match[0].length;
  }
  result += parseTwemoji(escapeHtml(text.slice(last))).replace(/\n/g, "<br>");
  return result;
}

const BADGE_ICONS = {
  house_bravery: "https://cdn.discordapp.com/badge-icons/8a88d63823d8a71cd5e390baa45efa02.png",
  house_brilliance: "https://cdn.discordapp.com/badge-icons/011940fd013da3f7fb926e4a1cd2e618.png",
  house_balance: "https://cdn.discordapp.com/badge-icons/3aa41de486fa12454c3761e8e223442e.png",
  hypesquad_events: "https://cdn.discordapp.com/badge-icons/bf01d1073931f921909045f3a39fd264.png",
  nitro: "https://cdn.discordapp.com/badge-icons/2ba85e8026a8614b640c2837bcdfe21b.png",
  booster: "https://cdn.discordapp.com/badge-icons/51040c70d4f20a921ad6674ff86fc95c.png",
  early_supporter: "https://cdn.discordapp.com/badge-icons/7060786766c9c840eb3019e725d2b358.png",
  certified_moderator: "https://cdn.discordapp.com/badge-icons/fee1624003e2fee35cb398e125dc479b.png"
};

const BADGE_SVGS = {
  staff: `<svg viewBox="0 0 24 24" width="18" height="18"><path fill="#5865f2" d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.5 14.5-3.5-3.5 1.5-1.5 3.5 3.5-1.5 1.5zm-5-3.5L8 9.5l3.5-3.5 3.5 3.5-3.5 3.5z"/><path fill="#fff" d="m8.5 14.5 2-2-1.5-1.5-2 2 1.5 1.5z"/></svg>`,
  partner: `<svg viewBox="0 0 24 24" width="18" height="18"><path fill="#5865f2" d="M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm4.4 12.8a3.2 3.2 0 0 1-4.4 0L12 14.8l-.04.04a3.2 3.2 0 0 1-4.4 0 3.2 3.2 0 0 1 0-4.4l2.4-2.4a3.2 3.2 0 0 1 4.4 0 3.2 3.2 0 0 1 0 4.4L13.2 13.6l1.2 1.2a1.5 1.5 0 0 0 2 0 1.5 1.5 0 0 0 0-2l-1.2-1.2 1.4-1.4a3.2 3.2 0 0 1 0 4.6z"/></svg>`,
  bug_hunter_1: `<svg viewBox="0 0 24 24" width="18" height="18"><path fill="#23a55a" d="M19 13v-2c0-.55-.45-1-1-1h-1.1c-.24-1.2-.82-2.26-1.63-3.08L16.7 5.5c.39-.39.39-1.02 0-1.41-.39-.39-1.02-.39-1.41 0l-1.9 1.9C12.92 5.94 12.47 5.9 12 5.9s-.92.04-1.39.09l-1.9-1.9c-.39-.39-1.02-.39-1.41 0-.39.39-.39 1.02 0 1.41l1.43 1.42C7.92 7.74 7.34 8.8 7.1 10H6c-.55 0-1 .45-1 1v2c0 .55.45 1 1 1h1.03c.08 1.13.48 2.16 1.11 3.01L7.29 18.9c-.39.39-.39 1.02 0 1.41.39.39 1.02.39 1.41 0l1.83-1.83c.47.2 1 .32 1.47.37V21c0 .55.45 1 1 1s1-.45 1-1v-2.15c.47-.05 1-.17 1.47-.37l1.83 1.83c.39.39 1.02.39 1.41 0 .39-.39.39-1.02 0-1.41l-1.85-1.89c.63-.85 1.03-1.88 1.11-3.01H18c.55 0 1-.45 1-1z"/></svg>`,
  bug_hunter_2: `<svg viewBox="0 0 24 24" width="18" height="18"><path fill="#f0b232" d="M19 13v-2c0-.55-.45-1-1-1h-1.1c-.24-1.2-.82-2.26-1.63-3.08L16.7 5.5c.39-.39.39-1.02 0-1.41-.39-.39-1.02-.39-1.41 0l-1.9 1.9C12.92 5.94 12.47 5.9 12 5.9s-.92.04-1.39.09l-1.9-1.9c-.39-.39-1.02-.39-1.41 0-.39.39-.39 1.02 0 1.41l1.43 1.42C7.92 7.74 7.34 8.8 7.1 10H6c-.55 0-1 .45-1 1v2c0 .55.45 1 1 1h1.03c.08 1.13.48 2.16 1.11 3.01L7.29 18.9c-.39.39-.39 1.02 0 1.41.39.39 1.02.39 1.41 0l1.83-1.83c.47.2 1 .32 1.47.37V21c0 .55.45 1 1 1s1-.45 1-1v-2.15c.47-.05 1-.17 1.47-.37l1.83 1.83c.39.39 1.02.39 1.41 0 .39-.39.39-1.02 0-1.41l-1.85-1.89c.63-.85 1.03-1.88 1.11-3.01H18c.55 0 1-.45 1-1z"/></svg>`,
  verified_bot_developer: `<svg viewBox="0 0 24 24" width="18" height="18"><path fill="#5865f2" d="M20.5 11h-1.1A7.47 7.47 0 0 0 13 4.6V3.5a1 1 0 0 0-2 0v1.1A7.47 7.47 0 0 0 4.6 11H3.5a1 1 0 0 0 0 2h1.1A7.47 7.47 0 0 0 11 19.4v1.1a1 1 0 0 0 2 0v-1.1a7.47 7.47 0 0 0 6.4-6.4h1.1a1 1 0 0 0 0-2zM8.5 13.5a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm7 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3z"/></svg>`,
  active_developer: `<svg viewBox="0 0 24 24" width="18" height="18"><path fill="#23a55a" d="M9.4 16.6 4.8 12l4.6-4.6L8 6l-6 6 6 6 1.4-1.4zm5.2 0 4.6-4.6-4.6-4.6L16 6l6 6-6 6-1.4-1.4z"/></svg>`
};

function buildProfileBadgesHtml(info) {
  if (!info) return '';
  const badges = [];
  const seen = new Set();

  if (Array.isArray(info.badges)) {
    for (const b of info.badges) {
      if (!b) continue;
      const id = String(b.id || b.description || '');
      if (seen.has(id)) continue;
      seen.add(id);
      if (id.includes('hypesquad_house_1')) seen.add('house_bravery');
      if (id.includes('hypesquad_house_2')) seen.add('house_brilliance');
      if (id.includes('hypesquad_house_3')) seen.add('house_balance');
      if (id.includes('hypesquad_events')) seen.add('hypesquad_events');
      if (id.includes('premium') || id.includes('nitro')) seen.add('nitro');
      if (id.includes('booster') || id.includes('guild_booster')) seen.add('booster');
      if (id.includes('early_supporter')) seen.add('early_supporter');
      if (id.includes('certified_moderator')) seen.add('certified_moderator');
      if (b.icon) {
        badges.push(`<div class="profile-badge-item" title="${escapeHtml(b.description || id)}"><img src="https://cdn.discordapp.com/badge-icons/${escapeHtml(b.icon)}.png" alt="" style="width:18px;height:18px;display:block;" onerror="this.parentElement.style.display='none'"></div>`);
      }
    }
  }

  const f = Number(info.publicFlags) || 0;
  if ((f & 1) && !seen.has('staff')) { seen.add('staff'); badges.push(`<div class="profile-badge-item" title="Discord Staff">${BADGE_SVGS.staff}</div>`); }
  if ((f & 2) && !seen.has('partner')) { seen.add('partner'); badges.push(`<div class="profile-badge-item" title="Partnered Server Owner">${BADGE_SVGS.partner}</div>`); }
  if ((f & 4) && !seen.has('hypesquad_events')) { seen.add('hypesquad_events'); badges.push(`<div class="profile-badge-item" title="HypeSquad Events"><img src="${BADGE_ICONS.hypesquad_events}" alt="" style="width:18px;height:18px;display:block;"></div>`); }
  if ((f & 8) && !seen.has('bug_hunter_1')) { seen.add('bug_hunter_1'); badges.push(`<div class="profile-badge-item" title="Bug Hunter Level 1">${BADGE_SVGS.bug_hunter_1}</div>`); }
  if ((f & 64) && !seen.has('house_bravery')) { seen.add('house_bravery'); badges.push(`<div class="profile-badge-item" title="HypeSquad Bravery"><img src="${BADGE_ICONS.house_bravery}" alt="" style="width:18px;height:18px;display:block;"></div>`); }
  if ((f & 128) && !seen.has('house_brilliance')) { seen.add('house_brilliance'); badges.push(`<div class="profile-badge-item" title="HypeSquad Brilliance"><img src="${BADGE_ICONS.house_brilliance}" alt="" style="width:18px;height:18px;display:block;"></div>`); }
  if ((f & 256) && !seen.has('house_balance')) { seen.add('house_balance'); badges.push(`<div class="profile-badge-item" title="HypeSquad Balance"><img src="${BADGE_ICONS.house_balance}" alt="" style="width:18px;height:18px;display:block;"></div>`); }
  if ((f & 512) && !seen.has('early_supporter')) { seen.add('early_supporter'); badges.push(`<div class="profile-badge-item" title="Early Supporter"><img src="${BADGE_ICONS.early_supporter}" alt="" style="width:18px;height:18px;display:block;"></div>`); }
  if ((f & 16384) && !seen.has('bug_hunter_2')) { seen.add('bug_hunter_2'); badges.push(`<div class="profile-badge-item" title="Bug Hunter Level 2">${BADGE_SVGS.bug_hunter_2}</div>`); }
  if ((f & 131072) && !seen.has('verified_bot_developer')) { seen.add('verified_bot_developer'); badges.push(`<div class="profile-badge-item" title="Early Verified Bot Developer">${BADGE_SVGS.verified_bot_developer}</div>`); }
  if ((f & 262144) && !seen.has('certified_moderator')) { seen.add('certified_moderator'); badges.push(`<div class="profile-badge-item" title="Discord Certified Moderator"><img src="${BADGE_ICONS.certified_moderator}" alt="" style="width:18px;height:18px;display:block;"></div>`); }
  if ((f & 4194304) && !seen.has('active_developer')) { seen.add('active_developer'); badges.push(`<div class="profile-badge-item" title="Active Developer">${BADGE_SVGS.active_developer}</div>`); }
  if (info.premiumSince && !seen.has('nitro')) { seen.add('nitro'); badges.push(`<div class="profile-badge-item" title="Nitro Subscriber"><img src="${BADGE_ICONS.nitro}" alt="" style="width:18px;height:18px;display:block;"></div>`); }
  if (info.premiumGuildSince && !seen.has('booster')) { seen.add('booster'); badges.push(`<div class="profile-badge-item" title="Server Booster"><img src="${BADGE_ICONS.booster}" alt="" style="width:18px;height:18px;display:block;"></div>`); }

  if (!badges.length) return '';
  return `<div class="profile-badges-wrap">${badges.join('')}</div>`;
}

async function showProfilePopup(userId, clickEvent) {
  const existing = document.querySelector('.profile-popup-wrap');
  if (existing) existing.remove();
  const popup = document.createElement('div');
  popup.className = 'profile-popup-wrap';
  const closePopup = () => {
    if (popup.dataset.closing) return;
    popup.dataset.closing = "1";
    popup.classList.add("popup-closing");
    setTimeout(() => popup.remove(), 150);
  };

  const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
  const vpWidth = window.innerWidth / zoom;
  const vpHeight = window.innerHeight / zoom;
  const pw = Math.min(345, vpWidth - 16);

  const targetEl = clickEvent?.target
    ? (clickEvent.target.closest("[data-author-id], .member-item") || clickEvent.target)
    : null;
  const rect = targetEl?.getBoundingClientRect ? targetEl.getBoundingClientRect() : null;

  const cx = clickEvent ? (clickEvent.clientX / zoom) : (vpWidth / 2 - pw / 2);
  const cy = clickEvent ? (clickEvent.clientY / zoom) : (vpHeight / 2 - 200);

  const placePopup = () => {
    const ph = popup.offsetHeight || 420;
    let px = cx;
    let py = cy;

    if (rect) {
      const tRight = rect.right / zoom;
      const tLeft = rect.left / zoom;
      const tTop = rect.top / zoom;
      const tBottom = rect.bottom / zoom;

      if (tRight + pw + 12 <= vpWidth) {
        px = tRight + 8;
      } else if (tLeft - pw - 8 >= 8) {
        px = tLeft - pw - 8;
      } else {
        px = Math.max(8, Math.min(cx, vpWidth - pw - 8));
      }

      py = tTop;
      if (py + ph > vpHeight - 12) {
        py = Math.max(12, tBottom - ph);
      }
    } else {
      if (cx + pw > vpWidth - 12) {
        px = Math.max(8, cx - pw);
      } else {
        px = Math.max(8, cx);
      }
      if (cy + ph > vpHeight - 12) {
        py = Math.max(12, cy - ph);
      } else {
        py = Math.max(12, cy);
      }
    }

    px = Math.max(8, Math.min(px, vpWidth - pw - 8));
    py = Math.max(8, Math.min(py, vpHeight - ph - 8));

    popup.style.left = `${px}px`;
    popup.style.top = `${py}px`;
  };

  popup.style.cssText = `position:fixed;left:8px;top:8px;width:${pw}px;max-height:${Math.round(vpHeight - 16)}px;overflow-y:auto;background:var(--bg-profile);border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,0.7), 0 0 0 1px rgba(255,255,255,0.05);z-index:2000;font-family:"Whitney","Nunito Sans",sans-serif;`;
  popup.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-muted);">Loading...</div>';
  document.body.appendChild(popup);
  placePopup();
  try {
    const info = await api.userInfo(userId);
    if (!info || !info.id) { closePopup(); showToast('Failed to load profile'); return; }
    const display = info.displayName || info.username || "Unknown";
    const uname = info.username || '';
    const initial = initials(display);
    const accent = info.accentColor ? '#' + info.accentColor.toString(16).padStart(6, '0') : 'var(--accent)';
    const isSelf = userId === state.user?.id;
    const isFriend = !isSelf && (state.friends?.friends || []).some(f => f.id === userId);
    const pendingRel = !isSelf && (state.friends?.pending || []).find(f => f.id === userId);
    const isIncoming = pendingRel?.type === 3;
    const isOutgoing = pendingRel?.type === 4;
    let actionsHtml = '';
    if (isSelf) {
      actionsHtml = '<button class="popup-close pp-btn" style="flex:1;height:32px;border:none;border-radius:3px;background:var(--bg-mid);color:var(--text-muted);font-size:13px;font-weight:500;cursor:default;font-family:inherit;">Your Profile</button>';
    } else if (isIncoming) {
      actionsHtml = '<button class="pp-accept" style="flex:1;height:32px;border:none;border-radius:3px;background:var(--accent);color:#fff;font-size:13px;font-weight:500;cursor:pointer;font-family:inherit;">Accept</button><button class="pp-ignore" style="flex:1;height:32px;border:none;border-radius:3px;background:var(--bg-mid);color:var(--text-primary);font-size:13px;font-weight:500;cursor:pointer;font-family:inherit;">Ignore</button>';
    } else if (isOutgoing) {
      actionsHtml = '<button class="pp-cancel" style="flex:1;height:32px;border:none;border-radius:3px;background:var(--bg-mid);color:var(--text-primary);font-size:13px;font-weight:500;cursor:pointer;font-family:inherit;">Cancel Request</button>';
    } else if (isFriend) {
      actionsHtml = '<button class="pp-msg" style="flex:1;height:32px;border:none;border-radius:3px;background:var(--accent);color:#fff;font-size:13px;font-weight:500;cursor:pointer;font-family:inherit;">Message</button><button class="pp-unfriend" style="flex:1;height:32px;border:none;border-radius:3px;background:var(--bg-mid);color:var(--text-primary);font-size:13px;font-weight:500;cursor:pointer;font-family:inherit;">Unfriend</button>';
    } else {
      actionsHtml = '<button class="pp-addfriend" style="flex:1;height:32px;border:none;border-radius:3px;background:var(--accent);color:#fff;font-size:13px;font-weight:500;cursor:pointer;font-family:inherit;">Add Friend</button>';
    }
    const badgesHtml = buildProfileBadgesHtml(info);
    const fernBadge = info.isFernUser
      ? '<span style="display:inline-flex;align-items:center;gap:4px;margin-left:8px;padding:2px 8px;border-radius:999px;background:var(--accent-grad);color:#0c1910;font-size:0.7rem;font-weight:800;text-transform:uppercase;letter-spacing:0.03em;white-space:nowrap;">Fern</span>'
      : '';
    const userStatus = info.status || state.presences?.[userId] || "online";
    const dotColor = userStatus === "idle" ? "#f0b232" : userStatus === "dnd" ? "#f23f43" : (userStatus === "offline" || userStatus === "invisible") ? "#80848e" : "#23a55a";
    const customStatus = info.customStatus;
    const customEmojiHtml = customStatus?.emoji
      ? (customStatus.emoji.id
          ? customEmojiImageHtml(customStatus.emoji, 18)
          : (customStatus.emoji.name ? parseTwemoji(customStatus.emoji.name) : ""))
      : "";
    const customStatusTextHtml = renderDiscordText(customStatus?.text || "");
    const customStatusHtml = (customEmojiHtml || customStatusTextHtml)
      ? `<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;color:var(--text-secondary);font-size:0.9rem;line-height:1.35;">${customEmojiHtml}${customStatusTextHtml ? `<span>${customStatusTextHtml}</span>` : ""}</div>`
      : "";
    const decoUrl = state.friends?.friends?.find(f => f.id === userId)?.avatarDecorationUrl || state.dms?.find(d => d.userId === userId)?.avatarDecorationUrl || (userId === state.user?.id ? state.user?.avatarDecorationUrl : null);
    const avatarWrapperStyle = 'border:6px solid var(--bg-2);border-radius:50%;position:relative;';
    const avatarImgStyle = 'display:block;width:80px;height:80px;border-radius:50%;position:relative;z-index:0;';
    const decoHtml = decoUrl ? `<img src="${escapeHtml(decoUrl)}" alt="" style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:92px;height:92px;pointer-events:none;z-index:1;display:block;">` : '';
    const dotHtml = `<div style="position:absolute;bottom:-4px;right:-4px;width:16px;height:16px;border:4px solid var(--bg-2);border-radius:50%;background:${dotColor};z-index:2;box-sizing:content-box;"></div>`;
    popup.innerHTML = `<div style="${info.bannerUrl ? `height:120px;background-image:url(${escapeHtml(info.bannerUrl)});background-size:100% auto;background-position:center;` : `height:60px;background:${accent};`}border-radius:9px 9px 0 0;overflow:hidden;width:100%;flex-shrink:0;"></div>
<div style="padding:15px;position:relative;">
  <div style="position:absolute;display:flex;flex-direction:row;align-items:flex-end;justify-content:space-between;width:calc(100% - 30px);top:-50px;left:15px;">
    <div style="${avatarWrapperStyle}">
      ${info.avatarUrl ? `<img src="${escapeHtml(info.avatarUrl)}" alt="" style="${avatarImgStyle}">` : `<div style="width:80px;height:80px;border-radius:50%;background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;font-size:28px;font-weight:700;">${escapeHtml(initial)}</div>`}
      ${decoHtml}
      ${dotHtml}
    </div>
    ${badgesHtml}
  </div>
  <div style="background:var(--bg-3);border-radius:8px;padding:13px;margin-top:40px;">
    <div style="display:flex;flex-direction:column;gap:4px;margin-bottom:8px;">
      <div style="color:var(--text-primary);font-weight:600;font-size:1.3rem;display:flex;align-items:center;flex-wrap:wrap;">
        ${escapeHtml(display)}
        ${fernBadge}
      </div>
      <div style="color:var(--text-secondary);font-size:0.9rem;display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
        <span>${escapeHtml(uname)}</span>
        ${info.pronouns ? `<span style="color:var(--text-faint);">&bull;</span><span style="color:var(--text-secondary);font-size:0.9rem;">${escapeHtml(info.pronouns)}</span>` : ''}
      </div>
      <div style="color:var(--text-faint);font-size:0.75rem;font-family:monospace;">${escapeHtml(userId)}</div>
    </div>
    ${customStatusHtml}
    <hr style="border:none;border-top:0.5px solid var(--bg-hover);margin:8px 0;">
    ${info.bio ? `<div style="margin:12px 0 16px;"><div style="color:var(--text-secondary);font-weight:700;text-transform:uppercase;font-size:0.8rem;margin-bottom:8px;">About Me</div><div style="color:var(--text-secondary);font-size:0.9rem;line-height:1.35;">${renderMarkdown(info.bio, null, null)}</div></div>` : ''}
    <div class="pp-actions" style="margin:12px 0;display:flex;gap:8px;">${actionsHtml}</div>
  </div>
</div>`;
    placePopup();
    popup.querySelector('.pp-accept')?.addEventListener('click', async () => {
      try { const f = await api.acceptFriend(userId); setState({ friends: f }); closePopup(); showToast('Accepted'); } catch { showToast('Failed'); }
    });
    popup.querySelector('.pp-ignore')?.addEventListener('click', async () => {
      try { await api.removeFriend(userId); const f = await api.friends(); setState({ friends: f }); closePopup(); } catch { showToast('Failed'); }
    });
    popup.querySelector('.pp-cancel')?.addEventListener('click', async () => {
      try { await api.removeFriend(userId); const f = await api.friends(); setState({ friends: f }); closePopup(); } catch { showToast('Failed'); }
    });
    popup.querySelector('.pp-addfriend')?.addEventListener('click', async () => {
      try {
        await api.addFriend(uname);
        closePopup();
        pushNotification({ title: "Friend Request Sent", description: `Request sent to ${uname}.` });
      } catch (e) {
        closePopup();
        if (isCaptchaError(e)) {
          showCaptchaFallback("Complete this action on the original Discord client.");
        } else {
          showToast('Failed');
        }
      }
    });
    popup.querySelector('.pp-unfriend')?.addEventListener('click', async () => {
      try { await api.removeFriend(userId); const f = await api.friends(); setState({ friends: f }); closePopup(); showToast('Unfriended'); } catch { showToast('Failed'); }
    });
    popup.querySelector('.pp-msg')?.addEventListener('click', () => {
      const dm = state.dms.find(d => d.userId === userId);
      if (dm) { closePopup(); selectDm(dm.id); }
      else showToast('Open a DM first');
    });
    popup.querySelector('.popup-close')?.addEventListener('click', () => closePopup());
    const close = (e) => { if (!popup.contains(e.target)) { closePopup(); document.removeEventListener('mousedown', close); } };
    setTimeout(() => document.addEventListener('mousedown', close), 10);
    document.addEventListener('keydown', function(e) { if (e.key === 'Escape') { closePopup(); document.removeEventListener('keydown', this); } });
  } catch { closePopup(); showToast('Failed to load profile'); }
}

document.addEventListener("contextmenu", (e) => {
  const serverBtn = e.target.closest(".rail-nav-btn[data-guild-id], .rail-nav-btn[data-route^='/channels/']");
  if (!serverBtn) return;
  e.preventDefault();
  const existing = document.querySelector(".guild-context-menu");
  if (existing) existing.remove();
  const guildId = serverBtn.dataset.guildId || serverBtn.getAttribute("data-route")?.split("/")[2];
  const name = serverBtn.getAttribute("title") || "Server";
  if (!guildId) return;
  const menu = document.createElement("div");
  menu.className = "guild-context-menu";
  menu.style.cssText = "position:fixed;z-index:2000;background:var(--bg-3);border:1px solid var(--bg-hover);border-radius:4px;padding:4px 0;min-width:180px;box-shadow:0 8px 24px rgba(0,0,0,0.5);font-size:14px;";
  const inFolder = getFolderForGuild(guildId);
  let folderItems = "";
  if (inFolder) {
    folderItems += `<div class="guild-ctx-item" data-remove-from-folder="${escapeHtml(guildId)}" style="padding:8px 12px;cursor:pointer;display:flex;align-items:center;gap:8px;color:var(--text-muted);"><i class="fa-solid fa-folder-minus"></i> Remove from ${escapeHtml(inFolder.name)}</div>`;
  }
  if (state.folders.length > 0) {
    folderItems += '<div class="guild-ctx-item" data-add-to-folder style="padding:8px 12px;cursor:pointer;display:flex;align-items:center;gap:8px;color:var(--text-muted);"><i class="fa-solid fa-folder-plus"></i> Add to Folder</div>';
  }
  folderItems += '<div class="guild-ctx-item" data-create-folder style="padding:8px 12px;cursor:pointer;display:flex;align-items:center;gap:8px;color:var(--text-muted);"><i class="fa-solid fa-folder"></i> Create Folder</div>';
  menu.innerHTML = `<div class="guild-ctx-header" style="padding:6px 12px;color:var(--text-secondary);font-size:12px;font-weight:600;text-transform:uppercase;border-bottom:1px solid var(--bg-hover);">${escapeHtml(name)}</div>
    ${folderItems}
    <div class="guild-ctx-item leave-guild" data-guild-id="${escapeHtml(guildId)}" style="padding:8px 12px;cursor:pointer;color:#f23f43;display:flex;align-items:center;gap:8px;"><i class="fa-solid fa-sign-out-alt"></i> Leave Guild</div>`;
  document.body.appendChild(menu);
  const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
  const vpWidth = window.innerWidth / zoom;
  const vpHeight = window.innerHeight / zoom;
  const clickX = e.clientX / zoom;
  const clickY = e.clientY / zoom;
  const mWidth = menu.offsetWidth || 180;
  const mHeight = menu.offsetHeight || 140;
  let posX = clickX + mWidth > vpWidth - 10 ? Math.max(10, clickX - mWidth) : Math.max(10, clickX);
  let posY = clickY + mHeight > vpHeight - 10 ? Math.max(10, clickY - mHeight) : Math.max(10, clickY);
  menu.style.left = `${posX}px`;
  menu.style.top = `${posY}px`;
  menu.querySelector(".leave-guild")?.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    menu.remove();
    const confirmed = await showConfirmModal("Leave Server", `Are you sure you want to leave ${name}?`, "Leave");
    if (!confirmed) return;
    try {
      await api.leaveGuild(guildId);
      const guilds = state.guilds.filter(g => g.id !== guildId);
      setState({ guilds, selectedGuildId: null, selectedChannelId: null });
      pushNotification({ title: "Left Server", description: `You left ${name}.` });
    } catch { showToast("Failed to leave server"); }
  });
  menu.querySelector("[data-create-folder]")?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    menu.remove();
    createFolder("New Folder");
    addGuildToFolder(guildId, state.folders[state.folders.length - 1].id);
  });
  menu.querySelector("[data-add-to-folder]")?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    menu.remove();
    showFolderPicker(guildId);
  });
  menu.querySelector("[data-remove-from-folder]")?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    menu.remove();
    removeGuildFromFolder(guildId);
  });
  setTimeout(() => {
    const handleDismiss = (ev) => {
      if (!menu.contains(ev.target)) {
        menu.remove();
        document.removeEventListener("click", handleDismiss);
        document.removeEventListener("contextmenu", handleDismiss);
      }
    };
    document.addEventListener("click", handleDismiss);
    document.addEventListener("contextmenu", handleDismiss);
  }, 10);
});

document.addEventListener("contextmenu", (e) => {
  const folderEl = e.target.closest("[data-folder-toggle], [data-folder-id]");
  if (!folderEl) return;
  e.preventDefault();
  const folderId = folderEl.dataset.folderToggle || folderEl.dataset.folderId;
  if (!folderId) return;
  const folder = state.folders.find(f => f.id === folderId);
  if (!folder) return;
  const existing = document.querySelector(".guild-context-menu");
  if (existing) existing.remove();
  const menu = document.createElement("div");
  menu.className = "guild-context-menu";
  menu.style.cssText = "position:fixed;z-index:2000;background:var(--bg-3);border:1px solid var(--bg-hover);border-radius:4px;padding:4px 0;min-width:180px;box-shadow:0 8px 24px rgba(0,0,0,0.5);font-size:14px;";
  menu.innerHTML = `<div class="guild-ctx-header" style="padding:6px 12px;color:var(--text-secondary);font-size:12px;font-weight:600;text-transform:uppercase;border-bottom:1px solid var(--bg-hover);">${escapeHtml(folder.name)}</div>
    <div class="guild-ctx-item" data-rename-folder="${escapeHtml(folderId)}" style="padding:8px 12px;cursor:pointer;display:flex;align-items:center;gap:8px;color:var(--text-muted);"><i class="fa-solid fa-pen"></i> Rename Folder</div>
    <div class="guild-ctx-item" data-delete-folder="${escapeHtml(folderId)}" style="padding:8px 12px;cursor:pointer;color:#f23f43;display:flex;align-items:center;gap:8px;"><i class="fa-solid fa-trash"></i> Delete Folder</div>`;
  document.body.appendChild(menu);
  const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
  const vpWidth = window.innerWidth / zoom;
  const vpHeight = window.innerHeight / zoom;
  const clickX = e.clientX / zoom;
  const clickY = e.clientY / zoom;
  const mWidth = menu.offsetWidth || 180;
  const mHeight = menu.offsetHeight || 100;
  let posX = clickX + mWidth > vpWidth - 10 ? Math.max(10, clickX - mWidth) : Math.max(10, clickX);
  let posY = clickY + mHeight > vpHeight - 10 ? Math.max(10, clickY - mHeight) : Math.max(10, clickY);
  menu.style.left = `${posX}px`;
  menu.style.top = `${posY}px`;
  const close = () => menu.remove();
  menu.querySelector("[data-rename-folder]")?.addEventListener("click", async () => {
    menu.remove();
    const name = prompt("Folder name:", folder.name);
    if (name && name.trim()) renameFolder(folderId, name.trim());
  });
  menu.querySelector("[data-delete-folder]")?.addEventListener("click", async () => {
    menu.remove();
    const confirmed = await showConfirmModal("Delete Folder", `Remove "${folder.name}"? Guilds will not be affected.`, "Delete");
    if (confirmed) deleteFolder(folderId);
  });
  document.addEventListener("click", close, { once: true });
  document.addEventListener("contextmenu", close, { once: true });
});

document.addEventListener("click", async (event) => {
  const closeDmBtn = event.target.closest("[data-close-dm]");
  if (closeDmBtn) {
    event.preventDefault();
    const dmId = closeDmBtn.getAttribute("data-close-dm");
    if (dmId) {
      const dm = state.dms.find(d => d.id === dmId);
      if (dm && !dm.userId) {
        leaveGroup(dmId);
      } else {
        const updated = state.dms.filter(dm => dm.id !== dmId);
        setState({ dms: updated });
      }
    }
    return;
  }

  const routeTrigger = event.target.closest("[data-route]");
  if (routeTrigger) {
    event.preventDefault();
    const route = routeTrigger.getAttribute("data-route");
    if (route && route !== window.location.pathname) {
      history.pushState({}, "", route);
      applyRoute();
    }
    return;
  }

  const buyBtn = event.target.closest("[data-buy-license]");
  if (buyBtn) {
    event.preventDefault();
    buyFernLicense(buyBtn.getAttribute("data-buy-license"));
    return;
  }

  const addFriendBtn = event.target.closest("[data-add-friend-toggle]");
  if (addFriendBtn) {
    event.preventDefault();
    setState({ showAddFriend: !state.showAddFriend });
    return;
  }

  const filterBtn = event.target.closest("[data-friend-filter]");
  if (filterBtn) {
    event.preventDefault();
    setState({ friendFilter: filterBtn.getAttribute("data-friend-filter"), showAddFriend: false });
    return;
  }

  const acceptBtn = event.target.closest("[data-accept-friend]");
  if (acceptBtn) {
    event.preventDefault();
    const friendId = acceptBtn.getAttribute("data-accept-friend");
    if (friendId) {
      try {
        const friends = await api.acceptFriend(friendId);
        setState({ friends });
      } catch {
        setState({ friendError: "Could not accept friend request." });
      }
    }
    return;
  }

  const ignoreBtn = event.target.closest("[data-ignore-friend]");
  if (ignoreBtn) {
    event.preventDefault();
    const friendId = ignoreBtn.getAttribute("data-ignore-friend");
    if (friendId) {
      try {
        const friends = await api.ignoreFriend(friendId);
        setState({ friends });
      } catch {
        setState({ friendError: "Could not remove friend request." });
      }
    }
    return;
  }

  const removeBtn = event.target.closest("[data-remove-friend]");
  if (removeBtn) {
    event.preventDefault();
    const friendId = removeBtn.getAttribute("data-remove-friend");
    var username = "";
    var dm = state.dms.find(function(d) { return d.userId === friendId || d.id === friendId; });
    if (dm) username = dm.displayName || dm.username || "";
    if (!username && state.friends) {
      var f = state.friends.friends.find(function(f) { return f.id === friendId; });
      if (f) username = f.displayName || f.username || "";
    }
    var confirmed = await showConfirmModal(
      'Remove "' + username + '"',
      "Are you sure you want to remove " + (username || "this user") + " from your friends?",
      "Remove Friend"
    );
    if (confirmed && friendId) {
      try {
        await api.removeFriend(friendId);
        const friends = await api.friends();
        setState({ friends, friendError: "" });
      } catch (error) {
        setState({ friendError: "Could not remove friend." });
      }
    }
    return;
  }

  const unblockBtn = event.target.closest("[data-unblock-user]");
  if (unblockBtn) {
    event.preventDefault();
    const userId = unblockBtn.getAttribute("data-unblock-user");
    var confirmed = await showConfirmModal("Unblock User", "Are you sure you want to unblock this user?", "Unblock");
    if (confirmed && userId) {
      try {
        await api.unblockUser(userId);
        const friends = await api.friends();
        setState({ friends, friendError: "" });
      } catch (error) {
        setState({ friendError: "Could not unblock user." });
      }
    }
    return;
  }

  const blockBtn = event.target.closest("[data-block-user]");
  if (blockBtn) {
    event.preventDefault();
    const userId = blockBtn.getAttribute("data-block-user");
    var confirmed = await showConfirmModal(
      "Block User",
      "Are you sure you want to block this user?",
      "Block"
    );
    if (confirmed && userId) {
      try {
        await api.blockUser(userId);
        const friends = await api.friends();
        setState({ friends, friendError: "" });
      } catch (error) {
        setState({ friendError: "Could not block user." });
      }
    }
    return;
  }

  const cancelReply = event.target.closest("[data-cancel-reply]");
  if (cancelReply) {
    event.preventDefault();
    clearReply();
    hideReplyBar();
    return;
  }

  const reactionBtn = event.target.closest(".reaction-btn");
  if (reactionBtn) {
    event.preventDefault();
    const emoji = reactionBtn.getAttribute("data-react-emoji");
    const channelId = reactionBtn.getAttribute("data-react-channel");
    const messageId = reactionBtn.getAttribute("data-react-msg");
    if (emoji && channelId && messageId) {
      const isActive = reactionBtn.classList.contains("reaction-active");
      updateMessageReaction(channelId, messageId, emoji, !isActive, true);

      try {
        if (isActive) {
          await api.removeReaction(channelId, messageId, emoji);
        } else {
          await api.addReaction(channelId, messageId, emoji);
        }
      } catch (err) {
        updateMessageReaction(channelId, messageId, emoji, isActive, true);
        console.error("Reaction sync failed:", err);
      }
    }
    return;
  }

});

document.addEventListener("submit", async (event) => {
  // Always prevent default – we handle submission via fetch
  event.preventDefault();

  const friendForm = event.target.closest("[data-friend-form]");
  if (friendForm) {
    const input = friendForm.querySelector('input[name="username"]');
    const username = input?.value.trim();
    if (!username) return;

    const submitButton = friendForm.querySelector('button[type="submit"]');
    submitButton.disabled = true;
    setState({ friendError: "" });

    try {
      const friends = await api.addFriend(username);
      setState({ friends, friendError: "", showAddFriend: false, friendFormValue: "" });
    } catch (error) {
      if (isCaptchaError(error)) {
        showCaptchaFallback("Complete this action on the original Discord client.");
      } else {
        setState({ friendError: error.message || "Friend could not be added." });
      }
    } finally {
      submitButton.disabled = false;
      input.focus();
    }
    return;
  }

  const form = event.target.closest("[data-message-form]");
  if (!form) return;

  if (form.dataset.sending === "true") {
    return;
  }
  form.dataset.sending = "true";

  const dmId = form.getAttribute("data-dm-id");
  const input = form.querySelector('textarea[name="message"]');
  const content = input?.value.trim();

  if (!dmId || !content) {
    form.dataset.sending = "false";
    return;
  }

  const replyTo = state.replyTo?.message?.id;
  const replyChannel = state.replyTo?.channelId;
  const encryptParam = state.encryptionEnabled ? "true" : "false";

  const activeDm = state.dms.find(dm => dm.id === dmId);
  const activeMessages = state.messagesByDm[dmId] || [];
  if (activeDm && activeDm.type === 1 && !activeDm.lastMessageId && activeMessages.length === 0) {
    setState({ messageError: "" });
    showCaptchaFallback("You must send the first message to this user on the original Discord client before using Fern.");
    form.dataset.sending = "false";
    return;
  }

  try {
    const now = Date.now();
    const cooldown = Math.max(0, 500 - (now - _lastMsgSent));
    if (cooldown > 0) await sleep(cooldown);
    const message = await api.sendMessage(dmId, content, replyTo, encryptParam);
    _lastMsgSent = Date.now();
    if (message && message.id) {
      addDmMessage(dmId, message);
      setReadCursor(dmId, message.id);
      api.markRead(dmId, message.id);
    }
    if (state.replyTo) {
      clearReply();
      hideReplyBar();
    }
    await refreshActiveDmMessages();
    promoteActiveDm();
    input.value = "";
    setState({ messageError: "" });
    const listEl = document.querySelector('.message-list');
    if (listEl) {
      listEl.scrollTop = listEl.scrollHeight;
    }
  } catch (error) {
    if (error instanceof ApiError && error.status === 429) {
      showRateLimitPopup(error.data?.retryAfter);
      setState({ messageError: "" });
    } else if (isCaptchaError(error)) {
      setState({ messageError: "" });
      showCaptchaFallback("Complete this action on the original Discord client.");
    } else if (error instanceof ApiError && error.status === 401) {
      setState({ messageError: "" });
      showCaptchaFallback("Session expired. Log in on Discord.");
    } else if (error instanceof ApiError && error.status === 403 && error.data?.error === "first_message_required") {
      setState({ messageError: "" });
      showCaptchaFallback("You must send the first message to this user on the original Discord client before using Fern.");
    } else {
      console.error("Message send failed:", error);
      const msg = error instanceof ApiError && error.status === 403
        ? "You cannot send messages here. You may not share a server with this user or they may have removed you as a friend."
        : "Message could not be sent. The user may not share a server with you or may have removed you as a friend.";
      setState({ messageError: msg });
      showToast(msg);
    }
  } finally {
    form.dataset.sending = "false";
    if (input) input.focus();
  }
});

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-upload-btn]");
  if (!btn) return;
  e.preventDefault();
  const form = btn.closest("[data-message-form]");
  const fileInput = form?.querySelector("[data-file-input]");
  if (fileInput) fileInput.click();
});

document.addEventListener("click", (e) => {
  const inviteBtn = e.target.closest("[data-invite-btn]");
  if (!inviteBtn) return;
  e.preventDefault();
  const userId = inviteBtn.getAttribute("data-invite-user");
  showInviteModal(userId);
});

document.addEventListener("change", async (e) => {
  const input = e.target.closest("[data-file-input]");
  const encryptParam = state.encryptionEnabled ? "true" : "false";
  if (!input) return;
  const file = input.files?.[0];
  if (!file) return;
  const channelId = input.getAttribute("data-channel-id");
  const btn = input.parentElement?.querySelector("[data-upload-btn]");
  if (btn) btn.classList.add("uploading");
  try {
    const formData = new FormData();
    formData.append("file", file);
    const resp = await fetch(`/api/upload/${encodeURIComponent(channelId)}?encrypt=${encryptParam}`, {
      method: "POST",
      body: formData,
    });
    if (!resp.ok) {
      showToast("Upload failed");
    }
  } catch {
    showToast("Upload failed");
  } finally {
    if (btn) btn.classList.remove("uploading");
    input.value = "";
  }
});

let whisperMediaRecorder = null;
let whisperAudioChunks = [];
let whisperRecordingStartTime = 0;
let whisperTimerInterval = null;
let activeWhisperBtn = null;

async function startWhisperRecording(channelId, btnEl) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    showToast("Microphone not supported on this browser");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    whisperAudioChunks = [];
    whisperMediaRecorder = new MediaRecorder(stream);
    whisperRecordingStartTime = Date.now();
    activeWhisperBtn = btnEl;
    btnEl.classList.add("recording");
    btnEl.innerHTML = '<span class="rec-dot"></span><span class="rec-time">0:00</span>';

    whisperTimerInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - whisperRecordingStartTime) / 1000);
      const m = Math.floor(elapsed / 60);
      const s = String(elapsed % 60).padStart(2, "0");
      const timeEl = btnEl.querySelector(".rec-time");
      if (timeEl) timeEl.textContent = `${m}:${s}`;
    }, 500);

    whisperMediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) whisperAudioChunks.push(e.data);
    };

    whisperMediaRecorder.onstop = async () => {
      clearInterval(whisperTimerInterval);
      btnEl.classList.remove("recording");
      btnEl.innerHTML = '<svg class="whisper-waveform" viewBox="0 0 40 20" fill="none"><path d="M2 10h4l3-6 4 12 4-16 4 18 4-10 3 4h12" stroke="#23a55a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      stream.getTracks().forEach(track => track.stop());

      if (whisperAudioChunks.length === 0) return;
      const mime = whisperMediaRecorder.mimeType || "audio/webm";
      const ext = mime.includes("ogg") ? "ogg" : mime.includes("mp4") ? "m4a" : "webm";
      const audioBlob = new Blob(whisperAudioChunks, { type: mime });
      if (audioBlob.size < 1000) return;

      const formData = new FormData();
	  const encryptParam = state.encryptionEnabled ? "true" : "false";
      formData.append("file", audioBlob, `whisper-${Date.now()}.${ext}`);
      btnEl.classList.add("uploading");
      try {
        const resp = await fetch(`/api/upload/${encodeURIComponent(channelId)}?encrypt=${encryptParam}`, {
          method: "POST",
          body: formData,
        });
        if (!resp.ok) {
          showToast("Failed to send Whisper");
        } else {
          showToast("Whisper voice note sent");
        }
      } catch {
        showToast("Failed to send Whisper");
      } finally {
        btnEl.classList.remove("uploading");
        activeWhisperBtn = null;
      }
    };

    whisperMediaRecorder.start(100);
  } catch (err) {
    console.error("Whisper recording error:", err);
    if (!navigator.mediaDevices || !window.isSecureContext) {
      showToast("Microphone requires a secure connection (HTTPS or localhost)");
    } else if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
      showToast("Mic denied. Click the padlock/settings icon in your URL bar to allow access.");
    } else {
      showToast("Microphone error: " + err.message);
    }
    activeWhisperBtn = null;
  }
}

function stopWhisperRecording() {
  if (whisperMediaRecorder && whisperMediaRecorder.state === "recording") {
    whisperMediaRecorder.stop();
  }
}

document.addEventListener("click", (e) => {
  const whisperBtn = e.target.closest("[data-whisper-btn]");
  if (!whisperBtn) return;
  e.preventDefault();
  const channelId = whisperBtn.getAttribute("data-channel-id");
  if (!channelId) return;

  if (whisperMediaRecorder && whisperMediaRecorder.state === "recording") {
    stopWhisperRecording();
  } else {
    startWhisperRecording(channelId, whisperBtn);
  }
});

function showCaptchaFallback(message) {
  const existing = document.getElementById("captcha-modal");
  if (existing) existing.remove();
  const modal = document.createElement("div");
  modal.id = "captcha-modal";
  modal.style.cssText = `
    position: fixed; inset: 0; background: rgba(0,0,0,0.75);
    display: flex; justify-content: center; align-items: center;
    z-index: 9999; padding: 20px; backdrop-filter: blur(2px);
  `;
  modal.innerHTML = `
    <div style="background: var(--bg-1); border: 1px solid rgba(255,255,255,0.08); border-radius:10px; padding:24px; max-width:420px; width:92vw; text-align:center; color:var(--text-primary); box-shadow: 0 16px 48px rgba(0,0,0,0.6);">
      <div style="width:48px;height:48px;border-radius:50%;background:rgba(240,178,50,0.12);color:#f0b232;display:flex;align-items:center;justify-content:center;margin:0 auto 14px;font-size:22px;">
        <i class="fa-solid fa-triangle-exclamation"></i>
      </div>
      <h3 style="color:var(--text-primary);font-size:18px;font-weight:700;margin-bottom:8px;">Verification Required</h3>
      <p style="color:var(--text-secondary); font-size:14px; line-height:1.45; margin-bottom:8px;">${escapeHtml(message || "Discord requires verification for this action.")}</p>
      <p style="color:var(--text-muted); font-size:12.5px; margin-bottom:20px;">Please complete this action on the original Discord client.</p>
      <div style="display:flex; gap:10px; justify-content:center;">
        <button id="fallbackDiscord" style="border:0; border-radius:5px; padding:9px 20px; background:var(--accent); color:#fff; cursor:pointer; font-weight:700; font-size:13px; font-family:inherit;">Open Discord</button>
        <button id="fallbackClose" style="border:0; border-radius:5px; padding:9px 18px; background:var(--bg-mid); color:var(--text-primary); cursor:pointer; font-weight:600; font-size:13px; font-family:inherit;">Close</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.querySelector("#fallbackDiscord").addEventListener("click", () => {
    window.open("https://discord.com/app", "_blank");
    modal.remove();
  });
  modal.querySelector("#fallbackClose").addEventListener("click", () => modal.remove());
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.remove(); });
}

async function solveCaptchaViaNop4(userId, sitekey, rqdata, rqtoken) {
  const modal = document.createElement("div");
  modal.id = "captcha-modal";
  modal.style.cssText = `
    position: fixed; inset: 0; background: rgba(0,0,0,0.7);
    display: flex; justify-content: center; align-items: center;
    z-index: 9999; padding: 20px;
  `;
  modal.innerHTML = `
    <div style="background: var(--bg-3); border-radius:12px; padding:24px; max-width:400px; text-align:center; color:var(--text-primary);">
      <h3>Captcha required.</h3>
      <p style="color:var(--text-secondary); margin-top:8px;">A browser window is opening to solve the captcha automatically. Please wait...</p>
      <div style="margin:16px auto 0; width:32px; height:32px; border:3px solid var(--bg-hover); border-top-color:var(--accent); border-radius:50%; animation:spindis .75s infinite;"></div>
      <button id="cancelCaptcha" style="margin-top:16px; border:0; border-radius:4px; padding:8px 16px; background:var(--bg-mid); color:var(--text-primary); cursor:pointer; font-weight:700;">Cancel</button>
    </div>
  `;
  document.body.appendChild(modal);

  let cancelled = false;
  modal.querySelector("#cancelCaptcha").addEventListener("click", () => {
    cancelled = true;
    modal.remove();
    setState({ friendError: "CAPTCHA cancelled." });
  });

  try {
    const response = await fetch("/api/captcha", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, sitekey, rqdata, rqtoken }),
    });
    if (cancelled) return null;
    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.error || "CAPTCHA service failed");
    }
    const data = await response.json();
    modal.remove();
    return data.token;
  } catch (error) {
    if (!cancelled) {
      modal.remove();
      showCaptchaFallback(error.message || "Couldn't solve captcha.");
    }
    return null;
  }
}

function updateMessageList(appState) {
  const channelId = appState.selectedDmId || appState.selectedChannelId;
  if (!channelId || !/^\d{17,20}$/.test(channelId)) return;
  const listEl = document.querySelector('.message-list');
  if (!listEl) return;

  const messages = appState.messagesByDm[channelId] || [];
  const contentSig = messages.map(m => {
    const rSig = (m.reactions || []).map(r => (r.emoji?.id || r.emoji?.name || "") + ":" + r.count + (r.me ? ":me" : "")).join(",");
    return m.id + ":" + (m.type || 0) + ":" + m.content + ":" + rSig;
  }).join("|");
  const fp = messages.length + ":" + contentSig + ":" + (appState.messagesLoading ? "L" : "R") + ":" + (appState.messageError || "") + ":" + (appState.readCursors?.[channelId] || "");
  if (_lastMsgFingerprint.get(channelId) === fp) return;
  _lastMsgFingerprint.set(channelId, fp);

  const scrollPos = listEl.scrollTop;
  const scrollHeight = listEl.scrollHeight;
  const nearBottom = scrollPos + listEl.clientHeight >= scrollHeight - 200;
  const shouldScroll = nearBottom || _pendingDmScroll;

  const isDm = !!appState.selectedDmId;
  const selectedDm = isDm
    ? (appState.dms.find((dm) => dm.id === channelId) || { id: channelId, displayName: 'Unknown', username: 'Unknown' })
    : { id: channelId, displayName: 'Channel', username: 'Channel' };
  const user = appState.user;

  listEl.innerHTML = MessageListHtml(
    messages,
    appState.messagesLoading,
    appState.messageError,
    selectedDm,
    user,
    channelId,
    appState.selectedGuildId,
    appState.readCursors?.[channelId],
    appState.channelsByGuild[appState.selectedGuildId]?.roles
  );

  if (messages.length > 0 && !appState.messagesLoading) {
    const lastId = messages[messages.length - 1].id;
    if (_prevLastMsgId.get(channelId) !== undefined && _prevLastMsgId.get(channelId) !== lastId) {
      const rows = listEl.querySelectorAll('.message-row');
      if (rows.length > 0) rows[rows.length - 1].classList.add('just-arrived');
    }
    _prevLastMsgId.set(channelId, lastId);
  }

  requestAnimationFrame(() => {
    if (shouldScroll) {
      _suppressReadScrollUntil = Date.now() + 500;
      listEl.scrollTop = listEl.scrollHeight;
    } else {
      listEl.scrollTop = scrollPos;
    }
    hydrateInviteCards();
  });
  if (!appState.messagesLoading) _pendingDmScroll = false;
}

window._INVITE_CACHE = new Map();
const _inviteCardPending = new Set();

function hydrateInviteCards() {
  const cards = document.querySelectorAll('.discord-invite-card[data-invite-code]');
  for (const card of cards) {
    const code = card.getAttribute('data-invite-code');
    if (!code) continue;
    if (window._INVITE_CACHE.has(code)) {
      applyInviteDataToCard(card, window._INVITE_CACHE.get(code));
      continue;
    }
    if (_inviteCardPending.has(code)) continue;
    _inviteCardPending.add(code);
    api.getInvite(code).then(data => {
      _inviteCardPending.delete(code);
      if (data && data.guild) {
        window._INVITE_CACHE.set(code, data);
        const matchingCards = document.querySelectorAll(`.discord-invite-card[data-invite-code="${CSS.escape(code)}"]`);
        matchingCards.forEach(c => applyInviteDataToCard(c, data));
      }
    }).catch(() => {
      _inviteCardPending.delete(code);
    });
  }
}

function applyInviteDataToCard(card, data) {
  if (!card || !data) return;
  const guild = data.guild || {};
  const isJoined = Boolean(guild.id && state.guilds?.some(g => g.id === guild.id));
  const iconWrap = card.querySelector('.invite-card-icon');
  const nameEl = card.querySelector('.invite-card-name');
  const countsEl = card.querySelector('.invite-card-counts');
  const btn = card.querySelector('.invite-join-btn');

  if (iconWrap) {
    if (guild.icon) {
      iconWrap.innerHTML = `<img src="https://cdn.discordapp.com/icons/${escapeHtml(guild.id)}/${escapeHtml(guild.icon)}.png?size=96" alt="">`;
    } else if (guild.name) {
      iconWrap.innerHTML = `<div class="invite-icon-placeholder">${escapeHtml(guild.name.slice(0, 2).toUpperCase())}</div>`;
    }
  }
  if (nameEl && guild.name) {
    nameEl.textContent = guild.name;
    nameEl.title = guild.name;
  }
  if (countsEl && (data.approximate_member_count !== undefined || data.approximate_presence_count !== undefined)) {
    const presence = data.approximate_presence_count || 0;
    const members = data.approximate_member_count || 0;
    countsEl.innerHTML = `<span class="invite-online-dot"></span>${presence.toLocaleString()} Online · ${members.toLocaleString()} Members`;
  }
  if (btn && isJoined) {
    btn.textContent = "Joined";
    btn.classList.add("joined");
    btn.disabled = true;
  }
}

async function loadMembers(channelId, appState) {
  const isDm = !!appState.selectedDmId;
  const currentGuildId = appState.selectedGuildId;
  try {
    let members;
    if (isDm) {
      members = await api.channelMembers(channelId);
    } else {
      if (!currentGuildId) return;
      members = await api.guildMembers(currentGuildId);
    }
    if (isDm) {
      if (state.selectedDmId !== channelId) return;
    } else {
      if (state.selectedGuildId !== currentGuildId) return;
    }
    const list = Array.isArray(members) ? members : (members?.items ? members.items : null);
    const roles = members?.roles || [];
    if (Array.isArray(list)) {
      const newMembers = { ...state.membersByChannel };
      if (channelId) newMembers[channelId] = list;
      if (currentGuildId) newMembers['guild_' + currentGuildId] = list;
      const newRoles = { ...(state.rolesByGuild || {}) };
      if (currentGuildId && roles.length > 0) newRoles[currentGuildId] = roles;
      const presences = { ...state.presences };
      for (const m of list) {
        if (m.status && m.status !== "offline" && !presences[m.id]) {
          presences[m.id] = m.status;
        }
      }
      for (const f of (state.friends?.friends || [])) {
        if (f.status && !presences[f.id]) {
          presences[f.id] = f.status;
        }
      }
      setState({ membersByChannel: newMembers, rolesByGuild: newRoles, presences });
      updateMemberList();
    }
    else if (members) console.warn('[members] unexpected format:', members);
  } catch (e) {
    console.warn('[members] fetch failed:', e);
  }
}

function updateMemberList() {
  const rightEl = document.querySelector('.rightside-area .bottom .right');
  if (!rightEl) return;
  const channelId = state.selectedDmId || state.selectedChannelId;
  const guildId = state.selectedGuildId;
  const isGroup = !!state.selectedDmId && !(state.dms.find(d => d.id === state.selectedDmId)?.userId);
  if (state.selectedDmId) {
    const dm = state.dms.find(d => d.id === state.selectedDmId);
    if (dm && dm.userId) {
      rightEl.style.display = 'none';
      return;
    }
  }
  if (!channelId && !guildId) {
    rightEl.style.display = '';
    return;
  }
  rightEl.style.display = '';
  if (guildId && !state.membersByChannel['guild_' + guildId]) {
    rightEl.innerHTML = '<h1>Members</h1><div class="sec"><p>Loading...</p></div>';
    return;
  }
  const members = guildId ? (state.membersByChannel['guild_' + guildId] || []) : (channelId ? (state.membersByChannel[channelId] || []) : []);
  const memberList = Array.isArray(members) ? members.slice() : [];
  const seenIds = new Set(memberList.map(m => m.id));

  if (!guildId && channelId && state.messagesByDm && state.messagesByDm[channelId]) {
    for (const msg of state.messagesByDm[channelId]) {
      const authorId = msg.author_id || (typeof msg.author === "object" ? msg.author?.id : null);
      if (authorId && !seenIds.has(authorId)) {
        seenIds.add(authorId);
        memberList.push({
          id: authorId,
          username: authorName(msg),
          displayName: authorName(msg),
          avatarUrl: msg.authorAvatar || null,
          avatarDecorationUrl: msg.authorAvatarDecoration || null,
          roles: [],
        });
      }
    }
  }

  if (state.user?.id && !seenIds.has(state.user.id)) {
    seenIds.add(state.user.id);
    memberList.push({
      ...state.user,
      displayName: state.user.displayName || state.user.username,
      roles: [],
    });
  }

  if (!isGroup && memberList.length === 0) {
    rightEl.innerHTML = '<h1>Members</h1><div class="sec"><p>No members</p></div>';
    return;
  }

  const presences = state.presences || {};
  let sectionsHtml = "";

  if (guildId) {
    const guildRoles = state.rolesByGuild?.[guildId] || state.channelsByGuild?.[guildId]?.roles || [];
    let groupRoles = guildRoles.filter(r => r.hoist).sort((a, b) => (b.position || 0) - (a.position || 0));
    if (groupRoles.length === 0 && guildRoles.length > 0) {
      groupRoles = guildRoles.filter(r => r.name !== "@everyone").sort((a, b) => (b.position || 0) - (a.position || 0));
    }
    const roleMap = new Map(guildRoles.map(r => [String(r.id), r]));

    const roleBuckets = new Map();
    for (const r of groupRoles) {
      roleBuckets.set(String(r.id), { role: r, members: [] });
    }
    const onlineBucket = [];
    const offlineBucket = [];

    for (const m of memberList) {
      const status = m.status || presences[m.id] || "offline";
      const mRoles = (Array.isArray(m.roles) ? m.roles : []).map(String);

      let memberRoleColor = null;
      if (mRoles.length > 0) {
        const sortedRoles = mRoles.map(rid => roleMap.get(rid)).filter(Boolean).sort((a, b) => (b.position || 0) - (a.position || 0));
        const coloredRole = sortedRoles.find(r => r.color);
        if (coloredRole) memberRoleColor = coloredRole.color;
      }

      const highestRole = groupRoles.find(r => mRoles.includes(String(r.id)));
      if (highestRole && roleBuckets.has(String(highestRole.id))) {
        roleBuckets.get(String(highestRole.id)).members.push({
          m,
          status,
          roleColor: highestRole.color || memberRoleColor,
          isOnline: status !== "offline"
        });
      } else {
        if (status === "offline") {
          offlineBucket.push({ m, status: "offline", roleColor: memberRoleColor });
        } else {
          onlineBucket.push({ m, status, roleColor: memberRoleColor });
        }
      }
    }

    for (const bucket of roleBuckets.values()) {
      if (bucket.members.length > 0) {
        bucket.members.sort((a, b) => (b.isOnline ? 1 : 0) - (a.isOnline ? 1 : 0));
        const rows = bucket.members.map(item => memberRowHtml(item.m, item.status, false, item.roleColor)).join("");
        sectionsHtml += `<div class="member-section"><div class="member-section-header">${escapeHtml(bucket.role.name)} — ${bucket.members.length}</div>${rows}</div>`;
      }
    }

    if (onlineBucket.length > 0) {
      const rows = onlineBucket.map(item => memberRowHtml(item.m, item.status, false, item.roleColor)).join("");
      sectionsHtml += `<div class="member-section"><div class="member-section-header">ONLINE — ${onlineBucket.length}</div>${rows}</div>`;
    }

    if (offlineBucket.length > 0) {
      const rows = offlineBucket.map(item => memberRowHtml(item.m, item.status, false, item.roleColor)).join("");
      sectionsHtml += `<div class="member-section"><div class="member-section-header">OFFLINE — ${offlineBucket.length}</div>${rows}</div>`;
    }
  } else {
    const online = [];
    const offline = [];
    for (const m of memberList) {
      const status = m.status || presences[m.id] || "offline";
      if (status === "offline") offline.push(m);
      else online.push({ m, status });
    }
    if (online.length > 0) {
      const rows = online.map(o => memberRowHtml(o.m, o.status, isGroup && state.user?.id !== o.m.id)).join("");
      sectionsHtml += `<div class="member-section"><div class="member-section-header">ONLINE — ${online.length}</div>${rows}</div>`;
    }
    if (offline.length > 0) {
      const rows = offline.map(m => memberRowHtml(m, "offline", isGroup && state.user?.id !== m.id)).join("");
      sectionsHtml += `<div class="member-section"><div class="member-section-header">OFFLINE — ${offline.length}</div>${rows}</div>`;
    }
  }

  const controls = isGroup ? `
    <div class="group-controls">
      <button type="button" class="group-control" data-add-group-member="${escapeHtml(channelId)}"><i class="fa-solid fa-user-plus"></i> Add Members</button>
      <button type="button" class="group-control danger" data-leave-group="${escapeHtml(channelId)}"><i class="fa-solid fa-sign-out-alt"></i> Leave Group</button>
    </div>` : "";

  rightEl.innerHTML = '<h1>Members</h1>' + controls + sectionsHtml;

  const mobileOverlay = document.querySelector('[data-mobile-members-parent]');
  if (mobileOverlay && mobileOverlay.style.display !== 'none') {
    const content = mobileOverlay.querySelector('.overlay-content');
    if (content) {
      const clone = rightEl.cloneNode(true);
      clone.style.cssText = 'display:block!important;padding:24px 20px;width:100%;box-sizing:border-box;';
      const heading = clone.querySelector('h1');
      if (heading) heading.style.fontSize = '16px';
      content.innerHTML = '';
      content.appendChild(clone);
    }
  }
}

function memberRowHtml(member, status, canRemove, roleColor) {
  const name = member.displayName || member.username || "Unknown";
  const initial = initials(name);
  const avatarUrl = member.avatarUrl || (state.friends?.friends || []).find(f => f.id === member.id)?.avatarUrl || (state.dms || []).find(d => d.userId === member.id)?.avatarUrl;
  const avatar = avatarUrl
    ? `<img src="${escapeHtml(avatarUrl)}" alt="" class="member-avatar-img">`
    : `<div class="member-avatar-placeholder">${escapeHtml(initial)}</div>`;
  const decoUrl = member.avatarDecorationUrl || (state.friends?.friends || []).find(f => f.id === member.id)?.avatarDecorationUrl || (state.dms || []).find(d => d.userId === member.id)?.avatarDecorationUrl;
  const deco = decoUrl
    ? `<img src="${escapeHtml(decoUrl)}" alt="" class="member-decoration" style="width:32px!important;height:32px!important">`
    : "";
  const kick = canRemove
    ? `<button type="button" class="group-member-kick" data-kick-group-member="${escapeHtml(member.id)}" title="Kick ${escapeHtml(name)}"><i class="fa-solid fa-user-minus"></i></button>`
    : "";
  const colorStyle = roleColor ? ` style="color:${escapeHtml(roleColor)}"` : '';
  return `<div class="member-item" data-user-id="${escapeHtml(member.id)}"><div class="member-avatar">${avatar}${deco}<div class="member-status-dot ${status}"></div></div><span class="member-name"${colorStyle}>${escapeHtml(name)}</span>${kick}</div>`;
}

function updateMemberPresence(userId, status) {
  const el = document.querySelector(`.member-item[data-user-id="${userId}"]`);
  if (!el) return;
  const dot = el.querySelector('.member-status-dot');
  if (dot) {
    dot.className = 'member-status-dot ' + status;
  }
}

function goToRoute(route) {
  if (!route) return;
  if (route !== window.location.pathname) history.pushState({}, "", route);
  applyRoute();
}

function navigateToLogout() {
  const fade = document.getElementById("page-fade");
  if (fade) {
    fade.classList.add("active");
    setTimeout(() => { window.location.href = "/logout"; }, 320);
  } else {
    window.location.href = "/logout";
  }
}

let _pingAudio = null;
function playPingSound() {
  if (!state.settings?.pingSound) return;
  try {
    if (!_pingAudio) _pingAudio = new Audio("/static/assets/ping.mp3");
    _pingAudio.currentTime = 0;
    _pingAudio.play();
  } catch {}
}

function pushNotification({ title, description, avatarUrl, actionLabel, onClick }) {
  let stack = document.querySelector('.notification-stack');
  if (!stack) {
    stack = document.createElement('div');
    stack.className = 'notification-stack';
    document.body.appendChild(stack);
  }
  while (stack.children.length >= 5) stack.firstElementChild.remove();
  const card = document.createElement('div');
  card.className = 'notification-card';
  const avatar = avatarUrl ? `<img class="notification-avatar" src="${escapeHtml(avatarUrl)}" alt="">` : '';
  const action = actionLabel && onClick ? `<button class="notification-action">${escapeHtml(actionLabel)}</button>` : '';
  card.innerHTML = `${avatar}<div class="notification-content"><div class="notification-title">${escapeHtml(title)}</div>${description ? `<div class="notification-desc">${escapeHtml(description)}</div>` : ''}${action}</div><button class="notification-close">&times;</button>`;
  const dismiss = () => {
    if (!card.isConnected) return;
    card.classList.add('leaving');
    setTimeout(() => card.remove(), 250);
  };
  card.querySelector('.notification-close').addEventListener('click', dismiss);
  if (onClick) card.querySelector('.notification-action')?.addEventListener('click', () => { onClick(); dismiss(); });
  stack.appendChild(card);
  setTimeout(dismiss, 6000);
  return card;
}

async function notifyNewDm(dm) {
  const name = dm.displayName || dm.username || "someone";
  const card = pushNotification({
    title: `New message from ${name}`,
    description: "New message",
    avatarUrl: dm.avatarUrl,
    actionLabel: "Open DM",
    onClick: () => goToRoute(`/channels/@me/${dm.id}`),
  });
  try {
    const payload = await api.messages(dm.id);
    if (!card.isConnected) return;
    const items = (payload.items || []).slice().reverse();
    const last = items[items.length - 1];
    if (!last) return;
    const desc = card.querySelector('.notification-desc');
    if (desc && last.content) desc.textContent = last.content;
    const myId = state.user?.id;
    const isMention = Array.isArray(last.mentions) && last.mentions.some(m => String(m.id) === String(myId));
    if (isMention) {
      playPingSound();
      const titleEl = card.querySelector('.notification-title');
      if (titleEl) titleEl.textContent = `${last.authorDisplay || last.author || "Someone"} mentioned you`;
    }
  } catch {}
}

function showToast(message) {
  const existing = document.querySelector('.toast-notification');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = 'toast-notification';
  toast.textContent = message;
  Object.assign(toast.style, {
    position: 'fixed', bottom: '24px', right: '16px',
    background: 'var(--bg-2)', color: 'var(--text-normal)', padding: '10px 20px', borderRadius: '8px',
    fontSize: '14px', zIndex: '9999', border: '1px solid var(--bg-hover)',
    boxShadow: '0 4px 12px rgba(0,0,0,0.4)', transition: 'opacity 0.3s',
    animation: 'none',
  });
  document.body.appendChild(toast);
  setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 2500);
}

function updateEncToggleIcons() {
  const on = state.encryptionEnabled;
  document.querySelectorAll('#enc-toggle').forEach(el => {
    el.classList.toggle("active", on);
  });
}

subscribe((newState) => {
  try {
    const isDmSelected = newState.selectedDmId && /^\d{17,20}$/.test(newState.selectedDmId);
    const isChannelSelected = newState.selectedChannelId && /^\d{17,20}$/.test(newState.selectedChannelId);
    const active = isDmSelected || isChannelSelected;
    if (active) {
      startDmPolling();
    } else {
      stopDmPolling();
    }

    if (newState.loading || newState.error) {
      initialRender(newState);
      return;
    }

    if (!fullRendered) {
      fullRendered = true;
      initialRender(newState);
      cacheElements();
      return;
    }

    const currentSelectedDm = newState.selectedDmId;
    const currentSelectedChannel = newState.selectedChannelId;
    const activeId = currentSelectedDm || currentSelectedChannel;

      if (active) {
          const currentBlocked = newState.friends?.blocked || [];
          const prevBlocked = state.friends?.blocked || [];
          const blockedChanged = currentBlocked.length !== prevBlocked.length
            || currentBlocked.some((u, i) => u?.id !== prevBlocked[i]?.id);
          if (activeId !== prevSelectedDmId || blockedChanged) {
            if (state.replyTo) { clearReply(); hideReplyBar(); }
            prevSelectedDmId = activeId;
            _pendingDmScroll = true;
            updateChatArea(newState);
            cacheElements();
            const listEl = document.querySelector('.message-list');
            if (listEl) {
              requestAnimationFrame(() => {
                _suppressReadScrollUntil = Date.now() + 500;
                listEl.scrollTop = listEl.scrollHeight;
              });
            }
            loadMembers(activeId, newState);
        } else {
          updateMessageList(newState);
          updateMemberList();
        }
    } else {
      prevSelectedDmId = null;
      updateChatArea(newState);
      cacheElements();
      updateMemberList();
      if (newState.selectedGuildId && !state.membersByChannel['guild_' + newState.selectedGuildId]) {
        loadMembers(newState.selectedGuildId, newState);
      }
    }

    updateServerRail(newState);
    updateSidebar(newState);
    updateEncToggleIcons();
    setTimeout(setupMobileNav, 50);
  } catch (error) {
    console.error('[INIT] Render error:', error);
    setState({
      loading: false,
      error: `UI error: ${error.message || 'Unknown error'}`,
    });
  }
});

window.addEventListener("popstate", applyRoute);

document.addEventListener("input", (event) => {
  const friendInput = event.target.closest("[data-friend-form] input[name='username']");
  if (friendInput) {
    setState({ friendFormValue: friendInput.value });
    return;
  }
  const searchInput = event.target.closest("[data-search-input]");
  if (!searchInput) return;
  const term = searchInput.value.trim().toLowerCase();
  const resultsContainer = searchInput.closest(".top-nav").querySelector("[data-search-results]");
  if (!resultsContainer) return;
  if (!term) {
    resultsContainer.classList.remove("open");
    resultsContainer.innerHTML = "";
    return;
  }
  const { guilds, dms } = state;
  const guildResults = [];
  const dmResults = [];
  const seenGuilds = new Set();
  const seenDms = new Set();
  for (const g of guilds) {
    const name = (g.name || "").toLowerCase();
    if (name.includes(term) && !seenGuilds.has(g.id)) {
      seenGuilds.add(g.id);
      guildResults.push(g);
    }
  }
  for (const dm of dms) {
    const name = (dm.displayName || dm.username || "").toLowerCase();
    let matches = name.includes(term);
    if (!matches && !dm.userId) {
      const members = state.membersByChannel[dm.id];
      if (members) {
        matches = members.some(m => (m.displayName || m.username || "").toLowerCase().includes(term));
      }
    }
    if (matches && !seenDms.has(dm.id)) {
      seenDms.add(dm.id);
      dmResults.push(dm);
    }
  }
  let html = "";
  if (guildResults.length) {
    html += '<div class="search-header">Servers</div>';
    html += guildResults.map(g => `<div class="search-item" data-search-route="/channels/${escapeHtml(g.id)}/${escapeHtml(g.id)}">
      ${g.iconUrl ? `<img src="${escapeHtml(g.iconUrl)}" alt="">` : `<div class="avatar-placeholder-sm" style="width:28px;height:28px;font-size:11px;flex-shrink:0">${escapeHtml(initials(g.name))}</div>`}
      <span class="search-item-name">${escapeHtml(g.name)}</span>
      <span class="search-item-type">Server</span>
    </div>`).join("");
  }
  if (dmResults.length) {
    html += '<div class="search-header">Direct Messages</div>';
    html += dmResults.map(dm => `<div class="search-item" data-search-route="/channels/@me/${escapeHtml(dm.id)}">
      ${dm.avatarUrl ? `<img src="${escapeHtml(dm.avatarUrl)}" alt="">` : `<div class="avatar-placeholder-sm" style="width:28px;height:28px;font-size:11px;flex-shrink:0">${escapeHtml(initials(dm.displayName || dm.username))}</div>`}
      <span class="search-item-name">${escapeHtml(dm.displayName || dm.username)}</span>
      <span class="search-item-type">DM</span>
    </div>`).join("");
  }
  if (!html) {
    html = '<div class="search-empty">No results found</div>';
  }
  resultsContainer.innerHTML = html;
  resultsContainer.classList.add("open");
});

document.addEventListener("click", (event) => {
  const searchItem = event.target.closest("[data-search-route]");
  if (!searchItem) {
    const searchResults = document.querySelector("[data-search-results].open");
    if (searchResults && !event.target.closest("[data-search-input]")) {
      searchResults.classList.remove("open");
    }
    return;
  }
  event.preventDefault();
  const route = searchItem.getAttribute("data-search-route");
  const resultsContainer = searchItem.closest("[data-search-results]");
  if (resultsContainer) {
    resultsContainer.classList.remove("open");
    resultsContainer.innerHTML = "";
    const input = resultsContainer.closest(".top-nav").querySelector("[data-search-input]");
    if (input) input.value = "";
  }
  if (route && route !== window.location.pathname) {
    history.pushState({}, "", route);
    applyRoute();
  }
});

document.addEventListener("sellauth:success", () => {
  window.location.href = "/confirm";
});

console.log('[INIT] Boot sequence');
loadApplicationData()
    .then(() => {
        console.log('[INIT] Initialization complete');
        setupWebSocket();
        initContextMenuListeners();
        checkVersion();
        setTimeout(refreshOwnPresence, 3000);
        startLicenseExpiryCheck();
        setInterval(refreshDms, 15000);
    })
  .catch((error) => {
    console.error('[INIT] Boot error:', error);
    setState({
      loading: false,
      error: `Startup error: ${error.message || 'Unknown error'}`,
    });
  });

async function checkVersion() {
  try {
    const res = await api.version();
    const ver = res?.version;
    if (!ver) return;
    const prev = localStorage.getItem('client_version');
    if (prev && prev !== ver) {
      showToast(`Updated to v${ver} — Reloading...`);
      localStorage.setItem('client_version', ver);
      if ('caches' in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map(k => caches.delete(k)));
      }
      setTimeout(() => location.reload(), 2000);
    } else if (!prev) {
      localStorage.setItem('client_version', ver);
    }
  } catch {}
}

async function refreshOwnPresence() {
  try {
    const user = await api.me();
    if (user?.status && user?.id) {
      const presences = { ...state.presences, [user.id]: user.status };
      setState({ presences });
      document.querySelectorAll('.profile .friend-status-dot').forEach(el => {
        el.className = 'friend-status-dot ' + user.status;
      });
    }
  } catch {}
} 

function setupWebSocket() {
  socket.on('friends_update', (friends) => {
    setState({ friends, friendError: '' });
  });
  socket.on('dms_update', (dmsPayload) => {
    const activeId = state.selectedDmId || state.selectedChannelId;
    const dms = (dmsPayload.items || []).map(d => {
      if (d.id === activeId) {
        return { ...d, unread: false };
      }
      return d;
    });
    lastUnreadDmIds = new Set(dms.filter(d => d.unread).map(d => d.id));
    setState({ dms, dmReason: dmsPayload.reason || '' });
  });
  socket.on('notification', (notif) => {
    if (!notif || !notif.channelId) return;
    const activeId = state.selectedDmId || state.selectedChannelId;
    const isMention = isUserMentioned(notif, state.user);
    if (isMention) {
      playPingSound();
    }
    if (notif.channelId === activeId) return;

    const channelId = String(notif.channelId);
    let targetGuildId = notif.guildId || null;
    if (!targetGuildId) {
      for (const g of state.guilds || []) {
        const gChs = state.channelsByGuild[g.id]?.text || [];
        if (gChs.some(c => String(c.id) === channelId)) {
          targetGuildId = g.id;
          break;
        }
      }
    }

    const patch = {};
    if (targetGuildId) {
      patch.guildUnreads = { ...state.guildUnreads, [targetGuildId]: true };
      patch.channelUnreads = { ...state.channelUnreads, [channelId]: true };
      if (isMention) {
        patch.guildMentions = { ...state.guildMentions, [targetGuildId]: (state.guildMentions[targetGuildId] || 0) + 1 };
        patch.channelMentions = { ...state.channelMentions, [channelId]: (state.channelMentions[channelId] || 0) + 1 };
      }
    } else {
      patch.dms = (state.dms || []).map(d => d.id === channelId ? { ...d, unread: true } : d);
      if (isMention) {
        patch.dmMentions = { ...state.dmMentions, [channelId]: (state.dmMentions[channelId] || 0) + 1 };
      }
    }
    setState(patch);

    const title = isMention
      ? `${notif.author || "Someone"} mentioned you`
      : `New message from ${notif.author || "someone"}`;
    const route = targetGuildId ? `/channels/${targetGuildId}/${channelId}` : `/channels/@me/${channelId}`;
    pushNotification({
      title,
      description: notif.content || (isMention ? "You were mentioned" : "New message"),
      avatarUrl: notif.avatarUrl,
      actionLabel: targetGuildId ? "Open Channel" : "Open DM",
      onClick: () => goToRoute(route),
    });
  });
  socket.on('session_expired', (payload) => {
    const message = (payload && payload.message) || 'Session expired';
    window.location.assign('/login?error=' + encodeURIComponent(message));
  });
  socket.on('presence_update', (payload) => {
    const { userId, status } = payload;
    if (!userId) return;
    const presences = { ...state.presences, [userId]: status };
    setState({ presences });
    updateMemberPresence(userId, status);
    if (state.user?.id === userId) {
      document.querySelectorAll('.profile .friend-status-dot').forEach(el => {
        el.className = 'friend-status-dot ' + status;
      });
    }
    const friends = state.friends;
    if (friends) {
      let changed = false;
      for (const list of [friends.friends, friends.pending]) {
        for (const f of list) {
          if (f.id === userId) {
            f.status = status;
            changed = true;
          }
        }
      }
      if (changed) setState({ friends: { ...friends } });
    }
    const dms = state.dms;
    if (dms) {
      let changed = false;
      for (const dm of dms) {
        if (dm.userId === userId) {
          dm.status = status;
          changed = true;
        }
      }
      if (changed) setState({ dms: [...dms] });
    }
  });
  socket.on('typing_start', (payload) => {
    if (!payload || !payload.channelId || !payload.userId) return;
    const { channelId, userId } = payload;
    if (state.user && (userId === state.user.id || userId === String(state.user.id))) return;
    let resolvedName = payload.username;
    if (!resolvedName || resolvedName === 'Someone') {
      const uIdStr = String(userId);
      const chIdStr = String(channelId);
      const friend = state.friends?.friends?.find(f => String(f.id) === uIdStr);
      if (friend) {
        resolvedName = friend.displayName || friend.username;
      } else {
        const dm = state.dms?.find(d => String(d.userId) === uIdStr || String(d.id) === chIdStr);
        if (dm) {
          resolvedName = dm.displayName || dm.name || dm.username;
        }
      }
    }
    const finalName = resolvedName || 'Someone';
    const channelTyping = { ...(state.typingByChannel?.[channelId] || {}) };
    if (channelTyping[userId]?.timeoutId) {
      clearTimeout(channelTyping[userId].timeoutId);
    }
    const timeoutId = setTimeout(() => {
      const cur = { ...(state.typingByChannel?.[channelId] || {}) };
      delete cur[userId];
      const next = { ...state.typingByChannel, [channelId]: cur };
      setState({ typingByChannel: next });
      updateTypingUI();
    }, 9000);
    channelTyping[userId] = { username: finalName, timeoutId };
    const next = { ...state.typingByChannel, [channelId]: channelTyping };
    setState({ typingByChannel: next });
    updateTypingUI();
  });
  socket.on('typing_stop', (payload) => {
    if (!payload || !payload.channelId) return;
    clearTypingUser(payload.channelId, payload.userId);
  });
  socket.on('message_reaction_add', (payload) => {
    if (!payload || !payload.channel_id || !payload.message_id) return;
    const isMe = String(payload.user_id) === String(state.user?.id);
    updateMessageReaction(payload.channel_id, payload.message_id, payload.emoji, true, isMe);
  });
  socket.on('message_reaction_remove', (payload) => {
    if (!payload || !payload.channel_id || !payload.message_id) return;
    const isMe = String(payload.user_id) === String(state.user?.id);
    updateMessageReaction(payload.channel_id, payload.message_id, payload.emoji, false, isMe);
  });
  socket.on('disconnect', () => {
    console.warn('[ws] Disconnected, retrying in 5s…');
    setTimeout(() => {
      if (!socket.connected) {
        socket.connect();
      }
    }, 5000);
  });
}
