import { api } from "./api.js";
import { state, setState, setReplyTo, clearReply, setDmMessages, markDeleteCooldown, setReadCursor, updateMessageReaction } from "./state.js";
import { parseTwemoji } from "./twemoji-util.js";

// helpers
function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
}

function getChannelIdFromMessage(message, channelId) {
    return channelId || message.channel_id || null;
}

function getGuildIdFromMessage(message, guildId) {
    return guildId || message.guild_id || null;
}

function buildMessageUrl(message, channelId, guildId) {
    const base = "https://discord.com/channels";
    const guildPart = guildId ? guildId : "@me";
    return `${base}/${guildPart}/${channelId}/${message.id}`;
}

// toast
function showNotification(text, type = "info") {
    const existing = document.querySelector(".toast-notification");
    if (existing) existing.remove();
    const toast = document.createElement("div");
    toast.className = "toast-notification";
    toast.textContent = text;
    Object.assign(toast.style, {
        position: "fixed",
        bottom: "24px",
        left: "50%",
        transform: "translateX(-50%)",
        background: type === "error" ? "#f23f42" : "var(--bg-2)",
        color: "var(--text-primary)",
        padding: "10px 20px",
        borderRadius: "8px",
        fontFamily: "'gg sans', sans-serif",
        fontSize: "14px",
        boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
        zIndex: 999999,
        maxWidth: "90vw",
        textAlign: "center",
        animation: "fadeInUp 200ms ease-out",
    });
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateX(-50%) translateY(16px)";
        toast.style.transition = "opacity 200ms, transform 200ms";
        setTimeout(() => toast.remove(), 250);
    }, 3000);
}

