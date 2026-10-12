import aiohttp
import requests
import random
import string
from utils.build import make_client, ClientIdentity
import requests

global CHROME_VERSION
global EDGE_VERSION

CHROME_VERSION = None
EDGE_VERSION = None

class Settings:
    @staticmethod
    async def set_up():
        CHROME_VERSION = get_latest_chrome_version()
        EDGE_VERSION = CHROME_VERSION
        print("[SETUP] Browser versions set.")

SEC_CH_UA = (
    f'"Chromium";v="{CHROME_VERSION}", '
    f'"Microsoft Edge";v="{EDGE_VERSION}", '
    f'"Not-A.Brand";v="99"'
)
SEC_CH_UA_PLATFORM = '"Xbox"'

USER_AGENT = (
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    f"AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/{CHROME_VERSION}.0.0.0 Safari/537.36 "
    f"Edg/{EDGE_VERSION}.0.0.0 "
    f"Xbox; Xbox One)"
)
_CACHE = None

def _get_identity() -> ClientIdentity:
    global _CACHE
    if _CACHE is not None:
        return _CACHE[0]
    return make_client()

def fetch(url):
    identity = _get_identity()
    dl_headers = identity.browse_headers()
    dl_headers["accept-encoding"] = "gzip, deflate, br"
    resp = requests.get(url, headers=dl_headers, timeout=30)
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")

def get_latest_chrome_version():
    try:
        r = requests.get(
            "https://versionhistory.googleapis.com/v1/chrome/platforms/win/channels/stable/versions",
            timeout=5,
        )
        version = r.json()["versions"][0]["version"]
        major = int(version.split(".")[0])
        return major
    except Exception as e:
        return 131

async def fetch_discord_cookies(session):
        async with session.get("https://discord.com"):

            cookies = session.cookie_jar.filter_cookies("https://discord.com")
            dcfduid = cookies.get("__dcfduid")
            sdcfduid = cookies.get("__sdcfduid")

            return dcfduid, sdcfduid

def generate_nonce(length=18):
    return ''.join(random.choices(string.digits, k=length))

class Discord:
    @staticmethod
    async def send(content, channel, token):

        json_data = {
            'mobile_network_type': 'unknown',
            'content': f'{content}',
            'nonce': generate_nonce(),
            'tts': False,
            'flags': 0,
        }

        url = f'https://discord.com/api/v9/channels/{channel}/messages'

        async with aiohttp.ClientSession() as session:
            dcfduid, sdcfduid = await fetch_discord_cookies(session)
            cookies = {"dcfduid": dcfduid, "sdcfduid": sdcfduid}
            headers = {
                'accept': '*/*',
                'accept-language': 'it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7',
                'authorization': f"{token}",
                'content-type': 'application/json',
                'origin': 'https://discord.com',
                'priority': 'u=1, i',
                'referer': f'https://discord.com/channels/@me/{channel}',
                'sec-ch-ua': '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin',
                "sec-fetch-user": "?1",
                "upgrade-insecure-requests": "1",
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
                'x-debug-options': 'bugReporterEnabled',
                'x-discord-locale': 'en-GB',
                'x-discord-timezone': 'Europe/Paris',

            }
            async with session.post(url, cookies=cookies, headers=headers, json=json_data) as response:
                return response.status, await response.text()
    @staticmethod
    async def read(channel, token, limit=10):

        url = f'https://discord.com/api/v9/channels/{channel}/messages'

        async with aiohttp.ClientSession() as session:
            dcfduid, sdcfduid = await fetch_discord_cookies(session)
            cookies = {"dcfduid": dcfduid, "sdcfduid": sdcfduid}
            params = {
                'limit': f'{limit}',
            }

            headers = {
                'accept': '*/*',
                'accept-language': 'it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7',
                'authorization': f"{token}",
                'content-type': 'application/json',
                'origin': 'https://discord.com',
                'priority': 'u=1, i',
                'referer': f'https://discord.com/channels/@me/{channel}',
                'sec-ch-ua': '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin',
                "sec-fetch-user": "?1",
                "upgrade-insecure-requests": "1",
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
                'x-debug-options': 'bugReporterEnabled',
                'x-discord-locale': 'en-GB',
                'x-discord-timezone': 'Europe/Paris',

            }
            async with session.get(url, cookies=cookies, headers=headers, params=params) as response:
                return response.status, await response.text()
    @staticmethod
    async def sticker(sticker_id, fmt="png"):

        url = f'https://cdn.discordapp.com/stickers/{sticker_id}.{fmt}'

        async with aiohttp.ClientSession() as session:

            headers = {
                'accept': '*/*',
                'accept-language': 'en-US,en;q=0.9',
                'origin': 'https://discord.com',
                'priority': 'u=1, i',
                'referer': 'https://discord.com/channels/@me/',
                'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="138"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9238 Chrome/138.0.7204.251 Electron/37.6.0 Safari/537.36',
                'x-debug-options': 'bugReporterEnabled',
                'x-discord-locale': 'en-US',
                'x-discord-timezone': 'UTC',
            }
            async with session.get(url, headers=headers) as response:
                content = await response.read()
                content_type = response.headers.get(
                    "Content-Type",
                    "application/json"
                )

                return response.status, content, content_type