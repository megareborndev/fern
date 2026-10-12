export const state = {
    loading: true,
    error: "",
    user: null,
    guilds: [],
    dms: [],
    dmReason: "",
    friends: { pending: [], friends: [], blocked: [] },
    friendError: "",
    messagesByDm: {},
    membersByChannel: {},
    presences: {},
    messagesLoading: false,
    messageError: "",
    selectedGuildId: null,
    selectedDmId: null,
    selectedChannelId: null,
    channelsByGuild: {},
    channelsLoading: false,
    activePanel: null,
    showAddFriend: false,
    friendFilter: "Online",
    contextMenu: null,
    replyTo: null,
    reactions: {},
    pins: new Set(),
    unreadMarkers: {},
    readCursors: {},
    encryptionEnabled: localStorage.getItem("fern_encryption_enabled") === null ? true : localStorage.getItem("fern_encryption_enabled") === "1",
    friendFormValue: "",
    settings: {
        showAllChannels: localStorage.getItem("fern_settings_show_all") === "1",
        obscure: localStorage.getItem("fern_settings_obscure") === "1",
        pingSound: localStorage.getItem("fern_settings_ping_sound") !== "0",
    },
    folders: [],
    typingByChannel: {},
    guildUnreads: {},
    guildMentions: {},
    channelUnreads: {},
    channelMentions: {},
    dmMentions: {},
};

(function loadFolders() {
    try {
        const raw = localStorage.getItem("fern_folders");
        if (raw) state.folders = JSON.parse(raw);
    } catch {}
})();

function saveFolders() {
    try { localStorage.setItem("fern_folders", JSON.stringify(state.folders)); } catch {}
}

const listeners = new Set();

export function subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
}

export function setState(patch) {
    Object.assign(state, patch);
    for (const listener of listeners) listener(state);
}

export function selectGuild(guildId) {
    setState({ selectedGuildId: guildId, selectedDmId: null, selectedChannelId: null, activePanel: null });
}

export function selectGuildChannel(channelId) {
    setState({ selectedChannelId: channelId, selectedDmId: null, activePanel: null, messageError: "", messagesLoading: true });
}

export function selectDm(dmId) {
    setState({ selectedDmId: dmId, selectedGuildId: null, selectedChannelId: null, activePanel: null, messageError: "", messagesLoading: true });
}

export function setActivePanel(panel) {
    setState({ activePanel: panel, selectedDmId: null, selectedGuildId: null, selectedChannelId: null });
}

export function setGuildChannels(guildId, channels) {
    setState({
        channelsByGuild: {
            ...state.channelsByGuild,
            [guildId]: channels
        }
    });
}

export function setDmMessages(dmId, newMessages) {
  const existing = state.messagesByDm[dmId] || [];
  // build map
  const existingMap = new Map(existing.map(m => [m.id, m]));
  let changed = false;
  const merged = [];
  for (const msg of newMessages) {
    const existingMsg = existingMap.get(msg.id);
    if (!existingMsg) {
      //
      merged.push(msg);
      changed = true;
    } else if (JSON.stringify(existingMsg) !== JSON.stringify(msg)) {
      merged.push(msg);
      changed = true;
    } else {
      merged.push(existingMsg);
    }
  }
  const newIds = new Set(newMessages.map(m => m.id));
  for (const msg of existing) {
    if (!newIds.has(msg.id)) {
      changed = true;
    }
  }
  if (changed) {
    let nextTyping = state.typingByChannel;
    const typing = state.typingByChannel?.[dmId];
    if (typing) {
      let curTyping = { ...typing };
      let typingChanged = false;
      for (const msg of newMessages) {
        if (!existingMap.has(msg.id)) {
          const aId = String(msg.author_id || msg.author?.id || "");
          for (const k of Object.keys(curTyping)) {
            if (k === aId || curTyping[k]?.username === msg.author || curTyping[k]?.username === msg.authorDisplay) {
              if (curTyping[k]?.timeoutId) clearTimeout(curTyping[k].timeoutId);
              delete curTyping[k];
              typingChanged = true;
            }
          }
          if (state.selectedDmId === dmId) {
            for (const k of Object.keys(curTyping)) {
              if (curTyping[k]?.timeoutId) clearTimeout(curTyping[k].timeoutId);
            }
            curTyping = {};
            typingChanged = true;
            break;
          }
        }
      }
      if (typingChanged) {
        nextTyping = { ...state.typingByChannel };
        if (Object.keys(curTyping).length === 0) {
          delete nextTyping[dmId];
        } else {
          nextTyping[dmId] = curTyping;
        }
      }
    }
    setState({
      messagesByDm: {
        ...state.messagesByDm,
        [dmId]: merged
      },
      typingByChannel: nextTyping
    });
  }
}

export function addDmMessage(dmId, message) {
    setDmMessages(dmId, [...(state.messagesByDm[dmId] || []), message]);
}

