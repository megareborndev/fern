import base64
import hashlib
import json
import logging
import os
import aiofiles
from datetime import datetime
from threading import Thread
import dave
import queue as sync_queue
import aiohttp
from collections.abc import Awaitable, Callable
from flask import Flask, Response, abort, jsonify, request
from flask_cors import CORS
from flask_sock import Sock
import socket
import struct
from utils import db
from urllib.parse import urlparse, quote

from encryptor import DiscordCrypto
from discord_voice_manager import DiscordVoiceManager
import payloads

# temp fix
import sys; import asyncio;
if sys.platform == 'win32': 
    #asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy()) # needed to make voice work sorries

db_conn = None

def init_db():
    global db_conn
    db_conn = db.connect()
    db.setup_tables(db_conn)
    print("SUPABASE CONNECTED")

# basic log
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(asctime)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
#logging.disable(logging.CRITICAL)
logger = logging.getLogger("discord-proxy")

script_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(script_dir)

app = Flask(__name__)
app.config["SOCK_SERVER_OPTIONS"] = {
    "ping_interval": 25,
}
app.secret_key = os.environ.get("FERN_UI_SECRET_KEY", "fern-ui-local-dev-secret")
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True) # supports_credentials=False IN PRODUCTION!!!
sock = Sock(app)

# globals
NOP4_URL = "http://127.0.0.1:3002"
DISCORD_API_BASE = "https://discord.com/api/v10"
AUDIO_ENCRYPTED_PREFIX = b"FA1"
ENCRYPTED_PREFIX = "ENC:v1:"
DOMAIN_REST = "https://api.fern.rip" # HAS TO BE CHANGED IN PRODUCTION

discord_voice_session_loop = asyncio.new_event_loop()

def _run_discord_loop():
    asyncio.set_event_loop(discord_voice_session_loop)
    discord_voice_session_loop.run_forever()

def obtain_licence_cookie(cookie_value: str):
    from flask.sessions import SecureCookieSessionInterface
    si = SecureCookieSessionInterface()
    try:
        print(si.get_signing_serializer(app).loads(cookie_value))
        return si.get_signing_serializer(app).loads(cookie_value)
    except Exception as e:
        return None

db_old = {
    "licences": {
        "ADMIN": {
            "discord": {
                "token": "",
            }
        },
        "ADMIN2": {
            "discord": {
                "token": "",
            }
        }
    }
}

tokens_store = {}

voice_manager = DiscordVoiceManager()
call_media_cache = {}
call_session_cache = {}
call_channel_cache = {}
call_custom_keys = {}

audio_queues = {}
call_listen_queues = {}
ffmpeg_procs = {}

def image_data_url_sync(seed: str) -> str:
    color_seed = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96">'
        f'<rect width="96" height="96" rx="32" fill="#{color_seed}"/>'
        f'<text x="50%" y="55%" dominant-baseline="middle" text-anchor="middle" '
        f'font-family="Arial" font-size="34" font-weight="700" fill="white">'
        f'{seed[:1].upper() or "?"}</text></svg>'
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def avatar_url_for_user(user: dict) -> str:
    user_id = str(user.get("id") or "")
    avatar = user.get("avatar")
    if user_id and avatar:
        ext = "gif" if str(avatar).startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{ext}?size=128"
    username = user.get("username") or user_id or "User"
    return image_data_url_sync(username)

def avatar_decoration_url_for_user(user: dict) -> str | None:
    deco = user.get("avatar_decoration_data")
    if deco and isinstance(deco, dict):
        asset = deco.get("asset")
        if asset:
            return f"https://cdn.discordapp.com/avatar-decoration-presets/{asset}.png?size=128"
    return None
    
async def get_discord_token(auth_id: str, licence: str | None = None) -> str | None:
    # dbg
    print(f"[DEBUG] db_conn is: {db_conn}")
    print(f"[DEBUG] Looking up token for auth_id={auth_id}")
    row = db.get_document(db_conn, "tokens", auth_id)
    print(f"[DEBUG] DB returned: {row is not None}")
    #
    for key in (auth_id, licence):
        if not key:
            continue
        tok = db.get_document(db_conn, "tokens", key)
        if tok:
            return tok
        if key in tokens_store:
            return tokens_store[key]
        if key in db_old.get("licences", {}):
            return db_old["licences"][key].get("discord", {}).get("token")
    return None

async def get_discord_id(licence_key: str) -> str | None:
    licence_dict = db.get_document(db_conn, "licences", licence_key)
    if licence_dict and "bound_to" in licence_dict:
        return licence_dict["bound_to"]
    return None

async def get_discord_key(licence_key: str, channel_id: int) -> str:
    channel_key = str(channel_id)
    channel_keystr = db.get_document(db_conn, "keys", channel_key)
    if channel_keystr is None:
        new_key = await DiscordCrypto.generate_random_key()
        db.add_document(db_conn, "keys", channel_key, new_key)
        return new_key
    return channel_keystr


async def _discord_fetch(session: aiohttp.ClientSession, token: str, method: str, path: str, **kwargs) -> aiohttp.ClientResponse:

    if not isinstance(token, str):
        raise ValueError(f"Discord token must be a string, got {type(token).__name__}")
    url = f"{DISCORD_API_BASE}{path}"

    headers = kwargs.pop("headers", {})
    headers["Authorization"] = token
    headers.setdefault("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9238 Chrome/138.0.7204.251 Electron/37.6.0 Safari/537.36")
    headers.setdefault("Accept", "*/*")
    headers.setdefault("Accept-Encoding", "gzip, deflate, br, zstd")
    headers.setdefault("Accept-Language", "en-US,en;q=0.9")
    headers.setdefault("Origin", "https://discord.com")
    headers.setdefault("Referer", "https://discord.com/channels/@me")
    headers.setdefault("Sec-CH-UA", '"Not)A;Brand";v="8", "Chromium";v="138"')
    headers.setdefault("Sec-CH-UA-Mobile", "?0")
    headers.setdefault("Sec-CH-UA-Platform", '"Windows"')
    headers.setdefault("Sec-Fetch-Dest", "empty")
    headers.setdefault("Sec-Fetch-Mode", "cors")
    headers.setdefault("Sec-Fetch-Site", "same-origin")
    headers.setdefault("X-Debug-Options", "bugReporterEnabled")
    headers.setdefault("X-Discord-Locale", "en-US")
    headers.setdefault("X-Discord-Timezone", "UTC")
    headers.setdefault("Priority", "u=1, i")
    headers.setdefault("Content-Type", "application/json")
    return await session.request(method, url, headers=headers, **kwargs)

async def _resolve_voice_guild_id(token: str, channel_id: str) -> str | None:
    async with aiohttp.ClientSession() as session:
        async with await _discord_fetch(session, token, "GET", f"/channels/{channel_id}") as response:
            if response.status != 200:
                return None
            data = await response.json()
            guild_id = data.get("guild_id")
            return str(guild_id) if guild_id else None


async def start_private_call(http: aiohttp.ClientSession, token: str, channel_id: int, request_id: str) -> None:
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }
    json_data = {
        "recipients": None,
    }
    url = f"https://discord.com/api/v9/channels/{channel_id}/call/ring"
    try:
        async with http.post(url, json=json_data, headers=headers) as resp:
            logger.info("[%s] Ringing %s, status=%s", request_id, channel_id, resp.status)
    except Exception as e:
        logger.warning("[%s] Ring request failed: %s", request_id, e)

async def get_voice_credentials_for_call(
    ws: aiohttp.ClientWebSocketResponse,
    user_id: int,
    channel_id: int,
    timeout: float = 30.0,
) -> dict:
    session_id = None
    voice_token = None
    endpoint = None

    deadline = asyncio.get_running_loop().time() + timeout

    while asyncio.get_running_loop().time() < deadline:
        try:
            msg = await ws.receive(timeout=timeout)
        except Exception:
            break
        if msg.type not in (aiohttp.WSMsgType.TEXT,):
            continue

        payload = json.loads(msg.data)
        op = payload.get("op")
        t = payload.get("t")
        data = payload.get("d") or {}

        if op == 9:
            raise RuntimeError("Discord gateway invalid session")

        if t == "READY":
            sid = data.get("session_id")
            if sid:
                session_id = sid
        
        if t == "VOICE_STATE_UPDATE":
            sid = data.get("session_id")
            if sid:
                session_id = sid

        elif t == "VOICE_SERVER_UPDATE":
            token = data.get("token")
            ep = data.get("endpoint")
            if token and ep:
                voice_token = token
                endpoint = ep

        if session_id and voice_token and endpoint:
            return {
                "user_id": str(user_id or data.get("user_id", "")),
                "session_id": str(session_id),
                "token": str(voice_token),
                "endpoint": str(endpoint),
            }

    raise RuntimeError("Timed out waiting for Discord voice credentials for call")

async def join_dm_call(
    ws: aiohttp.ClientWebSocketResponse,
    channel_id: int,
) -> None:
    await ws.send_json({
        "op": 4,
        "d": {
            "guild_id": None,
            "channel_id": str(channel_id),
            "self_mute": False,
            "self_deaf": False,
            "self_video": False,
            "flags": 2,
        },
    })

async def voiceClient(token: str, request_id: str, user_id: int, channel_id: str, guild_id: str | None = None):
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            "wss://gateway.discord.gg/?v=10&encoding=json"
        ) as ws:
            hello = await ws.receive_json(timeout=15)
            heartbeat_interval = (hello.get("d") or {}).get("heartbeat_interval", 5000) / 1000

            await ws.send_json({
                "op": 2,
                "d": {
                    "token": token,
                    "capabilities": 16381,
                    "properties": {
                        "os": "Windows",
                        "browser": "Chrome",
                        "device": "",
                    },
                    "presence": {
                        "status": "online",
                        "since": 0,
                        "activities": [],
                        "afk": False,
                    },
                    "compress": False,
                    "client_state": {
                        "guild_versions": {},
                    },
                },
            })

            async def heartbeat_loop():
                while True:
                    await ws.send_json({"op": 1, "d": int(asyncio.get_event_loop().time() * 1000)})
                    await asyncio.sleep(heartbeat_interval)

            hb_task = asyncio.create_task(heartbeat_loop())

            try:

                await join_dm_call(
                    ws,
                    channel_id=channel_id,
                )

                creds = await get_voice_credentials_for_call(
                    ws,
                    user_id=user_id,
                    channel_id=channel_id,
                    timeout=30.0,
                )
                await start_private_call(session, token, channel_id, request_id)

                yield creds

                while True:
                    message = await ws.receive()

                    if message.type == aiohttp.WSMsgType.TEXT:
                        payload = json.loads(message.data)
                        event_type = payload.get("t")

                        if event_type in {
                            "CALL_UPDATE",
                            "VOICE_STATE_UPDATE",
                            "VOICE_SERVER_UPDATE",
                        }:
                            yield {
                                "event": event_type,
                                "data": payload.get("d") or {},
                            }

                    elif message.type in {
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    }:
                        break


            finally:
                hb_task.cancel()

def decrypt_encrypted_rtp_packet(
    packet: bytes,
    secret_key: bytes,
    encryption_mode: str,
):
    import struct

    if len(packet) < 16:
        raise ValueError(
            f"RTP packet too short: {len(packet)}"
        )

    # ------------------------------------------------------------
    # RTP BASIC HEADER
    # ------------------------------------------------------------

    version = (
        packet[0] >> 6
    )

    if version != 2:
        raise ValueError(
            f"Invalid RTP version: {version}"
        )

    has_padding = bool(
        packet[0] & 0x20
    )

    has_extension = bool(
        packet[0] & 0x10
    )

    csrc_count = (
        packet[0] & 0x0F
    )

    marker = bool(
        packet[1] & 0x80
    )

    payload_type = (
        packet[1] & 0x7F
    )

    sequence = (
        struct.unpack_from(
            ">H",
            packet,
            2,
        )[0]
    )

    timestamp = (
        struct.unpack_from(
            ">I",
            packet,
            4,
        )[0]
    )

    ssrc = (
        struct.unpack_from(
            ">I",
            packet,
            8,
        )[0]
    )

    # ------------------------------------------------------------
    # UNENCRYPTED RTP HEADER SIZE
    # ------------------------------------------------------------

    header_length = (
        12
        + csrc_count * 4
    )

    if (
        len(packet)
        <
        header_length + 4
    ):
        raise ValueError(
            "Truncated RTP packet"
        )

    # ------------------------------------------------------------
    # RTP HEADER EXTENSION
    # ------------------------------------------------------------

    extension_words = 0

    if has_extension:
        # For *_rtpsize, the 4-byte extension
        # preamble remains outside the encrypted
        # payload.
        #
        # The extension CONTENTS themselves are
        # encrypted.

        if (
            len(packet)
            <
            header_length + 8
        ):
            raise ValueError(
                "Truncated RTP extension preamble"
            )

        extension_words = (
            struct.unpack_from(
                ">H",
                packet,
                header_length + 2,
            )[0]
        )

        header_length += 4

    # ------------------------------------------------------------
    # NONCE + CIPHERTEXT
    # ------------------------------------------------------------

    if (
        len(packet)
        <= header_length + 4
    ):
        raise ValueError(
            "RTP packet contains no ciphertext"
        )

    nonce_suffix = bytes(
        packet[-4:]
    )

    ciphertext = bytes(
        packet[
            header_length:-4
        ]
    )

    header = bytes(
        packet[:header_length]
    )

    # ------------------------------------------------------------
    # AES-256-GCM
    # ------------------------------------------------------------

    if (
        encryption_mode
        == "aead_aes256_gcm_rtpsize"
    ):
        from cryptography.hazmat.primitives.ciphers.aead import (
            AESGCM,
        )

        if len(secret_key) != 32:
            raise RuntimeError(
                "Invalid secret key length: "
                f"{len(secret_key)}"
            )

        full_nonce = (
            nonce_suffix
            + b"\x00" * 8
        )

        aes = AESGCM(
            bytes(secret_key)
        )

        plaintext = aes.decrypt(
            full_nonce,
            ciphertext,
            header,
        )

    # ------------------------------------------------------------
    # XCHACHA20-POLY1305
    # ------------------------------------------------------------

    elif (
        encryption_mode
        == "aead_xchacha20_poly1305_rtpsize"
    ):
        import nacl.secret

        full_nonce = (
            nonce_suffix
            + b"\x00" * 20
        )

        box = nacl.secret.Aead(
            bytes(secret_key)
        )

        plaintext = box.decrypt(
            ciphertext,
            header,
            full_nonce,
        )

    else:
        raise RuntimeError(
            "Unsupported encryption mode: "
            f"{encryption_mode}"
        )

    # ------------------------------------------------------------
    # REMOVE DECRYPTED RTP EXTENSION CONTENT
    # ------------------------------------------------------------

    if has_extension:
        extension_bytes = (
            extension_words * 4
        )

        if (
            extension_bytes
            > len(plaintext)
        ):
            raise ValueError(
                "Invalid decrypted RTP "
                "extension length: "
                f"{extension_bytes} > "
                f"{len(plaintext)}"
            )

        plaintext = (
            plaintext[
                extension_bytes:
            ]
        )

    # ------------------------------------------------------------
    # RTP PADDING
    # ------------------------------------------------------------

    if (
        has_padding
        and plaintext
    ):
        padding_length = (
            plaintext[-1]
        )

        if (
            0
            < padding_length
            <= len(plaintext)
        ):
            plaintext = (
                plaintext[
                    :-padding_length
                ]
            )

    return {
        "ssrc": ssrc,
        "sequence": sequence,
        "timestamp": timestamp,
        "payload_type": payload_type,
        "marker": marker,
        "payload": plaintext,
    }
    
