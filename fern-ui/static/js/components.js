
import { parseTwemoji, isOnlyEmoji } from "./twemoji-util.js";

export function dotClass(status) {
  if (status === "idle") return "idle";
  if (status === "dnd") return "dnd";
  if (status === "offline") return "offline";
  return "online";
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function initials(name) {
  return String(name || "?")
    .trim()
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function mentionNameHtml(message, id, fallback, selfUserId, roles) {
  const mentions = message?.mentions || [];
  const rolesInMessage = message?.mention_roles || [];
  const channels = message?.mention_channels || [];
  const strId = String(id);
  const user = mentions.find((m) => String(m.id) === strId);
  if (user) {
    const name = user.global_name || user.username || fallback;
    const isSelf = String(user.id) === String(selfUserId || "");
    return `<span class="mention${isSelf ? " mention-self" : ""}">@${escapeHtml(name)}</span>`;
  }
  if (rolesInMessage.includes(strId)) {
    const role = (roles || []).find((r) => String(r.id) === strId);
    const roleName = role?.name || fallback;
    const roleColor = role?.color ? ` style="color:#${Number(role.color).toString(16).padStart(6, "0")}"` : "";
    return `<span class="mention mention-role"${roleColor}>@${escapeHtml(roleName)}</span>`;
  }
  const channel = channels.find((c) => String(c.id) === strId);
  if (channel) {
    return `<span class="mention mention-channel">#${escapeHtml(channel.name || fallback)}</span>`;
  }
  return `<span class="mention">@${escapeHtml(fallback)}</span>`;
}

function formatDiscordTimestamp(unixSecs, format = "f") {
  const ms = Number(unixSecs) * 1000;
  const d = new Date(ms);
  if (isNaN(d.getTime())) return `<time class="msg-timestamp">${escapeHtml(unixSecs)}</time>`;
  const now = Date.now();
  const diffSec = Math.round((ms - now) / 1000);
  if (format === "R") {
    const absSec = Math.abs(diffSec);
    let rel = "";
    if (absSec < 60) rel = diffSec <= 0 ? "just now" : "in a few seconds";
    else if (absSec < 3600) {
      const mins = Math.round(absSec / 60);
      rel = diffSec < 0 ? `${mins} minute${mins === 1 ? "" : "s"} ago` : `in ${mins} minute${mins === 1 ? "" : "s"}`;
    } else if (absSec < 86400) {
      const hrs = Math.round(absSec / 3600);
      rel = diffSec < 0 ? `${hrs} hour${hrs === 1 ? "" : "s"} ago` : `in ${hrs} hour${hrs === 1 ? "" : "s"}`;
    } else if (absSec < 2592000) {
      const days = Math.round(absSec / 86400);
      rel = diffSec < 0 ? `${days} day${days === 1 ? "" : "s"} ago` : `in ${days} day${days === 1 ? "" : "s"}`;
    } else if (absSec < 31536000) {
      const months = Math.round(absSec / 2592000);
      rel = diffSec < 0 ? `${months} month${months === 1 ? "" : "s"} ago` : `in ${months} month${months === 1 ? "" : "s"}`;
    } else {
      const yrs = Math.round(absSec / 31536000);
      rel = diffSec < 0 ? `${yrs} year${yrs === 1 ? "" : "s"} ago` : `in ${yrs} year${yrs === 1 ? "" : "s"}`;
    }
    return `<time class="msg-timestamp" title="${escapeHtml(d.toLocaleString())}">${escapeHtml(rel)}</time>`;
  }
  if (format === "t") return `<time class="msg-timestamp" title="${escapeHtml(d.toLocaleString())}">${escapeHtml(d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }))}</time>`;
  if (format === "T") return `<time class="msg-timestamp" title="${escapeHtml(d.toLocaleString())}">${escapeHtml(d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" }))}</time>`;
  if (format === "d") return `<time class="msg-timestamp" title="${escapeHtml(d.toLocaleString())}">${escapeHtml(d.toLocaleDateString([], { month: "2-digit", day: "2-digit", year: "numeric" }))}</time>`;
  if (format === "D") return `<time class="msg-timestamp" title="${escapeHtml(d.toLocaleString())}">${escapeHtml(d.toLocaleDateString([], { month: "long", day: "numeric", year: "numeric" }))}</time>`;
  if (format === "F") return `<time class="msg-timestamp" title="${escapeHtml(d.toLocaleString())}">${escapeHtml(d.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: "numeric" }))} ${escapeHtml(d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }))}</time>`;
  return `<time class="msg-timestamp" title="${escapeHtml(d.toLocaleString())}">${escapeHtml(d.toLocaleDateString([], { month: "long", day: "numeric", year: "numeric" }))} ${escapeHtml(d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }))}</time>`;
}

export function renderMarkdown(text, message, selfUserId, roles) {
  const placeholder = [];
  let src = text;

  src = src.replace(/```(?:([a-zA-Z0-9_+-]+)?\n)?([\s\S]*?)```/g, (match, lang, code) => {
    placeholder.push(`<pre class="msg-codeblock"><code>${escapeHtml(code || "")}</code></pre>`);
    return `\u0000${placeholder.length - 1}\u0000`;
  });
  src = src.replace(/`([^`\n]+)`/g, (match, code) => {
    placeholder.push(`<code class="msg-inline-code">${escapeHtml(code)}</code>`);
    return `\u0000${placeholder.length - 1}\u0000`;
  });
  src = src.replace(/\|\|([\s\S]*?)\|\|/g, (match, spoil) => {
    placeholder.push(`<span class="msg-spoiler" onclick="this.classList.toggle('revealed')">${escapeHtml(spoil)}</span>`);
    return `\u0000${placeholder.length - 1}\u0000`;
  });
  src = src.replace(/<t:(\d+)(?::([tTdDfFR]))?>/g, (match, unixSec, fmt) => {
    placeholder.push(formatDiscordTimestamp(unixSec, fmt || "f"));
    return `\u0000${placeholder.length - 1}\u0000`;
  });
  src = src.replace(/\[([^\]]+)\]\((https?:\/\/[^\s\)<>]+)\)/g, (match, label, url) => {
    let formattedLabel = escapeHtml(label);
    formattedLabel = formattedLabel.replace(/\*\*\*([\s\S]*?)\*\*\*/g, (m, inner) => `<strong><em>${inner}</em></strong>`);
    formattedLabel = formattedLabel.replace(/_\*\*([\s\S]*?)\*\*_ /g, (m, inner) => `<em><strong>${inner}</strong></em>`);
    formattedLabel = formattedLabel.replace(/\*\*_([\s\S]*?)_\*\*/g, (m, inner) => `<strong><em>${inner}</em></strong>`);
    formattedLabel = formattedLabel.replace(/\*\*([\s\S]*?)\*\*/g, (m, inner) => `<strong>${inner}</strong>`);
    formattedLabel = formattedLabel.replace(/__\*\*([\s\S]*?)\*\*__/g, (m, inner) => `<u><strong>${inner}</strong></u>`);
    formattedLabel = formattedLabel.replace(/__\*([^*]+?)\*__/g, (m, inner) => `<u><em>${inner}</em></u>`);
    formattedLabel = formattedLabel.replace(/__([^_]+?)__/g, (m, inner) => `<u>${inner}</u>`);
    formattedLabel = formattedLabel.replace(/~~([\s\S]*?)~~/g, (m, inner) => `<s>${inner}</s>`);
    formattedLabel = formattedLabel.replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, (m, before, inner) => `${before}<em>${inner}</em>`);
    formattedLabel = formattedLabel.replace(/(^|[^_])_([^_\s][^_]*?)_/g, (m, before, inner) => `${before}<em>${inner}</em>`);
    placeholder.push(`<a class="msg-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" style="color:var(--link)">${formattedLabel}</a>`);
    return `\u0000${placeholder.length - 1}\u0000`;
  });

  src = src.replace(/<@!?(\d+)>/g, (match, id) => {
    placeholder.push(mentionNameHtml(message, id, "user", selfUserId, roles));
    return `\u0000${placeholder.length - 1}\u0000`;
  });
  src = src.replace(/<@&(\d+)>/g, (match, id) => {
    placeholder.push(mentionNameHtml(message, id, "role", selfUserId, roles));
    return `\u0000${placeholder.length - 1}\u0000`;
  });
  src = src.replace(/<#(\d+)>/g, (match, id) => {
    placeholder.push(mentionNameHtml(message, id, "channel", selfUserId, roles));
    return `\u0000${placeholder.length - 1}\u0000`;
  });
  src = src.replace(/<a?:(.+?):(\d+)>/g, (match, name, id) => {
    const animated = match[0] === "<" && match[1] === "a";
    const ext = animated ? "gif" : "png";
    placeholder.push(`<img class="msg-custom-emoji" src="https://cdn.discordapp.com/emojis/${escapeHtml(id)}.${ext}" alt="${escapeHtml(name)}" loading="lazy">`);
    return `\u0000${placeholder.length - 1}\u0000`;
  });

  src = escapeHtml(src);

  const urlRegex = /(https?:\/\/[^\s<]+)/gi;
  const imageExts = /\.(gif|png|jpg|jpeg|webp)(\?|$)/i;
  let processed = "";
  let lastIndex = 0;
  let match;
  while ((match = urlRegex.exec(src)) !== null) {
    const raw = match[0];
    const url = raw.replace(/[.,!?:;*]+$/, "");
    processed += src.slice(lastIndex, match.index);
    if (imageExts.test(url)) {
      processed += `<img src="${escapeHtml(url)}" alt="image" style="max-width:360px;max-height:360px;border-radius:4px" loading="lazy">`;
    } else {
      processed += `<a class="msg-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" style="color:var(--link)">${escapeHtml(url)}</a>`;
    }
    processed += raw.slice(url.length);
    lastIndex = urlRegex.lastIndex;
  }
  processed += src.slice(lastIndex);
  src = processed;

  const lines = src.split("\n");
  const processedLines = [];
  let inMultiQuote = false;
  const multiQuoteLines = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (inMultiQuote) {
      multiQuoteLines.push(line);
      continue;
    }
    if (line.startsWith("&gt;&gt;&gt; ")) {
      inMultiQuote = true;
      multiQuoteLines.push(line.slice(13));
      continue;
    }
    if (line.startsWith("&gt; ")) {
      processedLines.push(`<blockquote class="msg-quote">${line.slice(5)}</blockquote>`);
      continue;
    }
    if (line.startsWith("-# ")) {
      processedLines.push(`<span class="msg-subtext">${line.slice(3)}</span>`);
      continue;
    }
    if (line.startsWith("### ")) {
      processedLines.push(`<h3 class="msg-h3">${line.slice(4)}</h3>`);
      continue;
    }
    if (line.startsWith("## ")) {
      processedLines.push(`<h2 class="msg-h2">${line.slice(3)}</h2>`);
      continue;
    }
    if (line.startsWith("# ")) {
      processedLines.push(`<h1 class="msg-h1">${line.slice(2)}</h1>`);
      continue;
    }
    if (/^[-*] /.test(line)) {
      processedLines.push(`<div class="msg-list-item">${line.slice(2)}</div>`);
      continue;
    }
    const numMatch = /^(\d+)\. (.*)$/.exec(line);
    if (numMatch) {
      processedLines.push(`<div class="msg-list-item-num"><b>${numMatch[1]}.</b> ${numMatch[2]}</div>`);
      continue;
    }
    processedLines.push(line);
  }

  if (inMultiQuote && multiQuoteLines.length) {
    processedLines.push(`<blockquote class="msg-quote">${multiQuoteLines.join("\n")}</blockquote>`);
  }

  src = processedLines.join("\n");

  src = src.replace(/\*\*\*([\s\S]*?)\*\*\*/g, (match, inner) => `<strong><em>${inner}</em></strong>`);
  src = src.replace(/_\*\*([\s\S]*?)\*\*_ /g, (match, inner) => `<em><strong>${inner}</strong></em>`);
  src = src.replace(/\*\*_([\s\S]*?)_\*\*/g, (match, inner) => `<strong><em>${inner}</em></strong>`);
  src = src.replace(/\*\*([\s\S]*?)\*\*/g, (match, inner) => `<strong>${inner}</strong>`);
  src = src.replace(/__\*\*([\s\S]*?)\*\*__/g, (match, inner) => `<u><strong>${inner}</strong></u>`);
  src = src.replace(/__\*([^*]+?)\*__/g, (match, inner) => `<u><em>${inner}</em></u>`);
  src = src.replace(/__([^_]+?)__/g, (match, inner) => `<u>${inner}</u>`);
  src = src.replace(/~~([\s\S]*?)~~/g, (match, inner) => `<s>${inner}</s>`);
  src = src.replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, (match, before, inner) => `${before}<em>${inner}</em>`);
  src = src.replace(/(^|[^_])_([^_\s][^_]*?)_/g, (match, before, inner) => `${before}<em>${inner}</em>`);

  const isJumbo = isOnlyEmoji(text);

  src = src.split(/(\u0000\d+\u0000)/).map((part) => {
    const ph = /^\u0000(\d+)\u0000$/.exec(part);
    if (ph) {
      return placeholder[Number(ph[1])];
    }
    return parseTwemoji(part, isJumbo);
  }).join("");

  return src;
}

function formatMessageText(text, message, selfUserId, roles) {
  if (!text) return '';
  return renderMarkdown(text, message, selfUserId, roles);
}

export function authorName(msg, fallback) {
  if (msg?.authorDisplay) {
    if (typeof msg.authorDisplay === "object" && msg.authorDisplay !== null) {
      return msg.authorDisplay.global_name || msg.authorDisplay.username || fallback || "Unknown";
    }
    return String(msg.authorDisplay);
  }
  const raw = msg?.author ?? "";
  if (typeof raw === "object" && raw !== null) {
    return raw.global_name || raw.displayName || raw.username || fallback || "Unknown";
  }
  return raw || fallback || "Unknown";
}

function isSystemMessage(msg) {
  return typeof msg?.type === "number" && msg.type !== 0 && msg.type !== 19 && msg.type !== 20 && msg.type !== 21;
}

function renderSystemMessageRow(message, channelId, isMine, guildId) {
  const actionUser = isMine ? "You" : escapeHtml(authorName(message));
  let systemIcon = `<i class="fa-solid fa-bell" style="color:var(--text-muted);"></i>`;
  let systemText = "";

  if (message.type === 3) {
    systemIcon = `<i class="fa-solid fa-phone" style="color:var(--accent);"></i>`;
    systemText = `<strong>${actionUser}</strong> started a call.`;
  } else if (message.type === 67) {
    systemIcon = `<i class="fa-solid fa-user-check" style="color:var(--accent);"></i>`;
    systemText = isMine
      ? `<strong>You</strong> accepted the friend request.`
      : `<strong>${actionUser}</strong> accepted your friend request.`;
  } else if (message.type === 1) {
    const mention = message.mentions?.[0]?.global_name || message.mentions?.[0]?.username || "someone";
    systemIcon = `<i class="fa-solid fa-user-plus" style="color:var(--accent);"></i>`;
    systemText = `<strong>${actionUser}</strong> added <strong>${escapeHtml(mention)}</strong> to the group.`;
  } else if (message.type === 2) {
    systemIcon = `<i class="fa-solid fa-arrow-right-from-bracket" style="color:var(--text-muted);"></i>`;
    systemText = `<strong>${actionUser}</strong> left the group.`;
  } else if (message.type === 4) {
    systemIcon = `<i class="fa-solid fa-pen" style="color:var(--text-muted);"></i>`;
    systemText = `<strong>${actionUser}</strong> changed the channel name: <b>${escapeHtml(message.content || "")}</b>`;
  } else if (message.type === 5) {
    systemIcon = `<i class="fa-solid fa-pen" style="color:var(--text-muted);"></i>`;
    systemText = `<strong>${actionUser}</strong> changed the channel icon.`;
  } else if (message.type === 6) {
    systemIcon = `<i class="fa-solid fa-thumbtack" style="color:var(--text-muted);"></i>`;
    systemText = `<strong>${actionUser}</strong> pinned a message to this channel.`;
  } else if (message.type === 7) {
    systemIcon = `<i class="fa-solid fa-arrow-right-to-bracket" style="color:var(--accent);"></i>`;
    systemText = `<strong>${actionUser}</strong> joined the server.`;
  } else if (message.type >= 8 && message.type <= 11) {
    systemIcon = `<i class="fa-solid fa-gem" style="color:#f47fff;"></i>`;
    systemText = `<strong>${actionUser}</strong> boosted the server.`;
  } else if (message.content) {
    systemText = `<strong>${actionUser}</strong> ${escapeHtml(message.content)}`;
  } else {
    systemText = `<strong>${actionUser}</strong> performed an action.`;
  }

  return `<article class="message-row system-message"
    data-message-id="${escapeHtml(message.id)}"
    data-channel-id="${escapeHtml(channelId)}"
    ${guildId ? `data-guild-id="${escapeHtml(guildId)}"` : ""}>
    <div class="system-message-inner">
      <span class="system-message-icon">${systemIcon}</span>
      <span class="system-message-text">${systemText}</span>
      <time class="system-message-time">${escapeHtml(formatTime(message.createdAt))}</time>
    </div>
    ${reactionsHtml(message, channelId)}
  </article>`;
}

export function formatFileSize(bytes) {
  if (!bytes || bytes === 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0) + " " + units[i];
}

const FriendsSvg = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none"><path fill="#ed4245" d="M13 10a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"/><path fill="#ed4245" d="M3 5v-.75C3 3.56 3.56 3 4.25 3s1.24.56 1.33 1.25C6.12 8.65 9.46 12 13 12h1a8 8 0 0 1 8 8 2 2 0 0 1-2 2 .21.21 0 0 1-.2-.15 7.65 7.65 0 0 0-1.32-2.3c-.15-.2-.42-.06-.39.17l.25 2c.02.15-.1.28-.25.28H9a2 2 0 0 1-2-2v-2.22c0-1.57-.67-3.05-1.53-4.37A15.85 15.85 0 0 1 3 5Z"/></svg>';
const ShopSvg = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path fill="#ed4245" d="M2.63 4.19A3 3 0 0 1 5.53 2H7a1 1 0 0 1 1 1v3.98a3.07 3.07 0 0 1-.3 1.35A2.97 2.97 0 0 1 4.98 10c-2 0-3.44-1.9-2.9-3.83l.55-1.98ZM10 2a1 1 0 0 0-1 1v4a3 3 0 0 0 3 3 3 3 0 0 0 3-2.97V3a1 1 0 0 0-1-1h-4ZM17 2a1 1 0 0 0-1 1v3.98a3.65 3.65 0 0 0 0 .05A2.95 2.95 0 0 0 19.02 10c2 0 3.44-1.9 2.9-3.83l-.55-1.98A3 3 0 0 0 18.47 2H17Z"/><path fill="#ed4245" d="M21 11.42V19a3 3 0 0 1-3 3h-2.75a.25.25 0 0 1-.25-.25V16a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v5.75c0 .14-.11.25-.25.25H6a3 3 0 0 1-3-3v-7.58c0-.18.2-.3.37-.24a4.46 4.46 0 0 0 4.94-1.1c.1-.12.3-.12.4 0a4.49 4.49 0 0 0 6.58 0c.1-.12.3-.12.4 0a4.45 4.45 0 0 0 4.94 1.1c.17-.07.37.06.37.24Z"/></svg>';

export function SkeletonLayout() {
  return `
    <div class="loader-home">
      <img src="/static/assets/fern_icon.png" alt="" class="loader-logo">
      <h1>DID YOU KNOW</h1>
      <p class="rest-of-words">
        <span>
          <span class="button-style">CTRL</span>
          <span class="button-style">R</span>
          to refresh
        </span>
        <span>Fern client.</span>
      </p>
    </div>`;
}

export function LoginError(message) {
  return `
    <div class="center-state">
      <h1>Could not load Discord data</h1>
      <p>${escapeHtml(message)}</p>
      <a class="primary-link" href="/logout">Sign in again</a>
    </div>`;
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function reactionsHtml(message, channelId) {
  if (!message.reactions || !message.reactions.length) return "";
  return `<div class="message-reactions">${message.reactions.map(r => {
    const emoji = r.emoji || {};
    const img = emoji.id
      ? `<img src="https://cdn.discordapp.com/emojis/${emoji.id}.${emoji.animated ? "gif" : "png"}" alt="${escapeHtml(emoji.name || "")}" class="reaction-emoji">`
      : (parseTwemoji(emoji.name || "").replace(/class="emoji"/, 'class="reaction-emoji-twemoji"') || `<span class="reaction-emoji-text">${escapeHtml(emoji.name || "")}</span>`);
    const active = r.me ? " reaction-active" : "";
    return `<button type="button" class="reaction-btn${active}" data-react-emoji="${escapeHtml(emoji.name || "")}" data-react-channel="${escapeHtml(channelId)}" data-react-msg="${escapeHtml(message.id)}" title="${escapeHtml(emoji.name || "")}">${img}<span class="reaction-count">${r.count}</span></button>`;
  }).join("")}</div>`;
}

function avatarWithDecorationHtml(src, decorationUrl, size, fallbackText) {
  if (!src) {
    return `<div class="avatar-sm placeholder">${escapeHtml(fallbackText)}</div>`;
  }
  const dSize = size + 12;
  const deco = decorationUrl
    ? `<img src="${escapeHtml(decorationUrl)}" alt="" class="avatar-decoration" style="width:${dSize}px !important;height:${dSize}px !important">`
    : "";
  return `<img src="${escapeHtml(src)}" alt="" class="avatar-sm">${deco}`;
}

function forwardIndicatorHtml(message) {
  const HAS_SNAPSHOT = 16384;
  if (!message.flags || !(Number(message.flags) & HAS_SNAPSHOT)) return "";
  return `<div class="forward-indicator">
    <span class="forward-text">Forwarded</span>
  </div>`;
}

function forwardSnapshotHtml(message) {
  const snap = message.message_snapshots?.[0]?.message;
  if (!snap) return "";
  return escapeHtml(snap.content || "");
}

function replyIndicatorHtml(message) {
  const ref = message.referenced_message;
  const msgRef = message.message_reference;
  if (!msgRef) return "";
  if (msgRef.type === 1) return "";
  if (message.type === 6) return "";

  if (ref) {
    const refAuthor = ref.author?.global_name || ref.author?.username || "Unknown";
    let refContent = ref.content || "";
    if (refContent.startsWith("ENC:v1:")) {
      refContent = "(Encrypted message)";
    } else {
      refContent = refContent.slice(0, 80);
    }
    const refAvatar = ref.author?.avatar
      ? `<img src="https://cdn.discordapp.com/avatars/${ref.author.id}/${ref.author.avatar}.${ref.author.avatar.startsWith("a_") ? "gif" : "png"}?size=32" alt="" class="reply-avatar-img">`
      : `<div class="reply-avatar-placeholder">${escapeHtml(initials(refAuthor))}</div>`;
    return `<div class="reply-indicator">
      ${refAvatar}
      <div class="reply-text">
        <span class="reply-username">${escapeHtml(refAuthor)}</span>
        <span class="reply-preview">${escapeHtml(refContent)}</span>
      </div>
    </div>`;
  }

  return `<div class="reply-indicator">
    <div class="reply-text">
      <span class="reply-username">Reply</span>
      <span class="reply-preview">(Original message not loaded)</span>
    </div>
  </div>`;
}

export function AddFriendPanel({ friends, friendError, friendFilter, forceShowForm, friendFormValue }) {
  const pending = friends?.pending || [];
  const accepted = friends?.friends || [];

  const blocked = friends?.blocked || [];

  const showAddForm = forceShowForm;

  const avatarHtml = (f, size = 40) => {
    const dSize = size;
    return `<div class="friend-avatar-wrap" style="width:${dSize}px;height:${dSize}px">${f.avatarUrl ? `<img src="${escapeHtml(f.avatarUrl)}" alt="" class="friend-avatar" style="width:${dSize}px;height:${dSize}px">${f.avatarDecorationUrl ? `<img src="${escapeHtml(f.avatarDecorationUrl)}" alt="" class="avatar-decoration" style="width:${dSize}px !important;height:${dSize}px !important">` : ""}` : `<div class="friend-avatar-placeholder" style="width:${dSize}px;height:${dSize}px;font-size:${Math.round(dSize * 0.38)}px">${escapeHtml(initials(f.displayName || f.username))}</div>`}<span class="friend-status-dot ${dotClass(f.status)}"></span></div>`;
  };

  const blockedItems = friendFilter === "Blocked" ? blocked
    .map(f => `<div class="friend-item">${avatarHtml(f)}<span class="friend-name">${escapeHtml(f.displayName || f.username)}</span><button type="button" class="friend-action danger" data-unblock-user="${escapeHtml(f.id)}" title="Unblock"><i class="fa-solid fa-unlock"></i></button></div>`)
    .join("") : "";
  const showPending = friendFilter === "All" || friendFilter === "Pending";
  const showFriends = friendFilter === "Online" || friendFilter === "All";

  const filtered = friendFilter === "Online"
    ? accepted.filter(f => f.status && f.status !== "offline")
    : accepted;

  const incomingItems = showPending ? pending
    .filter(f => f.type === 3)
    .map(f => `<div class="friend-item request">${avatarHtml(f)}<span class="friend-name">${escapeHtml(f.displayName || f.username)}</span><span class="friend-tag">Incoming</span><button type="button" class="friend-action accept" data-accept-friend="${escapeHtml(f.id)}" title="Accept"><i class="fa-solid fa-check"></i></button><button type="button" class="friend-action danger" data-ignore-friend="${escapeHtml(f.id)}" title="Ignore"><i class="fa-solid fa-xmark"></i></button></div>`)
    .join("") : "";

  const outgoingItems = showPending ? pending
    .filter(f => f.type === 4)
    .map(f => `<div class="friend-item request">${avatarHtml(f)}<span class="friend-name">${escapeHtml(f.displayName || f.username)}</span><span class="friend-tag outgoing">Outgoing</span><button type="button" class="friend-action danger" data-ignore-friend="${escapeHtml(f.id)}" title="Cancel"><i class="fa-solid fa-xmark"></i></button></div>`)
    .join("") : "";

  const acceptedItems = showFriends ? filtered
    .map(f => `<div class="friend-item">${avatarHtml(f)}<span class="friend-name">${escapeHtml(f.displayName || f.username)}</span><button type="button" class="friend-action danger" data-block-user="${escapeHtml(f.id)}" title="Block"><i class="fa-solid fa-ban"></i></button><button type="button" class="friend-action danger" data-remove-friend="${escapeHtml(f.id)}" title="Remove"><i class="fa-solid fa-user-minus"></i></button></div>`)
    .join("") : "";

  const sectionLabel = (title, count) => `<div class="section-label"><span>${escapeHtml(title)}</span><span class="section-count">${count}</span></div>`;

  return `
    ${showAddForm ? `<form class="add-friend-form" data-friend-form>
      <div class="add-friend-input-wrap">
        <i class="fa-solid fa-user-plus add-friend-ico"></i>
        <input name="username" autocomplete="off" placeholder="Enter a username to add" maxlength="40" value="${escapeHtml(friendFormValue || '')}">
        <button type="submit">Send Request</button>
      </div>
    </form>` : ""}
    ${friendError ? `<div class="friend-error">${escapeHtml(friendError)}</div>` : ""}
    ${friendFilter === "Blocked" ? (blockedItems ? `<div class="friend-group">${sectionLabel("Blocked", blocked.length)}${blockedItems}</div>` : `<div class="friend-group">${sectionLabel("Blocked", 0)}<div class="friend-empty">No blocked users.</div></div>`) : ""}
    ${incomingItems ? `<div class="friend-group">${sectionLabel("Incoming", pending.filter(f => f.type === 3).length)}${incomingItems}</div>` : ""}
    ${outgoingItems ? `<div class="friend-group">${sectionLabel("Outgoing", pending.filter(f => f.type === 4).length)}${outgoingItems}</div>` : ""}
    ${acceptedItems ? `<div class="friend-group">${sectionLabel(friendFilter === "Online" ? "Online" : "Friends", filtered.length)}${acceptedItems}</div>` : ""}
  `;
}

export function unreadDividerFor(messages, readCursorId, userId) {
  const cursor = readCursorId ? String(readCursorId) : "";
  if (!cursor || !/^\d{17,20}$/.test(cursor)) return null;
  const cursorBig = BigInt(cursor);
  for (let i = 0; i < messages.length; i++) {
    const message = messages[i];
    if (userId && (message.author_id === userId || message.author?.id === userId)) {
      continue;
    }
    const id = message?.id;
    if (id && /^\d{17,20}$/.test(String(id)) && BigInt(id) > cursorBig) {
      return i;
    }
  }
  return null;
}

export function extractInviteCodes(text) {
  if (!text || typeof text !== "string") return [];
  const regex = /(?:https?:\/\/)?(?:www\.)?(?:discord\.gg|discordapp\.com\/invite|discord\.com\/invite)\/([a-zA-Z0-9_-]+)/gi;
  const codes = [];
  let match;
  while ((match = regex.exec(text)) !== null) {
    const code = match[1];
    if (code && !codes.includes(code)) {
      codes.push(code);
    }
  }
  return codes;
}

export function renderInviteCardHtml(code, stateGuilds) {
  const cached = (typeof window !== "undefined" && window._INVITE_CACHE && window._INVITE_CACHE.get(code)) || null;
  const guild = cached?.guild;
  const isJoined = Boolean(guild?.id && (stateGuilds || []).some(g => g.id === guild.id));
  const iconUrl = guild?.id && guild?.icon ? `https://cdn.discordapp.com/icons/${escapeHtml(guild.id)}/${escapeHtml(guild.icon)}.png?size=96` : '';
  const serverName = guild?.name || `discord.gg/${code}`;
  const presenceCount = cached?.approximate_presence_count;
  const memberCount = cached?.approximate_member_count;
  const countsText = (presenceCount !== undefined || memberCount !== undefined)
    ? `${(presenceCount || 0).toLocaleString()} Online · ${(memberCount || 0).toLocaleString()} Members`
    : `Discord Server`;

  return `<div class="discord-invite-card" data-invite-code="${escapeHtml(code)}">
    <div class="invite-card-header">You've been invited to join a server</div>
    <div class="invite-card-body">
      <div class="invite-card-icon">${iconUrl ? `<img src="${escapeHtml(iconUrl)}" alt="">` : `<div class="invite-icon-placeholder">${escapeHtml(serverName.slice(0, 2).toUpperCase())}</div>`}</div>
      <div class="invite-card-info">
        <div class="invite-card-name" title="${escapeHtml(serverName)}">${escapeHtml(serverName)}</div>
        <div class="invite-card-counts"><span class="invite-online-dot"></span>${escapeHtml(countsText)}</div>
      </div>
      <button type="button" class="invite-join-btn ${isJoined ? 'joined' : ''}" data-join-invite="${escapeHtml(code)}" ${isJoined ? 'disabled' : ''}>${isJoined ? 'Joined' : 'Join'}</button>
    </div>
  </div>`;
}

function renderEmbedsHtml(embeds) {
  if (!Array.isArray(embeds) || embeds.length === 0) return "";
  return embeds.map(embed => {
    if (!embed) return "";
    let colorHex = "var(--accent, #23a55a)";
    if (embed.color) {
      colorHex = `#${Number(embed.color).toString(16).padStart(6, "0")}`;
    }
    const authorHtml = embed.author && (embed.author.name || embed.author.icon_url)
      ? `<div class="embed-author">${embed.author.icon_url ? `<img src="${escapeHtml(embed.author.icon_url)}" class="embed-author-icon" alt="">` : ""}${embed.author.url ? `<a href="${escapeHtml(embed.author.url)}" target="_blank" rel="noopener noreferrer" class="embed-author-name">${escapeHtml(embed.author.name || "")}</a>` : `<span class="embed-author-name">${escapeHtml(embed.author.name || "")}</span>`}</div>`
      : "";
    const titleHtml = embed.title
      ? `<div class="embed-title">${embed.url ? `<a href="${escapeHtml(embed.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(embed.title)}</a>` : escapeHtml(embed.title)}</div>`
      : "";
    const descHtml = embed.description
      ? `<div class="embed-desc">${formatMessageText(embed.description)}</div>`
      : "";
    const fieldsHtml = Array.isArray(embed.fields) && embed.fields.length > 0
      ? `<div class="embed-fields">${embed.fields.map(f => `<div class="embed-field ${f.inline ? "inline" : ""}"><div class="embed-field-name">${escapeHtml(f.name || "")}</div><div class="embed-field-val">${formatMessageText(f.value || "")}</div></div>`).join("")}</div>`
      : "";
    const imgHtml = embed.image && (embed.image.url || embed.image.proxy_url)
      ? `<div class="embed-image"><img src="${escapeHtml(embed.image.url || embed.image.proxy_url)}" alt="" loading="lazy"></div>`
      : "";
    const thumbHtml = embed.thumbnail && (embed.thumbnail.url || embed.thumbnail.proxy_url)
      ? `<div class="embed-thumb"><img src="${escapeHtml(embed.thumbnail.url || embed.thumbnail.proxy_url)}" alt="" loading="lazy"></div>`
      : "";
    const footerHtml = (embed.footer || embed.timestamp)
      ? `<div class="embed-footer">${embed.footer?.icon_url ? `<img src="${escapeHtml(embed.footer.icon_url)}" class="embed-footer-icon" alt="">` : ""}<span>${escapeHtml(embed.footer?.text || "")}${embed.footer?.text && embed.timestamp ? " • " : ""}${embed.timestamp ? formatTime(embed.timestamp) : ""}</span></div>`
      : "";

    return `<div class="message-embed" style="border-left-color:${escapeHtml(colorHex)}"><div class="embed-grid"><div class="embed-main">${authorHtml}${titleHtml}${descHtml}${fieldsHtml}</div>${thumbHtml}</div>${imgHtml}${footerHtml}</div>`;
  }).join("");
}

export function MessageListHtml(
  messages,
  messagesLoading,
  messageError,
  selectedDm,
  user,
  channelId,
  guildId,
  readCursorId,
  roles
) {
  const api1 = window.FERN_BACKEND_URL || "http://45.154.98.198:5001";
	
  const readCursor = readCursorId ? String(readCursorId) : "";
   const unreadSplit = unreadDividerFor(messages, readCursor, user?.id);

  const messageRows = messages.map((message, rowIndex) => {
      const isMine =
        message.author === "me" || authorName(message) === "me" || message.author === user?.username;
      const isEncrypted = message.decrypted === true;
      const lockSvg = isEncrypted
        ? `<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" class="message-lock">
           <defs>
             <linearGradient id="lockGrad-${message.id}" x1="0%" y1="0%" x2="100%" y2="100%">
               <stop offset="0%" stop-color="#62d99a" />
               <stop offset="100%" stop-color="#1f8a51" />
             </linearGradient>
           </defs>
           <path d="M7.5 10.5V8a4.5 4.5 0 1 1 9 0v2.5" fill="none" stroke="url(#lockGrad-${message.id})" stroke-width="2.2" stroke-linecap="round" />
           <rect x="5.5" y="10.5" width="13" height="10" rx="2.5" fill="url(#lockGrad-${message.id})" />
         </svg>`
        : `<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" class="message-lock message-lock-red">
           <path d="M7.5 10.5V8a4.5 4.5 0 1 1 9 0v2.5" fill="none" stroke="#f23f42" stroke-width="2.2" stroke-linecap="round" />
           <rect x="5.5" y="10.5" width="13" height="10" rx="2.5" fill="#f23f42" />
         </svg>`;
      const isGif = message.isGif === true;
      const isSticker =
        Array.isArray(message.sticker_items) &&
        message.sticker_items.length > 0;
      const hasAttachments =
        Array.isArray(message.attachments) &&
        message.attachments.length > 0;
	  const attachment1 = message.attachments?.[0];
      const attachment2 = message.attachments?.[1];
      const attachment3 = message.attachments?.[2];
      const attachment4 = message.attachments?.[3];
      const attachment5 = message.attachments?.[4];
      const attachment6 = message.attachments?.[5];
      const attachment7 = message.attachments?.[6];
      const attachment8 = message.attachments?.[7];
      const attachment9 = message.attachments?.[8];
      const attachment10 = message.attachments?.[9];

      if (isSystemMessage(message)) {
        return renderSystemMessageRow(message, channelId, isMine, guildId);
      }

      let contentHtml = "";
      const text = message.content || "";
      const snapHtml = forwardSnapshotHtml(message);

	if (isSticker) {
		const stickerItem = message.sticker_items[0];
		const stickerId = stickerItem.id;
		const formatType = stickerItem.format_type;
		if (formatType === 3) {
			contentHtml = `
			  <div class="sticker-wrapper">
				<lottie-player
					  class="message-sticker"
					  src="${api1}/get/discord/sticker/${escapeHtml(String(stickerId))}?format=json"
					  autoplay
					  loop
					></lottie-player>
				  </div>
				`;
		} else {
			contentHtml = `
			  <div class="sticker-wrapper">
				<img
				  class="message-sticker"
				  src="${api1}/get/discord/sticker/${escapeHtml(String(stickerId))}"
				  alt="sticker"
				/>
			  </div>
			`;
		}
		  } else if (snapHtml) {
			contentHtml = `<div class="message-text">${snapHtml}</div>`;
		  } else if (text) {
			contentHtml = isGif ? `<div class="message-text"><img src="${escapeHtml(text)}" alt="gif" style="max-height: 360px; max-width: 360px;" /></div>`
				: `<div class="message-text">${formatMessageText(message.content, message, user?.id, roles)}${lockSvg}</div>`;
		  } else if (isEncrypted) {
			contentHtml = `<div class="message-text">${lockSvg}</div>`;
		  }
		
      const attachments = message.attachments || [];
      let attachmentsHtml = "";
      if (attachments.length > 0) {
        const imageAttachments = attachments.filter(a => a.content_type && a.content_type.startsWith("image/"));
        const otherAttachments = attachments.filter(a => !a.content_type || !a.content_type.startsWith("image/"));
        const downloadSvg = `<svg viewBox="0 0 16 16" style="width:14px;height:14px;fill:none;stroke:var(--text-secondary);stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round;"><path d="M8 2v8m0 0 3-3m-3 3-3-3M2 12v1a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-1"/></svg>`;
        let innerAttach = "";
        if (imageAttachments.length > 0) {
          const rows = [];
          for (let i = 0; i < imageAttachments.length; i += 2) {
            rows.push(imageAttachments.slice(i, i + 2));
          }
          for (const row of rows) {
            const imgs = row.map(a => {
              const w = a.width || 400;
              const h = a.height || 300;
              const ratio = Math.min(300 / h, 400 / w, 1);
              const dw = Math.round(w * ratio);
              const col = row.length === 1 ? `flex:0 0 ${dw}px;` : `flex:1;min-width:0;`;
              const dlUrl = `{encodeURIComponent(a.url)}&name=${encodeURIComponent(a.filename)}`;
              const dlBtn = `<a href="${dlUrl}" download="${escapeHtml(a.filename)}" style="position:absolute;top:4px;right:4px;width:28px;height:28px;display:flex;align-items:center;justify-content:center;border-radius:4px;background:var(--bg-3);z-index:1;text-decoration:none;opacity:0.8;" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.8'">${downloadSvg}</a>`;
              return `<div style="position:relative;${col}max-width:${dw}px;">${dlBtn}<a href="${escapeHtml(a.url)}" target="_blank" rel="noopener noreferrer" style="display:block;border-radius:4px;overflow:hidden;text-decoration:none;"><img src="${escapeHtml(a.url)}" alt="${escapeHtml(a.filename)}" style="display:block;width:100%;height:auto;object-fit:cover;max-height:300px;aspect-ratio:${w}/${h};border-radius:4px;"></a></div>`;
            }).join("");
            innerAttach += `<div style="display:flex;gap:4px;margin-top:4px;">${imgs}</div>`;
          }
        }
        if (otherAttachments.length > 0) {
          innerAttach += otherAttachments.map(a => {
            const ct = a.content_type || "";
			const fileIsDecrypted =
			typeof a.decrypted !== "undefined" && a.decrypted
			  ? a.decrypted
			  : "";
            const fn = a.filename || "Attachment";
            const sz = a.size ? formatFileSize(a.size) : "";
			const fileDecrypted = fileIsDecrypted
				? `<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" class="message-lock">
				   <defs>
					 <linearGradient id="lockGrad-${message.id}" x1="0%" y1="0%" x2="100%" y2="100%">
					   <stop offset="0%" stop-color="#62d99a" />
					   <stop offset="100%" stop-color="#1f8a51" />
					 </linearGradient>
				   </defs>
				   <path d="M7.5 10.5V8a4.5 4.5 0 1 1 9 0v2.5" fill="none" stroke="url(#lockGrad-${message.id})" stroke-width="2.2" stroke-linecap="round" />
				   <rect x="5.5" y="10.5" width="13" height="10" rx="2.5" fill="url(#lockGrad-${message.id})" />
				 </svg>`
				: `<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" class="message-lock message-lock-red">
				   <path d="M7.5 10.5V8a4.5 4.5 0 1 1 9 0v2.5" fill="none" stroke="#f23f42" stroke-width="2.2" stroke-linecap="round" />
				   <rect x="5.5" y="10.5" width="13" height="10" rx="2.5" fill="#f23f42" />
				 </svg>`;
            if (ct.startsWith("video/")) {
              const vw = a.width || 400;
              const vh = a.height || 225;
              const dlUrl = `${encodeURIComponent(a.url)}&name=${encodeURIComponent(fn)}`;
              const dlBtn = `<a href="${dlUrl}" download="${escapeHtml(fn)}" style="position:absolute;top:4px;right:4px;width:28px;height:28px;display:flex;align-items:center;justify-content:center;border-radius:4px;background:var(--bg-3);z-index:1;text-decoration:none;opacity:0.8;" onmouseover="this.style.opacity='1'" onmouseout="this.style.opacity='0.8'">${downloadSvg}</a>`;
              return `<div style="position:relative;margin-top:4px;max-width:400px;border-radius:8px;overflow:hidden;background:var(--bg-3);" class="video-embed">${dlBtn}<video controls preload="metadata" width="${vw}" height="${vh}" style="display:block;width:100%;height:auto;max-height:350px;aspect-ratio:${vw}/${vh};"><source src="${escapeHtml(a.url)}" type="${escapeHtml(ct)}"></video></div>`;
            }
            if (ct.startsWith("audio/")) {
              const dlUrl = `${encodeURIComponent(a.url)}&name=${encodeURIComponent(fn)}`;
              return `<div style="margin-top:4px;background:var(--bg-2);border:1px solid var(--bg-hover);border-radius:8px;padding:8px 10px;max-width:400px;"><a href="${dlUrl}" download="${escapeHtml(fn)}" style="display:flex;align-items:center;gap:8px;color:var(--text-normal);text-decoration:none;"><svg viewBox="0 0 24 24" style="flex:0 0 28px;width:28px;height:32px;"><path d="M6 2h8l4 4v16H6V2z" fill="#a5e0ae"/><path d="M14 2v5h5" fill="#2f7d45"/></svg><div style="flex:1;min-width:0;"><div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px;font-weight:600;color:var(--link);">${escapeHtml(fn)}</div><div style="color:var(--text-muted);font-size:12px;">${sz || "Audio file"} — Click to download</div></div><svg viewBox="0 0 16 16" style="flex:0 0 16px;width:16px;height:16px;fill:none;stroke:var(--text-secondary);stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round;"><path d="M8 2v8m0 0 3-3m-3 3-3-3M2 12v1a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-1"/></svg></a></div>`;
            }
            const dlUrl = `${encodeURIComponent(a.url)}&name=${encodeURIComponent(fn)}`;
            return `<div style="position:relative;display:flex;align-items:center;margin-top:4px;box-sizing:border-box;color:var(--text-normal);background:var(--bg-2);border:1px solid var(--bg-hover);border-radius:8px;padding:8px 10px;gap:8px;max-width:400px;"><a href="${escapeHtml(a.url)}" target="_blank" rel="noopener noreferrer" style="display:flex;align-items:center;flex:1;min-width:0;gap:8px;color:inherit;text-decoration:none;"><svg viewBox="0 0 24 24" style="flex:0 0 28px;width:28px;height:32px;"><path d="M6 2h8l4 4v16H6V2z" fill="#a5e0ae"/><path d="M14 2v5h5" fill="#2f7d45"/></svg><div style="min-width:0;"><div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:14px;font-weight:600;color:var(--link);">${escapeHtml(fn)}</div>${fileDecrypted} ${sz ? `<div style="color:var(--text-muted);font-size:12px;">${sz}</div>` : ""}</div></a><a href="${dlUrl}" download="${escapeHtml(fn)}" style="flex:0 0 28px;width:28px;height:28px;display:flex;align-items:center;justify-content:center;border-radius:4px;background:var(--bg-3);text-decoration:none;">${downloadSvg}</a></div>`;
          }).join("");
        }
        attachmentsHtml = `<div class="message-attachments">${innerAttach}</div>`;
      }

      const embedsHtml = renderEmbedsHtml(message.embeds);
      let embedsBlock = "";
      if (embedsHtml) {
        embedsBlock = `<div class="message-embeds">${embedsHtml}</div>`;
      }

      const inviteCodes = extractInviteCodes(message.content);
      let invitesBlock = "";
      if (inviteCodes.length > 0) {
        invitesBlock = `<div class="message-invites">${inviteCodes.map(c => renderInviteCardHtml(c, window._CURRENT_STATE?.guilds)).join("")}</div>`;
      }

      const isBot = Boolean(message.isBot || message.author?.bot || (typeof message.author === "object" && message.author?.bot));
      const botBadge = isBot ? `<span class="bot-badge">BOT</span>` : "";

      const showNormal = message.type === 0 || message.type === undefined || message.type === 19;
      const avatarUrl = isMine ? (user?.avatarUrl || message.authorAvatar || "") : (message.authorAvatar || "");
      const avatarDecoration = isMine ? (user?.avatarDecorationUrl || message.authorAvatarDecoration || "") : (message.authorAvatarDecoration || "");
      const avatarHtml = showNormal
        ? avatarWithDecorationHtml(avatarUrl, avatarDecoration, 32, initials(isMine ? "Me" : authorName(message)))
        : `<div class="avatar-sm"></div>`;

      const showTimestamp = message.type === 0 || message.type === undefined || message.type === 19;

      const rowClass = `message-row ${isMine ? "mine" : ""} ${
        isEncrypted ? "encrypted-message" : ""
      } ${isSticker ? "sticker-message" : ""}`;

      return `<article class="${rowClass}"
        data-message-id="${escapeHtml(message.id)}"
        data-channel-id="${escapeHtml(channelId)}"
        ${guildId ? `data-guild-id="${escapeHtml(guildId)}"` : ""}>
        <div class="message-avatar" data-author-id="${escapeHtml(message.author_id)}">${avatarHtml}</div>
        <div class="message-bubble">
          ${replyIndicatorHtml(message)}
          <div class="message-meta">
            <strong>${isMine ? "You" : escapeHtml(
              authorName(message)
            )}</strong>${botBadge}
            ${showTimestamp ? `<time>${escapeHtml(
              formatTime(message.createdAt)
            )}</time>` : ""}
          </div>
          ${forwardIndicatorHtml(message)}
          <div class="message-content">
            ${contentHtml}
            ${attachmentsHtml}
            ${embedsBlock}
            ${invitesBlock}
          </div>
          ${reactionsHtml(message, channelId)}
        </div>
      </article>`;
    });

  if (unreadSplit > 0 && unreadSplit < messages.length) {
    messageRows.splice(
      unreadSplit,
      0,
      `<div class="message-unread-divider"><span>Unread</span></div>`
    );
  }

  const finalItems = messageRows.join("");

  return `
    <style>
      .message-list {
        min-height: 0;
        overflow-y: auto;
        overflow-x: hidden;
        overscroll-behavior: contain;
        scrollbar-gutter: stable;
      }

      .message-list-inner {
        width: 100%;
        padding-bottom: 16px;
        box-sizing: border-box;
      }

      .message-row {
        width: 100%;
        min-width: 0;
        box-sizing: border-box;
      }

      .message-row.encrypted-message {
        position: relative;
        overflow: hidden;
        border-left: 0;
        background: linear-gradient(
          90deg,
          rgba(47, 158, 86, 0.25) 0,
          rgba(47, 158, 86, 0.18) 18px,
          rgba(47, 158, 86, 0.12) 55px,
          rgba(47, 158, 86, 0.07) 110px,
          rgba(47, 158, 86, 0.03) 220px,
          transparent 62%
        );
      }

      .message-row.encrypted-message::before {
        content: "";
        position: absolute;
        inset: 0;
        box-sizing: border-box;
        pointer-events: none;
        padding: 5px 10px 5px 12px;
        color: rgba(124, 224, 156, 0.34);
        font-family:
          "SFMono-Regular",
          Consolas,
          "Liberation Mono",
          Menlo,
          monospace;
        font-size: 10px;
        line-height: 2.05;
        letter-spacing: 0.22em;
        white-space: pre-wrap;
        word-break: break-word;
        overflow: hidden;
        text-align: left;
        text-shadow:
          0 0 5px rgba(46, 184, 114, 0.2);
        background: linear-gradient(
          90deg,
          rgba(46, 184, 114, 0.25) 0,
          rgba(46, 184, 114, 0.17) 14px,
          rgba(46, 184, 114, 0.09) 34px,
          rgba(46, 184, 114, 0.04) 60px,
          transparent 90px
        );
        opacity: 0.34;
        animation: matrixBlueFlow 9s linear infinite;
        z-index: 0;
      }

      .message-row .message-avatar {
        position: relative;
        z-index: 2;
        margin-left: 10px;
        flex: 0 0 auto;
        overflow: visible;
      }

      .message-avatar-hidden {
        visibility: hidden;
      }

      .message-row .message-bubble {
        position: relative;
        min-width: 0;
        overflow: hidden;
        border-left: 0;
        background: transparent;
        z-index: 1;
        margin-left: 10px;
      }

      .message-row.sticker-message {
        align-items: flex-start;
        padding-top: 0;
        padding-bottom: 0;
      }
      .message-row.sticker-message .message-meta {
        min-height: 0;
        margin-bottom: 1px;
      }
      .message-row.sticker-message .message-content {
        line-height: 0;
        gap: 0;
      }

      .message-row.sticker-message .message-bubble {
        overflow: visible;
      }

      .message-row.encrypted-message
        .message-bubble::after {
        content: "ENCRYPTED";
        position: absolute;
        right: 10px;
        top: 8px;
        pointer-events: none;
        color: rgba(124, 224, 156, 0.85);
        font-size: 9px;
        font-weight: 700;
        letter-spacing: 0.14em;
        font-family:
          "SFMono-Regular",
          Consolas,
          "Liberation Mono",
          Menlo,
          monospace;
        text-shadow:
          0 0 6px rgba(46, 184, 114, 0.28);
        opacity: 0.9;
        z-index: 0;
      }

      .message-row.encrypted-message
        .message-bubble > * {
        position: relative;
        z-index: 1;
      }

      .message-content {
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        gap: 4px;
        min-width: 0;
        width: 100%;
        text-align: left;
      }

      .message-text {
        text-align: left;
        width: 100%;
      }

      .message-text p {
        margin: 0;
        text-align: left;
      }

      .sticker-wrapper {
        display: block;
        width: 160px;
        height: 160px;
        flex: 0 0 160px;
        overflow: visible;
      }

      .message-sticker {
        display: block;
        width: 160px;
        height: 160px;
        max-width: 160px;
        max-height: 160px;
        overflow: visible;
      }

      .message-lock {
        vertical-align: -0.18em;
        flex: 0 0 auto;
      }

      .friend-action {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 8px;
      }

      .friend-action-time {
        opacity: 0.7;
        font-size: 0.85em;
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

      .forward-indicator {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 0 0 0 18px;
        position: relative;
      }

      .forward-indicator::before {
        content: "";
        position: absolute;
        left: 0;
        top: 2px;
        bottom: 2px;
        width: 2px;
        background: var(--text-faint);
        border-radius: 1px;
      }

      .forward-text {
        color: var(--text-muted);
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.02em;
      }

      .message-reactions {
        display: flex;
        flex-wrap: wrap;
        gap: 4px;
        margin-top: 4px;
      }

      .reaction-btn {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 2px 6px;
        border: 1px solid var(--bg-hover);
        border-radius: 8px;
        background: var(--bg-2);
        cursor: pointer;
        font-size: 13px;
        color: var(--text-normal);
        line-height: 1;
      }

      .reaction-btn:hover {
        border-color: var(--accent);
        background: var(--bg-1);
      }

      .reaction-btn.reaction-active {
        border-color: var(--accent);
        background: var(--accent-soft);
      }

      .reaction-emoji {
        width: 16px;
        height: 16px;
        object-fit: contain;
        vertical-align: middle;
      }

      .reaction-emoji-text {
        font-size: 16px;
        line-height: 1;
      }

      .reaction-count {
        font-size: 12px;
        font-weight: 500;
        color: var(--text-muted);
      }

      .reaction-btn.reaction-active .reaction-count {
        color: var(--text-normal);
      }

      @keyframes matrixBlueFlow {
        0% {
          transform: translateY(-8%);
        }

        100% {
          transform: translateY(8%);
        }
      }
    </style>
    <div class="message-list-inner">
      ${messagesLoading ? '<div class="message-system">Loading messages...</div>' : ""}
      ${messageError ? `<div class="message-system error">${escapeHtml(messageError)}</div>` : ""}
      ${finalItems || (!messagesLoading ? `<div class="message-system">No messages yet. Send the first one below.</div>` : "")}
    </div>
  `;
}

export const AppLayout = {
  full(state) {
    return `<main>
      <aside class="server-rail">${AppLayout.serverRail(state)}</aside>
      <div class="sidebar-chat" id="s">${AppLayout.sidebar(state)}</div>
      <div class="rightside-area" id="r">${AppLayout.chatArea(state)}</div>
    </main>`;
  },

  serverRail(state) {
    const { guilds, selectedGuildId, folders, user, presences, activePanel } = state;
    const isDmActive = !selectedGuildId;
    const userStatus = presences?.[user?.id] || user?.status || "online";

    const folderGuildIds = new Set();
    for (const f of folders) {
      for (const id of f.guildIds) folderGuildIds.add(id);
    }

    const getGuild = (id) => guilds.find(g => g.id === id);

    const guildIconHtml = (guild) => {
      const active = guild.id === selectedGuildId ? "active" : "";
      const mentions = (state.guildMentions && state.guildMentions[guild.id]) || 0;
      const hasUnread = (state.guildUnreads && state.guildUnreads[guild.id]) && !active;
      const unreadDot = hasUnread && mentions === 0 ? `<div class="guild-unread-dot"></div>` : "";
      const mentionBadge = mentions > 0 ? `<div class="guild-mention-badge">${mentions > 99 ? "99+" : mentions}</div>` : "";
      return `<div class="rail-nav-btn ${active} ${hasUnread ? "unread" : ""}" data-route="/channels/${escapeHtml(guild.id)}/${escapeHtml(guild.id)}" title="${escapeHtml(guild.name)}" data-guild-id="${escapeHtml(guild.id)}">
        ${unreadDot}
        ${guild.iconUrl
          ? `<img src="${escapeHtml(guild.iconUrl)}" alt="${escapeHtml(guild.name)}" class="server-icon ${active}">`
          : `<span>${escapeHtml(initials(guild.name))}</span>`}
        ${active ? '<span class="rail-active-dot"></span>' : ''}
        ${mentionBadge}
      </div>`;
    };

    let foldersHtml = "";
    for (const folder of folders) {
      const folderGuilds = folder.guildIds.map(getGuild).filter(Boolean);
      if (!folderGuilds.length) continue;
      const hasActive = folderGuilds.some(g => g.id === selectedGuildId);
      if (folder.collapsed) {
        const miniIcons = folderGuilds.slice(0, 4).map(g => {
          return g.iconUrl
            ? `<div class="folder-mini-icon" style="background-image:url(${escapeHtml(g.iconUrl)})"></div>`
            : `<div class="folder-mini-icon placeholder">${escapeHtml(initials(g.name))}</div>`;
        }).join("");
        foldersHtml += `<div class="rail-folder collapsed ${hasActive ? "active" : ""}" data-folder-toggle="${escapeHtml(folder.id)}" title="${escapeHtml(folder.name)}">${miniIcons}</div>`;
      } else {
        foldersHtml += `<div class="rail-folder expanded ${hasActive ? "active" : ""}" data-folder-id="${escapeHtml(folder.id)}"><div class="folder-header" data-folder-toggle="${escapeHtml(folder.id)}" title="${escapeHtml(folder.name)}"><div class="folder-bar"></div></div>${folderGuilds.map(guildIconHtml).join("")}</div>`;
      }
    }

    const ungrouped = guilds.filter(g => !folderGuildIds.has(g.id));
    const guildIcons = ungrouped.map(guildIconHtml).join("");

    return `<div class="rail-nav-group">
        <button type="button" class="rail-nav-btn server-home-btn ${isDmActive ? "active" : ""}" data-route="/channels/@me" title="Direct Messages">
          <div class="server-home-logo"></div>
          ${isDmActive ? '<span class="rail-active-dot"></span>' : ''}
        </button>
        ${foldersHtml}
        ${guildIcons}
        <button type="button" class="rail-nav-btn" data-homepage title="Home">
          <i class="fa-solid fa-house"></i>
        </button>
        <div class="rail-nav-btn" data-join-guild title="Add a Server">
          <i class="fa-solid fa-plus"></i>
        </div>
      </div>
      <div class="rail-bottom-group">
        <button type="button" class="rail-bottom-leaf ${state.encryptionEnabled ? "active" : ""}" id="enc-toggle" title="Toggle encryption (DE / EN)">
          <svg class="compass-dial" viewBox="0 0 48 48" width="48" height="48" aria-hidden="true">
            <circle cx="24" cy="24" r="22.5" class="compass-bezel" />
            <circle cx="24" cy="24" r="19.5" class="compass-ticks" />
            <line x1="24" y1="2" x2="24" y2="6" class="compass-cardinal" />
            <line x1="46" y1="24" x2="42" y2="24" class="compass-cardinal" />
            <line x1="24" y1="46" x2="24" y2="42" class="compass-cardinal" />
            <line x1="2" y1="24" x2="6" y2="24" class="compass-cardinal" />
            <text x="12" y="14" text-anchor="middle" class="compass-label compass-label-de">DE</text>
            <text x="36" y="14" text-anchor="middle" class="compass-label compass-label-en">EN</text>
            <g class="compass-needle">
              <image href="/static/assets/fern_icon.png" x="13" y="19.25" width="22" height="9.5" preserveAspectRatio="xMidYMid meet" />
            </g>
          </svg>
        </button>
        <div class="rail-user-avatar" data-open-discord="settings" title="Settings">
          ${user?.avatarUrl ? `<img src="${escapeHtml(user.avatarUrl)}" alt="">${user?.avatarDecorationUrl ? `<img src="${escapeHtml(user.avatarDecorationUrl)}" alt="" class="avatar-decoration">` : ""}<div class="friend-status-dot ${userStatus}"></div>` : `<div class="avatar-green-f">${escapeHtml(initials(user?.displayName || user?.username || "U"))}</div>`}
        </div>
      </div>`;
  },

  sidebar(state) {
    const { user, dms, guilds, friends, friendError, friendFormValue, selectedGuildId, selectedDmId, selectedChannelId, channelsByGuild, channelsLoading, activePanel, presences } = state;
    const selectedGuild = guilds.find(g => g.id === selectedGuildId) || null;
    const userStatus = presences?.[user?.id] || user?.status || "online";
    const isFriendsActive = !selectedGuildId && !selectedDmId && activePanel !== "settings" && activePanel !== "pricing";

    if (selectedGuild) {
      const guildChannels = channelsByGuild[selectedGuild.id] || null;
      const textChannels = guildChannels?.text || [];
      const voiceChannels = guildChannels?.voice || [];
      const categories = guildChannels?.categories || [];

      const uncategorized = textChannels.filter(ch => !ch.parent_id);
      const catMap = {};
      for (const ch of textChannels) {
        if (ch.parent_id) {
          if (!catMap[ch.parent_id]) catMap[ch.parent_id] = [];
          catMap[ch.parent_id].push(ch);
        }
      }

      const showAllChannels = state.settings?.showAllChannels === true;
      const visibleChannels = (list) => list.filter(ch => ch.accessible !== false || showAllChannels);

      const channelItem = (ch) => {
        const active = ch.id === selectedChannelId ? "active" : "";
        const restricted = ch.accessible === false;
        if (restricted && !showAllChannels) return "";
        const route = `/channels/${escapeHtml(selectedGuild.id)}/${escapeHtml(ch.id)}`;
        const mentions = (state.channelMentions && state.channelMentions[ch.id]) || 0;
        const isUnread = (state.channelUnreads && state.channelUnreads[ch.id]) && ch.id !== selectedChannelId;
        const mentionBadge = mentions > 0 ? `<span class="channel-mention-badge">${mentions}</span>` : "";
        return `<div class="list-item channel ${active} ${isUnread ? "unread" : ""} ${restricted ? "restricted" : ""}" ${restricted ? "" : `data-channel-id="${escapeHtml(ch.id)}" data-route="${route}"`}>
          <i class="fa-solid fa-hashtag"></i>
          <p>${escapeHtml(ch.name)}</p>
          ${mentionBadge}
          ${restricted ? '<i class="fa-solid fa-lock"></i>' : ""}
        </div>`;
      };

      let channelsHtml = channelsLoading
        ? '<div class="channel-loading">Loading...</div>'
        : '';

      if (!channelsLoading) {
        const sections = [];
        for (const cat of categories.sort((a, b) => a.position - b.position)) {
          const catChannels = visibleChannels(catMap[cat.id] || []);
          if (catChannels.length) {
            sections.push(`<div class="channel-category">${escapeHtml(cat.name)}</div>${catChannels.map(channelItem).join('')}`);
          }
        }
        const uncategorizedVisible = visibleChannels(uncategorized);
        if (uncategorizedVisible.length) {
          sections.push(`${categories.length ? '<div class="channel-category">General</div>' : ''}${uncategorizedVisible.map(channelItem).join('')}`);
        }
        const voiceItems = voiceChannels.map(ch => {
          const restricted = ch.accessible === false;
          if (restricted && !showAllChannels) return '';
          return `<div class="list-item channel voice ${restricted ? "restricted" : ""}" ${restricted ? "" : `data-voice-channel="${escapeHtml(ch.id)}" title="Join voice channel"`}><i class="fa-solid fa-phone"></i><p>${escapeHtml(ch.name)}</p></div>`;
        }).join('');
        if (voiceItems) sections.push(`<div class="channel-category">Voice Channels</div>${voiceItems}`);
        channelsHtml += sections.join('') || '<div class="channel-loading">No text channels</div>';
      }

      return `
        <div class="scrollable">
          <div class="guild-header">${escapeHtml(selectedGuild.name)}</div>
          <div class="list">${channelsHtml}</div>
        </div>
      `;
    }

    const dmItems = dms
      .map((dm) => {
        const isBlocked = (friends?.blocked || []).some(f => f.id === dm.userId);
        const active = dm.id === selectedDmId ? "active" : "";
        const unread = dm.unread && dm.id !== selectedDmId ? "unread" : "";
        const mentions = (state.dmMentions && state.dmMentions[dm.id]) || 0;
        const mentionBadge = mentions > 0 ? `<span class="dm-mention-badge">${mentions}</span>` : "";
        const isGroup = dm.type === 3;
        const recipientUser = isGroup ? null : (dm.recipients && dm.recipients[0] ? dm.recipients[0] : null);
        const recipientStatus = recipientUser ? (presences?.[recipientUser.id] || recipientUser.status || "offline") : (dm.status || (dm.userId && presences?.[dm.userId]) || "offline");
        const sd = isGroup ? "" : `<div class="friend-status-dot ${dotClass(recipientStatus)}"></div>`;
        const avatar = dm.avatarUrl
          ? `<img src="${escapeHtml(dm.avatarUrl)}" alt="">${dm.avatarDecorationUrl ? `<img src="${escapeHtml(dm.avatarDecorationUrl)}" alt="" class="avatar-decoration">` : ""}`
          : `<div class="avatar-placeholder-sm">${escapeHtml(initials(dm.displayName || dm.username))}</div>`;

        const subtext = dm.lastMessageSnippet || "";
        const timeText = dm.lastMessageTime ? formatTime(dm.lastMessageTime) : "";

        return `<div class="f-li ${active} ${unread} ${isBlocked ? "blocked" : ""}" data-route="/channels/@me/${escapeHtml(dm.id)}">
          <div class="f-li-indicator"></div>
          <div class="img-wrapper">
            ${avatar}
            ${sd}
          </div>
          <div class="f-li-info">
            <div class="f-li-top">
              <span class="f-li-name">${escapeHtml(dm.displayName || dm.username)}</span>
              ${timeText ? `<span class="f-li-time">${escapeHtml(timeText)}</span>` : ""}
              ${mentionBadge}
            </div>
            <p class="f-li-sub">${escapeHtml(subtext)}</p>
          </div>
          <i class="fa fa-xmark" data-close-dm="${escapeHtml(dm.id)}" title="Close DM"></i>
        </div>`;
      })
      .join("");

    return `
      <div class="scrollable" style="flex: 1; min-height: 0;">
        <div class="top-nav">
          <div class="search-input-wrap">
            <i class="fa-solid fa-magnifying-glass search-ico"></i>
            <input type="text" placeholder="Search or start a conversation" data-search-input autocomplete="off">
          </div>
          <div class="search-results" data-search-results></div>
        </div>
        <div class="list">
          <div class="sidebar-nav-item ${isFriendsActive ? "active" : ""}" data-route="/channels/@me">
            <div class="nav-icon-wrap green"><i class="fa-solid fa-user-group"></i></div>
            <span>Friends</span>
          </div>
          <div class="sidebar-nav-item ${activePanel === "pricing" ? "active" : ""}" data-open-fern-pricing>
            <div class="nav-icon-wrap"><i class="fa-solid fa-file-invoice"></i></div>
            <span>Fern Pricing</span>
            <span class="badge-pill badge-extend">EXTEND</span>
          </div>
          <div class="sidebar-nav-item" data-shop-soon>
            <div class="nav-icon-wrap"><i class="fa-solid fa-bag-shopping"></i></div>
            <span>Shop</span>
            <span class="badge-pill badge-soon">COMING SOON</span>
          </div>
        </div>
        <div class="dm">
          <div class="dm-header-wrap">
            <h1>DIRECT MESSAGES<i class="fa-solid fa-bars-staggered" style="font-size:10px;margin-left:6px;opacity:0.7"></i></h1>
            <i class="fa fa-plus plus-icon" data-create-group title="Create Group"></i>
          </div>
          <div class="friends">
            ${dmItems || '<div class="channel-loading">No conversations yet</div>'}
          </div>
        </div>
      </div>
    `;
  },

  chatArea(state) {
    const selectedDm = state.dms.find(dm => dm.id === state.selectedDmId) || null;
    const selectedGuild = state.guilds.find(g => g.id === state.selectedGuildId) || null;
    const { dmReason, messagesByDm, messagesLoading, messageError, user, selectedDmId, selectedChannelId, channelsByGuild, friends, friendError, activePanel, friendFilter, friendFormValue } = state;

    if (activePanel === "pricing") {
      return `
        <nav>
          <div class="left-side">
            <div class="one"><i class="fa-solid fa-tags"></i><p>Fern Pricing</p></div>
          </div>
          <div class="icons-end"></div>
        </nav>
        <div class="bottom">
          <div class="pricing-page">
            <div class="pricing-hero">
              <h1>Extend your Fern license</h1>
              <p>Head over to our website to purchase an extension and keep your client fully unlocked and up to date.</p>
            </div>
            <div class="pricing-grid">
              <div class="pricing-card" style="opacity:0.6;">
                <div class="pricing-card-top">
                  <div class="pricing-name">Monthly</div>
                  <div class="pricing-price">$5</div>
                  <div class="pricing-sub">temporarily disabled</div>
                </div>
                <ul class="pricing-feats">
                  <li><i class="fa-solid fa-check"></i> All features unlocked</li>
                  <li><i class="fa-solid fa-check"></i> End-to-end encryption</li>
                  <li><i class="fa-solid fa-check"></i> Friends, DMs & groups</li>
                  <li><i class="fa-solid fa-check"></i> Cancel anytime</li>
                </ul>
                <a class="pricing-btn disabled" href="#" style="opacity:0.45;pointer-events:none;cursor:not-allowed;">Monthly</a>
              </div>
              <div class="pricing-card featured">
                <div class="pricing-card-top">
                  <div class="pricing-name">Lifetime</div>
                  <div class="pricing-price">$5</div>
                  <div class="pricing-sub">first 50 users before price increases</div>
                </div>
                <ul class="pricing-feats">
                  <li><i class="fa-solid fa-check"></i> Everything in Monthly</li>
                  <li><i class="fa-solid fa-check"></i> Lifetime license key</li>
                  <li><i class="fa-solid fa-check"></i> All future updates</li>
                  <li><i class="fa-solid fa-check"></i> No recurring fees</li>
                </ul>
                <a class="pricing-btn" href="/#pricing">Extend lifetime</a>
              </div>
            </div>
            <div class="pricing-page-foot">
              <p class="pricing-note">Limited launch offer: <b>$5</b> lifetime for the first 50 users before price increases.</p>
            </div>
          </div>
        </div>
      `;
    }

    if (activePanel === "settings") {
      return `
        <nav>
          <div class="left-side">
            <div class="one" data-open-discord="settings"><i class="fa-solid fa-gear"></i><p>Settings</p></div>
          </div>
          <div class="icons-end">
            <a href="/logout" title="Log Out"><svg width="20" height="20" viewBox="0 0 20 20"><path fill="#ed4245" fill-rule="evenodd" d="M2.75 3.02A3 3 0 0 1 5 2h10a3 3 0 0 1 3 3v7.64c0 .44-.55.7-.95.55a3 3 0 0 0-3.17 4.93l.02.03a.5.5 0 0 1-.35.85h-.05a.5.5 0 0 0-.5.5 2.5 2.5 0 0 1-3.68 2.2l-5.8-3.09A3 3 0 0 1 2 16V5a3 3 0 0 1 .76-1.98Zm1.3 1.95A.04.04 0 0 0 4 5v11c0 .36.2.68.49.86l5.77 3.08a.5.5 0 0 0 .74-.44V8.02a.5.5 0 0 0-.32-.46l-6.63-2.6Z" clip-rule="evenodd"/></svg></a>
          </div>
        </nav>
        <div class="bottom">
          <div class="left centered">
            <h2>User Settings</h2>
            <p>Settings panel coming soon. Your session is active.</p>
            <a class="primary-link" href="/logout">Log Out</a>
          </div>
        </div>
      `;
    }

    if (selectedGuild) {
      const guildChannels = channelsByGuild[selectedGuild.id] || null;
      const textChannels = guildChannels?.text || [];
      const currentChannel = textChannels.find(ch => ch.id === selectedChannelId) || null;

      if (!currentChannel) {
        return `
          <nav>
            <div class="left-side">
            <div class="one"><i class="fa-solid fa-hashtag"></i><p>${escapeHtml(selectedGuild.name)}</p></div>
          </div>
        </nav>
          <div class="bottom">
            <div class="left centered">
              <h2>${escapeHtml(selectedGuild.name)}</h2>
              <p>Select a text channel to start chatting.</p>
            </div>
          </div>
    `;
    }

      const messages = selectedChannelId ? messagesByDm[selectedChannelId] || [] : [];

      return `
        <nav>
          <div class="left-side">
            <div class="one"><i class="fa-solid fa-hashtag"></i><p>${escapeHtml(currentChannel.name)}</p></div>
          </div>
          <div class="icons-end">
            <svg width="20" height="20" viewBox="0 0 24 24" data-ring-channel="${escapeHtml(selectedChannelId || currentChannel.id)}" title="Ring" style="cursor:pointer;vertical-align:middle;flex-shrink:0"><path fill="currentColor" d="M2 7.4A5.4 5.4 0 0 1 7.4 2c.36 0 .7.22.83.55l1.93 4.64a1 1 0 0 1-.43 1.25L7 10a8.52 8.52 0 0 0 7 7l1.12-2.24a1 1 0 0 1 1.19-.51l5.06 1.56c.38.11.63.46.63.85C22 19.6 19.6 22 16.66 22h-.37C8.39 22 2 15.6 2 7.71V7.4ZM13 3a1 1 0 0 1 1-1 8 8 0 0 1 8 8 1 1 0 1 1-2 0 6 6 0 0 0-6-6 1 1 0 0 1-1-1Z"/></svg>
          </div>
        </nav>
        <div class="bottom">
          <div class="left">
            <section class="message-list">
              <div class="message-list-inner">
                ${messagesLoading ? '<div class="message-system">Loading messages...</div>' : ''}
                ${messageError ? `<div class="message-system error">${escapeHtml(messageError)}</div>` : ''}
                ${messages.map(msg => {
                  const isMine = msg.author === "me" || authorName(msg) === "me";
                  if (isSystemMessage(msg)) {
                    return renderSystemMessageRow(msg, selectedChannelId, isMine, selectedGuildId);
                  }
                  const avatarUrl = isMine ? (user?.avatarUrl || "") : (msg.authorAvatar || "");
                  const avatarDecoration = isMine ? (user?.avatarDecorationUrl || "") : (msg.authorAvatarDecoration || "");
                  const avatarHtml = avatarWithDecorationHtml(avatarUrl, avatarDecoration, 32, initials(isMine ? "Me" : authorName(msg)));
                  const isEncrypted = msg.decrypted === true;
                  let contentHtml;
                  if (msg.type === 6) {
                    const actionUser = isMine ? "You" : escapeHtml(authorName(msg));
                    contentHtml = `<div class="friend-action"><b>${actionUser} pinned a message to this channel.</b><span class="friend-action-time">${escapeHtml(formatTime(msg.createdAt))}</span></div>`;
                  } else {
                    const snapHtml = forwardSnapshotHtml(msg);
                    contentHtml = snapHtml || (formatMessageText(msg.content, msg, user?.id, guildChannels?.roles) + (isEncrypted
                      ? `<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" class="message-lock"><defs><linearGradient id="lockGrad-${msg.id}" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#62d99a" /><stop offset="100%" stop-color="#1f8a51" /></linearGradient></defs><path d="M7.5 10.5V8a4.5 4.5 0 1 1 9 0v2.5" fill="none" stroke="url(#lockGrad-${msg.id})" stroke-width="2.2" stroke-linecap="round" /><rect x="5.5" y="10.5" width="13" height="10" rx="2.5" fill="url(#lockGrad-${msg.id})" /></svg>`
                      : `<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" class="message-lock message-lock-red"><path d="M7.5 10.5V8a4.5 4.5 0 1 1 9 0v2.5" fill="none" stroke="#f23f42" stroke-width="2.2" stroke-linecap="round" /><rect x="5.5" y="10.5" width="13" height="10" rx="2.5" fill="#f23f42" /></svg>`));
                  }
                   return `<article class="message-row ${isMine ? "mine" : ""}">
                    <div class="message-avatar" data-author-id="${escapeHtml(msg.author_id)}">${avatarHtml}</div>
                    <div class="message-bubble">
                      ${replyIndicatorHtml(msg)}
                      <div class="message-meta">
                        <strong>${isMine ? "You" : escapeHtml(authorName(msg))}</strong>
                        <time>${escapeHtml(formatTime(msg.createdAt))}</time>
                      </div>
                      ${forwardIndicatorHtml(msg)}
                      <div class="message-content">${contentHtml}</div>
                      ${reactionsHtml(msg, selectedChannelId)}
                    </div>
                  </article>`;
                }).join("") || (!messagesLoading ? '<div class="message-system">No messages yet.</div>' : '')}
              </div>
            </section>
            <div class="typing-indicator" id="typing-indicator" data-channel-id="${escapeHtml(selectedChannelId || currentChannel.id)}">
              <div class="typing-dots">
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
              </div>
              <span class="typing-text"></span>
            </div>
            ${currentChannel.type === 0 || currentChannel.type === 5
              ? `<form class="message-composer" data-message-form data-dm-id="${escapeHtml(selectedChannelId || currentChannel.id)}">
              <div class="composer-container">
                <div class="input-wrapper">
                  <input type="file" name="attachment" style="display:none" data-file-input data-channel-id="${escapeHtml(selectedChannelId || currentChannel.id)}">
                  <button type="button" class="upload-btn" data-upload-btn title="Upload attachment"><svg aria-hidden="true" role="img" xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="none" viewBox="0 0 24 24"><path fill="currentColor" d="M13 3a1 1 0 1 0-2 0v8H3a1 1 0 1 0 0 2h8v8a1 1 0 0 0 2 0v-8h8a1 1 0 0 0 0-2h-8V3Z"></path></svg></button>
                  <textarea name="message" rows="1" maxlength="2000" placeholder="Message #${escapeHtml(currentChannel.name)}"></textarea>
                  <button type="button" class="whisper-btn" data-whisper-btn data-channel-id="${escapeHtml(selectedChannelId || currentChannel.id)}" title="Send a Whisper">
                    <svg class="whisper-waveform" viewBox="0 0 40 20" fill="none">
                      <path d="M2 10h4l3-6 4 12 4-16 4 18 4-10 3 4h12" stroke="#23a55a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                  </button>
                </div>
                <div class="whisper-footer-hint">
                  <span class="wh-text">Send a whisper</span>
                  <span class="wh-badge">Hold button</span>
                </div>
              </div>
            </form>`
              : `<div class="message-system" style="padding:12px 16px;margin-top:8px;border-top:1px solid var(--bg-hover)">
                <span style="color:var(--text-muted);font-size:13px">You cannot send messages in this channel.</span>
              </div>`}
          </div>
          <div class="right">
            <h1>Members</h1>
            <div class="sec"><p>Member data not loaded yet.</p></div>
          </div>
        </div>
      `;
    }

    if (selectedDm) {
      const messages = selectedDmId ? messagesByDm[selectedDmId] || [] : [];
      const friendMatch = (friends?.friends || []).find(f => f.id === selectedDm.userId || (selectedDm.username && f.username === selectedDm.username));
      const isBlocked = (friends?.blocked || []).some(f => f.id === selectedDm.userId);
      const isSystemDm = (selectedDm.type === 1 && !selectedDm.userId) || selectedDm.isBot;
      const disableInput = isBlocked || isSystemDm;
      const userId = selectedDm.userId || friendMatch?.id || selectedDm.id;
      const rel = getRelationshipActions(selectedDm, friends, user?.id);
      const actionsHtml = rel.actions
        .map(action => `<button type="button" class="relationship-action" data-${action.key}="${escapeHtml(userId)}" title="${escapeHtml(action.label)}">${escapeHtml(action.label)}</button>`)
        .join('');

      const isDirectDm = selectedDm.type === 1;
      const firstMessageOnDiscord = isDirectDm && !selectedDm.lastMessageId && !disableInput && messages.length === 0 && !messagesLoading;

      return `
          <nav class="chat-navbar">
            <div class="left-side">
              <div class="chat-header-user">
                <div class="dm-avatar-mini">
                  ${selectedDm.avatarUrl ? `<img src="${escapeHtml(selectedDm.avatarUrl)}" alt="">${selectedDm.avatarDecorationUrl ? `<img src="${escapeHtml(selectedDm.avatarDecorationUrl)}" alt="" class="avatar-decoration" style="width:36px !important;height:36px !important">` : ""}` : `<div class="avatar-placeholder-sm">${escapeHtml(initials(selectedDm.displayName || selectedDm.username))}</div>`}
                </div>
                <div class="chat-header-meta">
                  <div class="chat-header-name">
                    <span>${escapeHtml(selectedDm.displayName || selectedDm.username)}</span>
                    ${selectedDm.type === 3 ? '' : `<span class="status-indicator-dot ${dotClass(selectedDm.status || 'online')}"></span>`}
                  </div>
                </div>
              </div>
              ${actionsHtml ? `<div class="relationship-actions">${actionsHtml}</div>` : ''}
            </div>
          <div class="icons-end">
            <button type="button" class="header-action-btn" data-ring-channel="${escapeHtml(selectedDmId)}" title="Start Call">
              <i class="fa-solid fa-phone"></i>
            </button>
            <button type="button" class="header-action-btn" data-open-discord="settings" title="More Options">
              <i class="fa-solid fa-ellipsis"></i>
            </button>
          </div>
        </nav>
        <div class="bottom">
          <div class="left">
            <section class="message-list">
              ${messagesLoading ? '<div class="message-system">Loading messages...</div>' : ''}
              ${messageError ? `<div class="message-system error">${escapeHtml(messageError)}</div>` : ''}
              <div class="chat-date-divider"><span>Today</span></div>
              <div class="message-list-inner">
                ${messages.map(msg => {
                  const isMine = msg.author === "me" || authorName(msg) === "me";
                  if (isSystemMessage(msg)) {
                    return renderSystemMessageRow(msg, selectedDmId, isMine);
                  }
                  const avatarUrl = isMine ? (user?.avatarUrl || "") : (msg.authorAvatar || selectedDm.avatarUrl || "");
                  const avatarDecoration = isMine ? (user?.avatarDecorationUrl || "") : (msg.authorAvatarDecoration || selectedDm.avatarDecorationUrl || "");
                  const avatarHtml = avatarWithDecorationHtml(avatarUrl, avatarDecoration, 32, initials(isMine ? "Me" : (selectedDm.displayName || selectedDm.username)));
                  const isEncrypted = msg.decrypted === true;
                  const isInvite = msg.content && (msg.content.includes("invited you to join Fern") || msg.content.includes("Your Invite Perk") || msg.content.includes("fern.rip") || msg.content.includes("fern.vip"));
                  const inviteHeroCard = isInvite ? `<div class="fern-invite-hero-card">
                    <div class="fihc-lock"><i class="fa-solid fa-lock"></i></div>
                    <div class="fihc-body">
                      <h2 class="fihc-heading">You\u2019ve been<br>invited to <span class="fihc-green">Fern!</span></h2>
                      <div class="fihc-actions">
                        <a href="https://fern.rip/" target="_blank" rel="noopener noreferrer" class="fihc-accept-btn">Accept Invite</a>
                        <div class="fihc-brand">
                          <span class="fihc-brand-pill"><i class="fa-solid fa-plus"></i> <i class="fa-solid fa-leaf"></i> <b>Fern</b></span>
                          <span class="fihc-sub">Encrypted. Private. Yours.</span>
                        </div>
                      </div>
                    </div>
                  </div>` : "";
                  let contentHtml;
                  if (msg.type === 6) {
                    const actionUser = isMine ? "You" : escapeHtml(authorName(msg) || selectedDm.displayName || selectedDm.username);
                    contentHtml = `<div class="friend-action"><b>${actionUser} pinned a message to this channel.</b><span class="friend-action-time">${escapeHtml(formatTime(msg.createdAt))}</span></div>`;
                  } else {
                    const snapHtml = forwardSnapshotHtml(msg);
                    const inviteCodes = extractInviteCodes(msg.content);
                    const invitesHtml = inviteCodes.length > 0 ? `<div class="message-invites">${inviteCodes.map(c => renderInviteCardHtml(c, state?.guilds)).join("")}</div>` : "";
                    contentHtml = (inviteHeroCard ? inviteHeroCard : "") + (snapHtml || (formatMessageText(msg.content, msg, user?.id) + (isEncrypted
                      ? `<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" class="message-lock"><defs><linearGradient id="lockGrad-${msg.id}" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stop-color="#62d99a" /><stop offset="100%" stop-color="#1f8a51" /></linearGradient></defs><path d="M7.5 10.5V8a4.5 4.5 0 1 1 9 0v2.5" fill="none" stroke="url(#lockGrad-${msg.id})" stroke-width="2.2" stroke-linecap="round" /><rect x="5.5" y="10.5" width="13" height="10" rx="2.5" fill="url(#lockGrad-${msg.id})" /></svg>`
                      : `<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" class="message-lock message-lock-red"><path d="M7.5 10.5V8a4.5 4.5 0 1 1 9 0v2.5" fill="none" stroke="#f23f42" stroke-width="2.2" stroke-linecap="round" /><rect x="5.5" y="10.5" width="13" height="10" rx="2.5" fill="#f23f42" /></svg>`))) + invitesHtml;
                  }
                  return `<article class="message-row ${isMine ? "mine" : ""}"
                    data-message-id="${escapeHtml(msg.id)}"
                    data-channel-id="${escapeHtml(selectedDmId)}">
                    <div class="message-avatar" data-author-id="${escapeHtml(msg.author_id)}">${avatarHtml}</div>
                    <div class="message-bubble">
                      ${replyIndicatorHtml(msg)}
                      <div class="message-meta">
                        <strong>${isMine ? "You" : escapeHtml(authorName(msg) || selectedDm.displayName || selectedDm.username)}</strong>
                        <time>${escapeHtml(formatTime(msg.createdAt))}</time>
                      </div>
                      ${forwardIndicatorHtml(msg)}
                      <div class="message-content">${contentHtml}</div>
                      ${reactionsHtml(msg, selectedDmId)}
                    </div>
                  </article>`;
                }).join("") || (!messagesLoading ? (firstMessageOnDiscord ? '<div class="message-system">No messages yet. Send the first one on the Discord app to start the conversation.</div>' : '<div class="message-system">No messages yet. Send the first one below.</div>') : '')}
              </div>
            </section>
            <div class="typing-indicator" id="typing-indicator" data-channel-id="${escapeHtml(selectedDm.id)}">
              <div class="typing-dots">
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
              </div>
              <span class="typing-text"></span>
            </div>
            ${disableInput
              ? isBlocked
                ? `<div class="message-system" style="padding:12px 16px;margin-top:8px;border-top:1px solid var(--bg-hover);display:flex;align-items:center;justify-content:space-between">
                <div><span style="color:#f23f42;font-weight:600;font-size:14px">Blocked</span><span style="color:var(--text-muted);font-size:13px;margin-left:8px">| You cannot message this user.</span></div>
                <button type="button" data-unblock-user="${escapeHtml(selectedDm.userId)}" style="background:var(--bg-mid);color:var(--text-primary);border:none;border-radius:3px;padding:6px 14px;font-size:13px;font-weight:500;cursor:pointer">Unblock</button>
              </div>`
                : `<div class="message-system" style="padding:12px 16px;margin-top:8px;border-top:1px solid var(--bg-hover)">
                <span style="color:var(--text-muted);font-size:13px">You cannot send messages in this conversation.</span>
              </div>`
              : firstMessageOnDiscord
                ? `<div class="message-system" style="padding:12px 16px;margin-top:8px;border-top:1px solid var(--bg-hover);display:flex;align-items:center;justify-content:space-between">
                <div><span style="color:#f0b232;font-weight:600;font-size:14px">First message required on Discord</span><span style="color:var(--text-muted);font-size:13px;margin-left:8px">| Send the first message to this user on the original Discord client before using Fern.</span></div>
                <button type="button" data-first-message-discord="${escapeHtml(selectedDm.id)}" style="background:var(--accent);color:#fff;border:none;border-radius:3px;padding:6px 14px;font-size:13px;font-weight:600;cursor:pointer">Open Discord</button>
              </div>`
                : `<form class="message-composer" data-message-form data-dm-id="${escapeHtml(selectedDm.id)}">
              <div class="composer-container">
                <div class="input-wrapper">
                  <input type="file" name="attachment" style="display:none" data-file-input data-channel-id="${escapeHtml(selectedDm.id)}">
                  <button type="button" class="upload-btn" data-upload-btn title="Upload attachment"><svg aria-hidden="true" role="img" xmlns="http://www.w3.org/2000/svg" width="20" height="20" fill="none" viewBox="0 0 24 24"><path fill="currentColor" d="M13 3a1 1 0 1 0-2 0v8H3a1 1 0 1 0 0 2h8v8a1 1 0 0 0 2 0v-8h8a1 1 0 0 0 0-2h-8V3Z"></path></svg></button>
                  ${isDirectDm ? `<button type="button" class="invite-btn" data-invite-btn data-invite-user="${escapeHtml(userId)}" title="Invite to Fern"><svg aria-hidden="true" role="img" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24"><path fill="currentColor" d="M2,21h8a1,1,0,0,0,0-2H3.071A7.011,7.011,0,0,1,10,13a5.044,5.044,0,1,0-3.377-1.337A9.01,9.01,0,0,0,1,20,1,1,0,0,0,2,21ZM10,5A3,3,0,1,1,7,8,3,3,0,0,1,10,5ZM23,16a1,1,0,0,1-1,1H19v3a1,1,0,0,1-2,0V17H14a1,1,0,0,1,0-2h3V12a1,1,0,0,1,2,0v3h3A1,1,0,0,1,23,16Z"/></svg></button>` : ''}
                  <textarea name="message" rows="1" maxlength="2000" placeholder="Message ${escapeHtml(selectedDm.displayName || selectedDm.username)}"></textarea>
                  <button type="button" class="whisper-btn" data-whisper-btn data-channel-id="${escapeHtml(selectedDm.id)}" title="Send a Whisper voice message">
                    <svg class="whisper-waveform" viewBox="0 0 40 20" fill="none">
                      <path d="M2 10h4l3-6 4 12 4-16 4 18 4-10 3 4h12" stroke="#23a55a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                  </button>
                </div>
                <div class="whisper-footer-hint">
                  <span class="wh-text">Send a whisper</span>
                  <span class="wh-badge">Hold button</span>
                </div>
              </div>
            </form>`}
          </div>
          <div class="right">
            <h1>Members</h1>
            <div class="sec"><p>No members</p></div>
          </div>
        </div>
      `;
    }

    const showAddFriend = state.showAddFriend;
    if (showAddFriend) {
    return `
      <nav>
        <div class="left-side">
          <div class="one friends-title">${FriendsSvg}<p>Friends</p></div>
          <div class="buttons">
            <button class="${friendFilter === "Online" ? "active" : ""}" data-friend-filter="Online">Online</button>
            <button class="${friendFilter === "All" ? "active" : ""}" data-friend-filter="All">All</button>
            <button class="${friendFilter === "Pending" ? "active" : ""}" data-friend-filter="Pending">Pending</button>
            <button class="${friendFilter === "Blocked" ? "active" : ""}" data-friend-filter="Blocked">Blocked</button>
            <button data-add-friend-toggle>Add Friend</button>
          </div>
        </div>
        <div class="icons-end"></div>
      </nav>
      <div class="bottom">
        <div class="left add-friend-panel">
          ${AddFriendPanel({ friends, friendError, friendFilter, friendFormValue, forceShowForm: true })}
        </div>
      </div>`;
    }

    return `
      <nav>
        <div class="left-side">
          <div class="one friends-title">${FriendsSvg}<p>Friends</p></div>
          <div class="buttons">
            <button class="${friendFilter === "Online" ? "active" : ""}" data-friend-filter="Online">Online</button>
            <button class="${friendFilter === "All" ? "active" : ""}" data-friend-filter="All">All</button>
            <button class="${friendFilter === "Pending" ? "active" : ""}" data-friend-filter="Pending">Pending</button>
            <button class="${friendFilter === "Blocked" ? "active" : ""}" data-friend-filter="Blocked">Blocked</button>
            <button data-add-friend-toggle>Add Friend</button>
          </div>
        </div>
        <div class="icons-end"></div>
      </nav>
      <div class="bottom">
        ${(!friends?.friends?.length && !friends?.pending?.length)
          ? `<div class="left pane">
            <img src="/static/assets/fern_icon.png" alt="img-centre">
            <p>No one's around to play with Encryption.</p>
          </div>`
          : `<div class="left add-friend-panel">
            ${AddFriendPanel({ friends, friendError, friendFilter, friendFormValue })}
          </div>`
        }
      </div>
    `;
  },
};

