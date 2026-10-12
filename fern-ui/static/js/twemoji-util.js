const TWEMOJI_BASE = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@latest/assets/svg/";

export function toCodePoint(unicodeSurrogates) {
  const r = [];
  let c = 0;
  let p = 0;
  let i = 0;
  while (i < unicodeSurrogates.length) {
    c = unicodeSurrogates.charCodeAt(i++);
    if (p) {
      r.push((0x10000 + ((p - 0xD800) << 10) + (c - 0xDC00)).toString(16));
      p = 0;
    } else if (0xD800 <= c && c <= 0xDBFF) {
      p = c;
    } else {
      r.push(c.toString(16));
    }
  }
  return r.join("-");
}

export function grabTheRightIcon(raw) {
  let icon = toCodePoint(raw);
  if (!icon) return "";
  if (!icon.includes("200d")) {
    icon = icon.replace(/-fe0f/g, "");
  }
  return icon;
}

const EMOJI_REGEX = /(?:[\u2700-\u27bf]|(?:\ud83c[\udde6-\uddff]){2}|[\ud800-\udbff][\udc00-\udfff]|[\u0023-\u0039]\ufe0f?\u20e3|\u3299|\u3297|\u303d|\u3030|\u24c2|\ud83c[\udd70-\udd71]|\ud83c[\udd7e-\udd7f]|\ud83c\udd8e|\ud83c[\udd91-\udd9a]|\ud83c[\udde6-\uddff]|[\ud83c\ude01-\ude02]|\ud83c\ude1a|\ud83c\ude2f|[\ud83c\ude32-\ude3a]|[\ud83c\ude50-\ude51]|\u203c|\u2049|[\u25aa-\u25ab]|\u25b6|\u25c0|[\u25fb-\u25fe]|\u00a9|\u00ae|\u2122|\u2139|\ud83c\udc04|[\u2600-\u26ff]|\u2b55|\u2b1c|\u2b1b|\u2b50)/g;

export function parseTwemoji(html, isJumbo = false) {
  if (!html || typeof html !== "string") return "";
  if (typeof window !== "undefined" && window.twemoji && window.twemoji.parse) {
    try {
      return window.twemoji.parse(html, {
        folder: "svg",
        ext: ".svg",
        base: "https://cdn.jsdelivr.net/gh/jdecked/twemoji@latest/assets/",
        className: isJumbo ? "emoji jumboable" : "emoji",
        attributes: () => ({ draggable: "false" })
      });
    } catch {}
  }
  return html.replace(EMOJI_REGEX, (match) => {
    const icon = grabTheRightIcon(match);
    if (!icon) return match;
    const cls = isJumbo ? "emoji jumboable" : "emoji";
    return `<img class="${cls}" draggable="false" alt="${match}" src="${TWEMOJI_BASE}${icon}.svg" loading="lazy">`;
  });
}

export function isOnlyEmoji(text) {
  if (!text) return false;
  const clean = text.trim();
  if (!clean) return false;
  const withoutEmojis = clean.replace(EMOJI_REGEX, "").replace(/<a?:[A-Za-z0-9_]+:\d{17,21}>/g, "").replace(/\s+/g, "");
  return withoutEmojis.length === 0;
}