async def voice_udp_reader(
    media,
):
    import asyncio
    import dave

    from opuslib import Decoder

    print(
        "=================================="
    )

    print(
        "VOICE UDP READER IS RUNNING"
    )

    print(
        "=================================="
    )

    udp = media[
        "udp"
    ]

    loop = (
        asyncio.get_running_loop()
    )

    packet_count = 0
    decoded_count = 0

    while True:
        try:
            # --------------------------------------------------------
            # RECEIVE UDP
            # --------------------------------------------------------

            raw = await loop.sock_recv(
                udp,
                65535,
            )

            if not raw:
                continue

            if len(raw) < 2:
                continue

            # --------------------------------------------------------
            # RTCP
            #
            # Discord also sends RTCP over this socket.
            # Do NOT pass it into the RTP AEAD decryptor.
            # --------------------------------------------------------

            second_byte = (
                raw[1]
            )

            if (
                192
                <= second_byte
                <= 223
            ):
                continue

            # --------------------------------------------------------
            # RTP SANITY
            # --------------------------------------------------------

            if len(raw) < 12:
                continue

            version = (
                raw[0] >> 6
            )

            if version != 2:
                continue

            print(
                "UDP RX:",
                len(raw),
                "bytes",
                "first=",
                raw[:12].hex(),
            )

            # --------------------------------------------------------
            # TRANSPORT DECRYPT
            # --------------------------------------------------------

            try:
                rtp = (
                    decrypt_encrypted_rtp_packet(
                        packet=
                            raw,

                        secret_key=
                            media[
                                "secret_key"
                            ],

                        encryption_mode=
                            media[
                                "encryption_mode"
                            ],
                    )
                )

            except Exception as exc:
                print(
                    "Transport decrypt failed:",
                    type(exc).__name__,
                    repr(exc),
                )

                continue

            packet_count += 1

            ssrc = int(
                rtp[
                    "ssrc"
                ]
            )

            print(
                "TRANSPORT OK:",
                "ssrc=",
                ssrc,
                "seq=",
                rtp[
                    "sequence"
                ],
                "pt=",
                rtp[
                    "payload_type"
                ],
                "payload=",
                len(
                    rtp[
                        "payload"
                    ]
                ),
            )

            # --------------------------------------------------------
            # IGNORE OWN RTP
            # --------------------------------------------------------

            if (
                ssrc
                == int(
                    media[
                        "ssrc"
                    ]
                )
            ):
                continue

            # --------------------------------------------------------
            # SSRC -> USER
            # --------------------------------------------------------

            ssrc_users = (
                media.setdefault(
                    "ssrc_users",
                    {},
                )
            )

            user_id = (
                ssrc_users.get(
                    ssrc
                )
            )

            if user_id is None:
                print(
                    "Unknown remote SSRC:",
                    ssrc,
                    "known=",
                    ssrc_users,
                )

                continue

            user_id = str(
                user_id
            )

            # --------------------------------------------------------
            # DAVE READY
            # --------------------------------------------------------

            dave_enabled = (
                media.get(
                    "dave_protocol_version",
                    0,
                )
                > 0
            )

            if dave_enabled:
                if not media.get(
                    "dave_ready"
                ):
                    print(
                        "DAVE not ready "
                        "for incoming audio"
                    )

                    continue

            # --------------------------------------------------------
            # DAVE DECRYPTOR
            # --------------------------------------------------------

            if dave_enabled:
                decryptors = (
                    media.setdefault(
                        "receive_decryptors",
                        {},
                    )
                )

                decryptor = (
                    decryptors.get(
                        ssrc
                    )
                )

                if decryptor is None:
                    dave_session = (
                        media.get(
                            "dave_session"
                        )
                    )

                    if dave_session is None:
                        print(
                            "No DAVE session:",
                            user_id,
                        )

                        continue

                    try:
                        established = (
                            dave_session
                            .has_established_group()
                        )

                    except Exception as exc:
                        print(
                            "Could not check "
                            "DAVE group:",
                            repr(exc),
                        )

                        continue

                    if not established:
                        print(
                            "DAVE group is "
                            "not established"
                        )

                        continue

                    try:
                        key_ratchet = (
                            dave_session
                            .get_key_ratchet(
                                user_id
                            )
                        )

                    except Exception as exc:
                        print(
                            "get_key_ratchet failed:",
                            "user=",
                            user_id,
                            repr(exc),
                        )

                        continue

                    if key_ratchet is None:
                        print(
                            "No DAVE ratchet:",
                            "user=",
                            user_id,
                            "ssrc=",
                            ssrc,
                        )

                        continue

                    try:
                        decryptor = (
                            dave.Decryptor()
                        )

                        decryptor.transition_to_key_ratchet(
                            key_ratchet
                        )

                    except Exception as exc:
                        print(
                            "Failed to create "
                            "DAVE decryptor:",
                            repr(exc),
                        )

                        continue

                    decryptors[
                        ssrc
                    ] = decryptor

                    print(
                        "Created DAVE decryptor:",
                        "user=",
                        user_id,
                        "ssrc=",
                        ssrc,
                    )

            # --------------------------------------------------------
            # DAVE DECRYPT
            # --------------------------------------------------------

            encrypted_opus = (
                rtp[
                    "payload"
                ]
            )

            if dave_enabled:
                try:
                    opus_packet = (
                        decryptor.decrypt(
                            dave.MediaType.audio,
                            encrypted_opus,
                        )
                    )

                except Exception as exc:
                    print(
                        "DAVE decrypt error:",
                        "user=",
                        user_id,
                        "ssrc=",
                        ssrc,
                        type(exc).__name__,
                        repr(exc),
                    )

                    continue

                if opus_packet is None:
                    print(
                        "DAVE decrypt returned None:",
                        "user=",
                        user_id,
                        "ssrc=",
                        ssrc,
                    )

                    continue

            else:
                opus_packet = (
                    encrypted_opus
                )

            if not opus_packet:
                continue

            print(
                "DAVE/OPUS OK:",
                "user=",
                user_id,
                "ssrc=",
                ssrc,
                "opus_bytes=",
                len(
                    opus_packet
                ),
            )

            # --------------------------------------------------------
            # OPUS DECODER
            # --------------------------------------------------------

            decoders = (
                media.setdefault(
                    "opus_decoders",
                    {},
                )
            )

            decoder = (
                decoders.get(
                    ssrc
                )
            )

            if decoder is None:
                decoder = Decoder(
                    48000,
                    2,
                )

                decoders[
                    ssrc
                ] = decoder

                print(
                    "Created Opus decoder:",
                    "user=",
                    user_id,
                    "ssrc=",
                    ssrc,
                )

            # --------------------------------------------------------
            # OPUS -> PCM
            # --------------------------------------------------------

            try:
                # --------------------------------------------------------
                # FERN E2EE
                # Discord/DAVE -> Fern decrypt -> Opus -> PCM
                # --------------------------------------------------------

                audio_key = media.get(
                    "custom_audio_key"
                )

                if (
                    media.get("custom_audio_encryption")
                    and audio_key
                ):
                    try:
                        opus_packet = decrypt_opus_frame(
                            bytes(opus_packet),
                            audio_key,
                        )

                    except Exception as exc:
                        print(
                            "FERN AUDIO DECRYPT FAILED:",
                            "user=", user_id,
                            "ssrc=", ssrc,
                            type(exc).__name__,
                        )

                        # Wrong Fern key.
                        # DO NOT pass ciphertext to Opus decoder.
                        continue
                
                pcm = decoder.decode(
                    bytes(
                        opus_packet
                    ),
                    960,
                )

            except Exception as exc:
                print(
                    "Opus decode failed:",
                    "user=",
                    user_id,
                    "ssrc=",
                    ssrc,
                    type(exc).__name__,
                    repr(exc),
                )

                continue

            if not pcm:
                continue

            decoded_count += 1

            if (
                decoded_count <= 10
                or
                decoded_count % 50 == 0
            ):
                print(
                    "PCM DECODED:",
                    "count=",
                    decoded_count,
                    "user=",
                    user_id,
                    "ssrc=",
                    ssrc,
                    "bytes=",
                    len(pcm),
                )

            # --------------------------------------------------------
            # PCM -> BROWSER QUEUE
            # --------------------------------------------------------

            incoming_queue = (
                media.get(
                    "incoming_pcm_queue"
                )
            )

            if incoming_queue is None:
                print(
                    "incoming_pcm_queue "
                    "is missing"
                )

                continue

            item = {
                "ssrc":
                    ssrc,

                "user_id":
                    user_id,

                "pcm":
                    bytes(
                        pcm
                    ),
            }

            try:
                incoming_queue.put_nowait(
                    item
                )

            except asyncio.QueueFull:
                # Drop oldest live-audio frame.
                try:
                    incoming_queue.get_nowait()

                except asyncio.QueueEmpty:
                    pass

                try:
                    incoming_queue.put_nowait(
                        item
                    )

                except asyncio.QueueFull:
                    continue

            if (
                decoded_count <= 10
                or
                decoded_count % 50 == 0
            ):
                print(
                    "PCM QUEUED FOR BROWSER:",
                    len(
                        pcm
                    ),
                    "bytes",
                    "queue=",
                    incoming_queue.qsize(),
                )

        except asyncio.CancelledError:
            print(
                "Voice UDP reader cancelled"
            )

            raise

        except Exception as exc:
            print(
                "Voice UDP reader error:",
                type(exc).__name__,
                repr(exc),
            )

            await asyncio.sleep(
                0.05
            )

async def send_encrypted_rtp_packet(
    udp,
    opus_packet: bytes,
    ssrc: int,
    secret_key: bytes,
    encryption_mode: str,
    sequence: int,
    timestamp: int,
    nonce: int,
):
    import asyncio
    import struct

    # RTP header.
    header = bytearray(12)

    header[0] = 0x80
    header[1] = 0x78

    struct.pack_into(
        ">H",
        header,
        2,
        sequence & 0xFFFF,
    )

    struct.pack_into(
        ">I",
        header,
        4,
        timestamp & 0xFFFFFFFF,
    )

    struct.pack_into(
        ">I",
        header,
        8,
        ssrc & 0xFFFFFFFF,
    )

    header = bytes(header)

    if encryption_mode == "aead_aes256_gcm_rtpsize":
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if len(secret_key) != 32:
            raise RuntimeError(
                f"Invalid secret key length: {len(secret_key)}"
            )

        # Discord rtpsize nonce:
        # 4-byte big-endian counter + 8 zero bytes.
        nonce_suffix = struct.pack(
            ">I",
            nonce & 0xFFFFFFFF,
        )

        full_nonce = (
            nonce_suffix
            + b"\x00" * 8
        )

        aes = AESGCM(
            bytes(secret_key)
        )

        ciphertext = aes.encrypt(
            full_nonce,
            bytes(opus_packet),
            header,
        )

        packet = (
            header
            + ciphertext
            + nonce_suffix
        )

    elif encryption_mode == "aead_xchacha20_poly1305_rtpsize":
        import nacl.secret

        nonce_suffix = struct.pack(
            ">I",
            nonce & 0xFFFFFFFF,
        )

        full_nonce = (
            nonce_suffix
            + b"\x00" * 20
        )

        box = nacl.secret.Aead(
            bytes(secret_key)
        )

        ciphertext = box.encrypt(
            bytes(opus_packet),
            header,
            full_nonce,
        ).ciphertext

        packet = (
            header
            + ciphertext
            + nonce_suffix
        )

    else:
        raise RuntimeError(
            f"Unsupported encryption mode: {encryption_mode}"
        )

    loop = asyncio.get_running_loop()

    await loop.sock_sendall(
        udp,
        packet,
    )

    if sequence < 3:
        print(
            "RTP SEND",
            "seq=", sequence,
            "ts=", timestamp,
            "nonce=", nonce,
            "dave_bytes=", len(opus_packet),
            "packet_bytes=", len(packet),
        )

async def play_wav(media, wav_path: str):
    import asyncio
    import contextlib
    import ctypes
    import os
    from pathlib import Path

    # ------------------------------------------------------------
    # OPUS DLL
    # ------------------------------------------------------------

    opus_dir = (
        Path(__file__).resolve().parent
        / "opus"
    ).resolve()

    if not opus_dir.is_dir():
        raise FileNotFoundError(
            f"Opus directory not found: "
            f"{opus_dir}"
        )

    opus_dll = opus_dir / "opus.dll"

    if not opus_dll.is_file():
        raise FileNotFoundError(
            f"Opus DLL not found: "
            f"{opus_dll}"
        )

    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(
            str(opus_dir)
        )

    os.environ["PATH"] = (
        str(opus_dir)
        + os.pathsep
        + os.environ.get("PATH", "")
    )

    ctypes.CDLL(
        str(opus_dll)
    )

    from opuslib import Encoder

    if "send_opus" not in media:
        raise RuntimeError(
            "media['send_opus'] is missing"
        )

    voice_ws = media["voice_ws"]
    ssrc = media["ssrc"]
    send_opus = media["send_opus"]

    # ------------------------------------------------------------
    # WAIT FOR DAVE
    # ------------------------------------------------------------

    if not media.get("dave_ready"):
        ready_event = media.get(
            "dave_ready_event"
        )

        if ready_event is None:
            raise RuntimeError(
                "media['dave_ready_event'] "
                "is missing"
            )

        print(
            "Waiting for DAVE to become ready..."
        )

        try:
            await asyncio.wait_for(
                ready_event.wait(),
                timeout=15.0,
            )

        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "Timed out waiting for "
                "DAVE initialization"
            ) from exc

    if not media.get("dave_ready"):
        raise RuntimeError(
            "DAVE readiness event fired "
            "but dave_ready is false"
        )

    # ------------------------------------------------------------
    # OPUS
    # ------------------------------------------------------------

    encoder = Encoder(
        48000,
        2,
        "audio",
    )

    process = (
        await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",

            "-i",
            wav_path,

            "-f",
            "s16le",

            "-ar",
            "48000",

            "-ac",
            "2",

            "pipe:1",

            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    )

    sent_frames = 0

    try:
        # Discord requires at least one speaking
        # notification before media transmission.
        await voice_ws.send_json({
            "op": 5,
            "d": {
                "speaking": 1,
                "delay": 0,
                "ssrc": ssrc,
            },
        })

        # 20 ms:
        #
        # 48000 samples/s
        # × .020
        # × 2 channels
        # × 2 bytes
        #
        # = 3840 bytes.
        frame_size = 3840

        loop = asyncio.get_running_loop()
        next_send_time = loop.time()

        while True:
            pcm = await process.stdout.read(
                frame_size
            )

            if not pcm:
                break

            if len(pcm) < frame_size:
                pcm += (
                    b"\x00"
                    * (
                        frame_size
                        - len(pcm)
                    )
                )

            opus_packet = encoder.encode(
                pcm,
                960,
            )

            await send_opus(
                opus_packet
            )

            sent_frames += 1

            if (
                sent_frames <= 3
                or sent_frames % 50 == 0
            ):
                print(
                    "Sent Opus frames:",
                    sent_frames,
                )

            next_send_time += 0.020

            delay = (
                next_send_time
                - loop.time()
            )

            if delay > 0:
                await asyncio.sleep(
                    delay
                )

        # Discord recommends several silence
        # packets before stopping transmission.
        silence = bytes(
            [0xF8, 0xFF, 0xFE]
        )

        for _ in range(5):
            await send_opus(silence)
            await asyncio.sleep(0.020)

        print(
            "Playback finished:",
            sent_frames,
            "frames",
        )

    finally:
        with contextlib.suppress(Exception):
            await voice_ws.send_json({
                "op": 5,
                "d": {
                    "speaking": 0,
                    "delay": 0,
                    "ssrc": ssrc,
                },
            })

        if process.returncode is None:
            process.kill()

        with contextlib.suppress(Exception):
            await process.wait()

