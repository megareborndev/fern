# cyberbeach.cc & discord.gg/cyberbeach
import time, os, logging, sys
from colorama import Fore, Style
from utils.version import __version__
# from config import load_config
# config = load_config()

START_TIME = time.time()

# ! CUSTOM LEVELS
AD_LEVEL = 25
logging.addLevelName(AD_LEVEL, "AD")

def ad(self, message, *args, **kwargs):
    if self.isEnabledFor(AD_LEVEL):
        self._log(AD_LEVEL, message, args, **kwargs)

logging.Logger.ad = ad

CHECK_LEVEL = 21
logging.addLevelName(CHECK_LEVEL, "CHECK")

def check(self, message, *args, **kwargs):
    if self.isEnabledFor(CHECK_LEVEL):
        self._log(CHECK_LEVEL, message, args, **kwargs)

logging.Logger.check = check

# ! STATS
STATS = {
    "unlocked": 0,
    "locked": 0,
    "rate": 0,
}

# ! CPM
def get_cpm():
    elapsed = time.time() - START_TIME
    total = (
        STATS["unlocked"] + STATS["invalid"] + STATS["locked"] + STATS["rate"] + STATS["error"]
    )
    return int((total / elapsed) * 60) if elapsed > 0 else 0

# ! LIVE
def update_stats(code):
    STATS["checked"] += 1
    if code == 200:
        STATS["valid"] += 1
    elif code == 401:
        STATS["invalid"] += 1
    elif code == 403:
        STATS["locked"] += 1
    elif code == 429:
        STATS["rate"] += 1
    else:
        STATS["error"] += 1

# ! FORMAT
def format_token_id(token: str) -> str:
    if token and len(token) > 10:
        return f"{token[:4]}...{token[-4:]}"
    elif token and len(token) > 0:
        return f"{token[:4]}.."
    else:
        return "Unknown***"

# ! PALETTE
class Beach:
    OCEAN = Fore.CYAN
    DEEP = Fore.BLUE
    SAND = Fore.LIGHTYELLOW_EX
    SUNSET = Fore.LIGHTMAGENTA_EX
    CORAL = Fore.LIGHTRED_EX
    FOAM = Fore.WHITE
    PALM = Fore.GREEN
    GLOW = Style.BRIGHT
    INFO = FOAM
    WARNING = SAND
    ERROR = CORAL
    DEBUG = DEEP

# ! HELPER
class CyberBeachFormatter(logging.Formatter):
    LEVEL_ABBREV = {
        "DEBUG":     "DBG ",
        "INFO":      "INF ",
        "WARNING":   "WRN ",
        "ERROR":     "ERR ",
        "CRITICAL":  "CRT ",
        "CHECK":     "SUC ",
    }

    LEVEL_COLORS = {
        "DEBUG":    Fore.LIGHTBLUE_EX,
        "ERROR":    Fore.LIGHTRED_EX,
        "CHECK":    Fore.LIGHTGREEN_EX,
    }

    def format(self, record):
        time_str = f"{Fore.YELLOW}[{Style.RESET_ALL}{time.strftime('%H:%M', time.localtime(record.created))}{Fore.YELLOW}]{Style.RESET_ALL}"
        abbr = self.LEVEL_ABBREV.get(record.levelname, record.levelname[:3].upper())
        color = self.LEVEL_COLORS.get(record.levelname, "")
        if color:
            level_abbr = f"{color}{abbr}{Style.RESET_ALL}"
        else:
            level_abbr = abbr
        msg = record.getMessage()
        return f"{time_str} {level_abbr} {msg}"

# ! LOGGER
DEBUG_MODE = True  # config.get("debug", False)

def setup_logger(name="cyberbeach"):
    os.makedirs("log", exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)
    console.setFormatter(CyberBeachFormatter())

    file = logging.FileHandler("log/logs.txt", encoding="utf-8")
    file.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)
    file.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S"
    ))

    logger.addHandler(console)
    logger.addHandler(file)
    return logger

log = setup_logger(__name__)

# ! MAP
STATUS_MAP = {
    200: ("UNLOCKED ✅", Beach.PALM),
    401: ("INVALID ❌", Beach.CORAL),
    403: ("LOCKED 🔒", Beach.SUNSET),
    429: ("RATE ⏳", Beach.SAND),
}

def format_status(code):
    label, color = STATUS_MAP.get(code, ("ERROR 💥", Beach.CORAL))
    return f"{color}{label:<12} [{code}]{Style.RESET_ALL}"

# ! SYSTEM
def system(cmd=None, title=None):
    if cmd == "clear":
        os.system("cls" if os.name == "nt" else "clear")
    if title and os.name == "nt":
        os.system(f'title "{title}"')

# ! TITLE
_last = 0
def update_title(title=None, delay=0.5):
    if title is None:
        title = f"Palm-Gen v{__version__}"
    global _last
    now = time.time()
    if now - _last > delay:
        t = (
            f"{title} | "
            f"Unlocked: {STATS['unlocked']} "
            f"Locked: {STATS['locked']} "
            f"Rate: {STATS['rate']} "
        )
        system(title=t)
        _last = now

# ! BANNER
def show_banner():
    print(Fore.YELLOW + Style.BRIGHT + rf"""
╭─╮╭─╮╷  ╭┬╮
├─╯├─┤│  │││
╵  ╵ ╵╰─╴╵ ╵
""" + Style.RESET_ALL)

# ! EXIT
def exit_program():
    input("press enter to exit")