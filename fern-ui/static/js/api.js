export class ApiError extends Error {
  constructor(message, status, data = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

async function requestJson(path, options = {}) {
  const { headers: extraHeaders, ...rest } = options;
  const response = await fetch(path, {
    // credentials: "same-origin", WILL BE RE-ENABLED IN PRODUCTION!!!
	credentials: "include", // DEVELOPEMENT ENVIRONMENT ONLY!
    headers: {
      Accept: "application/json",
      ...(rest.body ? { "Content-Type": "application/json" } : {}),
      ...extraHeaders,
    },
    ...rest,
  });

  let rawText = null;
  let data = null;
  try {
    rawText = await response.text();
    if (rawText) {
      data = JSON.parse(rawText);
    }
  } catch {
    data = null;
  }

  if (response.status === 401) {
    const message = data?.message || "Session expired";
    window.location.assign("/login?error=" + encodeURIComponent(message));
    throw new ApiError(message, 401);
  }

  if (response.status === 428) {
    throw new ApiError(
      data?.message || "Captcha required",
      428,
      data || {}
    );
  }

  if (response.status === 429) {
    throw new ApiError(
      data?.message || "You are getting rate limited",
      429,
      {
        ...(data || {}),
        retryAfter: data?.retry_after ?? response.headers.get("Retry-After") ?? null,
      }
    );
  }

  if (!response.ok) {
    throw new ApiError(`Request failed: ${path}`, response.status, data);
  }

  return data;
}

export const api = {
  me: () => requestJson("/api/me"),
  version: () => requestJson("/api/version"),
  guilds: () => requestJson("/api/guilds"),
  dms: () => requestJson("/api/dms"),
  markRead: (channelId, lastMessageId) =>
    requestJson(`/api/read/${encodeURIComponent(channelId)}`, {
      method: "POST",
      body: JSON.stringify({ last_message_id: lastMessageId }),
    }),
  readCursor: (channelId) =>
    requestJson(`/api/read/${encodeURIComponent(channelId)}`),
  settings: () => requestJson("/api/settings"),
  licenseStatus: () => requestJson("/api/license/status"),
  referralStats: () => requestJson("/api/referral/stats"),
  inviteUser: (inviteeId) =>
    requestJson("/api/referral/invite", {
      method: "POST",
      body: JSON.stringify({ invitee_id: inviteeId }),
    }),
  changelog: () => requestJson("/api/changelog"),
  safetyScore: () => requestJson("/api/safety-score"),
  obscureRoute: (refresh = false) =>
    requestJson(`/api/obscure/route${refresh ? "?refresh=1" : ""}`, {
      method: refresh ? "POST" : "GET",
    }),
  getAlias: (userId) => requestJson(userId ? `/api/alias?user_id=${encodeURIComponent(userId)}` : "/api/alias"),
  claimAlias: (alias, userId) =>
    requestJson("/api/alias/claim", {
      method: "POST",
      body: JSON.stringify({ alias, user_id: userId }),
    }),
  toggleAlias: (enabled, userId) =>
    requestJson("/api/alias/toggle", {
      method: "POST",
      body: JSON.stringify({ enabled, user_id: userId }),
    }),
  friends: () => requestJson("/api/friends"),
  addFriend: (username, discriminator = "0000", captcha = null, rqtoken = null) => {
    const body = { username, discriminator };
    if (captcha) body.captcha = captcha;
    if (rqtoken) body.rqtoken = rqtoken;
    return requestJson("/api/friends", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  acceptFriend: (friendId) =>
    requestJson(`/api/friends/${encodeURIComponent(friendId)}`, {
      method: "POST",
    }),
  guildChannels: (guildId) =>
    requestJson(`/api/guilds/${encodeURIComponent(guildId)}/channels`),
  guildMembers: (guildId) =>
    requestJson(`/api/guilds/${encodeURIComponent(guildId)}/members?limit=1000`),
  channelMembers: (channelId) =>
    requestJson(`/api/channels/${encodeURIComponent(channelId)}/members`),
  userInfo: (userId) =>
    requestJson(`/api/users/${encodeURIComponent(userId)}`),
  joinGuild: (inviteCode, captcha = null, rqtoken = null) => {
    const body = { invite_code: inviteCode };
    if (captcha) body.captcha = captcha;
    if (rqtoken) body.rqtoken = rqtoken;
    return requestJson("/api/guilds/join", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  getInvite: (inviteCode) =>
    requestJson(`/api/invites/${encodeURIComponent(inviteCode)}`).catch(() => null),
  leaveGuild: (guildId) =>
    requestJson(`/api/guilds/${encodeURIComponent(guildId)}/leave`, {
      method: "DELETE",
    }),
  messages: (dmId) =>
    requestJson(`${window.FERN_BACKEND_URL || "http://45.154.98.198:5001"}/read/discord/${encodeURIComponent(dmId)}`),
  sendMessage: (dmId, content, replyTo, encrypt) => {
    const body = { channel_id: dmId, content };
    if (replyTo) body.reply_to = replyTo;
    if (encrypt !== undefined) body.encrypt = encrypt === true || encrypt === "true";
    return requestJson(`${window.FERN_BACKEND_URL || "http://45.154.98.198:5001"}/send/discord/${encodeURIComponent(dmId)}`,
      { method: "POST", body: JSON.stringify(body) }
    );
  },
  typing: (channelId) =>
    requestJson(`/api/typing/${encodeURIComponent(channelId)}`, {
      method: "POST",
    }).catch(() => null),
  session: () => requestJson("/api/session"),
  ringPrivateCall: (channelId) =>
    requestJson(`/api/ring/${encodeURIComponent(channelId)}`, {
      method: "POST",
    }),
  removeFriend: (friendId) =>
    requestJson(`/api/friends/${encodeURIComponent(friendId)}`, {
      method: "DELETE",
    }),
  ignoreFriend: (friendId) =>
    requestJson(`/api/friends/${encodeURIComponent(friendId)}`, {
      method: "DELETE",
    }),
  blockUser: (userId) =>
    requestJson(`/api/friends/${encodeURIComponent(userId)}/block`, {
      method: "POST",
    }),
  unblockUser: (userId) =>
    requestJson(`/api/friends/${encodeURIComponent(userId)}/block`, {
      method: "DELETE",
    }),
  deleteMessage: (channelId, messageId) =>
    requestJson(`/api/messages/${encodeURIComponent(channelId)}/${encodeURIComponent(messageId)}`, {
        method: "DELETE",
    }),

  editMessage: (channelId, messageId, content) =>
      requestJson(`/api/messages/${encodeURIComponent(channelId)}/${encodeURIComponent(messageId)}`, {
          method: "PATCH",
          body: JSON.stringify({ content }),
      }),

  pinMessage: (channelId, messageId) =>
      requestJson(`/api/pins/${encodeURIComponent(channelId)}/${encodeURIComponent(messageId)}`, {
          method: "PUT",
      }),

  unpinMessage: (channelId, messageId) =>
      requestJson(`/api/pins/${encodeURIComponent(channelId)}/${encodeURIComponent(messageId)}`, {
          method: "DELETE",
      }),

  addReaction: (channelId, messageId, emoji) => {
    let emojiParam = emoji;
    if (typeof emoji === "object" && emoji !== null) {
      emojiParam = emoji.id ? `${emoji.name}:${emoji.id}` : (emoji.name || "");
    } else if (typeof emoji === "string") {
      const match = /^<(?:a)?:([A-Za-z0-9_]+):(\d{17,21})>$/.exec(emoji);
      if (match) emojiParam = `${match[1]}:${match[2]}`;
    }
    return requestJson(`/api/reactions/${encodeURIComponent(channelId)}/${encodeURIComponent(messageId)}/${encodeURIComponent(emojiParam)}`, {
      method: "PUT",
    });
  },

  removeReaction: (channelId, messageId, emoji) => {
    let emojiParam = emoji;
    if (typeof emoji === "object" && emoji !== null) {
      emojiParam = emoji.id ? `${emoji.name}:${emoji.id}` : (emoji.name || "");
    } else if (typeof emoji === "string") {
      const match = /^<(?:a)?:([A-Za-z0-9_]+):(\d{17,21})>$/.exec(emoji);
      if (match) emojiParam = `${match[1]}:${match[2]}`;
    }
    return requestJson(`/api/reactions/${encodeURIComponent(channelId)}/${encodeURIComponent(messageId)}/${encodeURIComponent(emojiParam)}`, {
      method: "DELETE",
    });
  },  
  forwardMessage: (channelId, messageId, targetChannelId) =>
    requestJson("/api/forward", {
        method: "POST",
        body: JSON.stringify({
            channel_id: channelId,
            message_id: messageId,
            target_channel_id: targetChannelId,
        }),
    }),
  createGroup: (recipients, name) => {
    const body = { recipients };
    if (name) body.name = name;
    return requestJson("/api/groups", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  addGroupMember: (groupId, userId) =>
    requestJson(`/api/groups/${encodeURIComponent(groupId)}/recipients`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),
  removeGroupMember: (groupId, userId) =>
    requestJson(`/api/groups/${encodeURIComponent(groupId)}/recipients/${encodeURIComponent(userId)}`, {
      method: "DELETE",
    }),
  sendMessageViaFlask: (channelId, content, replyTo, captcha, rqtoken) => {
    const body = { channel_id: channelId, content };
    if (replyTo) body.reply_to = replyTo;
    if (captcha) body.captcha = captcha;
    if (rqtoken) body.rqtoken = rqtoken;
    return requestJson("/api/send", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
};