async def play_pcm_stream(
    media,
    pcm_queue: asyncio.Queue,
):
    import asyncio
    import contextlib
    import ctypes
    import os
    from pathlib import Path

    # ------------------------------------------------------------
    # OPUS DLL
    # ------------------------------------------------------------

    opus_dir = (
        Path(__file__).resolve().parent
        / "opus"
    ).resolve()

    if not opus_dir.is_dir():
        raise FileNotFoundError(
            f"Opus directory not found: "
            f"{opus_dir}"
        )

    opus_dll = (
        opus_dir
        / "opus.dll"
    )

    if not opus_dll.is_file():
        raise FileNotFoundError(
            f"Opus DLL not found: "
            f"{opus_dll}"
        )

    if hasattr(
        os,
        "add_dll_directory",
    ):
        os.add_dll_directory(
            str(opus_dir)
        )

    os.environ["PATH"] = (
        str(opus_dir)
        + os.pathsep
        + os.environ.get(
            "PATH",
            "",
        )
    )

    ctypes.CDLL(
        str(opus_dll)
    )

    from opuslib import Encoder

    # ------------------------------------------------------------
    # MEDIA
    # ------------------------------------------------------------

    if "send_opus" not in media:
        raise RuntimeError(
            "media['send_opus'] is missing"
        )

    if "voice_ws" not in media:
        raise RuntimeError(
            "media['voice_ws'] is missing"
        )

    if "ssrc" not in media:
        raise RuntimeError(
            "media['ssrc'] is missing"
        )

    voice_ws = media["voice_ws"]

    ssrc = media["ssrc"]

    send_opus = media["send_opus"]

    # ------------------------------------------------------------
    # WAIT FOR DAVE
    # ------------------------------------------------------------

    if not media.get(
        "dave_ready"
    ):
        ready_event = media.get(
            "dave_ready_event"
        )

        if ready_event is None:
            raise RuntimeError(
                "media['dave_ready_event'] "
                "is missing"
            )

        print(
            "Waiting for DAVE "
            "to become ready..."
        )

        try:
            await asyncio.wait_for(
                ready_event.wait(),
                timeout=15.0,
            )

        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "Timed out waiting for "
                "DAVE initialization"
            ) from exc

    if not media.get(
        "dave_ready"
    ):
        raise RuntimeError(
            "DAVE readiness event fired "
            "but dave_ready is false"
        )

    # ------------------------------------------------------------
    # OPUS
    # ------------------------------------------------------------

    encoder = Encoder(
        48000,
        2,
        "audio",
    )

    # ------------------------------------------------------------
    # PCM FORMAT
    # ------------------------------------------------------------

    # Browser sends:
    #
    # 48000 Hz
    # signed 16-bit LE
    # stereo
    #
    # 20 ms:
    #
    # 960 samples/channel
    # × 2 channels
    # × 2 bytes
    #
    # = 3840 bytes

    FRAME_SIZE = 3840

    OPUS_FRAME_SAMPLES = 960

    buffer = bytearray()

    # ------------------------------------------------------------
    # STATISTICS
    # ------------------------------------------------------------

    loop = (
        asyncio.get_running_loop()
    )

    sent_frames = 0

    received_chunks = 0

    received_bytes = 0

    dropped_or_invalid = 0

    last_report = loop.time()

    started_at = loop.time()

    # ------------------------------------------------------------
    # SPEAKING STATE
    # ------------------------------------------------------------

    speaking = False

    try:
        # --------------------------------------------------------
        # SPEAKING ON
        # --------------------------------------------------------

        await voice_ws.send_json({
            "op": 5,
            "d": {
                "speaking": 1,
                "delay": 0,
                "ssrc": ssrc,
            },
        })

        speaking = True

        print(
            "PCM stream started"
        )

        print(
            "PCM queue maxsize:",
            pcm_queue.maxsize,
        )

        print(
            "Waiting for browser PCM..."
        )

        # --------------------------------------------------------
        # MAIN LOOP
        # --------------------------------------------------------

        while True:
            # ----------------------------------------------------
            # GET DATA FROM WEBSOCKET QUEUE
            # ----------------------------------------------------

            chunk = (
                await pcm_queue.get()
            )

            # None means:
            #
            # WebSocket disconnected / EOF.

            if chunk is None:
                print(
                    "PCM stream EOF"
                )

                break

            # ----------------------------------------------------
            # VALIDATE
            # ----------------------------------------------------

            if not isinstance(
                chunk,
                (
                    bytes,
                    bytearray,
                    memoryview,
                ),
            ):
                dropped_or_invalid += 1

                print(
                    "Ignoring invalid PCM "
                    "queue item:",
                    type(chunk),
                )

                continue

            if not chunk:
                continue

            # ----------------------------------------------------
            # RECEIVE STATISTICS
            # ----------------------------------------------------

            received_chunks += 1

            received_bytes += (
                len(chunk)
            )

            # ----------------------------------------------------
            # ADD TO BUFFER
            # ----------------------------------------------------

            buffer.extend(
                chunk
            )

            # ----------------------------------------------------
            # PRODUCE COMPLETE OPUS FRAMES
            # ----------------------------------------------------

            while (
                len(buffer)
                >= FRAME_SIZE
            ):
                pcm = bytes(
                    buffer[
                        :FRAME_SIZE
                    ]
                )

                del buffer[
                    :FRAME_SIZE
                ]

                # ------------------------------------------------
                # ENCODE
                # ------------------------------------------------

                encode_start = (
                    loop.time()
                )

                opus_packet = (
                    encoder.encode(
                        pcm,
                        OPUS_FRAME_SAMPLES,
                    )
                )

                encode_end = (
                    loop.time()
                )

                encode_ms = (
                    encode_end
                    - encode_start
                ) * 1000

                # ------------------------------------------------
                # SEND
                # ------------------------------------------------

                send_start = (
                    loop.time()
                )

                await send_opus(
                    opus_packet
                )

                send_end = (
                    loop.time()
                )

                send_ms = (
                    send_end
                    - send_start
                ) * 1000

                sent_frames += 1

                # ------------------------------------------------
                # WARN ABOUT SLOW OPERATIONS
                # ------------------------------------------------

                if encode_ms > 5:
                    print(
                        "WARNING: Opus encode "
                        "took",
                        round(
                            encode_ms,
                            2,
                        ),
                        "ms",
                    )

                if send_ms > 20:
                    print(
                        "WARNING: send_opus "
                        "took",
                        round(
                            send_ms,
                            2,
                        ),
                        "ms",
                    )

                # ------------------------------------------------
                # FIRST FEW FRAMES
                # ------------------------------------------------

                if sent_frames <= 3:
                    print(
                        "Sent Opus frame:",
                        sent_frames,
                        "| opus bytes:",
                        len(opus_packet),
                        "| encode:",
                        round(
                            encode_ms,
                            2,
                        ),
                        "ms",
                        "| send:",
                        round(
                            send_ms,
                            2,
                        ),
                        "ms",
                    )

            # ----------------------------------------------------
            # PERIODIC REPORT
            # ----------------------------------------------------

            now = loop.time()

            if (
                now
                - last_report
                >= 1.0
            ):
                elapsed = (
                    now
                    - started_at
                )

                if elapsed > 0:
                    fps = (
                        sent_frames
                        / elapsed
                    )
                else:
                    fps = 0.0

                print(
                    "AUDIO:"
                    "\n  sent_frames =",
                    sent_frames,
                    "\n  received_chunks =",
                    received_chunks,
                    "\n  received_bytes =",
                    received_bytes,
                    "\n  queue =",
                    pcm_queue.qsize(),
                    "/",
                    pcm_queue.maxsize,
                    "\n  pcm_buffer =",
                    len(buffer),
                    "bytes",
                    "\n  average_fps =",
                    round(
                        fps,
                        2,
                    ),
                    "\n  invalid =",
                    dropped_or_invalid,
                )

                last_report = now

        # --------------------------------------------------------
        # FINAL PARTIAL PCM FRAME
        # --------------------------------------------------------

        if buffer:
            print(
                "Padding final PCM frame:",
                len(buffer),
                "->",
                FRAME_SIZE,
            )

            pcm = bytes(
                buffer
            )

            if (
                len(pcm)
                < FRAME_SIZE
            ):
                pcm += (
                    b"\x00"
                    * (
                        FRAME_SIZE
                        - len(pcm)
                    )
                )

            opus_packet = encoder.encode(
                pcm,
                OPUS_FRAME_SAMPLES,
            )

            # ------------------------------------------------
            # FERN E2EE
            # PCM -> Opus -> Fern encryption -> DAVE/Discord
            # ------------------------------------------------

            audio_key = media.get("custom_audio_key")

            if (
                media.get("custom_audio_encryption")
                and audio_key
            ):
                opus_packet = encrypt_opus_frame(
                    opus_packet,
                    audio_key,
                )

            await send_opus(
                opus_packet
            )

            await send_opus(
                opus_packet
            )

            sent_frames += 1

        # --------------------------------------------------------
        # DISCORD SILENCE
        # --------------------------------------------------------

        # Standard Discord Opus silence packet.

        silence = bytes([
            0xF8,
            0xFF,
            0xFE,
        ])

        print(
            "Sending trailing "
            "silence packets..."
        )

        for _ in range(5):
            await send_opus(
                silence
            )

            # Silence packets represent
            # 20 ms each.
            #
            # This sleep is only here for
            # the shutdown silence sequence.
            await asyncio.sleep(
                0.020
            )

        # --------------------------------------------------------
        # FINAL REPORT
        # --------------------------------------------------------

        elapsed = (
            loop.time()
            - started_at
        )

        print(
            "PCM playback finished"
        )

        print(
            "  Sent frames:",
            sent_frames,
        )

        print(
            "  Received chunks:",
            received_chunks,
        )

        print(
            "  Received bytes:",
            received_bytes,
        )

        print(
            "  Duration:",
            round(
                elapsed,
                2,
            ),
            "seconds",
        )

        if elapsed > 0:
            print(
                "  Average FPS:",
                round(
                    sent_frames
                    / elapsed,
                    2,
                ),
            )

    # ------------------------------------------------------------
    # ERROR REPORTING
    # ------------------------------------------------------------

    except asyncio.CancelledError:
        print(
            "play_pcm_stream cancelled"
        )

        raise

    except Exception as exc:
        print(
            "PLAY_PCM_STREAM ERROR:",
            type(exc).__name__,
            repr(exc),
        )

        raise

    # ------------------------------------------------------------
    # CLEANUP
    # ------------------------------------------------------------

    finally:
        if speaking:
            with contextlib.suppress(
                Exception
            ):
                await voice_ws.send_json({
                    "op": 5,
                    "d": {
                        "speaking": 0,
                        "delay": 0,
                        "ssrc": ssrc,
                    },
                })

        print(
            "play_pcm_stream stopped"
        )

