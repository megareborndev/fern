API_BASE = "https://discord.com/api/v9"
GATEWAY_URL = "wss://gateway.discord.gg/?v=9&encoding=json"

ENDPOINT_ME = "/users/@me"
ENDPOINT_GUILD_MEMBERS = "/guilds/{guild_id}/members"
ENDPOINT_DM_CHANNEL = "/users/@me/channels"
ENDPOINT_CHANNEL_MESSAGES = "/channels/{channel_id}/messages"
ENDPOINT_CHANNEL_TYPING = "/channels/{channel_id}/typing"

GATEWAY_DISPATCH = 0
GATEWAY_HEARTBEAT = 1
GATEWAY_IDENTIFY = 2
GATEWAY_PRESENCE_UPDATE = 3
GATEWAY_RESUME = 6
GATEWAY_RECONNECT = 7
GATEWAY_REQUEST_GUILD_MEMBERS = 8
GATEWAY_INVALID_SESSION = 9
GATEWAY_SEARCH_RECENT_MEMBERS = 35
GATEWAY_HELLO = 10
GATEWAY_HEARTBEAT_ACK = 11

INTENTS_ALL = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5) | (1 << 6) | (1 << 7) | (1 << 8) | (1 << 9) | (1 << 10) | (1 << 11) | (1 << 12) | (1 << 13) | (1 << 14) | (1 << 15) | (1 << 16) | (1 << 20) | (1 << 21)
DISCORD_CLIENT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9210 Chrome/134.0.6998.205 Electron/35.3.0 Safari/537.36"

HCAPTCHA_SITEKEY = "4c672d35-0701-42b2-88c3-78380b0db560"

ANYSOLVER_BASE = "https://api.anysolver.com"
ANYSOLVER_CREATE_TASK = "/createTask"
ANYSOLVER_GET_RESULT = "/getTaskResult"
ANYSOLVER_GET_BALANCE = "/getBalance"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
IMAGE_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

DEFAULT_CONFIG = {
    "dm": {
        "delay_min": 0.0,
        "delay_max": 0.0,
        "typing_min": 0.0,
        "typing_max": 0.0,
        "burst_size": 999,
        "burst_cooldown_min": 0,
        "burst_cooldown_max": 0,
        "max_concurrent": 5,
        "checkpoint_interval": 50,
    },
    "captcha": {
        "api_key": "",
        "cloudflare_api_key": "",
        "sitekey": HCAPTCHA_SITEKEY,
        "timeout": 120,
    },
}