const messageElements = new Map();

export function updateMessageList(container, messages, selectedDm, user) {
  const existingIds = new Set();
  for (const child of container.children) {
    const id = child.dataset.messageId;
    if (id) existingIds.add(id);
  }

  const newMessages = messages.filter(m => !existingIds.has(m.id));
  const removedIds = [];
  for (const child of container.children) {
    const id = child.dataset.messageId;
    if (id && !messages.some(m => m.id === id)) {
      removedIds.push(id);
    }
  }

  for (const id of removedIds) {
    const el = messageElements.get(id);
    if (el && el.parentNode === container) {
      container.removeChild(el);
    }
    messageElements.delete(id);
  }

  const sortedNew = [...newMessages].sort((a, b) => (a.createdAt || '').localeCompare(b.createdAt || ''));
  for (const msg of sortedNew) {
    const html = messageRowHtml(msg, selectedDm, user);
    const temp = document.createElement('div');
    temp.innerHTML = html;
    const element = temp.firstElementChild;
    element.dataset.messageId = msg.id;
    container.appendChild(element);
    messageElements.set(msg.id, element);
  }
}

function getRelationshipActions(dmUser, friendsData, currentUserId) {
  if (!dmUser?.id) return { type: 'none', actions: [] };

  const userId = dmUser.userId || dmUser.id;
  if (!userId || userId === currentUserId) return { type: 'none', actions: [] };
  if (!dmUser.userId) return { type: 'group', actions: [] };

  return {
    type: 'dm',
    actions: [
      { label: 'Remove Friend', key: 'remove-friend' },
      { label: 'Block User', key: 'block-user' },
    ],
  };
}