// actions
const actions = [
    {
        id: "reply",
        label: "Reply",
        icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M10 9V5l-7 7 7 7v-4.1c5 0 8.5 1.6 11 5.1-1-5-4-10-11-11z" fill="currentColor"/></svg>',
        condition: () => true,
        handler: async (message, channelId, guildId) => {
            setReplyTo({ message, channelId, guildId });
            hideContextMenu();
            showReplyBar(message);
            const composer = document.querySelector(".message-composer textarea");
            if (composer) composer.focus();
        }
    },
    {
        id: "add_reaction",
        label: "Add Reaction",
        icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm3.5-9c.83 0 1.5-.67 1.5-1.5S16.33 8 15.5 8 14 8.67 14 9.5s.67 1.5 1.5 1.5zm-7 0c.83 0 1.5-.67 1.5-1.5S9.33 8 8.5 8 7 8.67 7 9.5 7.67 11 8.5 11zm3.5 6.5c2.33 0 4.31-1.46 5.11-3.5H6.89c.8 2.04 2.78 3.5 5.11 3.5z" fill="currentColor"/></svg>',
        condition: () => true,
        handler: (message, channelId, guildId) => {
            hideContextMenu();
            showEmojiPicker(message, channelId);
        }
    },
    {
        id: "forward",
        label: "Forward",
        icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 4l-1.41 1.41L16.17 11H4v2h12.17l-5.58 5.59L12 20l8-8z" fill="currentColor"/></svg>',
        condition: () => true,
        handler: (message, channelId, guildId) => {
            hideContextMenu();
            showForwardModal(message, channelId, guildId);
        }
    },
    {
        id: "copy_text",
        label: "Copy Text",
        icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z" fill="currentColor"/></svg>',
        condition: () => true,
        handler: (message) => {
            navigator.clipboard.writeText(message.content).then(() => {
                showNotification("Text copied to clipboard");
            }).catch(() => {
                // fallback
                const textarea = document.createElement("textarea");
                textarea.value = message.content;
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand("copy");
                document.body.removeChild(textarea);
                showNotification("Text copied to clipboard");
            });
            hideContextMenu();
        }
    },
    {
        id: "copy_link",
        label: "Copy Message Link",
        icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M3.9 12c0-1.71 1.39-3.1 3.1-3.1h4V7H7c-2.76 0-5 2.24-5 5s2.24 5 5 5h4v-1.9H7c-1.71 0-3.1-1.39-3.1-3.1zM8 13h8v-2H8v2zm9-6h-4v1.9h4c1.71 0 3.1 1.39 3.1 3.1s-1.39 3.1-3.1 3.1h-4V17h4c2.76 0 5-2.24 5-5s-2.24-5-5-5z" fill="currentColor"/></svg>',
        condition: () => true,
        handler: (message, channelId, guildId) => {
            const url = buildMessageUrl(message, channelId, guildId);
            navigator.clipboard.writeText(url).then(() => {
                showNotification("Message link copied");
            }).catch(() => {
                // fallback
                const textarea = document.createElement("textarea");
                textarea.value = url;
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand("copy");
                document.body.removeChild(textarea);
                showNotification("Message link copied");
            });
            hideContextMenu();
        }
    },
    {
        id: "copy_id",
        label: "Copy Message ID",
        icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M19 3h-4.18C14.4 1.84 13.3 1 12 1c-1.3 0-2.4.84-2.82 2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-7 0c.55 0 1 .45 1 1s-.45 1-1 1-1-.45-1-1 .45-1 1-1zm2 14H7v-2h7v2zm3-4H7v-2h10v2zm0-4H7V7h10v2z" fill="currentColor"/></svg>',
        condition: () => true,
        handler: (message) => {
            navigator.clipboard.writeText(message.id).then(() => {
                showNotification("Message ID copied");
            }).catch(() => {
                const textarea = document.createElement("textarea");
                textarea.value = message.id;
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand("copy");
                document.body.removeChild(textarea);
                showNotification("Message ID copied");
            });
            hideContextMenu();
        }
    },
    {
        id: "copy_author_id",
        label: "Copy Author ID",
        icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4zm0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4z" fill="currentColor"/></svg>',
        condition: () => true,
        handler: (message) => {
            const authorId = message.author_id || message.author?.id;
            if (authorId) {
                navigator.clipboard.writeText(authorId).then(() => {
                    showNotification("Author ID copied");
                }).catch(() => {
                    const textarea = document.createElement("textarea");
                    textarea.value = authorId;
                    document.body.appendChild(textarea);
                    textarea.select();
                    document.execCommand("copy");
                    document.body.removeChild(textarea);
                    showNotification("Author ID copied");
                });
            } else {
                showNotification("No author ID available", "error");
            }
            hideContextMenu();
        }
    },
    {
        id: "copy_channel_id",
        label: "Copy Channel ID",
        icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M20 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm0 14H4V6h16v12z" fill="currentColor"/></svg>',
        condition: () => true,
        handler: (message, channelId) => {
            if (channelId) {
                navigator.clipboard.writeText(channelId).then(() => {
                    showNotification("Channel ID copied");
                }).catch(() => {
                    const textarea = document.createElement("textarea");
                    textarea.value = channelId;
                    document.body.appendChild(textarea);
                    textarea.select();
                    document.execCommand("copy");
                    document.body.removeChild(textarea);
                    showNotification("Channel ID copied");
                });
            } else {
                showNotification("No channel ID available", "error");
            }
            hideContextMenu();
        }
    },
    {
      id: "mark_unread",
      label: "Mark as Unread",
      icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm-.89-13l-1.42 1.41L11.29 10H7v2h4.29l-1.6 1.59L11.11 15l3.5-3.5L11.11 8z" fill="currentColor"/></svg>',
      condition: () => true,
       handler: (message, channelId) => {
         if (channelId) {
           const messages = state.messagesByDm[channelId] || [];
           const index = messages.findIndex(m => m.id === message.id);
           let targetId = "";
           if (index !== -1) {
             const prevIndex = index - 1;
             if (prevIndex >= 0) {
               targetId = messages[prevIndex].id;
             }
           }
           setReadCursor(channelId, targetId);
           api.markRead(channelId, targetId).catch(() => {});
           showNotification("Message marked as unread");
         }
         hideContextMenu();
       }
    },
    {
        id: "details",
        label: "View Details",
        icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm1-13h-2v6h2V7zm0 8h-2v2h2v-2z" fill="currentColor"/></svg>',
        condition: () => true,
        handler: (message) => {
            hideContextMenu();
            showDetailModal(message);
        }
    },
    // parent only
    {
        id: "edit",
        label: "Edit Message",
        icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z" fill="currentColor"/></svg>',
        condition: (message, channelId, guildId, state) => {
            const isMine = message.author?.id === state.user?.id || message.author_id === state.user?.id;
            return isMine;
        },
        handler: (message, channelId) => {
            hideContextMenu();
            const newContent = prompt("Edit your message:", message.content);
            if (newContent !== null && newContent.trim() !== "" && newContent !== message.content) {
                api.editMessage(channelId, message.id, newContent.trim())
                    .then(() => {
                        const messages = state.messagesByDm[channelId] || [];
                        const idx = messages.findIndex(m => m.id === message.id);
                        if (idx !== -1) {
                            const updated = { ...messages[idx], content: newContent.trim() };
                            const newMessages = [...messages];
                            newMessages[idx] = updated;
                            setState({
                                messagesByDm: {
                                    ...state.messagesByDm,
                                    [channelId]: newMessages,
                                }
                            });
                        }
                        showNotification("Message edited");
                    })
                    .catch(err => {
                        showNotification("Failed to edit message: " + (err.message || "Unknown error"), "error");
                    });
            }
        }
    },
    {
        id: "delete",
        label: "Delete Message",
        icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z" fill="currentColor"/></svg>',
        condition: (message, channelId, guildId, state) => {
            const isMine = message.author?.id === state.user?.id || message.author_id === state.user?.id;
            return isMine;
        },
        handler: async (message, channelId) => {
            hideContextMenu();
            const confirmed = await showDeleteMessageModal(message);
            if (!confirmed) return;
            try {
                await api.deleteMessage(channelId, message.id);
                markDeleteCooldown(channelId, message.id);
                const messages = state.messagesByDm[channelId] || [];
                const filtered = messages.filter(m => m.id !== message.id);
                setState({
                    messagesByDm: {
                        ...state.messagesByDm,
                        [channelId]: filtered,
                    }
                });
                showNotification("Message deleted");
            } catch (err) {
                showNotification("Failed to delete message: " + (err.message || "Unknown error"), "error");
            }
        }
    },

    {
        id: "pin",
        label: "Pin Message",
        icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z" fill="currentColor"/></svg>',
        condition: (message, channelId, guildId, state) => {
            return !state.pins.has(message.id);
        },
        handler: async (message, channelId) => {
            hideContextMenu();
            try {
                await api.pinMessage(channelId, message.id);
                state.pins.add(message.id);
                const payload = await api.messages(channelId);
                if (payload && payload.items) {
                    setDmMessages(channelId, payload.items);
                }
            } catch (err) {
                showNotification("Failed to pin message: " + (err.message || "Unknown error"), "error");
            }
        }
    },
    {
        id: "unpin",
        label: "Unpin Message",
        icon: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z" fill="currentColor"/></svg>',
        condition: (message, channelId, guildId, state) => {
            return state.pins.has(message.id);
        },
        handler: async (message, channelId) => {
            hideContextMenu();
            try {
                await api.unpinMessage(channelId, message.id);
                state.pins.delete(message.id);
                showNotification("Message unpinned");
            } catch (err) {
                showNotification("Failed to unpin message: " + (err.message || "Unknown error"), "error");
            }
        }
    },
];

