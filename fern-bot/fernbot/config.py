import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:5000")
API_KEY_FILE = os.getenv("API_KEY_FILE", "fern-ui/json/admin_key.json")
REVIEW_CHANNEL_ID = int(os.getenv("REVIEW_CHANNEL_ID", "0"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))
TICKET_COOLDOWN = int(os.getenv("TICKET_COOLDOWN", "300"))