function messageRowHtml(msg, selectedDm, user) {
  const authorName = typeof msg.author === "object" && msg.author !== null
    ? (msg.author.username || msg.author.id || "Unknown")
    : msg.author;
  const authorDisplay = typeof msg.authorDisplay === "object"
    ? (msg.authorDisplay.username || msg.authorDisplay.global_name || "")
    : (msg.authorDisplay || "");
  const isMine = authorName === "me" || authorName === user?.username;
  const avatarUrl = isMine ? (user?.avatarUrl || "") : (msg.authorAvatar || "");
  const avatarDecoration = isMine ? (user?.avatarDecorationUrl || "") : (msg.authorAvatarDecoration || "");
  const avatarHtml = avatarWithDecorationHtml(avatarUrl, avatarDecoration, 32, initials(isMine ? "Me" : (authorDisplay || authorName)));
  return `<article class="message-row ${isMine ? "mine" : ""}">
    <div class="message-avatar" data-author-id="${escapeHtml(msg.author_id)}">${avatarHtml}</div>
    <div class="message-bubble">
      <div class="message-meta">
        <strong>${isMine ? "You" : escapeHtml(authorDisplay || authorName)}</strong>
        <time>${escapeHtml(formatTime(msg.createdAt))}</time>
      </div>
      <div class="message-content">${formatMessageText(msg.content, msg, user?.id)}</div>
    </div>
  </article>`;
}