function showReplyBar(message) {
    const existing = document.querySelector(".reply-bar");
    if (existing) existing.remove();
    const author = message.authorDisplay || message.author || "Unknown";
    const avatarUrl = message.authorAvatar || "";
    const avatarHtml = avatarUrl
        ? `<img src="${escapeHtml(avatarUrl)}" alt="" class="reply-bar-avatar">`
        : `<div class="reply-bar-avatar placeholder">${escapeHtml(initials(author))}</div>`;
    const bar = document.createElement("div");
    bar.className = "reply-bar";
    bar.innerHTML = '<div class="reply-bar-indicator"></div>' + avatarHtml + '<div class="reply-bar-info"><span class="reply-bar-author">' + escapeHtml(author) + '</span><span class="reply-bar-content">' + escapeHtml((message.content || "").slice(0, 80)) + '</span></div><button type="button" class="reply-bar-close" data-cancel-reply aria-label="Cancel reply"><svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M18 6L6 18M6 6l12 12" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/></svg></button>';
    const composer = document.querySelector(".message-composer");
    if (composer) {
        composer.parentNode.insertBefore(bar, composer);
    }
}

function initials(name) {
    return String(name || "?").trim().split(/\s+/).map(function(p) { return p[0]; }).join("").slice(0, 2).toUpperCase();
}

export function hideReplyBar() {
    const bar = document.querySelector(".reply-bar");
    if (bar) bar.remove();
}

// DOM
let menuElement = null;
let menuCloseListeners = [];

function hideContextMenu() {
    if (menuElement) {
        menuElement.remove();
        menuElement = null;
    }
    for (const listener of menuCloseListeners) {
        document.removeEventListener(listener.type, listener.handler);
    }
    menuCloseListeners = [];
}