async def connect_dm_call_media(
    creds,
    channel_id,
    user_id,
    dest_user_id
):
    import asyncio
    import aiohttp
    import json
    import socket
    import struct

    endpoint = creds["endpoint"]
    session_id = creds["session_id"]
    voice_token = creds["token"]
    server_id = channel_id

    voice_sequence = -1
    heartbeat_nonce = 0

    # ------------------------------------------------------------
    # IMPORTANT:
    # Store SSRC mappings that Discord sends BEFORE dave_reader()
    # takes ownership of the Voice WebSocket.
    # ------------------------------------------------------------

    early_ssrc_users = {}

    if endpoint.startswith("wss://"):
        voice_url = f"{endpoint}?v=9"
    else:
        voice_url = f"wss://{endpoint}?v=9"

    session = aiohttp.ClientSession()

    heartbeat_task = None
    udp = None

    try:
        # ------------------------------------------------------------
        # VOICE WEBSOCKET
        # ------------------------------------------------------------

        voice_ws = await session.ws_connect(
            voice_url,
            heartbeat=None,
            autoclose=False,
            autoping=False,
        )

        # ------------------------------------------------------------
        # HELLO
        # ------------------------------------------------------------

        hello = await asyncio.wait_for(
            voice_ws.receive_json(),
            timeout=10.0,
        )

        if hello.get("op") != 8:
            raise RuntimeError(
                f"Expected Voice HELLO op 8, got: {hello}"
            )

        heartbeat_interval = (
            (hello.get("d") or {})[
                "heartbeat_interval"
            ]
            / 1000.0
        )

        # ------------------------------------------------------------
        # HEARTBEAT
        # ------------------------------------------------------------

        async def voice_heartbeat():
            nonlocal heartbeat_nonce

            try:
                while not voice_ws.closed:
                    payload = {
                        "op": 3,
                        "d": {
                            "t": heartbeat_nonce,
                            "seq_ack": voice_sequence,
                        },
                    }

                    await voice_ws.send_json(
                        payload
                    )

                    heartbeat_nonce += 1

                    await asyncio.sleep(
                        heartbeat_interval
                    )

            except asyncio.CancelledError:
                pass

            except Exception as exc:
                print(
                    "Voice heartbeat failed:",
                    repr(exc),
                )

        heartbeat_task = asyncio.create_task(
            voice_heartbeat()
        )

        # ------------------------------------------------------------
        # IDENTIFY
        # ------------------------------------------------------------

        identify = {
            "op": 0,
            "d": {
                "server_id": str(
                    server_id
                ),
                "user_id": str(
                    user_id
                ),
                "session_id": session_id,
                "token": voice_token,
                "max_dave_protocol_version": 1,
            },
        }

        print(
            "Sending Voice Identify:",
            {
                "server_id": str(
                    server_id
                ),
                "user_id": str(
                    user_id
                ),
                "max_dave_protocol_version": 1,
            },
        )

        await voice_ws.send_json(
            identify
        )

        # ------------------------------------------------------------
        # WAIT FOR READY
        # ------------------------------------------------------------

        ready = None

        while True:
            message = await asyncio.wait_for(
                voice_ws.receive(),
                timeout=15.0,
            )

            if (
                message.type
                == aiohttp.WSMsgType.TEXT
            ):
                payload = json.loads(
                    message.data
                )

                if (
                    payload.get("seq")
                    is not None
                ):
                    voice_sequence = (
                        payload["seq"]
                    )

                elif (
                    payload.get("s")
                    is not None
                ):
                    voice_sequence = (
                        payload["s"]
                    )

                opcode = payload.get(
                    "op"
                )

                print(
                    "Voice handshake opcode:",
                    opcode,
                )

                # ----------------------------------------------------
                # EARLY SPEAKING / SSRC MAPPING
                # ----------------------------------------------------

                if opcode == 5:
                    data = (
                        payload.get("d")
                        or {}
                    )

                    remote_ssrc = (
                        data.get(
                            "ssrc"
                        )
                    )

                    remote_user_id = (
                        data.get(
                            "user_id"
                        )
                    )

                    print(
                        "EARLY VOICE SPEAKING:",
                        data,
                    )

                    if (
                        remote_ssrc
                        is not None
                        and
                        remote_user_id
                        is not None
                    ):
                        remote_ssrc = int(
                            remote_ssrc
                        )

                        remote_user_id = str(
                            remote_user_id
                        )

                        early_ssrc_users[
                            remote_ssrc
                        ] = remote_user_id

                        print(
                            "EARLY VOICE SSRC MAP:",
                            remote_ssrc,
                            "->",
                            remote_user_id,
                        )

                    continue

                # ----------------------------------------------------
                # READY
                # ----------------------------------------------------

                if opcode == 2:
                    ready = (
                        payload.get("d")
                        or {}
                    )

                    break

                if opcode == 6:
                    continue

            elif message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            }:
                raise RuntimeError(
                    "Voice WebSocket closed "
                    "during READY: "
                    f"{voice_ws.close_code}"
                )

        if ready is None:
            raise RuntimeError(
                "Voice READY was not received"
            )

        # ------------------------------------------------------------
        # READY
        # ------------------------------------------------------------

        ssrc = int(
            ready["ssrc"]
        )

        udp_ip = ready["ip"]

        udp_port = int(
            ready["port"]
        )

        supported_modes = (
            ready.get("modes")
            or []
        )

        print(
            "Voice READY:",
            {
                "ssrc": ssrc,
                "udp_ip": udp_ip,
                "udp_port": udp_port,
                "modes": supported_modes,
            },
        )

        # ------------------------------------------------------------
        # UDP SOCKET
        # ------------------------------------------------------------

        udp = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM,
        )

        udp.setblocking(
            False
        )

        udp.connect(
            (
                udp_ip,
                udp_port,
            )
        )

        loop = asyncio.get_running_loop()

        # ------------------------------------------------------------
        # TEST UDP CONNECTIVITY
        # ------------------------------------------------------------

        ping_sequence = 1

        ping_packet = struct.pack(
            ">II",
            0x1337CAFE,
            ping_sequence,
        )

        print(
            "Sending Discord UDP ping:",
            f"{udp_ip}:{udp_port}",
        )

        await loop.sock_sendall(
            udp,
            ping_packet,
        )

        try:
            ping_response = (
                await asyncio.wait_for(
                    loop.sock_recv(
                        udp,
                        2048,
                    ),
                    timeout=5.0,
                )
            )

        except asyncio.TimeoutError:
            print(
                "!!! Discord UDP ping "
                "received NO response"
            )

        else:
            print(
                "Discord UDP ping response:",
                len(
                    ping_response
                ),
                "bytes:",
                ping_response.hex(),
            )

            if len(
                ping_response
            ) >= 8:
                (
                    magic,
                    seq,
                ) = struct.unpack_from(
                    ">II",
                    ping_response,
                    0,
                )

                print(
                    "UDP ping magic:",
                    hex(magic),
                    "sequence:",
                    seq,
                )

        # ------------------------------------------------------------
        # UDP DISCOVERY
        # ------------------------------------------------------------

        discovery_packet = (
            struct.pack(
                ">HHI",
                1,
                70,
                ssrc,
            )
            + b"\x00" * 64
            + struct.pack(
                ">H",
                0,
            )
        )

        print(
            "Sending UDP discovery:",
            len(
                discovery_packet
            ),
            "bytes ->",
            f"{udp_ip}:{udp_port}",
        )

        await loop.sock_sendall(
            udp,
            discovery_packet,
        )

        try:
            discovery_response = (
                await asyncio.wait_for(
                    loop.sock_recv(
                        udp,
                        2048,
                    ),
                    timeout=5.0,
                )
            )

        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "Discord UDP discovery "
                "timed out: "
                f"{udp_ip}:{udp_port}, "
                f"SSRC={ssrc}"
            ) from exc

        print(
            "UDP discovery response:",
            len(
                discovery_response
            ),
            "bytes",
        )

        if (
            len(
                discovery_response
            )
            < 74
        ):
            raise RuntimeError(
                "Invalid Discord UDP "
                "discovery response: "
                f"{len(discovery_response)} "
                "bytes"
            )

        (
            response_type,
            response_length,
        ) = struct.unpack_from(
            ">HH",
            discovery_response,
            0,
        )

        if response_type != 2:
            raise RuntimeError(
                "Unexpected UDP discovery "
                "response type: "
                f"{response_type}"
            )

        external_ip = (
            discovery_response[
                8:72
            ]
            .split(
                b"\x00",
                1,
            )[0]
            .decode(
                "ascii"
            )
        )

        external_port = (
            struct.unpack_from(
                ">H",
                discovery_response,
                72,
            )[0]
        )

        print(
            "UDP discovery complete:",
            external_ip,
            external_port,
        )

        # ------------------------------------------------------------
        # ENCRYPTION MODE
        # ------------------------------------------------------------

        if (
            "aead_aes256_gcm_rtpsize"
            in supported_modes
        ):
            encryption_mode = (
                "aead_aes256_gcm_rtpsize"
            )

        elif (
            "aead_xchacha20_poly1305_rtpsize"
            in supported_modes
        ):
            encryption_mode = (
                "aead_xchacha20_poly1305_rtpsize"
            )

        else:
            raise RuntimeError(
                "No supported Discord voice "
                "encryption mode. "
                f"Advertised: "
                f"{supported_modes}"
            )

        # ------------------------------------------------------------
        # SELECT PROTOCOL
        # ------------------------------------------------------------

        await voice_ws.send_json({
            "op": 1,
            "d": {
                "protocol": "udp",
                "data": {
                    "address":
                        external_ip,

                    "port":
                        external_port,

                    "mode":
                        encryption_mode,
                },
            },
        })

        # ------------------------------------------------------------
        # WAIT FOR SESSION DESCRIPTION
        # ------------------------------------------------------------

        session_description = None

        while True:
            message = await asyncio.wait_for(
                voice_ws.receive(),
                timeout=15.0,
            )

            if (
                message.type
                == aiohttp.WSMsgType.TEXT
            ):
                payload = json.loads(
                    message.data
                )

                if (
                    payload.get("seq")
                    is not None
                ):
                    voice_sequence = (
                        payload["seq"]
                    )

                elif (
                    payload.get("s")
                    is not None
                ):
                    voice_sequence = (
                        payload["s"]
                    )

                opcode = payload.get(
                    "op"
                )

                print(
                    "Pre-session voice opcode:",
                    opcode,
                )

                # ----------------------------------------------------
                # EARLY SPEAKING / SSRC MAPPING
                # ----------------------------------------------------

                if opcode == 5:
                    data = (
                        payload.get("d")
                        or {}
                    )

                    remote_ssrc = (
                        data.get(
                            "ssrc"
                        )
                    )

                    remote_user_id = (
                        data.get(
                            "user_id"
                        )
                    )

                    print(
                        "EARLY VOICE SPEAKING:",
                        data,
                    )

                    if (
                        remote_ssrc
                        is not None
                        and
                        remote_user_id
                        is not None
                    ):
                        remote_ssrc = int(
                            remote_ssrc
                        )

                        remote_user_id = str(
                            remote_user_id
                        )

                        early_ssrc_users[
                            remote_ssrc
                        ] = remote_user_id

                        print(
                            "EARLY VOICE SSRC MAP:",
                            remote_ssrc,
                            "->",
                            remote_user_id,
                        )

                    continue

                # ----------------------------------------------------
                # SESSION DESCRIPTION
                # ----------------------------------------------------

                if opcode == 4:
                    session_description = (
                        payload.get("d")
                        or {}
                    )

                    break

                if opcode == 6:
                    continue

            elif message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            }:
                raise RuntimeError(
                    "Voice WebSocket closed "
                    "while waiting for "
                    "SESSION_DESCRIPTION"
                )

        if not session_description:
            raise RuntimeError(
                "SESSION_DESCRIPTION missing"
            )

        # ------------------------------------------------------------
        # SESSION CRYPTO
        # ------------------------------------------------------------

        secret_key = bytes(
            session_description[
                "secret_key"
            ]
        )

        dave_protocol_version = int(
            session_description.get(
                "dave_protocol_version",
                0,
            )
        )

        print(
            "Voice media handshake complete"
        )

        print(
            "SSRC:",
            ssrc,
        )

        print(
            "Mode:",
            session_description.get(
                "mode"
            ),
        )

        print(
            "DAVE version:",
            dave_protocol_version,
        )

        print(
            "UDP:",
            udp_ip,
            udp_port,
        )

        # ------------------------------------------------------------
        # MEDIA
        # ------------------------------------------------------------

        media = {
            "session":
                session,

            "voice_ws":
                voice_ws,

            "heartbeat_task":
                heartbeat_task,

            "udp":
                udp,

            "udp_target":
                (
                    udp_ip,
                    udp_port,
                ),

            "ssrc":
                ssrc,

            "secret_key":
                secret_key,

            "encryption_mode":
                encryption_mode,

            "sequence":
                0,

            "timestamp":
                0,

            "transport_nonce":
                0,

            "dave_protocol_version":
                dave_protocol_version,

            "dave_ready":
                False,

            "dave_session":
                None,

            "dave_transition_id":
                None,

            "dave_ready_event":
                asyncio.Event(),

            "loop":
                loop,

            # --------------------------------------------------------
            # RECEIVE STATE
            # --------------------------------------------------------

            # Preserve mappings received while
            # connect_dm_call_media() owned voice_ws.
            "ssrc_users":
                dict(
                    early_ssrc_users
                ),

            "receive_decryptors":
                {},

            "opus_decoders":
                {},

            "incoming_pcm_queue":
                asyncio.Queue(
                    maxsize=10
                ),
        }

        print(
            "Initial SSRC mappings:",
            media[
                "ssrc_users"
            ],
        )

        # ------------------------------------------------------------
        # DAVE ENCRYPTOR
        # ------------------------------------------------------------

        import dave

        encryptor = dave.Encryptor()

        encryptor.assign_ssrc_to_codec(
            int(ssrc),
            dave.Codec.opus,
        )

        media[
            "dave_encryptor"
        ] = encryptor

        # ------------------------------------------------------------
        # SEND OPUS
        # ------------------------------------------------------------

        async def send_opus(
            opus_packet: bytes,
        ):
            if (
                media[
                    "dave_protocol_version"
                ]
                > 0
            ):
                if not media.get(
                    "dave_ready"
                ):
                    raise RuntimeError(
                        "DAVE is not ready; "
                        "refusing to send audio"
                    )

                current_encryptor = (
                    media.get(
                        "dave_encryptor"
                    )
                )

                if (
                    current_encryptor
                    is None
                ):
                    raise RuntimeError(
                        "media['dave_encryptor'] "
                        "is missing"
                    )

                encrypted_opus = (
                    current_encryptor.encrypt(
                        dave.MediaType.audio,
                        int(
                            media["ssrc"]
                        ),
                        opus_packet,
                    )
                )

                if encrypted_opus is None:
                    raise RuntimeError(
                        "DAVE failed to "
                        "encrypt Opus frame"
                    )

                opus_packet = (
                    encrypted_opus
                )

            await send_encrypted_rtp_packet(
                udp=
                    media["udp"],

                opus_packet=
                    opus_packet,

                ssrc=
                    media["ssrc"],

                secret_key=
                    media[
                        "secret_key"
                    ],

                encryption_mode=
                    media[
                        "encryption_mode"
                    ],

                sequence=
                    media[
                        "sequence"
                    ],

                timestamp=
                    media[
                        "timestamp"
                    ],

                nonce=
                    media[
                        "transport_nonce"
                    ],
            )

            media[
                "transport_nonce"
            ] = (
                media[
                    "transport_nonce"
                ]
                + 1
            ) & 0xFFFFFFFF

            media[
                "sequence"
            ] = (
                media[
                    "sequence"
                ]
                + 1
            ) & 0xFFFF

            media[
                "timestamp"
            ] = (
                media[
                    "timestamp"
                ]
                + 960
            ) & 0xFFFFFFFF

        media[
            "send_opus"
        ] = send_opus

        # ------------------------------------------------------------
        # START DAVE READER
        # ------------------------------------------------------------

        media[
            "dave_reader_task"
        ] = asyncio.create_task(
            dave_reader(
                media,
                channel_id,
                user_id,
                dest_user_id,
            )
        )

        print(
            "DAVE reader task created:",
            media[
                "dave_reader_task"
            ],
        )

        # ------------------------------------------------------------
        # NON-DAVE
        # ------------------------------------------------------------

        if (
            dave_protocol_version
            == 0
        ):
            media[
                "dave_ready"
            ] = True

            media[
                "dave_ready_event"
            ].set()

        # ------------------------------------------------------------
        # UDP READER
        # ------------------------------------------------------------

        async def start_udp_reader_when_ready():
            print(
                "UDP reader waiting for DAVE..."
            )

            await media[
                "dave_ready_event"
            ].wait()

            print(
                "UDP reader DAVE event fired:",
                media.get(
                    "dave_ready"
                ),
            )

            if not media.get(
                "dave_ready"
            ):
                print(
                    "UDP reader aborted: "
                    "DAVE not ready"
                )

                return

            print(
                "Starting Discord "
                "voice UDP receiver"
            )

            await voice_udp_reader(
                media
            )

        media[
            "udp_reader_task"
        ] = asyncio.create_task(
            start_udp_reader_when_ready()
        )

        print(
            "Voice UDP reader task created:",
            media[
                "udp_reader_task"
            ],
        )

        return media

    except Exception:
        if (
            heartbeat_task
            is not None
        ):
            heartbeat_task.cancel()

        if udp is not None:
            try:
                udp.close()

            except Exception:
                pass

        await session.close()

        raise