export function updateMessageReaction(channelId, messageId, emojiObj, isAdd, isMe = true) {
  const msgs = state.messagesByDm[channelId];
  if (!msgs || !msgs.length) return;
  let emojiName = "";
  let emojiId = null;
  let emojiAnimated = false;
  if (typeof emojiObj === "string") {
    const match = /^<(a)?:([A-Za-z0-9_]+):(\d{17,21})>$/.exec(emojiObj);
    if (match) {
      emojiAnimated = Boolean(match[1]);
      emojiName = match[2];
      emojiId = match[3];
    } else {
      emojiName = emojiObj;
    }
  } else if (emojiObj && typeof emojiObj === "object") {
    emojiName = emojiObj.name || "";
    emojiId = emojiObj.id || null;
    emojiAnimated = Boolean(emojiObj.animated);
  }

  let msgFound = false;
  const updated = msgs.map(msg => {
    if (String(msg.id) !== String(messageId)) return msg;
    msgFound = true;
    const curReactions = Array.isArray(msg.reactions) ? [...msg.reactions] : [];
    const idx = curReactions.findIndex(r => {
      if (emojiId && r.emoji?.id) return String(r.emoji.id) === String(emojiId);
      if (!emojiId && !r.emoji?.id) return (r.emoji?.name || "") === emojiName;
      return false;
    });

    if (isAdd) {
      if (idx >= 0) {
        const existing = { ...curReactions[idx] };
        if (isMe) {
          if (existing.me) return msg;
          existing.me = true;
          existing.count = (existing.count || 0) + 1;
        } else {
          existing.count = (existing.count || 0) + 1;
        }
        curReactions[idx] = existing;
      } else {
        curReactions.push({
          count: 1,
          me: Boolean(isMe),
          emoji: {
            name: emojiName,
            id: emojiId,
            animated: emojiAnimated
          }
        });
      }
    } else {
      if (idx >= 0) {
        const existing = { ...curReactions[idx] };
        if (isMe) {
          if (!existing.me) return msg;
          existing.me = false;
          existing.count = Math.max(0, (existing.count || 1) - 1);
        } else {
          existing.count = Math.max(0, (existing.count || 1) - 1);
        }
        if (existing.count <= 0) {
          curReactions.splice(idx, 1);
        } else {
          curReactions[idx] = existing;
        }
      } else {
        return msg;
      }
    }
    return { ...msg, reactions: curReactions };
  });

  if (msgFound) {
    setState({
      messagesByDm: {
        ...state.messagesByDm,
        [channelId]: updated
      }
    });
  }
}

export function setReadCursor(channelId, lastMessageId) {
  setState({
    readCursors: {
      ...state.readCursors,
      [channelId]: lastMessageId,
    },
  });
}

export function setReplyTo(reply) {
    setState({ replyTo: reply });
}

export function clearReply() {
    setState({ replyTo: null });
}

export function createFolder(name) {
    const id = "folder_" + Date.now();
    const folder = { id, name, guildIds: [], collapsed: false };
    state.folders.push(folder);
    saveFolders();
    setState({});
}

export function deleteFolder(folderId) {
    state.folders = state.folders.filter(f => f.id !== folderId);
    saveFolders();
    setState({});
}

export function renameFolder(folderId, name) {
    const f = state.folders.find(f => f.id === folderId);
    if (f) { f.name = name; saveFolders(); setState({}); }
}

export function toggleFolder(folderId) {
    const f = state.folders.find(f => f.id === folderId);
    if (f) { f.collapsed = !f.collapsed; saveFolders(); setState({}); }
}

export function addGuildToFolder(guildId, folderId) {
    for (const f of state.folders) {
        f.guildIds = f.guildIds.filter(id => id !== guildId);
    }
    const target = state.folders.find(f => f.id === folderId);
    if (target && !target.guildIds.includes(guildId)) {
        target.guildIds.push(guildId);
    }
    saveFolders();
    setState({});
}

export function removeGuildFromFolder(guildId) {
    for (const f of state.folders) {
        f.guildIds = f.guildIds.filter(id => id !== guildId);
    }
    saveFolders();
    setState({});
    return null;
}

export function getFolderForGuild(guildId) {
    return state.folders.find(f => f.guildIds.includes(guildId)) || null;
}

export function getFolderGuilds(folderId) {
    const folder = state.folders.find(f => f.id === folderId);
    if (!folder) return [];
    return state.guilds.filter(g => folder.guildIds.includes(g.id));
}

const _deleteCooldown = new Map();

export function markDeleteCooldown(channelId, messageId) {
    _deleteCooldown.set(channelId, messageId);
}

export function checkDeleteCooldown(channelId) {
    const messageId = _deleteCooldown.get(channelId);
    _deleteCooldown.delete(channelId);
    return messageId || null;
}