function showContextMenu(event, message, channelId, guildId) {
    hideContextMenu();

    const availableActions = actions.filter(action => {
        try {
            return action.condition(message, channelId, guildId, state);
        } catch (err) {
            console.error("Context menu condition error for", action.id, err);
            return false;
        }
    });

    if (availableActions.length === 0) {
        showNotification("No actions available", "error");
        return;
    }

    const menu = document.createElement("div");
    menu.className = "message-context-menu";
    menu.style.cssText = `
        position: fixed;
        background: var(--bg-2);
        border-radius: 8px;
        box-shadow: 0 8px 24px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.05);
        min-width: 200px;
        max-width: 320px;
        max-height: calc(100vh - 24px);
        overflow-y: auto;
        padding: 6px 0;
        z-index: 99999;
        font-family: 'gg sans', Whitney, 'Helvetica Neue', Helvetica, Arial, sans-serif;
        font-size: 14px;
        color: var(--text-normal);
        animation: contextMenuIn 150ms ease-out;
        visibility: hidden;
    `;

    for (const action of availableActions) {
        const item = document.createElement("div");
        item.className = "context-menu-item";
        item.style.cssText = `
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 8px 16px;
            cursor: pointer;
            transition: background 100ms ease;
            border-radius: 4px;
            margin: 2px 6px;
        `;
        item.innerHTML = `
            <span style="width: 18px; height: 18px; flex-shrink: 0; color: var(--text-secondary);">${action.icon}</span>
            <span>${action.label}</span>
        `;
        item.addEventListener("mouseenter", () => {
            item.style.background = "var(--bg-mid)";
        });
        item.addEventListener("mouseleave", () => {
            item.style.background = "transparent";
        });
        item.addEventListener("click", (e) => {
            e.stopPropagation();
            action.handler(message, channelId, guildId);
        });
        menu.appendChild(item);
    }

    document.body.appendChild(menu);
    menuElement = menu;

    const zoom = parseFloat(getComputedStyle(document.documentElement).zoom) || 1;
    const vpWidth = window.innerWidth / zoom;
    const vpHeight = window.innerHeight / zoom;
    const clickX = event.clientX / zoom;
    const clickY = event.clientY / zoom;

    const mWidth = menu.offsetWidth || 240;
    const mHeight = menu.offsetHeight || (availableActions.length * 36 + 12);

    let x = clickX;
    let y = clickY;

    if (clickY + mHeight > vpHeight - 12) {
        y = Math.max(12, clickY - mHeight);
    } else {
        y = Math.max(12, clickY);
    }

    if (clickX + mWidth > vpWidth - 12) {
        x = Math.max(12, clickX - mWidth);
    } else {
        x = Math.max(12, clickX);
    }

    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;
    menu.style.visibility = "visible";

    const closeMenu = (e) => {
        if (menuElement && !menuElement.contains(e.target)) {
            hideContextMenu();
        }
    };
    const escapeHandler = (e) => {
        if (e.key === "Escape") hideContextMenu();
    };
    document.addEventListener("click", closeMenu);
    document.addEventListener("contextmenu", closeMenu);
    document.addEventListener("keydown", escapeHandler);
    menuCloseListeners = [
        { type: "click", handler: closeMenu },
        { type: "contextmenu", handler: closeMenu },
        { type: "keydown", handler: escapeHandler },
    ];
}

const commonEmojis = ["👍", "❤️", "😂", "😮", "😢", "🙏", "👀", "🔥", "⭐", "💯", "👎", "💀"];