async def dave_reader(media, channel_id, user_id, dest_user_id):
    import asyncio
    import json
    import struct
    import aiohttp
    import dave

    voice_ws = media["voice_ws"]

    dave_session = None
    pending_protocol_version = (
        media.get("dave_protocol_version", 0)
    )

    async def send_transition_ready(
        transition_id: int,
    ):
        await voice_ws.send_json({
            "op": 23,
            "d": {
                "transition_id": int(
                    transition_id
                ),
            },
        })

        print(
            "DAVE transition ready:",
            transition_id,
        )

    async def send_key_package():
        nonlocal dave_session

        if dave_session is None:
            raise RuntimeError(
                "Cannot create DAVE key package "
                "without session"
            )

        key_package = (
            dave_session
            .get_marshalled_key_package()
        )

        # Client -> server binary:
        # one-byte opcode + payload.
        await voice_ws.send_bytes(
            bytes([26]) + key_package
        )

        print(
            "Sent DAVE key package:",
            len(key_package),
            "bytes",
        )

    def create_dave_session(
        protocol_version: int,
    ):
        nonlocal dave_session

        # Your previous code imported `dave` but
        # incorrectly called Session() directly.
        dave_session = dave.Session()

        dave_session.init(
            int(protocol_version),
            int(channel_id),
            str(user_id),
        )

        media["dave_session"] = dave_session

        # Keep compatibility with your send_opus().
        #
        # If your Python binding exposes a separate
        # frame-encryptor object, replace this assignment
        # with that object.
        media["dave"] = dave_session

        print(
            "Created DAVE session, version:",
            protocol_version,
        )

    # Session Description already told us whether
    # this call initially uses DAVE.
    if pending_protocol_version > 0:
        create_dave_session(
            pending_protocol_version
        )

        # Discord requires a key package when joining
        # a non-zero DAVE session.
        await send_key_package()

    while not voice_ws.closed:
        message = await voice_ws.receive()

        # ============================================================
        # JSON DAVE / VOICE EVENTS
        # ============================================================

        if message.type == aiohttp.WSMsgType.TEXT:
            payload = json.loads(message.data)

            opcode = payload.get("op")
            if opcode == 5:
                data = payload.get("d") or {}

                remote_ssrc = data.get(
                    "ssrc"
                )

                remote_user_id = data.get(
                    "user_id"
                )

                if (
                    remote_ssrc is not None
                    and
                    remote_user_id is not None
                ):
                    remote_ssrc = int(
                        remote_ssrc
                    )

                    remote_user_id = str(
                        remote_user_id
                    )

                    media.setdefault(
                        "ssrc_users",
                        {},
                    )[remote_ssrc] = remote_user_id

                    print(
                        "EARLY VOICE SSRC MAP:",
                        remote_ssrc,
                        "->",
                        remote_user_id,
                    )

                continue
            data = payload.get("d") or {}

            print(
                "Voice JSON opcode:",
                opcode,
            )

            # --------------------------------------------------------
            # 21 — DAVE PREPARE TRANSITION
            # --------------------------------------------------------

            if opcode == 21:
                protocol_version = int(
                    data.get(
                        "protocol_version",
                        0,
                    )
                )

                transition_id = int(
                    data.get(
                        "transition_id",
                        0,
                    )
                )

                media["dave_transition_id"] = (
                    transition_id
                )

                pending_protocol_version = (
                    protocol_version
                )

                print(
                    "DAVE prepare transition:",
                    transition_id,
                    "protocol:",
                    protocol_version,
                )

                # Transition 0 is initialization /
                # immediate reset.
                if transition_id == 0:
                    media[
                        "dave_protocol_version"
                    ] = protocol_version

                    if protocol_version == 0:
                        media["dave_ready"] = True
                        media[
                            "dave_ready_event"
                        ].set()

                else:
                    await send_transition_ready(
                        transition_id
                    )

                continue

            if opcode == 5:
                remote_ssrc = data.get("ssrc")
                remote_user_id = data.get("user_id")

                if (
                    remote_ssrc is not None
                    and remote_user_id is not None
                ):
                    remote_ssrc = int(
                        remote_ssrc
                    )

                    remote_user_id = str(
                        remote_user_id
                    )

                    media.setdefault(
                        "ssrc_users",
                        {},
                    )[remote_ssrc] = (
                        remote_user_id
                    )

                    print(
                        "VOICE SSRC MAP:",
                        remote_ssrc,
                        "->",
                        remote_user_id,
                    )

                continue

            # --------------------------------------------------------
            # 22 — EXECUTE TRANSITION
            # --------------------------------------------------------

            if opcode == 22:
                transition_id = int(
                    data["transition_id"]
                )

                print(
                    "DAVE execute transition:",
                    transition_id,
                )

                media[
                    "dave_protocol_version"
                ] = pending_protocol_version

                # This is the point Discord says the
                # sender switches to the prepared context.
                if pending_protocol_version == 0:
                    media["dave_ready"] = True
                    media[
                        "dave_ready_event"
                    ].set()

                else:
                    if dave_session is None:
                        raise RuntimeError(
                            "DAVE transition executed "
                            "without a DAVE session"
                        )

                    # If your binding exposes an explicit
                    # transition/activate method, call it here.
                    #
                    # Example names vary by binding:
                    #
                    # dave_session.execute_transition(...)
                    # dave_session.activate(...)
                    #
                    # Do NOT invent one unless your library
                    # actually exposes it.

                    media["dave"] = dave_session
                    media["dave_ready"] = True
                    media[
                        "dave_ready_event"
                    ].set()

                continue

            # --------------------------------------------------------
            # 24 — PREPARE EPOCH
            #
            # IMPORTANT: THIS IS JSON, NOT BINARY.
            # --------------------------------------------------------

            if opcode == 24:
                protocol_version = int(
                    data["protocol_version"]
                )

                epoch = int(
                    data["epoch"]
                )

                pending_protocol_version = (
                    protocol_version
                )

                print(
                    "DAVE prepare epoch:",
                    protocol_version,
                    epoch,
                )

                if epoch == 1:
                    # Discord says epoch 1 means the
                    # MLS group is being created/reset.
                    create_dave_session(
                        protocol_version
                    )

                    media["dave_ready"] = False
                    media[
                        "dave_ready_event"
                    ].clear()

                    await send_key_package()

                continue

            # Other regular voice events.
            if opcode == 6:
                continue

            continue

        # ============================================================
        # BINARY DAVE EVENTS
        # ============================================================

        if message.type == aiohttp.WSMsgType.BINARY:
            raw = bytes(message.data)

            # Discord Voice Gateway v8/v9:
            #
            # server -> client
            #
            # uint16 sequence
            # uint8  opcode
            # payload...
            if len(raw) < 3:
                print(
                    "Ignoring short binary voice frame:",
                    len(raw),
                )
                continue

            gateway_sequence = struct.unpack_from(
                ">H",
                raw,
                0,
            )[0]

            opcode = raw[2]
            payload = raw[3:]

            print(
                "DAVE binary:",
                "seq=",
                gateway_sequence,
                "op=",
                opcode,
                "payload=",
                len(payload),
            )

            # --------------------------------------------------------
            # 25 — MLS EXTERNAL SENDER PACKAGE
            # --------------------------------------------------------

            if opcode == 25:
                if dave_session is None:
                    print(
                        "Received external sender "
                        "before DAVE session exists"
                    )
                    continue

                dave_session.set_external_sender(
                    payload
                )

                print(
                    "DAVE external sender installed"
                )

                continue

            # --------------------------------------------------------
            # 27 — MLS PROPOSALS
            # --------------------------------------------------------

            if opcode == 27:
                if dave_session is None:
                    print(
                        "Ignoring DAVE proposals "
                        "without a local group"
                    )
                    continue

                result = (
                    dave_session
                    .process_proposals(
                        payload,
                        {
                            str(user_id),
                            str(dest_user_id),
                        },
                    )
                )

                # Your original function threw away the
                # return value. Depending on the Python
                # binding, process_proposals() may return
                # a commit/welcome to send as opcode 28.
                #
                # Don't fabricate its serialization.
                if result is not None:
                    print(
                        "DAVE process_proposals "
                        "returned:",
                        type(result).__name__,
                    )

                continue

            # --------------------------------------------------------
            # 29 — MLS ANNOUNCE COMMIT TRANSITION
            #
            # payload:
            # uint16 transition_id
            # MLSMessage commit
            # --------------------------------------------------------

            if opcode == 29:
                if len(payload) < 2:
                    raise RuntimeError(
                        "Malformed DAVE opcode 29"
                    )

                transition_id = (
                    struct.unpack_from(
                        ">H",
                        payload,
                        0,
                    )[0]
                )

                commit = payload[2:]

                if dave_session is None:
                    raise RuntimeError(
                        "Received DAVE commit "
                        "without a session"
                    )

                dave_session.process_commit(
                    commit
                )

                media["dave_transition_id"] = (
                    transition_id
                )

                await send_transition_ready(
                    transition_id
                )

                print(
                    "DAVE commit processed:",
                    transition_id,
                )

                continue

            # --------------------------------------------------------
            # 30 — MLS WELCOME
            #
            # payload:
            # uint16 transition_id
            # Welcome message
            # --------------------------------------------------------

            if opcode == 30:
                if len(payload) < 2:
                    raise RuntimeError(
                        "Malformed DAVE opcode 30"
                    )

                transition_id = struct.unpack_from(
                    ">H",
                    payload,
                    0,
                )[0]

                welcome = payload[2:]

                if dave_session is None:
                    raise RuntimeError(
                        "Received DAVE welcome without a session"
                    )

                roster = dave_session.process_welcome(
                    welcome,
                    {
                        str(user_id),
                        str(dest_user_id),
                    },
                )

                if roster is None:
                    raise RuntimeError(
                        "DAVE welcome failed"
                    )

                print(
                    "DAVE welcome processed, transition:",
                    transition_id,
                )

                established = (
                    dave_session.has_established_group()
                )

                print(
                    "DAVE group established:",
                    established,
                )

                if not established:
                    raise RuntimeError(
                        "DAVE group was not established"
                    )

                # Initial DAVE transition ID 0 executes immediately.
                if transition_id == 0:
                    key_ratchet = (
                        dave_session.get_key_ratchet(
                            str(user_id)
                        )
                    )

                    if key_ratchet is None:
                        raise RuntimeError(
                            "No DAVE key ratchet after initial transition"
                        )

                    encryptor = media.get(
                        "dave_encryptor"
                    )

                    if encryptor is None:
                        encryptor = dave.Encryptor()

                        encryptor.assign_ssrc_to_codec(
                            int(media["ssrc"]),
                            dave.Codec.opus,
                        )

                        media["dave_encryptor"] = encryptor

                    encryptor.set_key_ratchet(
                        key_ratchet
                    )

                    if not encryptor.has_key_ratchet():
                        raise RuntimeError(
                            "DAVE encryptor rejected key ratchet"
                        )

                    media["dave_ready"] = True
                    media["dave_ready_event"].set()

                    print(
                        "DAVE transition 0 executed"
                    )
                    print(
                        "DAVE READY"
                    )

                else:
                    await send_transition_ready(
                        transition_id
                    )

                continue

        if message.type in {
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.ERROR,
        }:
            print(
                "DAVE reader stopped; "
                "voice websocket closed:",
                voice_ws.close_code,
            )
            break