function showEmojiPicker(message, channelId) {
    const overlay = document.createElement("div");
    overlay.className = "emoji-picker-overlay";
    overlay.style.cssText = `
        position: fixed;
        inset: 0;
        z-index: 99998;
        display: flex;
        align-items: center;
        justify-content: center;
        background: rgba(0,0,0,0.4);
        backdrop-filter: blur(2px);
        animation: fadeIn 150ms ease-out;
    `;
    const picker = document.createElement("div");
    picker.style.cssText = `
        background: var(--bg-3);
        border-radius: 12px;
        padding: 24px;
        max-width: 480px;
        width: 92%;
        box-shadow: 0 16px 48px rgba(0,0,0,0.6);
        color: var(--text-primary);
        font-family: 'gg sans', sans-serif;
    `;
    picker.innerHTML = `
        <h3 style="margin:0 0 14px; font-size:16px; font-weight:600;">Add a reaction</h3>
        <div style="display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 18px;">
            ${commonEmojis.map(e => `<button type="button" class="emoji-btn" data-emoji="${e}" style="font-size:26px; min-width:44px; min-height:42px; background:transparent; border:1px solid var(--bg-hover); border-radius:8px; padding:6px 12px; cursor:pointer; transition:background 100ms, transform 100ms; display:flex; align-items:center; justify-content:center;">${parseTwemoji(e)}</button>`).join('')}
        </div>
        <div style="display:flex; gap:8px;">
            <input type="text" id="custom-emoji-input" placeholder="Enter emoji (e.g. 😊)" style="flex:1; background:var(--bg-2); border:1px solid var(--bg-hover); border-radius:6px; padding:10px 12px; color:var(--text-primary); font-size:16px;">
            <button type="button" id="add-custom-emoji" style="background:var(--accent); border:0; border-radius:6px; padding:10px 18px; color:#fff; font-weight:600; cursor:pointer;">Add</button>
        </div>
        <div style="margin-top:12px; text-align:right;">
            <button type="button" id="close-emoji-picker" style="background:transparent; border:0; color:var(--text-secondary); cursor:pointer; font-size:14px;">Cancel</button>
        </div>
    `;
    overlay.appendChild(picker);
    document.body.appendChild(overlay);

    const close = () => overlay.remove();

    const addReaction = async (emoji) => {
        try {
            updateMessageReaction(channelId, message.id, emoji, true, true);
            close();
            await api.addReaction(channelId, message.id, emoji);
        } catch (err) {
            updateMessageReaction(channelId, message.id, emoji, false, true);
            showNotification(`Failed to add reaction: ${err.message || "Unknown error"}`, "error");
        }
    };

    picker.querySelectorAll(".emoji-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const emoji = btn.dataset.emoji;
            addReaction(emoji);
        });
        btn.addEventListener("mouseenter", () => {
            btn.style.background = "var(--bg-mid)";
        });
        btn.addEventListener("mouseleave", () => {
            btn.style.background = "transparent";
        });
    });

    picker.querySelector("#add-custom-emoji").addEventListener("click", () => {
        const input = picker.querySelector("#custom-emoji-input");
        const emoji = input.value.trim();
        if (emoji) {
            addReaction(emoji);
        } else {
            showNotification("Please enter an emoji", "error");
        }
    });

    picker.querySelector("#custom-emoji-input").addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            picker.querySelector("#add-custom-emoji").click();
        }
    });

    picker.querySelector("#close-emoji-picker").addEventListener("click", close);
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) close();
    });
}