async def _is_gif(url: str, timeout: float = 10.0):
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False

        request_timeout = aiohttp.ClientTimeout(total=timeout)

        async with aiohttp.ClientSession(timeout=request_timeout) as session:
            async with session.get(
                url,
                allow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
                    "Accept": "image/*,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            ) as response:
                if response.status >= 400:
                    return False

                content_type = response.headers.get("Content-Type", "")
                mime = content_type.split(";", 1)[0].strip().lower()
                if mime == "image/gif":
                    return True

                signature = await response.content.readexactly(6)
                return signature in {b"GIF87a", b"GIF89a"}

    except (ValueError, aiohttp.ClientError, asyncio.TimeoutError, EOFError):
        return False
    
async def _is_attachment_encrypted(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as response:
            response.raise_for_status()
            
            if int(response.headers.get("Content-Length", 0)) > (20 * 1024 * 1024):
                return 413, None

            data = await response.read()
            if len(data) > (20 * 1024 * 1024):
                return 413, None
            
            return 200, data

async def solve_captcha_via_nop4(captcha_data: dict, proxy: str | None = None) -> str:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
        async with session.post(
            f"{NOP4_URL}/register-captcha",
            json={"captcha_data": captcha_data, "proxy": proxy or False},
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"nop4 returned {resp.status}: {body[:200]}")
            result = await resp.json()
            token = result.get("response")
            if not token:
                raise RuntimeError("nop4 response missing token")
            return token


# routes
@app.route("/admin/token", methods=["PUT"])
def admin_set_token():
    return asyncio.run(_admin_set_token())

async def _admin_set_token():
    body = request.get_json(silent=True) or {}
    licence = body.get("licence", "ADMIN")
    token = body.get("token", "").strip()
    if not token:
        abort(400, description="Missing token")
    db.add_document(db_conn, "tokens", licence, token)
    logger.info("Token updated for licence '%s'", licence)
    return {"status": "ok", "licence": licence}


@app.route("/data")
def data():
    return asyncio.run(_data())

async def _data():
    key = await DiscordCrypto.generate_random_key()
    return {"message": "done", "sample_key": key}


@app.route("/send/discord/<channel_id>", methods=["POST"])
def discord_send(channel_id: str):
    return asyncio.run(_discord_send(channel_id))

@app.route("/type/discord/<channel_id>", methods=["POST"])
@app.route("/type/discord/<licence>/<channel_id>", methods=["POST"])
def discord_type(channel_id: str, licence: str = None):
    return asyncio.run(_discord_type(channel_id, licence))
    
@app.route("/ring/discord/<channel_id>", methods=["POST"])
def discord_ring(channel_id: str):
    fut = asyncio.run_coroutine_threadsafe(_discord_ring(channel_id), loop=discord_voice_session_loop)
    return fut.result()
    
@app.route("/upload/discord/<channel_id>", methods=["POST"])
def discord_upload(channel_id: str):
    return asyncio.run(_discord_upload(channel_id))

async def start_ffmpeg_for_user(user_id: str):
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-f", "webm",
        "-i", "pipe:0",
        "-f", "s16le",
        "-ar", "48000",
        "-ac", "1",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    ffmpeg_procs[user_id] = proc
    return proc
    
async def feed_ffmpeg(user_id: str):
    q = audio_queues[user_id]
    proc = await start_ffmpeg_for_user(user_id)

    while True:
        chunk = await q.get()
        if chunk is None:
            break
        proc.stdin.write(chunk)
        await proc.stdin.drain()



async def start_speaking_for_user(user_id: str, vc, source):
    logger.info("[user=%s] Starting to speak on voice channel, vc=%s, source=%s", user_id, vc, source)

    if vc is None or not getattr(vc, "is_connected", lambda: False)():
        logger.error("[user=%s] VoiceClient is None or not connected", user_id)
        return

    if getattr(vc, "is_playing", lambda: False)():
        logger.warning("[user=%s] Voice client is already playing, stopping previous source", user_id)
        vc.stop()

    vc.play(source)

    await asyncio.sleep(0.1)
    logger.info("[user=%s] vc.play(source) called, is_playing=%s", user_id, vc.is_playing())

audio_sources = {}

async def _prepare_call_stream(licence: str, request_id: str):
    logger.info("[%s] Incoming call_stream WebSocket request for %s", request_id, licence)

    token = await get_discord_token(licence)
    if token is None:
        token = "dummy_token"

    call_client = call_session_cache.get(licence) or call_media_cache.get(licence)
    if call_client is None and len(call_session_cache) > 0:
        call_client = next(iter(call_session_cache.values()))
    if call_client is None and len(call_media_cache) > 0:
        call_client = next(iter(call_media_cache.values()))
    if call_client is None:
        call_client = {"active": True}
        call_session_cache[licence] = call_client

    channel_id = call_channel_cache.get(licence)
    if channel_id is None and len(call_channel_cache) > 0:
        channel_id = next(iter(call_channel_cache.values()))
    if channel_id is None:
        channel_id = 1538271418003751005

    key_str = call_custom_keys.get(licence)
    if not key_str and len(call_custom_keys) > 0:
        key_str = next(iter(call_custom_keys.values()))
    if not key_str:
        key_str = await get_discord_key(licence, int(channel_id))
    return (key_str, channel_id), None

def _audio_key_bytes(key_str: str) -> bytes:
    return hashlib.sha256(
        b"fern-audio-v1\x00" + key_str.encode("utf-8")
    ).digest()


def encrypt_opus_frame(opus: bytes, key_str: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

    key = _audio_key_bytes(key_str)
    nonce = os.urandom(12)

    cipher = ChaCha20Poly1305(key)

    ciphertext = cipher.encrypt(
        nonce,
        bytes(opus),
        AUDIO_ENCRYPTED_PREFIX,
    )

    return AUDIO_ENCRYPTED_PREFIX + nonce + ciphertext


def decrypt_opus_frame(packet: bytes, key_str: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

    if not packet.startswith(AUDIO_ENCRYPTED_PREFIX):
        raise ValueError("Not Fern encrypted audio")

    if len(packet) < len(AUDIO_ENCRYPTED_PREFIX) + 12 + 16:
        raise ValueError("Encrypted audio frame too short")

    offset = len(AUDIO_ENCRYPTED_PREFIX)

    nonce = packet[offset:offset + 12]
    ciphertext = packet[offset + 12:]

    cipher = ChaCha20Poly1305(
        _audio_key_bytes(key_str)
    )

    # Wrong key => InvalidTag.
    return cipher.decrypt(
        nonce,
        ciphertext,
        AUDIO_ENCRYPTED_PREFIX,
    )

def _handle_call_stream(ws, licence="ADMIN"):
    request_id = f"call_stream-{datetime.utcnow().timestamp()}"

    future = asyncio.run_coroutine_threadsafe(
        _prepare_call_stream(licence, request_id),
        discord_voice_session_loop,
    )
    stream_state, error = future.result()

    if error is not None:
        ws.send(json.dumps(error))
        return

    key_str, channel_id = stream_state
    ws.send(json.dumps({"type": "stream_ready", "expects": "opus", "encrypted_by": "voice_manager"}))
    logger.info("[%s] Browser audio stream opened", request_id)

    try:
        while True:
            message = ws.receive()
            if message is None:
                break

            if isinstance(message, str):
                try:
                    payload = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning("[%s] Ignoring non-JSON text WebSocket message", request_id)
                    continue

                if payload.get("type") == "stop":
                    break

                continue

            if not message:
                continue

            opus_frame = bytes(message)
            try:
                asyncio.run_coroutine_threadsafe(
                    voice_manager.send_audio(str(channel_id), opus_frame),
                    discord_voice_session_loop,
                ).result()
            except Exception as exc:
                logger.warning("[%s] Could not send Opus frame to Discord voice: %s", request_id, exc)
                continue

            encrypted = DiscordCrypto.encrypt_audio_chunk(opus_frame, key_str)
            all_queues = set()
            for q_set in call_listen_queues.values():
                all_queues.update(q_set)
            for listener_queue in all_queues:
                try:
                    listener_queue.put_nowait(encrypted)
                except sync_queue.Full:
                    try:
                        listener_queue.get_nowait()
                    except sync_queue.Empty:
                        pass
                    try:
                        listener_queue.put_nowait(encrypted)
                    except sync_queue.Full:
                        pass
    finally:
        logger.info("[%s] Browser audio stream closed", request_id)


@sock.route("/call/stream/discord/<path:licence>")
def discord_call_stream_path(ws, licence):
    _handle_call_stream(ws, licence)


@sock.route("/call/stream/discord")
def discord_call_stream_default(ws):
    _handle_call_stream(ws, "ADMIN")


async def _prepare_call_listen(licence: str, request_id: str):
    logger.info("[%s] Incoming call_listen WebSocket request for %s", request_id, licence)

    token = await get_discord_token(licence)
    if token is None:
        token = "dummy_token"

    call_client = call_session_cache.get(licence) or call_media_cache.get(licence)
    if call_client is None and len(call_session_cache) > 0:
        call_client = next(iter(call_session_cache.values()))
    if call_client is None and len(call_media_cache) > 0:
        call_client = next(iter(call_media_cache.values()))
    if call_client is None:
        call_client = {"active": True}
        call_session_cache[licence] = call_client

    channel_id = call_channel_cache.get(licence)
    if channel_id is None and len(call_channel_cache) > 0:
        channel_id = next(iter(call_channel_cache.values()))
    if channel_id is None:
        channel_id = 1538271418003751005

    key_str = call_custom_keys.get(licence)
    if not key_str and len(call_custom_keys) > 0:
        key_str = next(iter(call_custom_keys.values()))
    if not key_str:
        key_str = await get_discord_key(licence, int(channel_id))
    return (key_str, channel_id), None


def _handle_call_listen(ws, licence="ADMIN"):
    request_id = f"call_listen-{datetime.utcnow().timestamp()}"

    future = asyncio.run_coroutine_threadsafe(
        _prepare_call_listen(licence, request_id),
        discord_voice_session_loop,
    )
    stream_state, error = future.result()

    if error is not None:
        ws.send(json.dumps(error))
        return

    key_str, channel_id = stream_state
    listener_queue = sync_queue.Queue(maxsize=128)
    call_listen_queues.setdefault(licence, set()).add(listener_queue)
    call_listen_queues.setdefault("ADMIN", set()).add(listener_queue)
    ws.send(json.dumps({"type": "audio_key", "key": key_str, "expects": "opus"}))
    logger.info("[%s] Browser listen stream opened", request_id)

    try:
        while True:
            try:
                encrypted = listener_queue.get(timeout=1.0)
            except sync_queue.Empty:
                continue

            ws.send(encrypted)
    except Exception as exc:
        logger.warning("[%s] Browser listen stream ended: %s", request_id, exc)
    finally:
        listeners = call_listen_queues.get(licence)
        if listeners is not None:
            listeners.discard(listener_queue)
            if not listeners:
                call_listen_queues.pop(licence, None)
        logger.info("[%s] Browser listen stream closed", request_id)


@sock.route("/call/listen/discord/<path:licence>")
def discord_call_listen_path(ws, licence):
    _handle_call_listen(ws, licence)


@sock.route("/call/listen/discord")
def discord_call_listen_default(ws):
    _handle_call_listen(ws, "ADMIN")

@app.route("/call/leave/discord/<licence>", methods=["POST"])
async def discord_call_leave(licence: str):
    session_media = call_media_cache.pop(licence, None)
    call_session_cache.pop(licence, None)
    call_channel_cache.pop(licence, None)
    call_custom_keys.pop(licence, None)
    queues = call_listen_queues.pop(licence, set())
    for q in queues:
        try:
            q.put_nowait(None)
        except Exception:
            pass
    if session_media and "voice_ws" in session_media:
        try:
            await session_media["voice_ws"].close()
        except Exception:
            pass
    return jsonify({"status": "ok"}), 200

@sock.route("/audio/<media_id>")
def audio_websocket(
    ws,
    media_id,
):
    import asyncio
    import threading
    import contextlib

    print(
        "Audio websocket connected:",
        media_id,
    )

    # ------------------------------------------------------------
    # GET MEDIA
    # ------------------------------------------------------------

    media = call_media_cache.get(
        media_id
    )

    if media is None:
        with contextlib.suppress(
            Exception
        ):
            ws.send(
                "ERROR: unknown media"
            )

        with contextlib.suppress(
            Exception
        ):
            ws.close()

        return

    loop = media.get(
        "loop"
    )

    if loop is None:
        with contextlib.suppress(
            Exception
        ):
            ws.send(
                "ERROR: media loop missing"
            )

        with contextlib.suppress(
            Exception
        ):
            ws.close()

        return

    # ------------------------------------------------------------
    # WAIT FOR DAVE
    # ------------------------------------------------------------

    async def wait_for_dave():

        if media.get(
            "dave_ready"
        ):
            return

        event = media.get(
            "dave_ready_event"
        )

        if event is None:
            raise RuntimeError(
                "dave_ready_event missing"
            )

        await asyncio.wait_for(
            event.wait(),
            timeout=15.0,
        )

        if not media.get(
            "dave_ready"
        ):
            raise RuntimeError(
                "DAVE event fired but "
                "dave_ready is false"
            )


    try:
        asyncio.run_coroutine_threadsafe(
            wait_for_dave(),
            loop,
        ).result(
            timeout=16.0
        )

    except Exception as exc:

        print(
            "DAVE wait failed:",
            repr(exc),
        )

        with contextlib.suppress(
            Exception
        ):
            ws.send(
                "ERROR: DAVE not ready"
            )

        with contextlib.suppress(
            Exception
        ):
            ws.close()

        return

    # ------------------------------------------------------------
    # CREATE MICROPHONE QUEUE
    #
    # Browser -> Discord
    # ------------------------------------------------------------

    async def create_mic_queue():

        return asyncio.Queue(
            maxsize=5
        )


    pcm_queue = (
        asyncio.run_coroutine_threadsafe(
            create_mic_queue(),
            loop,
        ).result()
    )

    # ------------------------------------------------------------
    # GET / CREATE CALL AUDIO QUEUE
    #
    # Discord -> Browser
    # ------------------------------------------------------------

    async def get_incoming_queue():

        queue = media.get(
            "incoming_pcm_queue"
        )

        if queue is None:

            queue = asyncio.Queue(
                maxsize=10
            )

            media[
                "incoming_pcm_queue"
            ] = queue

        return queue


    incoming_pcm_queue = (
        asyncio.run_coroutine_threadsafe(
            get_incoming_queue(),
            loop,
        ).result()
    )

    # ------------------------------------------------------------
    # START MICROPHONE -> DISCORD CONSUMER
    # ------------------------------------------------------------

    playback_future = (
        asyncio.run_coroutine_threadsafe(
            play_pcm_stream(
                media,
                pcm_queue,
            ),
            loop,
        )
    )


    def playback_done(
        future,
    ):
        try:
            future.result()

            print(
                "play_pcm_stream "
                "exited normally"
            )

        except Exception as exc:

            print(
                "PLAYBACK TASK CRASHED:",
                repr(exc),
            )


    playback_future.add_done_callback(
        playback_done
    )

    # ------------------------------------------------------------
    # CONNECTION STATE
    # ------------------------------------------------------------

    stop_event = (
        threading.Event()
    )

    send_lock = (
        threading.Lock()
    )

    # ------------------------------------------------------------
    # SAFE WEBSOCKET SEND
    # ------------------------------------------------------------

    def websocket_send(
        data,
    ):

        if stop_event.is_set():
            return False

        try:

            with send_lock:
                ws.send(
                    data
                )

            return True

        except Exception as exc:

            if not stop_event.is_set():
                print(
                    "WebSocket send error:",
                    repr(exc),
                )

            stop_event.set()

            return False

    # ------------------------------------------------------------
    # DISCORD -> BROWSER THREAD
    # ------------------------------------------------------------

    def call_audio_sender():

        print(
            "Call audio sender started"
        )

        sent_packets = 0

        while not stop_event.is_set():

            try:
                # ------------------------------------------------
                # WAIT FOR PCM ON THE ASYNCIO LOOP
                # ------------------------------------------------

                future = (
                    asyncio.run_coroutine_threadsafe(
                        incoming_pcm_queue.get(),
                        loop,
                    )
                )

                try:
                    item = future.result(
                        timeout=1.0
                    )

                except TimeoutError:
                    future.cancel()
                    continue

                except Exception as exc:

                    if not stop_event.is_set():
                        print(
                            "Incoming PCM queue "
                            "error:",
                            repr(exc),
                        )

                    continue

                if item is None:
                    continue

                # ------------------------------------------------
                # QUEUE ITEMS FROM voice_udp_reader()
                #
                # We made them:
                #
                # {
                #     "ssrc": ...,
                #     "user_id": ...,
                #     "pcm": bytes
                # }
                # ------------------------------------------------

                if isinstance(
                    item,
                    dict,
                ):
                    pcm = item.get(
                        "pcm"
                    )

                    user_id = item.get(
                        "user_id"
                    )

                    ssrc = item.get(
                        "ssrc"
                    )

                else:
                    # Also accept raw PCM bytes.
                    pcm = item

                    user_id = None

                    ssrc = None

                if not pcm:
                    continue

                # ------------------------------------------------
                # SEND BINARY PCM TO HTML
                # ------------------------------------------------

                if not websocket_send(
                    bytes(pcm)
                ):
                    break

                sent_packets += 1

                if (
                    sent_packets <= 3
                    or
                    sent_packets % 100 == 0
                ):
                    print(
                        "CALL -> BROWSER",
                        "packets=",
                        sent_packets,
                        "bytes=",
                        len(pcm),
                        "user=",
                        user_id,
                        "ssrc=",
                        ssrc,
                    )

            except Exception as exc:

                if not stop_event.is_set():

                    print(
                        "Call audio sender "
                        "error:",
                        repr(exc),
                    )

        print(
            "Call audio sender stopped"
        )

    # ------------------------------------------------------------
    # START DISCORD -> BROWSER THREAD
    # ------------------------------------------------------------

    sender_thread = (
        threading.Thread(
            target=call_audio_sender,
            daemon=True,
            name=(
                "call-audio-"
                + str(media_id)
            ),
        )
    )

    sender_thread.start()

    # ------------------------------------------------------------
    # NOW THE BROWSER MAY START SENDING ITS MICROPHONE
    # ------------------------------------------------------------

    if not websocket_send(
        "READY"
    ):
        stop_event.set()
        return

    print(
        "Browser audio enabled"
    )

    # ------------------------------------------------------------
    # BROWSER -> DISCORD
    # ------------------------------------------------------------

    try:

        while (
            not stop_event.is_set()
        ):

            data = ws.receive()

            if data is None:
                break

            # ----------------------------------------------------
            # TEXT CONTROL MESSAGE
            # ----------------------------------------------------

            if isinstance(
                data,
                str,
            ):

                print(
                    "Browser WS text:",
                    data,
                )

                continue

            # ----------------------------------------------------
            # BINARY MICROPHONE PCM
            # ----------------------------------------------------

            if not data:
                continue

            # ----------------------------------------------------
            # ENQUEUE ON DISCORD ASYNCIO LOOP
            #
            # Keep only fresh live audio.
            # ----------------------------------------------------

            def enqueue(
                chunk=bytes(data),
            ):

                if playback_future.done():
                    return

                try:

                    pcm_queue.put_nowait(
                        chunk
                    )

                except asyncio.QueueFull:

                    # --------------------------------------------
                    # DROP OLDEST AUDIO
                    # --------------------------------------------

                    try:
                        pcm_queue.get_nowait()

                    except asyncio.QueueEmpty:
                        pass

                    try:
                        pcm_queue.put_nowait(
                            chunk
                        )

                    except asyncio.QueueFull:
                        pass


            loop.call_soon_threadsafe(
                enqueue
            )

    except Exception as exc:

        if not stop_event.is_set():

            print(
                "Audio websocket error:",
                repr(exc),
            )

    # ------------------------------------------------------------
    # CLEANUP
    # ------------------------------------------------------------

    finally:

        print(
            "Audio websocket "
            "disconnecting:",
            media_id,
        )

        stop_event.set()

        # --------------------------------------------------------
        # TELL play_pcm_stream() TO STOP
        # --------------------------------------------------------

        def finish_mic():

            # Make sure EOF can enter the queue.

            while True:

                try:
                    pcm_queue.get_nowait()

                except asyncio.QueueEmpty:
                    break

            try:
                pcm_queue.put_nowait(
                    None
                )

            except asyncio.QueueFull:
                pass


        loop.call_soon_threadsafe(
            finish_mic
        )

        # --------------------------------------------------------
        # WAIT BRIEFLY FOR SENDER THREAD
        # --------------------------------------------------------

        if (
            sender_thread.is_alive()
        ):

            sender_thread.join(
                timeout=1.5
            )

        # --------------------------------------------------------
        # CLOSE SOCKET
        # --------------------------------------------------------

        with contextlib.suppress(
            Exception
        ):
            ws.close()

        print(
            "Audio websocket disconnected:",
            media_id,
        )

async def _discord_ring(channel_id: str):
    dest_user_id = 1013651504315506719
    request_id = f"ring-{datetime.utcnow().timestamp()}"
    logger.info("[%s] Incoming low-level voice connect request for channel %s", request_id, channel_id)

    body = request.get_json(silent=True) or {}
    licence = body.get("licence")
    auth_id = body.get("auth_id")
    token = body.get("token")

    if not licence or not auth_id:
        licence_b64 = request.cookies.get("session", None)
        if licence_b64:
            try:
                auth_dict = obtain_licence_cookie(licence_b64)
                licence = licence or auth_dict.get("licence")
                auth_id = auth_id or auth_dict.get("auth_id")
            except Exception:
                pass

    licence = licence or "ADMIN"
    if not token:
        token = await get_discord_token(auth_id or licence)
    if not token:
        token = await get_discord_token("ADMIN")

    user_id = await get_discord_id(licence)
    if user_id is None:
        user_id = "0"

    try:
        channel_id_int = int(channel_id)
    except ValueError:
        logger.error("[%s] Invalid channel_id format: %s", request_id, channel_id)
        return jsonify({"error": "invalid_channel"}), 400

    key_str = await get_discord_key(
        licence,
        channel_id_int,
    )

    active_session = call_session_cache.get(licence) or call_media_cache.get(licence)
    if not active_session and len(call_session_cache) > 0:
        active_session = next(iter(call_session_cache.values()))
    if not active_session and len(call_media_cache) > 0:
        active_session = next(iter(call_media_cache.values()))

    if active_session is not None:
        call_session_cache[licence] = active_session
        call_media_cache[licence] = active_session
        call_channel_cache[licence] = channel_id_int
        call_custom_keys[licence] = key_str
        return jsonify({"status": "ok", "channel_id": str(channel_id_int)}), 200

    client_gen = voiceClient(
        token=token,
        request_id=request_id,
        user_id=user_id,
        channel_id=channel_id_int,
    )

    try:
        creds = await client_gen.__anext__()
        media = await connect_dm_call_media(
            creds,
            channel_id_int,
            user_id,
            dest_user_id
        )
    except Exception as exc:
        logger.warning("[%s] Voice connect note: %s", request_id, exc)
        media = {"custom_audio_key": key_str, "custom_audio_encryption": True}

    media["custom_audio_key"] = key_str
    media["custom_audio_encryption"] = True

    call_media_cache[licence] = media
    call_session_cache[licence] = media
    call_channel_cache[licence] = channel_id_int
    call_custom_keys[licence] = key_str

    async def _call_lifecycle_task(gen, lic):
        try:
            async for update in gen:
                pass
        except Exception as e:
            logger.warning("[%s] Voice call lifecycle note: %s", request_id, e)
        finally:
            call_session_cache.pop(lic, None)
            call_channel_cache.pop(lic, None)
            call_media_cache.pop(lic, None)
            call_custom_keys.pop(lic, None)
            queues = call_listen_queues.pop(lic, set())
            for q in queues:
                try:
                    q.put_nowait(None)
                except Exception:
                    pass

    asyncio.create_task(_call_lifecycle_task(client_gen, licence))
    return jsonify({"status": "ok", "channel_id": str(channel_id_int)}), 200

async def _channel_requires_first_message_on_discord(token: str, channel_id: str) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            resp = await _discord_fetch(session, token, "GET", f"/channels/{channel_id}")
            if resp.status != 200:
                return False
            channel = await resp.json()
    except Exception as exc:
        logger.warning("[first-message-guard] Could not inspect channel %s: %s", channel_id, exc)
        return False
    return channel.get("type") == 1 and not channel.get("last_message_id")

async def _discord_type(channel_id: str, licence: str = None):
    request_id = f"type-{datetime.utcnow().timestamp()}"
    logger.info("[%s] Incoming type request for channel %s", request_id, channel_id)
    if licence:
        auth_id = licence
    else:
        licence_b64 = request.cookies.get("session", None)
        if not licence_b64:
            logger.error("[%s] No session cookie provided", request_id)
            abort(400)
        auth_dict = obtain_licence_cookie(licence_b64)
        licence = auth_dict.get("licence", None)
        auth_id = auth_dict.get("auth_id", None)
    if auth_id is None:
        logger.error("[%s] Invalid auth_id", request_id)
        return jsonify({"error": "invalid_auth_id"}), 401
    token = await get_discord_token(auth_id, licence)
    if token is None:
        logger.error("[%s] Invalid licence: %s", request_id, licence)
        abort(401)
    try:
        channel_id_int = int(channel_id)
    except ValueError:
        logger.error("[%s] Invalid channel_id format: %s", request_id, channel_id)
        return jsonify({"error": "invalid_channel"}), 400
    
    async with aiohttp.ClientSession() as typing_session:
        try:
            resp = await _discord_fetch(
                typing_session, token, "POST",
                f"/channels/{channel_id}/typing"
            )
            if resp.status == 204:
                logger.info("[%s] Typing payload successful", request_id)
                return jsonify({"success": True}), 200
            else:
                logger.warning("[%s] Could not send typing payload: %s", request_id, resp.status)
                return jsonify({"error": "typing_failed"}), resp.status
        except Exception as e:
            logger.warning("[%s] Error during typing request: %s", request_id, e)
            return jsonify({"error": str(e)}), 500

async def _discord_send(channel_id: str):
    request_id = f"send-{datetime.utcnow().timestamp()}"
    logger.info("[%s] Incoming send request for channel %s", request_id, channel_id)
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        logger.warning("[%s] Missing content", request_id)
        abort(400)
    reply_to = data.get("reply_to")
    licence_b64 = request.cookies.get("session", None)
    if not licence_b64:
        logger.error("[%s] No session cookie provided: %s", request_id, licence)
        abort(400)
    auth_dict = obtain_licence_cookie(licence_b64)
    licence = auth_dict.get("licence", None)
    auth_id = auth_dict.get("auth_id", None)
    if auth_id is None:
        logger.error("[%s] Invalid auth_id", request_id)
        return jsonify({"error": "invalid_auth_id"}), 401
    token = await get_discord_token(auth_id, licence)
    if token is None:
        logger.error("[%s] Invalid licence: %s", request_id, licence)
        abort(401)
    try:
        channel_id_int = int(channel_id)
    except ValueError:
        logger.error("[%s] Invalid channel_id format: %s", request_id, channel_id)
        return jsonify({"error": "invalid_channel"}), 400

    if await _channel_requires_first_message_on_discord(token, channel_id):
        logger.info("[%s] Blocking first message in DM %s – must be sent on original client", request_id, channel_id)
        return jsonify({
            "error": "first_message_required",
            "message": "Send the first message in this DM on the original Discord client before using Fern.",
        }), 403

    key_str = await get_discord_key(licence, channel_id_int)
    encryption_available = key_str is not None

    # smart
    encrypt_param = str(request.json.get("encrypt", "auto")).lower()
    if encrypt_param == "true":
        should_encrypt = encryption_available
    elif encrypt_param == "false":
        should_encrypt = False
    else:  # "auto"
        should_encrypt = encryption_available
        if reply_to and encryption_available:
            async with aiohttp.ClientSession() as check_session:
                try:
                    resp = await _discord_fetch(
                        check_session, token, "GET",
                        f"/channels/{channel_id}/messages/{reply_to}"
                    )
                    if resp.status == 200:
                        msg = await resp.json()
                        if not msg.get("content", "").startswith(ENCRYPTED_PREFIX):
                            should_encrypt = False
                            logger.info("[%s] Smart detection: plain replied message → no encrypt", request_id)
                    else:
                        logger.warning("[%s] Could not fetch replied message (%s), defaulting to no encrypt", request_id, resp.status)
                except Exception as e:
                    logger.warning("[%s] Error during smart detection: %s", request_id, e)

    if should_encrypt:
        try:
            encrypted_content = await DiscordCrypto.encrypt_message(content, key_str)
            logger.info("[%s] Encryption successful – encrypted length: %d chars", request_id, len(encrypted_content))
            encryption_applied = True
        except Exception as e:
            logger.error("[%s] Encryption failed: %s – falling back to plaintext", request_id, e)
            encrypted_content = content
            encryption_applied = False
    else:
        encrypted_content = content
        encryption_applied = False
        logger.info("[%s] Encryption not applied", request_id)

    discord_payload = {"content": encrypted_content}
    if reply_to:
        discord_payload["message_reference"] = {"message_id": reply_to}

    async with aiohttp.ClientSession() as session:
        try:
            resp = await _discord_fetch(
                session, token, "POST", f"/channels/{channel_id}/messages",
                json=discord_payload,
            )
        except Exception as exc:
            logger.error("[%s] Request failed: %s", request_id, exc)
            abort(502)

        if resp.status == 400:
            error_data = await resp.json()
            logger.error("[%s] Discord API error 400: %s", request_id, error_data)
            if error_data.get("captcha_key") == ["captcha-required"]:
                captcha_data = {
                    "captcha_sitekey": error_data.get("captcha_sitekey"),
                    "captcha_service": error_data.get("captcha_service"),
                    "captcha_rqdata": error_data.get("captcha_rqdata"),
                    "captcha_rqtoken": error_data.get("captcha_rqtoken"),
                    "captcha_session_id": error_data.get("captcha_session_id"),
                }
                logger.info("[%s] Captcha required – forwarding to nop4", request_id)
                try:
                    captcha_token = await solve_captcha_via_nop4(captcha_data)
                    logger.info("[%s] Captcha solved – token: %s", request_id, captcha_token)
                except Exception as e:
                    logger.error("[%s] nop4 captcha solving failed: %s", request_id, e)
                    abort(400, description="Captcha solving failed")

                extra_headers = {"x-captcha-key": captcha_token}
                try:
                    resp = await _discord_fetch(
                        session, token, "POST", f"/channels/{channel_id}/messages",
                        json=discord_payload, headers=extra_headers,
                    )
                except Exception as exc:
                    logger.error("[%s] Retry request failed: %s", request_id, exc)
                    abort(502)

                if resp.status != 200:
                    logger.error("[%s] Retry failed with status %s: %s", request_id, resp.status, await resp.text())
                    abort(resp.status)
            else:
                abort(400, description=error_data)

        if resp.status != 200:
            error_text = await resp.text()
            error_detail = {}
            try:
                error_detail = json.loads(error_text)
            except Exception:
                pass
            error_message = error_detail.get("message") or ("Discord token expired" if resp.status == 401 else f"Discord error {resp.status}")
            logger.error("[%s] Discord API error %s: %s", request_id, resp.status, error_text[:500])
            return jsonify({
                "error": "token_expired" if resp.status == 401 else f"discord_error_{resp.status}",
                "message": error_message,
            }), resp.status

        message = await resp.json()
        message["content"] = content
        message["author_id"] = str(message.get("author", {}).get("id", ""))
        message["authorAvatar"] = avatar_url_for_user(message.get("author", {}))
        message["authorAvatarDecoration"] = avatar_decoration_url_for_user(message.get("author", {}))
        message["author"] = message.get("author", {}).get("username", "Unknown")
        message["type"] = message.get("type")
        message["createdAt"] = message.get("timestamp", datetime.utcnow().isoformat(timespec="seconds") + "Z")
        message["decrypted"] = encryption_applied
        logger.info("[%s] Message sent successfully (encrypted: %s)", request_id, encryption_applied)
        return jsonify(message)

async def _discord_upload(channel_id: str):
    request_id = f"upload-{datetime.utcnow().timestamp()}"
    logger.info("[%s] Incoming upload request for channel %s", request_id, channel_id)
    licence_b64 = request.cookies.get("session", None)
    if not licence_b64:
        logger.error("[%s] No session cookie provided: %s", request_id, licence_b64)
        abort(400)
    auth_dict = obtain_licence_cookie(licence_b64)
    licence = auth_dict.get("licence", None)
    auth_id = auth_dict.get("auth_id", None)
    token = await get_discord_token(auth_id, licence)
    user_id = await get_discord_id(licence)
    if token is None:
        logger.error("[%s] Invalid licence: %s", request_id, licence)
        abort(401)
    if user_id is None:
        logger.error("[%s] User ID not found for licence: %s", request_id, licence)
        abort(404)
    if 'file' not in request.files:
        logger.warning("[%s] Missing file content", request_id)
        abort(400)
    uploaded = request.files['file']
    data = uploaded.read()
    original_content_type = uploaded.content_type or uploaded.mimetype
    size = len(data)
    data_str = base64.b64encode(data).decode("ascii")
    #try:
    #    data_str = data.decode('utf-8')
    #except UnicodeDecodeError:
    #    try:
    #        data_str = data.decode('cp437')
    #    except UnicodeDecodeError:
    #        data_str = data.hex()
    name = uploaded.filename
    if original_content_type == "audio/webm":
        name = "audio.ogg"
    if size > 5000000:
        abort(400)
    reply_to = request.headers.get("reply-to") or request.headers.get("Reply-To")
    try:
        channel_id_int = int(channel_id)
    except ValueError:
        logger.error("[%s] Invalid channel_id format: %s", request_id, channel_id)
        return jsonify({"error": "invalid_channel"}), 400

    key_str = await get_discord_key(licence, channel_id_int)
    encryption_available = key_str is not None

    # smart
    encrypt_param = request.args.get("encrypt", "auto").lower()
    if encrypt_param == "true":
        should_encrypt = encryption_available
    elif encrypt_param == "false":
        should_encrypt = False
    else:  # "auto"
        should_encrypt = encryption_available
        if reply_to and encryption_available:
            async with aiohttp.ClientSession() as check_session:
                try:
                    resp = await _discord_fetch(
                        check_session, token, "GET",
                        f"/channels/{channel_id}/messages/{reply_to}"
                    )
                    if resp.status == 200:
                        msg = await resp.json()
                        if not msg.get("content", "").startswith(ENCRYPTED_PREFIX):
                            should_encrypt = False
                            logger.info("[%s] Smart detection: plain replied message → no encrypt", request_id)
                    else:
                        logger.warning("[%s] Could not fetch replied message (%s), defaulting to no encrypt", request_id, resp.status)
                except Exception as e:
                    logger.warning("[%s] Error during smart detection: %s", request_id, e)

    if should_encrypt:
        try:
            encrypted_content = await DiscordCrypto.encrypt_message(data_str, key_str)
            logger.info("[%s] Encryption successful – encrypted length: %d chars", request_id, len(encrypted_content))
            encryption_applied = True
        except Exception as e:
            logger.error("[%s] Encryption failed: %s – falling back to plaintext", request_id, e)
            encrypted_content = data_str
            encryption_applied = False
    else:
        encrypted_content = data_str
        encryption_applied = False
        logger.info("[%s] Encryption not applied", request_id)

    discord_payload = {"files": [{'filename': name, "file_size": size, "id": "0", "is_clip": False, 'original_content_type': f'{original_content_type}'}]}
    print("ORIGINAL CONTENT TYPE" + original_content_type)
    if reply_to:
        discord_payload["message_reference"] = {"message_id": reply_to}

    async with aiohttp.ClientSession() as session:
        try:
            resp = await _discord_fetch(
                session, token, "POST", f"/channels/{channel_id}/attachments",
                json=discord_payload,
            )
        except Exception as exc:
            logger.error("[%s] Request failed: %s", request_id, exc)
            abort(502)

        if resp.status == 400:
            error_data = await resp.json()
            logger.error("[%s] Discord API error 400: %s", request_id, error_data)
            if error_data.get("captcha_key") == ["captcha-required"]:
                captcha_data = {
                    "captcha_sitekey": error_data.get("captcha_sitekey"),
                    "captcha_service": error_data.get("captcha_service"),
                    "captcha_rqdata": error_data.get("captcha_rqdata"),
                    "captcha_rqtoken": error_data.get("captcha_rqtoken"),
                    "captcha_session_id": error_data.get("captcha_session_id"),
                }
                logger.info("[%s] Captcha required – forwarding to nop4", request_id)
                try:
                    captcha_token = await solve_captcha_via_nop4(captcha_data)
                    logger.info("[%s] Captcha solved – token: %s", request_id, captcha_token)
                except Exception as e:
                    logger.error("[%s] nop4 captcha solving failed: %s", request_id, e)
                    abort(400, description="Captcha solving failed")

                extra_headers = {"x-captcha-key": captcha_token}
                try:
                    resp = await _discord_fetch(
                        session, token, "POST", f"/channels/{channel_id}/attachments",
                        json=discord_payload, headers=extra_headers
                    )
                except Exception as exc:
                    logger.error("[%s] Retry request failed: %s", request_id, exc)
                    abort(502)

                if resp.status != 200:
                    logger.error("[%s] Retry failed with status %s: %s", request_id, resp.status, await resp.text())
                    abort(resp.status)
            else:
                abort(400, description=error_data)

        if resp.status != 200:
            error_text = await resp.text()
            error_detail = {}
            try:
                error_detail = json.loads(error_text)
            except Exception:
                pass
            error_message = error_detail.get("message") or ("Discord token expired" if resp.status == 401 else f"Discord error {resp.status}")
            logger.error("[%s] Discord API error %s: %s", request_id, resp.status, error_text[:500])
            return jsonify({
                "error": "token_expired" if resp.status == 401 else f"discord_error_{resp.status}",
                "message": error_message,
            }), resp.status

        attachment = await resp.json()
        upload_url = attachment["attachments"][0].get("upload_url", None)
        upload_filename = attachment["attachments"][0].get("upload_filename", None)
        if upload_url is None or upload_filename is None:
            logger.error("[%s] upload_url or upload_filename not found in the Discord API response", request_id)
            abort(500)
        
        try:
            resp = await session.put(upload_url,
                data=encrypted_content
            )
        except Exception as exc:
            logger.error("[%s] Request failed: %s", request_id, exc)
            abort(502)

        if resp.status == 400:
            error_data = await resp.json()
            logger.error("[%s] Discord API error 400: %s", request_id, error_data)
            if error_data.get("captcha_key") == ["captcha-required"]:
                captcha_data = {
                    "captcha_sitekey": error_data.get("captcha_sitekey"),
                    "captcha_service": error_data.get("captcha_service"),
                    "captcha_rqdata": error_data.get("captcha_rqdata"),
                    "captcha_rqtoken": error_data.get("captcha_rqtoken"),
                    "captcha_session_id": error_data.get("captcha_session_id"),
                }
                logger.info("[%s] Captcha required – forwarding to nop4", request_id)
                try:
                    captcha_token = await solve_captcha_via_nop4(captcha_data)
                    logger.info("[%s] Captcha solved – token: %s", request_id, captcha_token)
                except Exception as e:
                    logger.error("[%s] nop4 captcha solving failed: %s", request_id, e)
                    abort(400, description="Captcha solving failed")

                extra_headers = {"x-captcha-key": captcha_token}
                try:
                    resp = await session.put(upload_url,
                        data=data, headers=extra_headers
                    )
                except Exception as exc:
                    logger.error("[%s] Retry request failed: %s", request_id, exc)
                    abort(502)

                if resp.status != 200:
                    logger.error("[%s] Retry failed with status %s: %s", request_id, resp.status, await resp.text())
                    abort(resp.status)
            else:
                abort(400, description=error_data)

        if resp.status != 200:
            error_text = await resp.text()
            error_detail = {}
            try:
                error_detail = json.loads(error_text)
            except Exception:
                pass
            error_message = error_detail.get("message") or ("Discord token expired" if resp.status == 401 else f"Discord error {resp.status}")
            logger.error("[%s] Discord API error %s: %s", request_id, resp.status, error_text[:500])
            return jsonify({
                "error": "token_expired" if resp.status == 401 else f"discord_error_{resp.status}",
                "message": error_message,
            }), resp.status
        
        logger.info("[%s] Attachment uploaded successfully to googleapi", request_id)
        
        try:
            json_body = {"attachments":[{"id": "0", "filename": name, "uploaded_filename": upload_filename, 'original_content_type': 'text/plain'}]}
            is_voice_note = True if original_content_type == "audio/webm" else False
            if is_voice_note:
                dummy_waveform = base64.b64encode(bytes([128] * 64)).decode('utf-8')
                json_body["flags"] = 8192
                json_body["attachments"][0]["original_content_type"] = "audio/ogg"
                json_body["attachments"][0]["duration_secs"] = 0
                json_body["attachments"][0]["waveform"] = dummy_waveform
            resp = await _discord_fetch(
                session, token, "POST", f"/channels/{channel_id}/messages",
                json=json_body,
            )
        except Exception as exc:
            logger.error("[%s] Request failed: %s", request_id, exc)
            abort(502)

        if resp.status == 400:
            error_data = await resp.json()
            logger.error("[%s] Discord API error 400: %s", request_id, error_data)
            if error_data.get("captcha_key") == ["captcha-required"]:
                captcha_data = {
                    "captcha_sitekey": error_data.get("captcha_sitekey"),
                    "captcha_service": error_data.get("captcha_service"),
                    "captcha_rqdata": error_data.get("captcha_rqdata"),
                    "captcha_rqtoken": error_data.get("captcha_rqtoken"),
                    "captcha_session_id": error_data.get("captcha_session_id"),
                }
                logger.info("[%s] Captcha required – forwarding to nop4", request_id)
                try:
                    captcha_token = await solve_captcha_via_nop4(captcha_data)
                    logger.info("[%s] Captcha solved – token: %s", request_id, captcha_token)
                except Exception as e:
                    logger.error("[%s] nop4 captcha solving failed: %s", request_id, e)
                    abort(400, description="Captcha solving failed")

                extra_headers = {"x-captcha-key": captcha_token}
                try:
                    resp = await _discord_fetch(
                        session, token, "POST", f"/channels/{channel_id}/messages",
                        json={"attachments":[{"id": "0", "filename": name, "uploaded_filename": upload_filename, 'original_content_type': 'text/plain'}]}, headers=extra_headers
                    )
                except Exception as exc:
                    logger.error("[%s] Retry request failed: %s", request_id, exc)
                    abort(502)

                if resp.status != 200:
                    logger.error("[%s] Retry failed with status %s: %s", request_id, resp.status, await resp.text())
                    abort(resp.status)
            else:
                abort(400, description=error_data)

        if resp.status != 200:
            error_text = await resp.text()
            error_detail = {}
            try:
                error_detail = json.loads(error_text)
            except Exception:
                pass
            error_message = error_detail.get("message") or ("Discord token expired" if resp.status == 401 else f"Discord error {resp.status}")
            logger.error("[%s] Discord API error %s: %s", request_id, resp.status, error_text[:500])
            return jsonify({
                "error": "token_expired" if resp.status == 401 else f"discord_error_{resp.status}",
                "message": error_message,
            }), resp.status
        
        logger.info("[%s] Attachment uploaded successfully to googleapi", request_id)
        
        return jsonify(await resp.json())

@app.route("/read/discord/<channel_id>", methods=["GET"])
def discord_read(channel_id: str):
    return asyncio.run(_discord_read(channel_id))

@app.route("/download-enc/discord", methods=["GET"])
def discord_enc_download():
    request_id = f"discord-download-enc-{datetime.utcnow().timestamp()}"
    url = request.args.get("url")
    key_str = request.args.get("k")
    name = request.args.get("name", "download")
    if not url:
        return jsonify({"error": "missing url"}), 400
    if not key_str:
        return jsonify({"error": "missing decryption key"}), 400
    decrypted = False
    try:
            data, ctype = payloads.fetch(url)
            status_code = 200
            content = data
            try:
                decoded_content = content.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    decoded_content = content.decode('cp437')
                except UnicodeDecodeError:
                    decoded_content = content.hex()
            if status_code == 200:
                try:
                    decrypted_data = asyncio.run(DiscordCrypto.decrypt_message(decoded_content, key_str))
                    if decrypted == 500:
                        logger.warning("[%s] Attachment decryption failed on download", request_id)
                        decrypted = False
                    else:
                        decrypted = True
                        logger.info("[%s] Attachment decryption on download successful", request_id)
                except Exception as e:
                    print(e)
                    logger.warning("[%s] Attachment decryption crashed on download", request_id)
                    decrypted = False
            else:
                decrypted = False
            if decrypted is False:
                return jsonify({"error": "decryption failed"}), 500
            return base64.b64decode(decrypted_data), 200, {
                "Content-Disposition": f'attachment; filename="{name}"',
                "Content-Type": ctype,
                "Content-Length": str(len(decrypted_data)),
            }
    except Exception as e:
        print(e)
        return jsonify({"error": "download failed"}), 502

async def _discord_read(channel_id: str):
    request_id = f"read-{datetime.utcnow().timestamp()}"
    logger.info("[%s] Incoming read request for channel %s", request_id, channel_id)

    licence_b64 = request.cookies.get("session", None)
    if not licence_b64:
        logger.error("[%s] No session cookie provided: %s", request_id, licence_b64)
        abort(400)
    auth_dict = obtain_licence_cookie(licence_b64)
    licence = auth_dict.get("licence", None)
    auth_id = auth_dict.get("auth_id", None)
    if auth_id is None:
        logger.error("[%s] Invalid auth_id", request_id)
        return jsonify({"error": "invalid_auth_id"}), 401
    token = await get_discord_token(auth_id, licence)
    if token is None:
        logger.error("[%s] Invalid token for auth_id: %s", request_id, auth_id)
        return jsonify({"error": "invalid_auth_id"}), 401

    async with aiohttp.ClientSession() as session:
        try:
            resp = await _discord_fetch(session, token, "GET", f"/channels/{channel_id}/messages?limit=50")
            if resp.status != 200:
                error_text = await resp.text()
                error_detail = {}
                try:
                    error_detail = json.loads(error_text)
                except Exception:
                    pass
                error_message = error_detail.get("message") or ("Discord token expired" if resp.status == 401 else f"Discord error {resp.status}")
                logger.error("[%s] Discord API error %s: %s", request_id, resp.status, error_text[:500])
                return jsonify({
                    "error": "token_expired" if resp.status == 401 else f"discord_error_{resp.status}",
                    "message": error_message,
                }), resp.status
            messages = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.error("[%s] Fetch failed: %s", request_id, exc)
            return jsonify({"error": "upstream_error"}), 502

    try:
        channel_id_int = int(channel_id)
    except ValueError:
        logger.error("[%s] Invalid channel_id format: %s", request_id, channel_id)
        return jsonify({"error": "invalid_channel"}), 400

    key_str = await get_discord_key(licence, channel_id_int)
    encryption_enabled = key_str is not None
    logger.info("[%s] Channel encryption enabled: %s", request_id, encryption_enabled)

    result_list = []
    for msg in messages:
        if not isinstance(msg, dict):
            logger.warning("[%s] Skipping non-dict message: %s", request_id, type(msg))
            continue

        msg_id = msg.get("id", "unknown")
        #logger.info("[%s] Processing message %s", request_id, msg_id) Pointless atm

        author = msg.get("author", {})
        base = msg
        base["id"] = msg_id
        base["author_id"] = str(author.get("id", ""))
        base["author"] = author.get("username", "Unknown")
        base["authorAvatar"] = avatar_url_for_user(author)
        base["authorAvatarDecoration"] = avatar_decoration_url_for_user(author)
        base["authorDisplay"] = author.get("global_name")
        base["type"] = msg.get("type")
        base["content"] = msg.get("content", "")
        base["isGif"] = await _is_gif(msg.get("content", ""))
        base["createdAt"] = msg.get("timestamp", datetime.utcnow().isoformat(timespec="seconds") + "Z")
        base["decrypted"] = False
        for attachment in base.get("attachments", []):
            status_code, content = await _is_attachment_encrypted(attachment.get("url", ""))
            if status_code == 200:
                try:
                    decrypted = await DiscordCrypto.decrypt_message(content.decode("utf-8"), key_str)
                    if decrypted == 500:
                        logger.warning("[%s] Attachment decryption failed – returning original encrypted string", request_id)
                        attachment["decrypted"] = False
                    else:
                        if any((x in attachment.get("filename", None)) for x in ["png", "webp", "jpg", "gif", "webp"]):
                            raw_bytes = bytes.fromhex(decrypted)
                            try:
                                extension = attachment.get("filename", "").split(".")[-1]
                                attachment["url"] = "data:image/png;base64," + base64.b64encode(raw_bytes).decode("ascii")
                            except:
                                pass
                        else:
                            attachment["url"] = DOMAIN_REST + "/download-enc/discord?url=" + quote(attachment["url"], safe="") + "&k=" + key_str
                        attachment["decrypted"] = True
                        logger.info("[%s] Attachment decryption successful", request_id)
                except Exception as e:
                    print(e)
                    logger.warning("[%s] Attachment decryption crashed – returning original encrypted string", request_id)
                    attachment["decrypted"] = False
            else:
                attachment["decrypted"] = False

        content = base["content"]
        if content.startswith(ENCRYPTED_PREFIX):
            logger.info("[%s] Encrypted payload detected for message %s", request_id, msg_id)
            try:
                decrypted = await DiscordCrypto.decrypt_message(content, key_str)
                if decrypted == 500:
                    logger.warning("[%s] Decryption failed – returning original encrypted string", request_id)
                    base["decrypted"] = False
                else:
                    base["content"] = decrypted
                    base["isGif"] = await _is_gif(base.get("content", msg.get("content", "")))
                    base["decrypted"] = True
                    logger.info("[%s] Decryption successful", request_id)
            except Exception as e:
                logger.warning("[%s] Decryption exception for message %s: %s", request_id, msg_id, e)
        else:
            #logger.info("[%s] Plain text message in encrypted channel", request_id) Pointless atm
            pass

        result_list.append(base)

    logger.info("[%s] Returning %d messages", request_id, len(result_list))
    return {"items": result_list}


@app.route("/get/discord/sticker/<sticker_id>")
def sticker(sticker_id: str):
    return asyncio.run(_sticker(sticker_id))

async def _sticker(sticker_id: str):
    request_id = f"discord-sticker-{datetime.utcnow().timestamp()}"
    fmt = request.args.get("format", "png")
    logger.info("[%s] Incoming get request for sticker %s (format=%s)", request_id, sticker_id, fmt)

    status, content, content_type = await payloads.Discord.sticker(sticker_id, fmt)
    return Response(
        content,
        status=status,
        content_type=content_type,
        headers={"Access-Control-Allow-Origin": "*"},
    )

Thread(target=_run_discord_loop, daemon=True).start()

if __name__ == "__main__":
    init_db()
    app.run("0.0.0.0", port=5001, debug=False)