function formatPreviewTime(val) {
    if (!val) return "";
    try {
        const d = new Date(val);
        return isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch {
        return "";
    }
}

function showDeleteMessageModal(message) {
    return new Promise((resolve) => {
        const existing = document.querySelector(".delete-message-modal-overlay");
        if (existing) existing.remove();

        const overlay = document.createElement("div");
        overlay.className = "delete-message-modal-overlay";
        overlay.style.cssText = `
            position: fixed;
            inset: 0;
            z-index: 999999;
            display: flex;
            align-items: center;
            justify-content: center;
            background: rgba(0, 0, 0, 0.7);
            backdrop-filter: blur(4px);
            padding: 16px;
            animation: fadeIn 150ms ease-out;
        `;

        const authorName = message.authorDisplay || (typeof message.author === "object" ? (message.author.displayName || message.author.username) : message.author) || state.user?.displayName || state.user?.username || "User";
        const authorAvatar = message.authorAvatar || (typeof message.author === "object" ? message.author.avatarUrl : null) || state.user?.avatarUrl || "";
        const timeStr = formatPreviewTime(message.createdAt || message.timestamp);
        const contentText = escapeHtml(message.content || "");

        const avatarHtml = authorAvatar
            ? `<img src="${escapeHtml(authorAvatar)}" alt="" style="width:36px;height:36px;border-radius:50%;object-fit:cover;flex-shrink:0;">`
            : `<div style="width:36px;height:36px;border-radius:50%;background:var(--bg-mid, #2b2d31);color:var(--text-normal, #dbdee1);display:flex;align-items:center;justify-content:center;font-weight:600;font-size:13px;flex-shrink:0;">${escapeHtml((authorName[0] || "U").toUpperCase())}</div>`;

        const modal = document.createElement("div");
        modal.style.cssText = `
            background: var(--bg-2, #313338);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            padding: 20px 24px;
            max-width: 440px;
            width: 100%;
            box-shadow: 0 16px 40px rgba(0, 0, 0, 0.6);
            color: var(--text-primary, #f2f3f5);
            font-family: inherit;
            display: flex;
            flex-direction: column;
            gap: 16px;
        `;

        modal.innerHTML = `
            <div style="display:flex;flex-direction:column;gap:6px;">
                <h3 style="margin:0;font-size:18px;font-weight:700;color:var(--text-primary, #f2f3f5);">Delete Message</h3>
                <p style="margin:0;font-size:14px;color:var(--text-secondary, #949ba4);line-height:1.4;">Are you sure you want to delete this message?</p>
            </div>
            <div style="background:var(--bg-1, #2b2d31);border:1px solid rgba(255,255,255,0.06);border-radius:6px;padding:12px 14px;display:flex;gap:12px;align-items:flex-start;max-height:140px;overflow-y:auto;">
                ${avatarHtml}
                <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:3px;">
                    <div style="display:flex;align-items:baseline;gap:8px;">
                        <span style="font-size:14px;font-weight:600;color:var(--text-primary, #f2f3f5);">${escapeHtml(authorName)}</span>
                        ${timeStr ? `<span style="font-size:11px;color:var(--text-faint, #80848e);">${escapeHtml(timeStr)}</span>` : ""}
                    </div>
                    <div style="font-size:13.5px;color:var(--text-secondary, #dbdee1);word-break:break-word;line-height:1.4;">${contentText || '<em style="color:var(--text-faint,#80848e);">Attachment or embed</em>'}</div>
                </div>
            </div>
            <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:4px;">
                <button type="button" id="del-modal-cancel" style="padding:8px 18px;background:transparent;border:none;border-radius:4px;color:var(--text-normal, #dbdee1);font-size:14px;font-weight:500;cursor:pointer;transition:background 0.15s,color 0.15s;">Cancel</button>
                <button type="button" id="del-modal-confirm" style="padding:8px 20px;background:#da373c;border:none;border-radius:4px;color:#ffffff;font-size:14px;font-weight:600;cursor:pointer;transition:background 0.15s;box-shadow:0 2px 8px rgba(218,55,60,0.35);">Delete</button>
            </div>
        `;

        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        let closed = false;
        const close = (result) => {
            if (closed) return;
            closed = true;
            document.removeEventListener("keydown", onKeyDown);
            overlay.remove();
            resolve(result);
        };

        const onKeyDown = (e) => {
            if (e.key === "Escape") close(false);
            else if (e.key === "Enter") close(true);
        };

        document.addEventListener("keydown", onKeyDown);

        const cancelBtn = modal.querySelector("#del-modal-cancel");
        const confirmBtn = modal.querySelector("#del-modal-confirm");

        cancelBtn.onmouseenter = () => cancelBtn.style.background = "rgba(255, 255, 255, 0.07)";
        cancelBtn.onmouseleave = () => cancelBtn.style.background = "transparent";
        confirmBtn.onmouseenter = () => confirmBtn.style.background = "#b52c31";
        confirmBtn.onmouseleave = () => confirmBtn.style.background = "#da373c";

        cancelBtn.onclick = () => close(false);
        confirmBtn.onclick = () => close(true);
        overlay.onclick = (e) => {
            if (e.target === overlay) close(false);
        };
    });
}

function showForwardModal(message, channelId, guildId) {
    const overlay = document.createElement("div");
    overlay.className = "forward-modal-overlay";
    overlay.style.cssText = `
        position: fixed;
        inset: 0;
        z-index: 99998;
        display: flex;
        align-items: center;
        justify-content: center;
        background: rgba(0,0,0,0.6);
        backdrop-filter: blur(2px);
        animation: fadeIn 150ms ease-out;
        padding: 20px;
    `;
    const modal = document.createElement("div");
    modal.style.cssText = `
        background: var(--bg-3);
        border-radius: 12px;
        padding: 24px;
        max-width: 500px;
        width: 100%;
        max-height: 80vh;
        overflow-y: auto;
        box-shadow: 0 16px 48px rgba(0,0,0,0.6);
        color: var(--text-primary);
        font-family: 'gg sans', sans-serif;
    `;
    modal.innerHTML = `
        <h3 style="margin:0 0 12px;">Forward Message</h3>
        <p style="color:var(--text-secondary); margin-bottom:16px;">Select a destination:</p>
        <div id="forward-destinations" style="display:flex; flex-direction:column; gap:6px;"></div>
        <div style="margin-top:16px; text-align:right;">
            <button type="button" id="close-forward" style="background:transparent; border:0; color:var(--text-secondary); cursor:pointer; font-size:14px;">Cancel</button>
        </div>
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const close = () => overlay.remove();

    const destContainer = modal.querySelector("#forward-destinations");

    const dmItems = (state.dms || []).map(dm => ({
        id: dm.id,
        name: dm.displayName || dm.username,
        type: "dm",
        avatar: dm.avatarUrl,
    }));

    const guildItems = [];
    for (const guild of state.guilds || []) {
        const channels = state.channelsByGuild[guild.id]?.text || [];
        for (const ch of channels) {
            guildItems.push({
                id: ch.id,
                name: `${guild.name} / ${ch.name}`,
                type: "guild",
                guildId: guild.id,
                channelId: ch.id,
            });
        }
    }

    const allDestinations = [...dmItems, ...guildItems];

    if (allDestinations.length === 0) {
        destContainer.innerHTML = `<p style="color:var(--text-secondary);">No destinations available.</p>`;
    } else {
        for (const dest of allDestinations) {
            const item = document.createElement("div");
            item.style.cssText = `
                display: flex;
                align-items: center;
                gap: 10px;
                padding: 8px 12px;
                border-radius: 6px;
                cursor: pointer;
                transition: background 100ms ease;
            `;
            item.innerHTML = `
                <span style="font-size:20px;">${dest.type === "dm" ? "💬" : "📢"}</span>
                <span>${escapeHtml(dest.name)}</span>
            `;
            item.addEventListener("mouseenter", () => {
                item.style.background = "var(--bg-mid)";
            });
            item.addEventListener("mouseleave", () => {
                item.style.background = "transparent";
            });
            item.addEventListener("click", async () => {
                try {
                    const targetChannelId = dest.type === "dm" ? dest.id : dest.channelId;
                    await api.forwardMessage(channelId, message.id, targetChannelId);
                    showNotification(`Forwarded to ${dest.name}`);
                    close();
                } catch (err) {
                    var detail = err.message || "Unknown error";
                    if (err.status) detail += " (" + err.status + ")";
                    if (err.data && err.data.detail) detail += ": " + err.data.detail;
                    else if (err.data && err.data.error) detail += ": " + err.data.error;
                    showNotification("Failed to forward: " + detail, "error");
                }
            });
            destContainer.appendChild(item);
        }
    }

    modal.querySelector("#close-forward").addEventListener("click", close);
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) close();
    });
}

function showDetailModal(message) {
    const overlay = document.createElement("div");
    overlay.className = "detail-modal-overlay";
    overlay.style.cssText = `
        position: fixed;
        inset: 0;
        z-index: 99998;
        display: flex;
        align-items: center;
        justify-content: center;
        background: rgba(0,0,0,0.6);
        backdrop-filter: blur(2px);
        animation: fadeIn 150ms ease-out;
        padding: 20px;
    `;
    const modal = document.createElement("div");
    modal.style.cssText = `
        background: var(--bg-3);
        border-radius: 12px;
        padding: 24px;
        max-width: 480px;
        width: 100%;
        box-shadow: 0 16px 48px rgba(0,0,0,0.6);
        color: var(--text-primary);
        font-family: 'gg sans', sans-serif;
    `;
    const author = message.author || {};
    modal.innerHTML = `
        <h3 style="margin:0 0 12px;">Message Details</h3>
        <div style="display:grid; grid-template-columns: auto 1fr; gap:8px 16px; font-size:14px;">
            <span style="color:var(--text-secondary);">ID</span><span style="word-break:break-all;">${escapeHtml(message.id)}</span>
            <span style="color:var(--text-secondary);">Author</span><span>${escapeHtml(author.username || 'Unknown')} (${escapeHtml(author.id || 'N/A')})</span>
            <span style="color:var(--text-secondary);">Channel</span><span>${escapeHtml(message.channel_id || 'N/A')}</span>
            <span style="color:var(--text-secondary);">Timestamp</span><span>${escapeHtml(message.createdAt || message.timestamp || 'N/A')}</span>
            <span style="color:var(--text-secondary);">Content</span><span style="word-break:break-word;">${escapeHtml(message.content || '')}</span>
            ${message.attachments ? `<span style="color:var(--text-secondary);">Attachments</span><span>${message.attachments.length}</span>` : ''}
            ${message.reactions ? `<span style="color:var(--text-secondary);">Reactions</span><span>${Object.keys(message.reactions).length}</span>` : ''}
        </div>
        <div style="margin-top:20px; text-align:right;">
            <button type="button" id="close-detail" style="background:var(--accent); border:0; border-radius:4px; padding:8px 20px; color:#fff; font-weight:600; cursor:pointer;">Close</button>
        </div>
    `;
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    modal.querySelector("#close-detail").addEventListener("click", close);
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) close();
    });
}

export function initContextMenuListeners() {
    document.removeEventListener("contextmenu", contextMenuHandler);
    document.addEventListener("contextmenu", contextMenuHandler);
}

function contextMenuHandler(e) {
    const msgEl = e.target.closest(".message-row");
    if (!msgEl) return;
    e.preventDefault();
    const messageId = msgEl.dataset.messageId;
    const channelId = msgEl.dataset.channelId;
    const guildId = msgEl.dataset.guildId || null;
    if (!messageId || !channelId) {
        showNotification("Missing data-message-id or data-channel-id", "error");
        return;
    }

    const messages = state.messagesByDm[channelId] || [];
    var message = messages.find(function(m) { return m.id === messageId; });
    if (!message) {
        var allMsgs = [];
        for (var key in state.messagesByDm) {
            if (state.messagesByDm.hasOwnProperty(key)) {
                allMsgs = allMsgs.concat(state.messagesByDm[key]);
            }
        }
        message = allMsgs.find(function(m) { return m.id === messageId; });
    }
    if (!message) {
        message = { id: messageId, content: (msgEl.querySelector(".message-content")?.textContent) || "", author: (msgEl.querySelector(".message-meta strong")?.textContent) || "Unknown" };
    }
    showContextMenu(e, message, channelId, guildId);
}

const style = document.createElement("style");
style.textContent = `
    @keyframes contextMenuIn {
        from { opacity: 0; transform: scale(0.96) translateY(-4px); }
        to { opacity: 1; transform: scale(1) translateY(0); }
    }
    @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
    }
    @keyframes fadeInUp {
        from { opacity: 0; transform: translateX(-50%) translateY(16px); }
        to { opacity: 1; transform: translateX(-50%) translateY(0); }
    }
    .message-row {
        position: relative;
        cursor: default;
    }
    .context-menu-item {
        user-select: none;
    }
    .toast-notification {
        animation: fadeInUp 200ms ease-out;
    }
    .reply-bar {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 16px 4px;
        background: var(--bg-3);
        border-top: 1px solid var(--bg-hover);
        font-family: 'gg sans', sans-serif;
        font-size: 13px;
    }
    .reply-bar-indicator {
        width: 4px;
        height: 32px;
        border-radius: 2px;
        background: var(--link);
        flex-shrink: 0;
    }
    .reply-bar-info {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 2px;
    }
    .reply-bar-author {
        color: var(--link);
        font-weight: 600;
        font-size: 12px;
    }
    .reply-bar-content {
        color: var(--text-secondary);
        font-size: 13px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .reply-bar-close {
        background: none;
        border: none;
        color: var(--text-secondary);
        cursor: pointer;
        padding: 4px;
        border-radius: 4px;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
    }
    .reply-bar-close:hover {
        color: var(--text-normal);
        background: var(--bg-mid);
    }
    .reply-bar-avatar {
        width: 20px;
        height: 20px;
        border-radius: 50%;
        object-fit: cover;
        flex-shrink: 0;
    }
    .reply-bar-avatar.placeholder {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        background: var(--bg-3);
        color: var(--text-normal);
        font-size: 9px;
        font-weight: 700;
    }
    .reply-indicator {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 2px 0 0;
        margin-top: 0;
    }
    .message-row:has(.reply-indicator) .message-avatar {
        margin-top: 10px;
    }
    .reply-avatar-img {
        width: 18px;
        height: 18px;
        border-radius: 50%;
        object-fit: cover;
        flex-shrink: 0;
        z-index: 1;
    }
    .reply-avatar-placeholder {
        width: 18px;
        height: 18px;
        border-radius: 50%;
        background: var(--bg-3);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 8px;
        font-weight: 700;
        color: var(--text-normal);
        flex-shrink: 0;
        z-index: 1;
    }
    .reply-text {
        display: flex;
        align-items: center;
        gap: 6px;
        min-width: 0;
        flex: 1;
    }
    .reply-username {
        color: var(--link);
        font-size: 13px;
        font-weight: 500;
        white-space: nowrap;
        flex-shrink: 0;
    }
    .reply-preview {
        color: var(--text-muted);
        font-size: 13px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
`;
document.head.appendChild(style);