# fern core

from __future__ import annotations

import os
import contextlib

#with open(os.devnull, "w") as _dn, contextlib.redirect_stderr(_dn):
#    import eventlet
#
#    eventlet.monkey_patch()
#    pass

import base64
import hashlib
import json
import random
import re
import secrets
import threading
import time
import io
import qrcode
from PIL import Image as PilImage, ImageFilter
import websocket
import requests
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any
from flask import (
    Flask,
    Response,
    has_request_context,
    jsonify,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)
from flask_socketio import SocketIO, disconnect, emit

from utils.build import make_client, ClientIdentity
from httpz import Client as HttpzClient
from utils import db
from utils.core import setup_logger, Beach, Style, show_banner, system
from gateway_scraper import scrape_guild_gateway
from utils.qr_auth import QrAuthClient, RemoteAuthSession
from flask_cors import CORS

logger = setup_logger("app-direct")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# flask config
app = Flask(__name__)
app.secret_key = os.environ.get("FERN_UI_SECRET_KEY", "fern-ui-local-dev-secret")

CORS(
    app,
    origins=[
        "https://fern.rip",
        "https://api.fern.rip",
        "https://www.fern.rip",
    ],
    supports_credentials=True,
)

BACKEND_BASE_URL = os.environ.get("FERN_BACKEND_URL", "https://api.fern.rip").rstrip("/")
app.config['PERMANENT_SESSION_LIFETIME'] = 7 * 24 * 3600  # 7 days
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_DOMAIN=".fern.rip",
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
)
db_conn = None

def init_db():
    global db_conn
    db_conn = db.connect()
    db.setup_tables(db_conn)
    print("SUPABASE CONNECTED")

# globals
BASE_DIR = Path(__file__).resolve().parent
DISCORD_API = "https://discord.com/api/v10"
DEFAULT_ROUTE = "/channels/@me"
_token_to_auth: dict[str, str] = {}
_qr_sessions: dict[str, RemoteAuthSession] = {}
_qr_lock = threading.RLock()
_QR_TTL = 120
MESSAGE_CACHE_DIR = Path("./message_cache")
MESSAGE_CACHE_DIR.mkdir(exist_ok=True)

ALIASES_KEY = "data"
_aliases_lock = threading.Lock()

def _palm_header():
    # Work in progress
    return

def _load_aliases() -> dict:
    data = db.get_document(db_conn, "aliases", ALIASES_KEY)
    if not isinstance(data, dict):
        data = {"user_to_alias": {}, "alias_to_user": {}}
    data.setdefault("user_to_alias", {})
    data.setdefault("alias_to_user", {})
    return data

def _save_aliases(data: dict) -> None:
    # Ensure structure
    data.setdefault("user_to_alias", {})
    data.setdefault("alias_to_user", {})
    db.add_document(db_conn, "aliases", ALIASES_KEY, data)

def get_user_alias(user_id: str) -> dict:
    data = _load_aliases()
    user_map = data.get("user_to_alias", {})
    return user_map.get(str(user_id), {"alias": None, "enabled": False})

def get_user_active_alias(user_id: str) -> Optional[str]:
    u = get_user_alias(user_id)
    if u.get("enabled") and u.get("alias"):
        return u["alias"]
    return None

def claim_user_alias(user_id: str, new_alias: str) -> tuple[bool, str, dict]:
    user_id = str(user_id).strip()
    new_alias = new_alias.strip()
    if not (2 <= len(new_alias) <= 32):
        return False, "Alias must be between 2 and 32 characters", {}
    if not re.match(r'^[a-zA-Z0-9_\-\.]+$', new_alias):
        return False, "Alias can only contain letters, numbers, underscores, dashes, and periods", {}
    
    norm_alias = new_alias.lower()
    data = _load_aliases()
    user_map = data.setdefault("user_to_alias", {})
    alias_map = data.setdefault("alias_to_user", {})

    existing_owner = alias_map.get(norm_alias)
    if existing_owner and str(existing_owner) != user_id:
        return False, "This Fern alias is already claimed by another user", {}

    prev_info = user_map.get(user_id, {})
    old_alias = prev_info.get("alias")
    if old_alias and old_alias.lower() != norm_alias:
        alias_map.pop(old_alias.lower(), None)

    alias_map[norm_alias] = user_id
    user_info = {
        "alias": new_alias,
        "enabled": True,
        "claimed_at": datetime.utcnow().isoformat() + "Z"
    }
    user_map[user_id] = user_info
    _save_aliases(data)
    return True, "Alias claimed successfully", user_info

def toggle_user_alias(user_id: str, enabled: bool) -> tuple[bool, str, dict]:
    user_id = str(user_id).strip()
    data = _load_aliases()
    user_map = data.setdefault("user_to_alias", {})
    if user_id not in user_map or not user_map[user_id].get("alias"):
        return False, "No Fern alias claimed yet", {}
    user_map[user_id]["enabled"] = bool(enabled)
    _save_aliases(data)
    return True, "Alias setting updated", user_map[user_id]

def current_user_id() -> Optional[str]:
    token = bot_token()
    if not token:
        return None
    uid = session.get("user_id")
    if uid:
        return str(uid)
    onliner_uid = _current_user_id_for_token(token)
    if onliner_uid:
        session["user_id"] = onliner_uid
        return onliner_uid
    try:
        part = token.split(".")[0]
        part += "=" * ((4 - len(part) % 4) % 4)
        extracted = base64.b64decode(part).decode("utf-8", errors="ignore").strip()
        if extracted.isdigit() and len(extracted) >= 15:
            session["user_id"] = extracted
            return extracted
    except Exception:
        pass
    try:
        me = discord_api_request("/users/@me", token, method="GET")
        if isinstance(me, dict) and me.get("id"):
            session["user_id"] = str(me["id"])
            return str(me["id"])
    except Exception:
        pass
    auth_id = session.get("auth_id")
    if auth_id and str(auth_id).isdigit():
        return str(auth_id)
    return None

CLIENT_VERSION = "1.0.0"
LICENSE_SERVER_URL = 'http://127.0.0.1:5002'
ADMIN_KEY = 'u6a5r5lDNBFKPq0xX-mYHF6rDOTq7q5E'
ADMIN_WEBHOOK_URL = 'https://discord.com/api/webhooks/1535005332353581079/RzvV8KcHmPOYenUm2SaAksH9MrPZtRhOGFfSTr8C10CVxQt-7VZTcWxAhWrR6lCu6l6Q'
ADMIN_KEY_TTL = 7 * 24 * 3600
SERVICE_LABELS = {"api": "API", "ui": "Fern", "discord": "Discord"}
SERVICE_TARGETS = {
    "api": f"{BACKEND_BASE_URL}/data",
    "ui": "http://127.0.0.1:80/",
}
_CACHE = None

# vars
_last_friends: dict[str, dict] = {}
_last_dms: dict[str, dict] = {}
_last_notified_dm_message: dict[str, dict[str, str]] = {}
_sid_to_token: dict[str, str] = {}
_sid_to_auth: dict[str, str] = {}
_onliners: dict[str, "BackgroundOnliner"] = {}
_presences: dict[str, dict] = {}
_presences_lock = threading.Lock()
_user_name_cache: dict[str, str] = {}
_user_name_cache_lock = threading.Lock()

# live activity tracking
_active_sessions: dict[str, int] = {}
_activity_events: list[tuple[float, int]] = []
_activity_lock = threading.Lock()

def _record_activity(auth_id: str, connected: bool) -> None:
    with _activity_lock:
        current = _active_sessions.get(auth_id, 0)
        if connected:
            if current == 0:
                _activity_events.append((time.time(), 1))
            _active_sessions[auth_id] = current + 1
        elif current > 0:
            _active_sessions[auth_id] = current - 1
            if current - 1 == 0:
                _active_sessions.pop(auth_id, None)
                _activity_events.append((time.time(), -1))

def _activity_summary() -> dict:
    with _activity_lock:
        now = time.time()
        cutoff = now - 86400
        events = [(ts, d) for ts, d in _activity_events if ts > cutoff]
        _activity_events[:] = events
        active = sum(_active_sessions.values())
    running = 0
    peak = 0
    peak_ts = None
    hourly: dict[str, int] = {}
    for ts, delta in events:
        running += delta
        if running > peak:
            peak = running
            peak_ts = ts
        hour = datetime.fromtimestamp(ts).replace(minute=0, second=0, microsecond=0).isoformat(timespec="minutes")
        hourly[hour] = max(hourly.get(hour, 0), running)
    return {
        "active": active,
        "peak": peak,
        "peak_at": datetime.fromtimestamp(peak_ts).isoformat(timespec="minutes") if peak_ts else None,
        "hourly": hourly,
    }

# safety bs
SAFETY_INCREASE_AMOUNT = 0.5
SAFETY_DECREASE_AMOUNT = 5.0
SAFETY_DATA_FILE = "json/safety_data.json"
ENDPOINT_WINDOW = 20
ENDPOINT_MIN_SAMPLE = 4
ENDPOINT_BAN_THRESHOLD = 2
ENDPOINT_CAPTCHA_THRESHOLD = 3

# ws
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

# licensing

import requests as http_requests

def validate_license_with_server(license_key, user_id=None):
    try:
        r = http_requests.post(
            f"{LICENSE_SERVER_URL}/api/validate-license",
            json={"license_key": license_key, "user_id": user_id},
            timeout=5
        )
        return r.json()
    except Exception as e:
        logger.error(e)
        return {'valid': False, 'message': 'License server unavailable'}


def claim_trial_from_server(user_id):
    try:
        r = http_requests.post(
            f"{LICENSE_SERVER_URL}/api/trial/claim",
            json={"user_id": str(user_id)},
            timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            return {"success": True, "license_key": data["license_key"]}
        else:
            data = r.json()
            return {"success": False, "error": data.get("error", "Trial already used for this account.")}
    except Exception as e:
        logger.error(e)
        return {"success": False, "error": "License server unavailable."}


def _create_httpz_session(identity: ClientIdentity) -> HttpzClient:
    tls = identity.tls_data
    return HttpzClient(
        ja3=tls["ja3"],
        h2_fingerprint=tls["h2_fingerprint"],
        browser=tls["browser"],
        user_agent=identity.ua,
    )   

# memeber thing
import asyncio
from concurrent.futures import ThreadPoolExecutor
executor = ThreadPoolExecutor(max_workers=4)
def run_async_in_executor(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

# SafetyMeter
class SafetyMeter:
    def __init__(self, filepath):
        self.lock = threading.RLock()
        self.filepath = filepath
        self.score = 100.0
        self.invalidated_tokens = set()
        self.endpoints: dict[str, dict] = {}
        self.version = None
        self._load()
        self._check_version_reset()

    def _load(self):
        try:
            with open(self.filepath, 'r') as f:
                data = json.load(f)
            self.score = data.get('score', 100.0)
            self.invalidated_tokens = set(data.get('invalidated_tokens', []))
            self.endpoints = data.get('endpoints', {})
            self.version = data.get('version')
        except (FileNotFoundError, json.JSONDecodeError):
            self.score = 100.0
            self.invalidated_tokens = set()
            self.endpoints = {}
            self.version = None

    def _save(self):
        data = {
            'score': self.score,
            'invalidated_tokens': list(self.invalidated_tokens),
            'endpoints': self.endpoints,
            'version': self.version,
        }
        with open(self.filepath, 'w') as f:
            json.dump(data, f, indent=2)

    def _check_version_reset(self):
        current_version = CLIENT_VERSION
        if self.version != current_version:
            self.score = 100.0
            self.invalidated_tokens.clear()
            self.endpoints = {}
            self.version = current_version
            self._save()

    def record_success(self, token):
        if not token:
            return
        with self.lock:
            if token in self.invalidated_tokens:
                return
            self.score = min(100.0, self.score + SAFETY_INCREASE_AMOUNT)
            self._save()

    def record_failure(self, token):
        if not token:
            return
        with self.lock:
            if token in self.invalidated_tokens:
                return
            self.invalidated_tokens.add(token)
            self.score = max(0.0, self.score - SAFETY_DECREASE_AMOUNT)
            self._save()

    _KIND_KEY = {"ok": "ok", "ban": "bans", "captcha": "captchas"}

    def record_endpoint(self, path: str, kind: str) -> None:
        if not path or kind not in self._KIND_KEY:
            return
        with self.lock:
            rec = self.endpoints.setdefault(
                path,
                {"ok": 0, "bans": 0, "captchas": 0, "recent": []},
            )
            key = self._KIND_KEY[kind]
            rec[key] = rec.get(key, 0) + 1
            recent = rec.get("recent") or []
            recent.append(kind)
            if len(recent) > ENDPOINT_WINDOW:
                for old in recent[: len(recent) - ENDPOINT_WINDOW]:
                    old_key = self._KIND_KEY.get(old)
                    if old_key:
                        rec[old_key] = max(0, rec[old_key] - 1)
                recent = recent[len(recent) - ENDPOINT_WINDOW:]
            rec["recent"] = recent
            rec["last"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            self._save()

    def endpoint_health(self) -> list[dict]:
        with self.lock:
            out = []
            for path, rec in self.endpoints.items():
                ok = rec.get("ok", 0)
                bans = rec.get("bans", 0)
                captchas = rec.get("captchas", 0)
                total = ok + bans + captchas
                if total < ENDPOINT_MIN_SAMPLE:
                    continue
                flags = []
                if bans >= ENDPOINT_BAN_THRESHOLD:
                    flags.append("ban")
                if captchas >= ENDPOINT_CAPTCHA_THRESHOLD:
                    flags.append("captcha")
                if bans + captchas >= 4:
                    flags.append("degraded")
                if not flags:
                    continue
                endpoint_score = max(0.0, min(100.0, (ok / total) * 100.0 - (bans * 5.0) - (captchas * 2.5)))
                out.append({
                    "path": path,
                    "ok": ok,
                    "bans": bans,
                    "captchas": captchas,
                    "total": total,
                    "score": round(endpoint_score, 1),
                    "flags": flags,
                    "last": rec.get("last"),
                })
            out.sort(key=lambda e: (e["score"], e["path"]))
            return out

    def get_score(self):
        with self.lock:
            base = float(self.score)
            flagged = self.endpoint_health()
            if not flagged:
                return round(base, 1)

            penalty = 0.0
            for ep in flagged:
                flags = ep.get("flags", [])
                score = ep.get("score", 100.0)
                if "degraded" in flags or score <= 10.0:
                    penalty += 35.0
                elif "ban" in flags:
                    penalty += 20.0
                elif "captcha" in flags:
                    penalty += 10.0
                else:
                    penalty += max(0.0, (100.0 - score) * 0.25)

            effective = max(0.0, min(100.0, base - penalty))
            return round(effective, 1)

safety_meter = SafetyMeter(os.path.join(BASE_DIR, SAFETY_DATA_FILE))

def _endpoint_key(method: str, path: str) -> str:
    parts = path.split("/")
    norm = "/".join(":id" if p.isdigit() else p for p in parts)
    return f"{method} {norm}"

# BackgroundOnliner
class BackgroundOnliner:

    def __init__(self, token: str, auth_id: str):
        self.token = token
        self.auth_id = auth_id
        self.user_id = None
        self._stop = threading.Event()
        self._thread = None
        self.ready = threading.Event()
        self._ws = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def subscribe_guild(self, guild_id: str, channel_id: str = None):
        if self._ws:
            try:
                ch_map = {channel_id: [[0, 99]]} if channel_id else {str(guild_id): [[0, 99]]}
                payload = {
                    "op": 14,
                    "d": {
                        "guild_id": str(guild_id),
                        "channels": ch_map,
                        "typing": True,
                        "threads": True,
                        "activities": True
                    }
                }
                self._ws.send(json.dumps(payload))
            except Exception:
                pass

    def _fetch_own_presence(self, user_id):
        import time
        for _ in range(30):
            time.sleep(0.5)
            with _presences_lock:
                p = _presences.get(user_id)
                if p and p.get("status"):
                    payload = {"userId": user_id, "status": p["status"]}
                    for sid, tok in list(_sid_to_token.items()):
                        if tok == self.token:
                            socketio.emit("presence_update", payload, room=sid)
                    return
        try:
            settings = discord_api_request("/users/@me/settings", self.token, method="GET", record_safety=False)
            if isinstance(settings, dict):
                status = settings.get("status") or "online"
                with _presences_lock:
                    _presences[user_id] = {"user": {"id": user_id}, "status": status}
                payload = {"userId": user_id, "status": status}
                for sid, tok in list(_sid_to_token.items()):
                    if tok == self.token:
                        socketio.emit("presence_update", payload, room=sid)
        except Exception:
            pass

    def _run(self):
        while not self._stop.is_set():
            ws = None
            try:
                ws = websocket.WebSocket()
                ws.settimeout(10)
                ws.connect("wss://gateway.discord.gg/?v=9&encoding=json")
                self._ws = ws

                try:
                    hello = json.loads(ws.recv())
                except json.JSONDecodeError:
                    self.ready.set()
                    return

                heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000

                identify = {
                    "op": 2,
                    "d": {
                        "token": self.token,
                        "properties": {
                            "os": "Windows",
                            "browser": "Discord Client",
                            "release_channel": "stable",
                            "client_version": "1.0.9238",
                            "os_version": "10.0.26200",
                            "os_arch": "x64",
                            "app_arch": "x64",
                            "system_locale": "en-US",
                            "has_client_mods": False,
                            "client_launch_id": secrets.token_urlsafe(16),
                            "browser_user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9238 Chrome/138.0.7204.251 Electron/37.6.0 Safari/537.36",
                            "browser_version": "37.6.0",
                            "os_sdk_version": "26200",
                            "client_build_number": 72112,
                            "native_build_number": random.randint(84800, 84934),
                            "client_event_source": None,
                        },
                        "presence": {"status": "invisible", "since": 0},
                    },
                }
                ws.send(json.dumps(identify))

                ready = False
                for _ in range(10):
                    if self._stop.is_set():
                        self.ready.set()
                        return
                    try:
                        resp = json.loads(ws.recv())
                    except json.JSONDecodeError:
                        self.ready.set()
                        return
                    if resp.get("t") == "READY":
                        ready = True
                        break
                    if resp.get("op") == 9:
                        self.ready.set()
                        return

                if not ready:
                    self.ready.set()
                    return

                d = resp.get("d", {})
                user_id = str(d.get("user", {}).get("id", ""))
                self.user_id = user_id
                presences_list = d.get("presences", [])
                with _presences_lock:
                    for p in presences_list:
                        uid = p.get("user", {}).get("id")
                        if uid:
                            _presences[uid] = p

                self.ready.set()
                if user_id:
                    threading.Thread(target=self._fetch_own_presence, args=(user_id,), daemon=True).start()

                ws.settimeout(heartbeat_interval)
                while not self._stop.is_set():
                    try:
                        raw = ws.recv()
                        if not raw:
                            continue
                        event = json.loads(raw)
                        op = event.get("op")
                        t = event.get("t")
                        if op == 0 and t == "PRESENCE_UPDATE":
                            d2 = event.get("d", {})
                            uid = str(d2.get("user", {}).get("id") or "")
                            if uid:
                                with _presences_lock:
                                    _presences[uid] = d2

                                payload = {"userId": uid, "status": d2.get("status")}
                                for sid, tok in list(_sid_to_token.items()):
                                    if tok == self.token:
                                        socketio.emit("presence_update", payload, room=sid)
                        elif op == 0 and t == "GUILD_MEMBER_LIST_UPDATE":
                            d2 = event.get("d", {})
                            for op_item in d2.get("ops", []):
                                if op_item.get("op") in ("SYNC", "INSERT"):
                                    for it in op_item.get("items", []):
                                        if "member" in it:
                                            mem = it["member"]
                                            u = mem.get("user", {})
                                            uid = str(u.get("id") or "")
                                            pres = mem.get("presence", {})
                                            st = pres.get("status")
                                            if uid and st:
                                                with _presences_lock:
                                                    _presences[uid] = {"user": {"id": uid}, "status": st}
                                                payload = {"userId": uid, "status": st}
                                                for sid, tok in list(_sid_to_token.items()):
                                                    if tok == self.token:
                                                        socketio.emit("presence_update", payload, room=sid)
                        elif op == 0 and t == "READY_SUPPLEMENTAL":
                            d2 = event.get("d", {})
                            merged = d2.get("merged_presences", {})
                            for g_pres_list in merged.get("guilds", []):
                                for p in g_pres_list:
                                    uid = str(p.get("user_id") or "")
                                    st = p.get("status")
                                    if uid and st:
                                        with _presences_lock:
                                            _presences[uid] = {"user": {"id": uid}, "status": st}
                                        payload = {"userId": uid, "status": st}
                                        for sid, tok in list(_sid_to_token.items()):
                                            if tok == self.token:
                                                socketio.emit("presence_update", payload, room=sid)
                            for p in merged.get("friends", []):
                                uid = str(p.get("user_id") or "")
                                st = p.get("status")
                                if uid and st:
                                    with _presences_lock:
                                        _presences[uid] = {"user": {"id": uid}, "status": st}
                                    payload = {"userId": uid, "status": st}
                                    for sid, tok in list(_sid_to_token.items()):
                                        if tok == self.token:
                                            socketio.emit("presence_update", payload, room=sid)
                        elif op == 0 and t == "TYPING_START":
                            d2 = event.get("d", {})
                            ch_id = str(d2.get("channel_id", ""))
                            u_id = str(d2.get("user_id", ""))
                            if u_id and u_id != str(self.user_id):
                                username = get_user_active_alias(u_id)
                                if not username:
                                    member = d2.get("member", {})
                                    user_info = member.get("user", {}) if isinstance(member, dict) else {}
                                    username = user_info.get("global_name") or user_info.get("username") or (member.get("nick") if isinstance(member, dict) else None)
                                if not username:
                                    with _user_name_cache_lock:
                                        username = _user_name_cache.get(u_id)
                                if not username:
                                    with _presences_lock:
                                        p = _presences.get(u_id, {})
                                        u = p.get("user", {})
                                        username = u.get("global_name") or u.get("username")
                                if not username:
                                    try:
                                        cached_u = discord_api_request(f"/users/{u_id}", self.token, method="GET")
                                        if isinstance(cached_u, dict):
                                            username = cached_u.get("global_name") or cached_u.get("username")
                                            if username:
                                                with _user_name_cache_lock:
                                                    _user_name_cache[u_id] = username
                                    except Exception:
                                        pass
                                payload = {
                                    "channelId": ch_id,
                                    "userId": u_id,
                                    "username": username or "",
                                    "timestamp": d2.get("timestamp", int(time.time()))
                                }
                                for sid, tok in list(_sid_to_token.items()):
                                    if tok == self.token:
                                        socketio.emit("typing_start", payload, room=sid)
                        elif op == 0 and t == "MESSAGE_CREATE":
                            d2 = event.get("d", {})
                            ch_id = str(d2.get("channel_id", ""))
                            author = d2.get("author", {})
                            author_id = str(author.get("id", ""))
                            if author_id:
                                author_name = author.get("global_name") or author.get("username")
                                if author_name:
                                    with _user_name_cache_lock:
                                        _user_name_cache[author_id] = author_name
                            payload = {"channelId": ch_id, "userId": author_id}
                            for sid, tok in list(_sid_to_token.items()):
                                if tok == self.token:
                                    socketio.emit("typing_stop", payload, room=sid)
                        elif op == 0 and t in {"MESSAGE_REACTION_ADD", "MESSAGE_REACTION_REMOVE"}:
                            d2 = event.get("d", {})
                            event_name = "message_reaction_add" if t == "MESSAGE_REACTION_ADD" else "message_reaction_remove"
                            for sid, tok in list(_sid_to_token.items()):
                                if tok == self.token:
                                    socketio.emit(event_name, d2, room=sid)
                        elif op == 1:
                            ws.send(json.dumps({"op": 1, "d": None}))
                        elif op == 7:
                            break
                        elif op == 9:
                            return
                    except websocket.WebSocketTimeoutException:
                        ws.send(json.dumps({"op": 1, "d": None}))
                    except websocket.WebSocketConnectionClosedException:
                        break
                    except json.JSONDecodeError:
                        break

                ws.close()


            except Exception as e:
                logger.error(e)
                self.ready.set()
                if ws:
                    try:
                        ws.close()
                    except Exception:
                        pass

            if not self._stop.is_set():
                self._stop.wait(5)
# end session
def _unsign_session(cookie_value: str):
    from flask.sessions import SecureCookieSessionInterface
    si = SecureCookieSessionInterface()
    try:
        return si.get_signing_serializer(app).loads(cookie_value)
    except Exception:
        return {}

# messagelogger
class MessageLogger:
    def __init__(self, cache_dir: Path = MESSAGE_CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(exist_ok=True)
        self.memory_cache: Dict[str, Dict[str, Any]] = {}
    
    def _get_user_cache_file(self, user_id: str) -> Path:
        return self.cache_dir / f"user_{user_id}.json"
    
    def _get_channel_cache_file(self, channel_id: str) -> Path:
        return self.cache_dir / f"channel_{channel_id}.json"
    
    def _load_user_cache(self, user_id: str) -> Dict[str, Any]:
        cache_key = f"user_{user_id}"
        if cache_key in self.memory_cache:
            return self.memory_cache[cache_key]
        
        cache_file = self._get_user_cache_file(user_id)
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.memory_cache[cache_key] = data
                    return data
            except (json.JSONDecodeError, IOError):
                pass
        
        default = {
            "user_id": user_id,
            "created_at": datetime.utcnow().isoformat(),
            "messages": {},
            "deleted_messages": {},
            "interactions": []
        }
        self.memory_cache[cache_key] = default
        return default
    
    def _save_user_cache(self, user_id: str, data: Dict[str, Any]) -> None:
        cache_file = self._get_user_cache_file(user_id)
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Failed to save user cache for {user_id}: {e}")
    
    def _load_channel_cache(self, channel_id: str) -> Dict[str, Any]:
        cache_key = f"channel_{channel_id}"
        if cache_key in self.memory_cache:
            return self.memory_cache[cache_key]
        
        cache_file = self._get_channel_cache_file(channel_id)
        if cache_file.exists():
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.memory_cache[cache_key] = data
                    return data
            except (json.JSONDecodeError, IOError):
                pass
        
        default = {
            "channel_id": channel_id,
            "created_at": datetime.utcnow().isoformat(),
            "messages": {}
        }
        self.memory_cache[cache_key] = default
        return default
    
    def _save_channel_cache(self, channel_id: str, data: Dict[str, Any]) -> None:
        cache_file = self._get_channel_cache_file(channel_id)
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Failed to save channel cache for {channel_id}: {e}")
    
    def log_message_created(self, message: Dict[str, Any], current_user_id: str) -> None:
        author_id = str(message.get("author", {}).get("id", ""))
        channel_id = str(message.get("channel_id", ""))
        message_id = str(message.get("id", ""))
        
        if not all([author_id, channel_id, message_id]):
            return
        
        message_data = {
            "id": message_id,
            "channel_id": channel_id,
            "author_id": author_id,
            "content": message.get("content", ""),
            "created_at": message.get("timestamp", datetime.utcnow().isoformat()),
            "edited_at": message.get("edited_timestamp"),
            "deleted": False,
            "attachments": message.get("attachments", []),
            "embeds": message.get("embeds", []),
            "mentions": message.get("mentions", []),
            "reactions": message.get("reactions", [])
        }
        
        user_cache = self._load_user_cache(author_id)
        if author_id != current_user_id:
            if "interactions" in user_cache and current_user_id not in user_cache["interactions"]:
                user_cache["interactions"].append(current_user_id)
        
        user_cache["messages"][message_id] = message_data
        self._save_user_cache(author_id, user_cache)
        
        channel_cache = self._load_channel_cache(channel_id)
        channel_cache["messages"][message_id] = message_data
        self._save_channel_cache(channel_id, channel_cache)
    
    def log_message_updated(self, message: Dict[str, Any], current_user_id: str) -> None:
        author_id = str(message.get("author", {}).get("id", ""))
        message_id = str(message.get("id", ""))
        channel_id = str(message.get("channel_id", ""))
        
        if not all([author_id, message_id, channel_id]):
            return
        
        user_cache = self._load_user_cache(author_id)
        
        if message_id in user_cache["messages"]:
            user_cache["messages"][message_id].update({
                "content": message.get("content", ""),
                "edited_at": message.get("edited_timestamp", datetime.utcnow().isoformat()),
                "attachments": message.get("attachments", []),
                "embeds": message.get("embeds", [])
            })
        
        self._save_user_cache(author_id, user_cache)
        
        channel_cache = self._load_channel_cache(channel_id)
        if message_id in channel_cache["messages"]:
            channel_cache["messages"][message_id].update({
                "content": message.get("content", ""),
                "edited_at": message.get("edited_timestamp", datetime.utcnow().isoformat()),
                "attachments": message.get("attachments", []),
                "embeds": message.get("embeds", [])
            })
        self._save_channel_cache(channel_id, channel_cache)
    
    def log_message_deleted(self, message_id: str, author_id: str, channel_id: str, 
                           message_content: Optional[str] = None, metadata: Optional[Dict] = None) -> None:
        message_id = str(message_id)
        author_id = str(author_id)
        channel_id = str(channel_id)
        
        user_cache = self._load_user_cache(author_id)
        
        original_message = user_cache["messages"].get(message_id, {})
        
        deleted_entry = {
            "id": message_id,
            "channel_id": channel_id,
            "author_id": author_id,
            "original_content": message_content or original_message.get("content", ""),
            "deleted_at": datetime.utcnow().isoformat(),
            "created_at": original_message.get("created_at"),
            "metadata": metadata or {}
        }
        
        user_cache["deleted_messages"][message_id] = deleted_entry
        
        if message_id in user_cache["messages"]:
            user_cache["messages"][message_id]["deleted"] = True
        
        self._save_user_cache(author_id, user_cache)
        
        channel_cache = self._load_channel_cache(channel_id)
        if message_id in channel_cache["messages"]:
            channel_cache["messages"][message_id]["deleted"] = True
        channel_cache["messages"][f"deleted_{message_id}"] = deleted_entry
        self._save_channel_cache(channel_id, channel_cache)
    
    def get_user_messages(self, user_id: str, include_deleted: bool = False) -> List[Dict[str, Any]]:
        user_cache = self._load_user_cache(user_id)
        messages = list(user_cache.get("messages", {}).values())
        
        if not include_deleted:
            messages = [m for m in messages if not m.get("deleted", False)]
        
        return sorted(messages, key=lambda m: m.get("created_at", ""), reverse=True)
    
    def get_user_deleted_messages(self, user_id: str) -> List[Dict[str, Any]]:
        user_cache = self._load_user_cache(user_id)
        deleted = list(user_cache.get("deleted_messages", {}).values())
        return sorted(deleted, key=lambda m: m.get("deleted_at", ""), reverse=True)
    
    def get_channel_messages(self, channel_id: str) -> List[Dict[str, Any]]:
        channel_cache = self._load_channel_cache(channel_id)
        messages = [m for k, m in channel_cache.get("messages", {}).items() 
                   if not k.startswith("deleted_")]
        return sorted(messages, key=lambda m: m.get("created_at", ""), reverse=True)
    
    def get_user_interactions(self, user_id: str) -> List[str]:
        user_cache = self._load_user_cache(user_id)
        return user_cache.get("interactions", [])
    
    def get_message(self, user_id: str, message_id: str) -> Optional[Dict[str, Any]]:
        user_cache = self._load_user_cache(user_id)
        return user_cache.get("messages", {}).get(str(message_id))
    
    def search_user_messages(self, user_id: str, query: str) -> List[Dict[str, Any]]:
        messages = self.get_user_messages(user_id, include_deleted=True)
        query_lower = query.lower()
        return [m for m in messages if query_lower in m.get("content", "").lower()]
    
    def clear_user_cache(self, user_id: str) -> None:
        cache_key = f"user_{user_id}"
        if cache_key in self.memory_cache:
            del self.memory_cache[cache_key]
        
        cache_file = self._get_user_cache_file(user_id)
        if cache_file.exists():
            try:
                cache_file.unlink()
            except OSError as e:
                logger.error(f"Failed to clear cache for user {user_id}: {e}")
    
    def clear_channel_cache(self, channel_id: str) -> None:
        cache_key = f"channel_{channel_id}"
        if cache_key in self.memory_cache:
            del self.memory_cache[cache_key]
        
        cache_file = self._get_channel_cache_file(channel_id)
        if cache_file.exists():
            try:
                cache_file.unlink()
            except OSError as e:
                logger.error(f"Failed to clear cache for channel {channel_id}: {e}")
message_logger = MessageLogger()

#proxychainshit
class ProxyChain:

    def __init__(self, max_retries: int = 3, timeout: int = 10):
        self.max_retries = max_retries
        self.timeout = timeout
        self.proxies: List[str] = []
        self.current_index = 0
        self.failed_proxies: set = set()
        
    def fetch_free_proxies(self) -> List[str]:
        proxies = []
        
        try:
            resp = requests.get(
                'https://www.proxy-list.download/api/v1/get?type=http',
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data.get('data'), list):
                    proxies.extend([
                        f"http://{item['ip']}:{item['port']}"
                        for item in data['data'][:10]
                    ])
        except Exception:
            pass

        try:
            resp = requests.get(
                'https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt',
                timeout=5
            )
            if resp.status_code == 200:
                for line in resp.text.strip().split('\n'):
                    line = line.strip()
                    if line and ':' in line:
                        proxies.append(f"http://{line}")
        except Exception:
            pass
        proxies = list(set(proxies))[:20]
        self.proxies = proxies
        return proxies
    
    def get_next_proxy(self) -> Optional[str]:
        if not self.proxies:
            self.fetch_free_proxies()
        if not self.proxies:
            return None
        attempts = 0
        while attempts < len(self.proxies):
            proxy = self.proxies[self.current_index % len(self.proxies)]
            self.current_index += 1
            
            if proxy not in self.failed_proxies:
                return proxy
            attempts += 1
        self.failed_proxies.clear()
        if self.proxies:
            proxy = self.proxies[self.current_index % len(self.proxies)]
            self.current_index += 1
            return proxy
        return None
    def mark_proxy_failed(self, proxy: str) -> None:
        self.failed_proxies.add(proxy)

    def get_route(self, force_refresh: bool = False) -> dict:
        if force_refresh or not getattr(self, "_active_route", None):
            nodes = [
                {"country": "Germany", "flag": "🇩🇪", "city": "Frankfurt", "latency": "28ms"},
                {"country": "Switzerland", "flag": "🇨🇭", "city": "Zurich", "latency": "35ms"},
                {"country": "Iceland", "flag": "🇮🇸", "city": "Reykjavik", "latency": "49ms"},
                {"country": "Netherlands", "flag": "🇳🇱", "city": "Amsterdam", "latency": "24ms"},
                {"country": "Sweden", "flag": "🇸🇪", "city": "Stockholm", "latency": "41ms"},
                {"country": "Finland", "flag": "🇫🇮", "city": "Helsinki", "latency": "46ms"},
                {"country": "Canada", "flag": "🇨🇦", "city": "Montreal", "latency": "68ms"},
                {"country": "Norway", "flag": "🇳🇴", "city": "Oslo", "latency": "39ms"},
            ]
            self._active_route = random.sample(nodes, 3)
        return {
            "enabled": True,
            "hops": self._active_route,
            "total_hops": len(self._active_route),
            "status": "Encrypted & Routed"
        }

proxy_chain = ProxyChain()

# get_discord_client
def get_discord_client():
    global _CACHE
    if _CACHE is not None:
        return _CACHE[1], _CACHE[2]

    identity = make_client()
    fingerprint = identity.tls_data["ja3_hash"]
    client = _create_httpz_session(identity)
    base_headers = identity.api_headers(fingerprint)

    _CACHE = (identity, client, base_headers)
    return client, base_headers

def _get_identity() -> ClientIdentity:
    global _CACHE
    if _CACHE is not None:
        return _CACHE[0]
    return make_client()


# _log_request
def _log_request(method: str, url: str, headers: dict, body=None):
    safe_headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}
    logger.debug(
        f"{Beach.OCEAN}{method} {url}{Style.RESET_ALL}"
    )
    if body is not None:
        try:
            body_str = json.dumps(body) if isinstance(body, dict) else str(body)
        except Exception:
            body_str = "<unserializable>"
        logger.debug(
            f"  Body {Beach.FOAM}→{Style.RESET_ALL} "
            f"{Beach.OCEAN}{body_str}{Style.RESET_ALL}"
        )


# discord_api_request
def discord_api_request(
    path: str,
    token: str,
    method: str = "GET",
    json_data: dict | None = None,
    extra_headers: dict | None = None,
    record_safety: bool = True,
) -> dict | list | None:
    client, base_headers = get_discord_client()
    headers = base_headers.copy()
    headers["Authorization"] = token
    if extra_headers:
        headers.update(extra_headers)
    url = f"{DISCORD_API}{path}"
    _log_request(method, url, headers, json_data)
    try:
        if method == "GET":
            headers.pop("Content-Type", None)
            resp = client.get(url, headers=headers)
        elif method == "POST":
            headers["Content-Type"] = "application/json"
            if json_data is not None:
                resp = client.post(url, headers=headers, json=json_data)
            else:
                resp = client.post(url, headers=headers)
        elif method == "PUT":
            headers["Content-Type"] = "application/json"
            if json_data is not None:
                resp = client.put(url, headers=headers, json=json_data)
            else:
                resp = client.put(url, headers=headers)
        elif method == "DELETE":
            headers.pop("Content-Type", None)
            resp = client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        logger.debug(
            f"{Beach.OCEAN}{method} {path}{Style.RESET_ALL} "
            f"→ {Beach.OCEAN}{resp.status_code}{Style.RESET_ALL}"
        )
        raw_body = resp.content
        try:
            body_snippet = raw_body[:500].decode("utf-8", errors="replace")
            logger.debug(
                f"Text {Beach.FOAM}→{Style.RESET_ALL} "
                f"{Beach.OCEAN}{body_snippet}{Style.RESET_ALL}"
            )
        except Exception as e:
            logger.error(e)

        if resp.status_code == 401:
            if has_request_context():
                session.clear()
            if record_safety and token:
                safety_meter.record_failure(token)
            error_text = raw_body.decode("utf-8", errors="replace")
            raise urllib.error.HTTPError(url, 401, error_text, resp.headers, None)

        if resp.status_code not in (200, 201, 204):
            error_text = raw_body.decode("utf-8", errors="replace")
            if record_safety:
                ep_key = _endpoint_key(method, path)
                if resp.status_code in (401, 403):
                    safety_meter.record_endpoint(ep_key, "ban")
                elif resp.status_code == 400 and '"captcha-required"' in error_text:
                    safety_meter.record_endpoint(ep_key, "captcha")
            if resp.status_code == 403 and record_safety and token:
                safety_meter.record_failure(token)
            raise urllib.error.HTTPError(url, resp.status_code, error_text, resp.headers, None)

        if record_safety and token:
            safety_meter.record_success(token)
        if record_safety:
            safety_meter.record_endpoint(_endpoint_key(method, path), "ok")

        if resp.status_code == 204:
            return None
        return json.loads(raw_body.decode("utf-8"))

    except urllib.error.HTTPError as e:
        logger.error(e)
        raise
    except Exception as e:
        logger.error(e)
        raise


# image_data_url
def image_data_url(seed: str) -> str:
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

# avatar
def avatar_url_for_user(user: dict) -> str:
    user_id = str(user.get("id") or "")
    avatar = user.get("avatar")
    if user_id and avatar:
        ext = "gif" if str(avatar).startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{ext}?size=128"
    username = user.get("username") or user_id or "User"
    return image_data_url(username)


def avatar_decoration_url_for_user(user: dict) -> str | None:
    deco = user.get("avatar_decoration_data")
    if deco and isinstance(deco, dict):
        asset = deco.get("asset")
        if asset:
            return f"https://cdn.discordapp.com/avatar-decoration-presets/{asset}.png?size=128"
    return None


def user_payload(user: dict) -> dict:
    user_id = str(user.get("id") or "")
    username = user.get("username") or ""
    active_alias = get_user_active_alias(user_id)
    display = active_alias or user.get("global_name") or username
    status = "online"
    with _presences_lock:
        for uid, p in _presences.items():
            if str(uid) == user_id:
                status = p.get("status", "online")
                break
    return {
        "id": user_id,
        "username": username,
        "displayName": display,
        "avatarUrl": avatar_url_for_user(user),
        "avatarDecorationUrl": avatar_decoration_url_for_user(user),
        "status": status,
    }

def guild_payload(guild: dict) -> dict:
    guild_id = str(guild.get("id") or "")
    icon = guild.get("icon")
    icon_url = ""
    if guild_id and icon:
        ext = "gif" if str(icon).startswith("a_") else "png"
        icon_url = f"https://cdn.discordapp.com/icons/{guild_id}/{icon}.{ext}?size=96"
    return {
        "id": guild_id,
        "name": guild.get("name") or "Server",
        "iconUrl": icon_url,
        "owner": bool(guild.get("owner")),
        "permissions": str(guild.get("permissions") or ""),
    }

def dm_payload(channel: dict) -> dict:
    channel_id = str(channel.get("id") or "")
    ctype = channel.get("type", 1)
    last_message_id = str(channel.get("last_message_id") or "")
    if ctype == 1:
        recipients = channel.get("recipients", [])
        if recipients:
            user = recipients[0]
            user_id = str(user.get("id") or "")
            username = user.get("username") or "User"
            active_alias = get_user_active_alias(user_id)
            display = active_alias or user.get("global_name") or username
            is_bot = bool(user.get("bot"))
            presence_status = "offline"
            with _presences_lock:
                p = _presences.get(user_id)
                if p:
                    presence_status = p.get("status")
            return {
                "id": channel_id,
                "userId": user_id,
                "username": username,
                "displayName": display,
                "avatarUrl": avatar_url_for_user(user),
                "avatarDecorationUrl": avatar_decoration_url_for_user(user),
                "status": presence_status,
                "lastMessageId": last_message_id,
                "type": 1,
                "isBot": is_bot,
            }
        return {
            "id": channel_id,
            "userId": "",
            "username": "Unknown",
            "displayName": "Unknown",
            "avatarUrl": image_data_url(channel_id),
            "avatarDecorationUrl": None,
            "lastMessageId": last_message_id,
            "type": 1,
            "isBot": False,
        }
    # type 3
    name = channel.get("name") or "Group"
    icon = channel.get("icon")
    icon_url = ""
    if channel_id and icon:
        ext = "gif" if str(icon).startswith("a_") else "png"
        icon_url = f"https://cdn.discordapp.com/channel-icons/{channel_id}/{icon}.{ext}?size=128"
    if not icon_url:
        icon_url = image_data_url(name or channel_id)
    return {
        "id": channel_id,
        "username": name,
        "displayName": name,
        "avatarUrl": icon_url,
        "lastMessageId": last_message_id,
        "type": 3,
    }

def presence_snapshot(user_id: str) -> dict:
    with _presences_lock:
        p = _presences.get(str(user_id), {})
    if isinstance(p, dict):
        return p
    return {}

def custom_status_from_presence(presence: dict) -> dict | None:
    activities = presence.get("activities")
    if not isinstance(activities, list):
        return None
    for activity in activities:
        if not isinstance(activity, dict):
            continue
        if activity.get("type") != 4:
            continue
        emoji = activity.get("emoji")
        payload = {
            "text": activity.get("state") or "",
            "emoji": None,
        }
        if isinstance(emoji, dict):
            payload["emoji"] = {
                "id": str(emoji.get("id") or ""),
                "name": emoji.get("name") or "",
                "animated": bool(emoji.get("animated")),
            }
        return payload
    return None

def _current_user_id_for_token(token: str) -> str:
    for auth_id, onliner in _onliners.items():
        if onliner.token == token and onliner.user_id:
            return str(onliner.user_id)
    return ""

def _build_notification_payload(channel_id: str, message: dict) -> dict:
    author = message.get("author") or {}
    mentions = message.get("mentions") or []
    mention_ids = []
    for mention in mentions:
        if isinstance(mention, dict):
            mention_ids.append(str(mention.get("id") or ""))
        elif isinstance(mention, str):
            mention_ids.append(str(mention))
    author_id = str(author.get("id") or "")
    return {
        "channelId": str(channel_id),
        "guildId": str(message.get("guild_id") or ""),
        "messageId": str(message.get("id") or ""),
        "authorId": author_id,
        "author": get_user_active_alias(author_id) or author.get("global_name") or author.get("username") or "Unknown",
        "avatarUrl": avatar_url_for_user(author),
        "content": str(message.get("content") or ""),
        "mentions": [m for m in mention_ids if m],
    }

def get_relationships(token: str, record_safety: bool = True) -> dict[str, list]:
    try:
        data = discord_api_request("/users/@me/relationships", token, method="GET", record_safety=record_safety)
    except urllib.error.HTTPError as e:
        logger.error(e)
        return {"friends": [], "pending": [], "blocked": []}
    except urllib.error.URLError as e:
        logger.error(e)
        return {"friends": [], "pending": [], "blocked": []}
    if not isinstance(data, list):
        return {"friends": [], "pending": [], "blocked": []}

    friends, pending, blocked = [], [], []
    for rel in data:
        rel_type = rel.get("type")
        user = rel.get("user")
        if not user:
            continue
        uid = str(user.get("id") or "")
        presence_status = "offline"
        with _presences_lock:
            p = _presences.get(uid)
            if p:
                presence_status = p.get("status")
        item = {
            "id": uid,
            "username": user.get("username") or "Unknown",
            "displayName": get_user_active_alias(uid) or user.get("global_name") or user.get("username") or "Unknown",
            "avatarUrl": avatar_url_for_user(user),
            "avatarDecorationUrl": avatar_decoration_url_for_user(user),
            "status": presence_status,
            "type": rel_type,
        }
        if rel_type == 1:
            friends.append(item)
        elif rel_type == 2:
            blocked.append(item)
        elif rel_type == 3:
            pending.append(item)
        elif rel_type == 4:
            pending.append(item)
    return {"friends": friends, "pending": pending, "blocked": blocked}

def accept_friend_request(token: str, user_id: str, record_safety: bool = True) -> tuple[bool, str | None]:
    try:
        discord_api_request(
            f"/users/@me/relationships/{user_id}",
            token,
            method="PUT",
            json_data={"type": 1},
            record_safety=record_safety
        )
        return True, None
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "expired_session"
        if e.code == 404:
            return False, "relationship_not_found"
        if e.code == 400:
            return False, "bad_request"
        return False, f"discord_error_{e.code}"

# send_friend_request
def send_friend_request(
    token: str,
    username: str,
    discriminator: str | None = None,
    captcha: str | None = None,
    rqtoken: str | None = None,
    record_safety: bool = True,
) -> tuple[bool, dict | None, str | None]:
    if not username:
        return False, None, "missing_username"

    extra_headers = {}
    if captcha and rqtoken:
        extra_headers["x-captcha-key"] = captcha
        extra_headers["x-captcha-rqtoken"] = rqtoken

    path = "/users/@me/relationships"
    json_data = {"username": username}

    try:
        discord_api_request(path, token, method="POST", json_data=json_data, extra_headers=extra_headers)
        return True, None, None
    except urllib.error.HTTPError as e:
        logger.error(e)
        if e.code == 401:
            return False, None, "expired_session"
        if e.code == 404:
            return False, None, "user_not_found"
        if e.code == 429:
            return False, None, "rate_limited"
        if e.code == 400:
            try:
                data = json.loads(e.msg)
            except Exception:
                return False, None, "bad_request"
            if data.get("captcha_key"):
                rqtoken_header = e.headers.get("x-captcha-rqtoken") if e.headers else None
                return (
                    False,
                    {
                        "captcha_required": True,
                        "rqtoken": rqtoken_header or data.get("captcha_rqtoken"),
                        "captcha_sitekey": data.get("captcha_sitekey"),
                        "captcha_rqdata": data.get("captcha_rqdata"),
                        "captcha_service": data.get("captcha_service") or "hcaptcha",
                    },
                    "captcha_required",
                )
            return False, None, "bad_request"
        if e.code == 403:
            return False, None, "permission_denied"
        return False, None, f"discord_error_{e.code}"


# call_nop4_sync
def call_nop4_sync(captcha_data: dict, proxy: bool = False) -> str:
    url = "http://localhost:3002/register-captcha"
    payload = {"captcha_data": captcha_data, "proxy": proxy}
    try:
        resp = requests.post(url, json=payload, timeout=300)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Failed to reach nop4: {e}")
    if resp.status_code != 200:
        raise RuntimeError(f"nop4 returned {resp.status_code}: {resp.text}")
    data = resp.json()
    token = data.get("response")
    if not token:
        raise RuntimeError("nop4 did not return a token")
    return token

def _background_poller():
    while True:
        time.sleep(5)
        force = False
        _presence_dirty = True
        if _presence_dirty:
            _presence_dirty = False
            force = True
        for sid, token in list(_sid_to_token.items()):
            auth_id = _token_to_auth.get(token, "")
            cursors = db.get_document(db_conn, "read_cursors", auth_id)
            try:
                friends = get_relationships(token, record_safety=False)
                if force or friends != _last_friends.get(token):
                    _last_friends[token] = friends
                    socketio.emit("friends_update", friends, room=sid)

                channels = (
                    discord_api_request("/users/@me/channels", token, method="GET", record_safety=False) or []
                )
                dm_channels = [c for c in channels if c.get("type") in (1, 3)]
                dm_channels.sort(key=lambda c: c.get("last_message_id") or "", reverse=True)
                items = []
                if auth_id:
                    cursor_row = db.get_document(db_conn, "read_cursors", auth_id)
                    if cursor_row is None:
                        db.add_document(db_conn, "read_cursors", auth_id, {})
                        user_cursors = {}
                    else:
                        user_cursors = cursor_row
                else:
                    user_cursors = {}
                cursors_changed = False
                for c in dm_channels:
                    item = dm_payload(c)
                    ch_id = item.get("id", "")
                    last_msg = item.get("lastMessageId", "")
                    if auth_id and ch_id and last_msg:
                        if ch_id not in user_cursors:
                            user_cursors[ch_id] = last_msg
                            cursors_changed = True
                        item["unread"] = bool(user_cursors.get(ch_id) != last_msg)
                    else:
                        item["unread"] = False
                    items.append(item)
                if cursors_changed:
                    db.add_document(db_conn, "read_cursors", auth_id, user_cursors)
                dms_payload = {
                    "items": items,
                    "reason": "",
                }
                prev_dms_payload = _last_dms.get(token)
                if force or dms_payload != _last_dms.get(token):
                    _last_dms[token] = dms_payload
                    socketio.emit("dms_update", dms_payload, room=sid)
                prev_items_by_id = {}
                if isinstance(prev_dms_payload, dict):
                    for prev_item in prev_dms_payload.get("items", []):
                        prev_id = str(prev_item.get("id") or "")
                        if prev_id:
                            prev_items_by_id[prev_id] = str(prev_item.get("lastMessageId") or "")
                notif_cache = _last_notified_dm_message.setdefault(token, {})
                own_user_id = _current_user_id_for_token(token)
                for item in items:
                    channel_id = str(item.get("id") or "")
                    latest_message_id = str(item.get("lastMessageId") or "")
                    if not channel_id or not latest_message_id:
                        continue
                    previous_message_id = prev_items_by_id.get(channel_id, "")
                    if not previous_message_id:
                        notif_cache[channel_id] = latest_message_id
                        continue
                    if latest_message_id == previous_message_id:
                        continue
                    if notif_cache.get(channel_id) == latest_message_id:
                        continue
                    try:
                        latest_messages = discord_api_request(
                            f"/channels/{channel_id}/messages?limit=1",
                            token,
                            method="GET",
                            record_safety=False,
                        ) or []
                    except (urllib.error.HTTPError, urllib.error.URLError):
                        continue
                    if not isinstance(latest_messages, list) or not latest_messages:
                        continue
                    latest_message = latest_messages[0]
                    incoming_message_id = str(latest_message.get("id") or "")
                    if incoming_message_id != latest_message_id:
                        continue
                    author_id = str((latest_message.get("author") or {}).get("id") or "")
                    if own_user_id and author_id == own_user_id:
                        notif_cache[channel_id] = latest_message_id
                        continue
                    socketio.emit(
                        "notification",
                        _build_notification_payload(channel_id, latest_message),
                        room=sid,
                    )
                    notif_cache[channel_id] = latest_message_id

            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    error_message = "Session expired"
                    try:
                        detail = json.loads(e.reason or "")
                        if isinstance(detail, dict) and detail.get("message"):
                            error_message = detail["message"]
                    except Exception:
                        pass
                    socketio.emit("session_expired", {"message": error_message}, room=sid)
                    _sid_to_token.pop(sid, None)
                    _last_notified_dm_message.pop(token, None)
                    for aid, onliner in list(_onliners.items()):
                        if onliner.token == token:
                            onliner.stop()
                            _onliners.pop(aid, None)
                            break
                    disconnect(sid)
                continue
            except Exception:
                continue

@socketio.on("connect")
def handle_connect(auth=None):
    cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
    raw_cookie = request.cookies.get(cookie_name)
    if not raw_cookie:
        disconnect()
        return
    sess_data = _unsign_session(raw_cookie)
    auth_id = sess_data.get("auth_id")
    if not auth_id:
        disconnect()
        return
    token = db.get_document(db_conn, "tokens", auth_id)
    if not token:
        disconnect()
        return
    _sid_to_token[request.sid] = token
    _sid_to_auth[request.sid] = auth_id
    _record_activity(auth_id, True)
    onliner = _onliners.get(auth_id)
    if onliner and onliner.user_id:
        with _presences_lock:
            p = _presences.get(onliner.user_id)
        if p and p.get("status"):
            socketio.emit("presence_update", {"userId": onliner.user_id, "status": p["status"]}, room=request.sid)
    logger.debug(
        f"Connected {Beach.FOAM}→{Style.RESET_ALL} "
        f"{Beach.OCEAN}{request.sid}{Style.RESET_ALL} for auth "
        f"{Beach.OCEAN}{auth_id}{Style.RESET_ALL}"
    )

@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    auth_id = _sid_to_auth.pop(sid, None)
    _sid_to_token.pop(sid, None)
    if auth_id:
        _record_activity(auth_id, False)
    logger.debug(
        f"Disconnected {Beach.FOAM}→{Style.RESET_ALL} "
        f"{Beach.OCEAN}{sid}{Style.RESET_ALL}"
    )



LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Fern</title>
  <link rel="icon" href="/static/assets/favicon.ico">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg: #101a12;
      --card: #16261a;
      --input: #0e1710;
      --input-focus: #132216;
      --border: rgba(255, 255, 255, 0.14);
      --border-hover: rgba(255, 255, 255, 0.28);
      --green: #23a55a;
      --green-hover: #1e8e4c;
      --text: #e6eee7;
      --text-muted: #8fa290;
      --text-dim: #5c705e;
      --danger: #f87171;
      --danger-bg: rgba(239, 68, 68, 0.1);
      --danger-border: rgba(239, 68, 68, 0.22);
    }

    html, body {
      width: 100%;
      height: 100%;
      background: var(--bg);
      background-image: radial-gradient(850px circle at 50% 22%, #1b3021 0%, #142217 50%, var(--bg) 100%);
      color: var(--text);
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      -webkit-font-smoothing: antialiased;
      overflow-x: hidden;
    }

    body {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 24px;
      position: relative;
    }

    .page-fade {
      position: fixed; inset: 0; z-index: 9999; pointer-events: none;
      background: var(--bg); opacity: 0; transition: opacity 0.18s ease;
    }
    .page-fade.active { opacity: 1; pointer-events: auto; }

    .login-container {
      width: 100%;
      max-width: 620px;
      position: relative;
      z-index: 1;
    }

    .login-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 28px 28px 20px;
      box-shadow: 0 16px 36px rgba(0, 0, 0, 0.4);
    }

    .auth-body {
      display: flex;
      gap: 28px;
      align-items: center;
    }

    .col-auth {
      flex: 1;
      min-width: 0;
    }

    .col-qr {
      flex: 0 0 200px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
    }

    .auth-divider {
      width: 1px;
      align-self: stretch;
      background: var(--border);
      flex-shrink: 0;
    }

    .brand-header {
      margin-bottom: 18px;
    }

    .brand-title {
      font-size: 19px;
      font-weight: 600;
      letter-spacing: -0.3px;
      color: #ffffff;
      margin-bottom: 3px;
    }

    .brand-subtitle {
      font-size: 12.5px;
      color: var(--text-muted);
      font-weight: 400;
      line-height: 1.4;
    }

    .qr-title {
      font-size: 15px;
      font-weight: 600;
      letter-spacing: -0.2px;
      color: #ffffff;
      margin-bottom: 3px;
    }

    .qr-subtitle {
      font-size: 11px;
      color: var(--text-muted);
      margin-bottom: 12px;
      line-height: 1.35;
      max-width: 185px;
    }

    .qr-box {
      width: 160px;
      height: 160px;
      margin: 0 auto 8px;
      background: var(--input);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      position: relative;
      overflow: hidden;
      cursor: pointer;
    }

    .qr-box img#qr-image {
      width: 100%;
      height: 100%;
      border-radius: 8px;
      display: block;
    }

    .qr-loading {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 8px;
      color: var(--text-muted);
      font-size: 11px;
    }

    .qr-loading i {
      font-size: 18px;
      color: var(--green);
    }

    .qr-scanned-card {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 6px;
    }

    .qr-avatar {
      width: 52px;
      height: 52px;
      border-radius: 50%;
      border: 2px solid var(--green);
    }

    .qr-username {
      font-size: 12.5px;
      font-weight: 600;
      color: #ffffff;
    }

    .qr-status-text {
      text-align: center;
      font-size: 11px;
      color: var(--text-muted);
      line-height: 1.4;
      min-height: 15px;
    }

    .qr-status-text.highlight {
      color: #86efac;
      font-weight: 500;
    }

    .qr-status-text.success {
      color: #4ade80;
      font-weight: 500;
    }

    .qr-status-text.error {
      color: var(--danger);
    }

    .login-error {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--danger);
      background: var(--danger-bg);
      border: 1px solid var(--danger-border);
      padding: 8px 11px;
      border-radius: 8px;
      font-size: 11.5px;
      font-weight: 500;
      margin-bottom: 14px;
    }

    .login-error i {
      font-size: 12px;
      flex-shrink: 0;
    }

    .field-group {
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-bottom: 16px;
    }

    .input-wrap {
      position: relative;
      display: flex;
      align-items: center;
    }

    .field-input {
      width: 100%;
      background: var(--input);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10.5px 36px 10.5px 12px;
      color: #ffffff;
      font-size: 13px;
      font-family: inherit;
      outline: none;
      transition: border-color 0.15s ease, background 0.15s ease, box-shadow 0.15s ease;
    }

    .field-input::placeholder {
      color: var(--text-dim);
      font-size: 12.5px;
    }

    .field-input:hover {
      border-color: var(--border-hover);
    }

    .field-input:focus {
      border-color: var(--green);
      background: var(--input-focus);
      box-shadow: 0 0 0 2px rgba(35, 165, 90, 0.16);
    }

    .field-input.is-invalid {
      border-color: var(--danger);
    }

    .input-action-btn, .input-status {
      position: absolute;
      right: 8px;
      background: transparent;
      border: none;
      color: var(--text-dim);
      font-size: 12.5px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 24px;
      height: 24px;
      border-radius: 4px;
      transition: color 0.15s ease;
    }

    .input-action-btn:hover {
      color: #ffffff;
    }

    .input-status {
      pointer-events: none;
    }

    .input-status.valid { color: #4ade80; }
    .input-status.invalid { color: var(--danger); }
    .input-status.spinner { color: var(--text-muted); font-size: 11px; }

    .submit-btn {
      width: 100%;
      background: var(--green);
      color: #ffffff;
      border: none;
      border-radius: 8px;
      padding: 11px 16px;
      font-size: 13px;
      font-weight: 600;
      font-family: inherit;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      transition: background 0.15s ease;
    }

    .submit-btn:hover:not(:disabled) {
      background: var(--green-hover);
    }

    .submit-btn:active:not(:disabled) {
      transform: scale(0.99);
    }

    .submit-btn.loading {
      opacity: 0.8;
      cursor: not-allowed;
    }

    .card-footer {
      display: flex;
      align-items: center;
      justify-content: center;
      margin-top: 18px;
      padding-top: 12px;
      border-top: 1px solid rgba(255, 255, 255, 0.04);
    }

    .back-link {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 11.5px;
      color: var(--text-dim);
      text-decoration: none;
      transition: color 0.15s ease;
    }

    .back-link:hover {
      color: var(--text-muted);
    }

    @media (max-width: 640px) {
      .login-container { max-width: 350px; }
      .auth-body { flex-direction: column-reverse; gap: 20px; }
      .auth-divider { width: 100%; height: 1px; }
      .col-qr { flex: auto; width: 100%; }
    }
  </style>
</head>
<body>
  <div class="page-fade" id="page-fade"></div>

  <main class="login-container">
    <div class="login-card">
      <div class="auth-body">
        <div class="col-auth">
          <div class="brand-header">
            <h1 class="brand-title">Welcome Back!</h1>
            <p class="brand-subtitle">Enter your user token (or scan via mobile app)</p>
          </div>

          {% if error %}
          <div class="login-error">
            <i class="fa-solid fa-circle-exclamation"></i>
            <span>{{ error }}</span>
          </div>
          {% endif %}

          <form method="post">
            {% if next_path %}
            <input type="hidden" name="next" value="{{ next_path }}">
            {% endif %}
            
            <div class="field-group">
              <div class="input-wrap">
                <input id="bot_token_input" name="bot_token" type="password" class="field-input" placeholder="Discord User Token" autocomplete="off" spellcheck="false" required />
                <button type="button" class="input-action-btn" id="toggle-token-btn" title="Toggle visibility">
                  <i class="fa-regular fa-eye" id="token-eye-icon"></i>
                </button>
              </div>

              <div class="input-wrap">
                <input id="license_key_input" name="license_key" type="text" class="field-input" placeholder="License Key" autocomplete="off" spellcheck="false" />
                <span id="license-status" class="input-status"></span>
              </div>
            </div>

            <button type="submit" class="submit-btn" id="submit-btn">
              <span>Continue</span>
              <i class="fa-solid fa-arrow-right"></i>
            </button>
          </form>
        </div>

        <div class="auth-divider"></div>

        <div class="col-qr">
          <h2 class="qr-title">QR Code</h2>
          <p class="qr-subtitle">Log in instantly without sharing your user token (more safe)</p>
          <div class="qr-box" id="qr-box">
            <div class="qr-loading" id="qr-loading">
              <i class="fa-solid fa-circle-notch fa-spin"></i>
              <span>Generating...</span>
            </div>
            <img id="qr-image" src="" alt="Discord QR Code" style="display:none;" />
            <div id="qr-scanned" class="qr-scanned-card" style="display:none;">
              <img id="qr-avatar" class="qr-avatar" src="" alt="Avatar" />
              <span id="qr-username" class="qr-username"></span>
            </div>
          </div>
          <div id="qr-status" class="qr-status-text">Scan with Discord mobile app</div>
        </div>
      </div>

      <div class="card-footer">
        <a href="/" class="back-link" id="back-link">
          <i class="fa-solid fa-arrow-left"></i>
          <span>Back to Home</span>
        </a>
      </div>
    </div>
  </main>

  <script src="/static/js/bundles/login.bundle.js?v=6"></script>
</body>
</html>"""

APP_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fern</title>
<link rel="icon" href="/static/assets/favicon.ico">
<link rel="stylesheet" href="/static/css/app.css?v=227">
<style>
  .page-fade {
    position: fixed; inset: 0; z-index: 10000; pointer-events: none;
    background: #0c130d; opacity: 0; transition: opacity 0.32s ease;
  }
  .page-fade.active { opacity: 1; pointer-events: auto; }
</style>
</head>
<body>
<div class="page-fade" id="page-fade"></div>
<div id="app"></div>
<div id="confirm-modal" class="modal-overlay" style="display:none">
  <div class="modal-box">
    <h2 id="modal-title">Remove Friend</h2>
    <p id="modal-text">Are you sure?</p>
    <div class="modal-actions">
      <button id="modal-cancel" class="modal-btn modal-cancel">Cancel</button>
      <button id="modal-confirm" class="modal-btn modal-danger">Remove Friend</button>
    </div>
  </div>
</div>
<script src="https://static.sellauth.com/embed/v3.min.js" defer></script>
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@twemoji/api@latest/dist/twemoji.min.js" crossorigin="anonymous"></script>
<script>window.FERN_BACKEND_URL = "{{ backend_url }}"; window.FERN_LICENCE = "{{ licence }}";</script>
<script src="/static/js/bundles/voice.bundle.js?v=194"></script>
<script src="/static/js/bundles/app.bundle.js?v=237"></script>
</body>
</html>"""

LANDING_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fern</title>
<meta name="description" content="Fern is a privacy-focused, end-to-end encrypted Discord client. Pay once, own forever.">
<link rel="icon" href="/static/assets/favicon.ico">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0c130d;
    --bg-card: #121f14;
    --bg-card-hover: #162619;
    --border: #1d2e20;
    --green: #23a55a;
    --green-hover: #1e8b4c;
    --text: #e9f0ea;
    --text-2: #a8b8a9;
    --text-3: #5d6e5e;
    --max-w: 1200px;
  }
  html { overflow-x: clip; scroll-behavior: smooth; scroll-padding-top: 90px; }
  body {
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
    overflow-x: clip;
    position: relative;
    isolation: isolate;
  }
  body::before {
    content: ''; position: fixed; inset: 0; z-index: -2; pointer-events: none;
    background:
      radial-gradient(circle at 18% 12%, rgba(35,165,90,0.09), transparent 28%),
      radial-gradient(circle at 82% 34%, rgba(45,212,191,0.07), transparent 26%),
      linear-gradient(135deg, #0a110c 0%, var(--bg) 48%, #09140e 100%);
  }
  a { color: inherit; text-decoration: none; }
  img { max-width: 100%; display: block; }
  .wrap { max-width: var(--max-w); margin: 0 auto; padding: 0 24px; }

  .page-fade {
    position: fixed; inset: 0; z-index: 1000; pointer-events: none;
    background: var(--bg); opacity: 0; transition: opacity 0.35s ease;
  }
  .page-fade.active { opacity: 1; }
  .page-fade.show { pointer-events: auto; }

  .glow {
    position: absolute; border-radius: 50%; filter: blur(120px); opacity: 0.5;
    pointer-events: none; z-index: 0;
  }
  .glow-green { background: rgba(35,165,90,0.35); }
  .glow-teal { background: rgba(45,212,191,0.25); }
  .glow-dim { background: rgba(20,80,50,0.4); }

  #particle-field {
    position: fixed; inset: 0; width: 100%; height: 100%; z-index: -1;
    pointer-events: none; opacity: 0.72;
  }
  .glass {
    background:
      linear-gradient(135deg, rgba(255,255,255,0.13), rgba(255,255,255,0.035) 48%, rgba(35,165,90,0.07)),
      rgba(15,29,19,0.44);
    border: 1px solid rgba(255,255,255,0.18);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.2), inset 0 -1px 0 rgba(255,255,255,0.035), 0 18px 50px rgba(0,0,0,0.25);
    backdrop-filter: blur(28px) saturate(185%) brightness(1.08);
    -webkit-backdrop-filter: blur(28px) saturate(185%) brightness(1.08);
  }

  nav {
    position: fixed; top: 16px; left: 50%; transform: translateX(-50%); z-index: 100;
    max-width: calc(100% - 24px); width: max-content;
    background: linear-gradient(135deg, rgba(255,255,255,0.14), rgba(255,255,255,0.035)), rgba(12,25,16,0.6);
    backdrop-filter: blur(30px) saturate(190%) brightness(1.08);
    -webkit-backdrop-filter: blur(30px) saturate(190%) brightness(1.08);
    border: 1px solid rgba(255,255,255,0.19);
    border-radius: 100px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.22), inset 0 -1px 0 rgba(255,255,255,0.04), 0 12px 36px rgba(0,0,0,0.32);
    transition: background 0.4s, box-shadow 0.4s;
  }
  nav .inner {
    display: flex; align-items: center; justify-content: center;
    height: 48px; padding: 0 12px 0 16px;
    transition: padding 0.45s cubic-bezier(0.4, 0, 0.2, 1);
  }
  nav .brand {
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; padding: 4px 6px; border-radius: 8px;
    transition: opacity 0.2s, transform 0.2s;
  }
  nav .brand:hover { opacity: 0.85; transform: scale(1.04); }
  nav .brand img {
    height: 22px; width: auto; max-width: 52px; object-fit: contain; display: block;
  }
  nav .links {
    display: flex; align-items: center; gap: 4px; margin: 0 16px; overflow: hidden;
    max-width: 440px; opacity: 1; pointer-events: auto;
    transition: max-width 0.45s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.35s cubic-bezier(0.4, 0, 0.2, 1), margin 0.45s cubic-bezier(0.4, 0, 0.2, 1);
  }
  nav .links a {
    color: var(--text-2); font-size: 13.5px; font-weight: 500;
    padding: 6px 14px; border-radius: 100px;
    transition: color 0.15s, background 0.15s; white-space: nowrap;
  }
  nav .links a:hover { color: var(--text); background: rgba(255,255,255,0.08); }
  nav .nav-right {
    display: flex; align-items: center; gap: 8px; flex-shrink: 0; margin-left: 0;
    max-width: 120px; opacity: 1; pointer-events: auto;
    transition: max-width 0.45s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.35s cubic-bezier(0.4, 0, 0.2, 1), margin 0.45s cubic-bezier(0.4, 0, 0.2, 1);
  }
  nav .nav-icon-btn {
    width: 32px; height: 32px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    background: var(--green); color: #fff;
    border: 1px solid rgba(255, 255, 255, 0.18);
    font-size: 13px; text-decoration: none; flex-shrink: 0;
    box-shadow: 0 2px 10px rgba(35, 165, 90, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.22);
    transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
    cursor: pointer;
  }
  nav .nav-icon-btn:hover {
    background: var(--green); color: #fff; transform: translateY(-1px) scale(1.05);
    box-shadow: 0 0 14px rgba(35, 165, 90, 0.6), 0 3px 10px rgba(0, 0, 0, 0.35);
  }
  nav .nav-icon-btn:active {
    transform: translateY(0) scale(0.95);
  }
  nav.shrunk {
    background: linear-gradient(135deg, rgba(255,255,255,0.16), rgba(255,255,255,0.04)), rgba(9,19,12,0.85);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.22), 0 16px 40px rgba(0,0,0,0.5);
  }
  nav.shrunk .inner {
    padding: 0 16px;
  }
  nav.shrunk .links {
    max-width: 0; margin: 0; opacity: 0; pointer-events: none;
  }
  nav.shrunk .nav-right {
    max-width: 0; margin: 0; opacity: 0; pointer-events: none; overflow: hidden;
  }
  nav.shrunk:hover .inner,
  nav.shrunk:focus-within .inner {
    padding: 0 12px 0 16px;
  }
  nav.shrunk:hover .links,
  nav.shrunk:focus-within .links {
    max-width: 440px; margin: 0 16px; opacity: 1; pointer-events: auto;
  }
  nav.shrunk:hover .nav-right,
  nav.shrunk:focus-within .nav-right {
    max-width: 120px; opacity: 1; pointer-events: auto; overflow: visible;
  }

  .hero {
    position: relative; padding: 152px 24px 28px; text-align: center;
  }
  .cipher-field { position: absolute; inset: 76px 0 auto; height: 440px; overflow: hidden; pointer-events: none; z-index: 0; opacity: 0.6; }
  .cipher-stream {
    position: absolute; top: -60px; color: rgba(90,225,145,0.27); font: 500 10px/1.8 ui-monospace, SFMono-Regular, Consolas, monospace;
    letter-spacing: 0.15em; writing-mode: vertical-rl; text-orientation: upright; white-space: nowrap;
    filter: drop-shadow(0 0 7px rgba(35,165,90,0.28)); animation: cipherFall linear infinite;
  }
  .cipher-stream:nth-child(1) { left: 8%; animation-duration: 13s; animation-delay: -4s; }
  .cipher-stream:nth-child(2) { left: 21%; animation-duration: 18s; animation-delay: -12s; opacity: 0.55; }
  .cipher-stream:nth-child(3) { right: 18%; animation-duration: 15s; animation-delay: -8s; }
  .cipher-stream:nth-child(4) { right: 6%; animation-duration: 20s; animation-delay: -15s; opacity: 0.48; }
  @keyframes cipherFall { from { transform: translateY(-100%); } to { transform: translateY(520px); } }
  .hero .glow-green { width: 500px; height: 500px; top: -160px; left: 50%; transform: translateX(-50%); }
  .hero .glow-teal { width: 420px; height: 420px; top: 120px; right: -180px; }
  .hero .glow-dim { width: 360px; height: 360px; bottom: -120px; left: -120px; }
  .hero .badge {
    display: inline-flex; align-items: center; gap: 8px;
    background: rgba(35,165,90,0.12); border: 1px solid rgba(35,165,90,0.3);
    color: #6fdf9e; font-size: 13px; font-weight: 600;
    padding: 7px 16px; border-radius: 100px; margin-bottom: 28px;
    position: relative; z-index: 1;
  }
  .hero .badge .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); animation: pulse 2s infinite; }
  .hero .hero-title {
    position: relative; display: inline-block; margin-bottom: 20px; z-index: 1;
  }
  .hero .hero-title:has(.fern-wrap:hover) { z-index: 2; }
  .hero .fern-wrap {
    position: absolute; left: 90%; top: 50%; transform: translate(-100%, -50%);
    z-index: 0; pointer-events: none;
    animation: fernFloat 6s ease-in-out infinite;
  }
  .hero .fern-wrap:hover { animation-play-state: paused; z-index: 2; }
  .hero .fern-wrap:hover .fern-float { opacity: 1; z-index: 2; scale: 1.45; }
  .hero .fern-float {
    display: block; width: 510px; max-width: none; opacity: 0.22; cursor: pointer; pointer-events: auto;
    scale: 1;
    transition: opacity 0.4s ease, scale 0.55s cubic-bezier(0.22, 1, 0.36, 1);
  }
  @keyframes fernFloat {
    0%, 100% { transform: translate(-115%, -52%); }
    50% { transform: translate(-115%, -48%); }
  }
  .hero h1 {
    font-size: 64px; font-weight: 900; letter-spacing: -0.035em; line-height: 1.05;
    margin-bottom: 0; position: relative; z-index: 1;
  }
  .hero h1 .accent {
    background: linear-gradient(135deg, #34d26f 0%, #2dd4bf 100%);
    -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
  }
  .hero p { font-size: 18px; color: var(--text-2); max-width: 620px; margin: 0 auto 26px; position: relative; z-index: 1; }
  .hero .cta { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; position: relative; z-index: 1; }
  .hero .hero-img { position: relative; z-index: 1; margin: 34px auto 0; max-width: 1040px; width: 100%; }
  .hero .hero-img::before {
    content: ''; position: absolute; inset: -1px; z-index: 2; pointer-events: none; border-radius: 19px;
    background: linear-gradient(110deg, transparent 20%, rgba(111,223,158,0.3) 46%, rgba(45,212,191,0.12) 54%, transparent 80%);
    background-size: 240% 100%; animation: encryptedScan 6s ease-in-out infinite;
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    -webkit-mask-composite: xor; mask-composite: exclude; padding: 1px;
  }
  .encryption-status {
    position: absolute; left: 50%; top: 18px; transform: translateX(-50%); z-index: 3;
    display: flex; align-items: center; gap: 9px; padding: 8px 13px; border-radius: 100px;
    color: #b9eaca; font: 600 11px/1 'Inter', sans-serif; letter-spacing: 0.04em; white-space: nowrap;
  }
  .encryption-status i { color: #55d98b; filter: drop-shadow(0 0 6px rgba(85,217,139,0.6)); animation: lockPulse 2.4s ease-in-out infinite; }
  .encryption-status .signal { display: flex; gap: 3px; }
  .encryption-status .signal b { width: 3px; height: 3px; border-radius: 50%; background: #55d98b; animation: signalRun 1.5s ease-in-out infinite; }
  .encryption-status .signal b:nth-child(2) { animation-delay: 0.18s; }
  .encryption-status .signal b:nth-child(3) { animation-delay: 0.36s; }
  @keyframes encryptedScan { 0%, 18% { background-position: 180% 0; } 72%, 100% { background-position: -80% 0; } }
  @keyframes lockPulse { 0%,100% { opacity: 0.72; transform: scale(0.96); } 50% { opacity: 1; transform: scale(1.08); } }
  @keyframes signalRun { 0%,100% { opacity: 0.22; transform: scale(0.7); } 50% { opacity: 1; transform: scale(1); } }
  .hero .hero-img img {
    width: 100%; display: block; border-radius: 18px;
    box-shadow: 0 24px 80px rgba(0,0,0,0.55), 0 0 60px rgba(35,165,90,0.15);
    -webkit-mask-image: linear-gradient(to bottom, #000 62%, transparent 100%);
    mask-image: linear-gradient(to bottom, #000 62%, transparent 100%);
  }
  .hero .stats { display: none; }

  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }

  .btn {
    border: none; border-radius: 10px; padding: 13px 30px; font-size: 15px;
    font-weight: 600; cursor: pointer; transition: all 0.2s; font-family: inherit;
  }
  .btn-green { background: var(--green); color: #fff; }
  .btn-green:hover { background: var(--green-hover); transform: translateY(-1px); }
  .btn-out {
    background: linear-gradient(135deg, rgba(255,255,255,0.12), rgba(255,255,255,0.035));
    color: var(--text); border: 1px solid rgba(255,255,255,0.17);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.16), 0 8px 24px rgba(0,0,0,0.16);
    backdrop-filter: blur(20px) saturate(175%); -webkit-backdrop-filter: blur(20px) saturate(175%);
  }
  .btn-out:hover { border-color: var(--green); background: rgba(35,165,90,0.08); }

  .section { padding: 48px 0; position: relative; }
  .section-t { text-align: center; margin-bottom: 24px; }
  .section-t .eyebrow { color: var(--green); font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
  .section-t h2 { font-size: 38px; font-weight: 800; letter-spacing: -0.02em; margin-bottom: 10px; }
  .section-t p { color: var(--text-2); font-size: 16px; max-width: 640px; margin: 0 auto; }

  #features { position: relative; }
  #features .glow-dim { width: 500px; height: 500px; top: 100px; left: -200px; opacity: 0.3; }
  .feat-grid {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px;
    position: relative; z-index: 1;
  }
  .feat-card {
    background: linear-gradient(135deg, rgba(255,255,255,0.14), rgba(255,255,255,0.025) 48%, rgba(35,165,90,0.045)), rgba(7,18,11,0.34);
    border: 1px solid rgba(255,255,255,0.19); border-radius: 20px;
    padding: 26px 24px; position: relative; overflow: hidden;
    transition: border-color 0.25s, background 0.25s, box-shadow 0.25s, transform 0.25s;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.2), inset 1px 0 0 rgba(255,255,255,0.07), inset 0 -1px 0 rgba(255,255,255,0.035), 0 20px 50px rgba(0,0,0,0.2);
    backdrop-filter: blur(34px) saturate(190%) brightness(1.07);
    -webkit-backdrop-filter: blur(34px) saturate(190%) brightness(1.07);
  }
  .feat-card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, transparent, rgba(35,165,90,0.6), transparent);
    opacity: 0; transition: opacity 0.25s;
  }
  .feat-card:hover {
    background: linear-gradient(135deg, rgba(255,255,255,0.17), rgba(255,255,255,0.04) 48%, rgba(35,165,90,0.07)), rgba(8,21,12,0.38);
    border-color: rgba(255,255,255,0.27);
    transform: translateY(-3px);
  }
  .feat-card:hover::before { opacity: 1; }
  .feat-card .feat-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
  .feat-card .feat-head .ico {
    width: 40px; height: 40px; border-radius: 10px; flex-shrink: 0;
    background: rgba(35,165,90,0.12); color: var(--green);
    display: flex; align-items: center; justify-content: center; font-size: 17px;
  }
  .feat-card .feat-head h3 { font-size: 16px; font-weight: 600; }
  .feat-card p { color: var(--text-2); font-size: 14px; line-height: 1.6; }

  .showcase-box {
    background: linear-gradient(135deg, rgba(255,255,255,0.12), rgba(255,255,255,0.03) 52%, rgba(45,212,191,0.055)), rgba(13,28,17,0.4);
    border: 1px solid rgba(255,255,255,0.17); border-radius: 24px;
    aspect-ratio: 16 / 7.5; display: flex; align-items: center; justify-content: center;
    color: var(--text-3); gap: 10px; font-size: 14px; position: relative; overflow: hidden;
    transition: border-color 0.25s; box-shadow: inset 0 1px 0 rgba(255,255,255,0.2), inset 1px 0 0 rgba(255,255,255,0.06), 0 24px 60px rgba(0,0,0,0.22);
    backdrop-filter: blur(30px) saturate(190%) brightness(1.06); -webkit-backdrop-filter: blur(30px) saturate(190%) brightness(1.06);
  }
  .showcase-box:hover { border-color: rgba(42,64,48,0.8); }
  .showcase-box::before {
    content: ''; position: absolute; inset: 0;
    background: radial-gradient(ellipse at center, rgba(35,165,90,0.08), transparent 70%);
  }
  .showcase-box i { font-size: 26px; color: var(--green); }
  .showcase-box .play { position: relative; z-index: 1; display: flex; flex-direction: column; align-items: center; gap: 10px; cursor: pointer; }

  #chat-control { position: relative; }
  #chat-control .glow-green { width: 460px; height: 460px; top: -100px; right: -140px; opacity: 0.35; }
  #chat-control .glow-top { width: 520px; height: 320px; top: -180px; left: 50%; transform: translateX(-50%); opacity: 0.3; }
  .cc-banner {
    background: linear-gradient(135deg, rgba(255,255,255,0.12), rgba(255,255,255,0.035) 50%, rgba(35,165,90,0.06)), rgba(13,28,17,0.4);
    border: 1px solid rgba(255,255,255,0.17); border-radius: 20px;
    padding: 24px 32px; display: flex; align-items: center; gap: 20px;
    position: relative; z-index: 1; flex-wrap: wrap; overflow: hidden; margin-top: 24px;
    transition: all 0.25s; box-shadow: inset 0 1px 0 rgba(255,255,255,0.2), inset 1px 0 0 rgba(255,255,255,0.06), 0 22px 56px rgba(0,0,0,0.21);
    backdrop-filter: blur(28px) saturate(185%) brightness(1.06); -webkit-backdrop-filter: blur(28px) saturate(185%) brightness(1.06);
  }
  .cc-banner::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, transparent, rgba(35,165,90,0.6), transparent);
    opacity: 0; transition: opacity 0.25s;
  }
  .cc-banner:hover { background: linear-gradient(135deg, rgba(255,255,255,0.16), rgba(255,255,255,0.05), rgba(35,165,90,0.09)), rgba(14,31,19,0.46); border-color: rgba(255,255,255,0.25); transform: translateY(-3px); }
  .cc-banner:hover::before { opacity: 1; }
  .cc-banner .cc-icon {
    width: 40px; height: 40px; border-radius: 10px; flex-shrink: 0;
    background: rgba(35,165,90,0.12); color: var(--green);
    display: flex; align-items: center; justify-content: center; font-size: 17px;
  }
  .cc-banner .cc-body { flex: 1; min-width: 260px; }
  .cc-banner .cc-body h3 { font-size: 16px; font-weight: 600; margin-bottom: 6px; }
  .cc-banner .cc-body p { color: var(--text-2); font-size: 14px; max-width: 640px; }

  #trial { position: relative; }
  .trial-card {
    background: linear-gradient(135deg, rgba(255,255,255,0.14), rgba(255,255,255,0.025) 48%, rgba(35,165,90,0.045)), rgba(7,18,11,0.34);
    border: 1px solid rgba(255,255,255,0.19);
    border-radius: 20px;
    padding: 28px 36px;
    max-width: 880px;
    margin: 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
    position: relative;
    overflow: hidden;
    transition: all 0.25s;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.2), inset 1px 0 0 rgba(255,255,255,0.07), inset 0 -1px 0 rgba(255,255,255,0.035), 0 20px 50px rgba(0,0,0,0.2);
    backdrop-filter: blur(34px) saturate(190%) brightness(1.07);
    -webkit-backdrop-filter: blur(34px) saturate(190%) brightness(1.07);
  }
  .trial-card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, transparent, rgba(35,165,90,0.6), transparent);
    opacity: 0; transition: opacity 0.25s;
  }
  .trial-card:hover {
    background: linear-gradient(135deg, rgba(255,255,255,0.17), rgba(255,255,255,0.04) 48%, rgba(35,165,90,0.07)), rgba(8,21,12,0.38);
    border-color: rgba(255,255,255,0.27);
    transform: translateY(-3px);
  }
  .trial-card:hover::before { opacity: 1; }
  .trial-card .feat-head {
    display: flex; align-items: center; gap: 16px; flex: 1; min-width: 0;
  }
  .trial-card .feat-head .ico {
    width: 44px; height: 44px; border-radius: 11px; flex-shrink: 0;
    background: rgba(35,165,90,0.12); color: var(--green);
    display: flex; align-items: center; justify-content: center; font-size: 18px;
  }
  .trial-info h3 { font-size: 16px; font-weight: 600; margin-bottom: 4px; color: var(--text); }
  .trial-info p { color: var(--text-2); font-size: 13.5px; line-height: 1.5; margin: 0; }
  .trial-actions { flex-shrink: 0; }
  .trial-actions .btn { padding: 11px 22px; font-size: 13.5px; font-weight: 600; white-space: nowrap; }

  #pricing { position: relative; }
  #pricing .glow-green { width: 520px; height: 520px; bottom: -180px; left: 50%; transform: translateX(-50%); opacity: 0.3; }
  .price-hero { text-align: center; margin-bottom: 8px; position: relative; z-index: 1; }
  .price-hero .badge-ltd {
    display: inline-flex; align-items: center; gap: 8px;
    background: rgba(240,175,40,0.12); border: 1px solid rgba(240,175,40,0.35);
    color: #ffd166; font-size: 13px; font-weight: 700;
    padding: 8px 18px; border-radius: 100px; margin-bottom: 20px;
  }
  .price-hero .badge-ltd i { font-size: 12px; }
  .price-hero h2 { font-size: 42px; font-weight: 900; letter-spacing: -0.02em; margin-bottom: 10px; }
  .price-hero p { color: var(--text-2); font-size: 16px; }
  .plan-switch {
    display: flex; gap: 2px; margin: -20px auto 24px; padding: 3px;
    width: fit-content; background: linear-gradient(135deg, rgba(255,255,255,0.11), rgba(255,255,255,0.025));
    border: 1px solid rgba(255,255,255,0.15); border-radius: 12px; position: relative; z-index: 2;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.16); backdrop-filter: blur(18px) saturate(180%); -webkit-backdrop-filter: blur(18px) saturate(180%);
  }
  .plan-switch-btn {
    border: none; background: transparent; color: var(--text-3); cursor: pointer;
    font-family: inherit; font-size: 12px; font-weight: 600; padding: 5px 18px;
    border-radius: 7px; transition: all 0.2s;
  }
  .plan-switch-btn.active { background: rgba(35,165,90,0.18); color: #6fdf9e; }
  .plan-switch-btn:not(.active):hover { color: var(--text-2); }
  .plan-switch-btn:disabled { opacity: 0.45; cursor: not-allowed; pointer-events: none; }
  .price-card {
    max-width: 900px; margin: 16px auto 0;
    background: linear-gradient(135deg, rgba(255,255,255,0.14), rgba(255,255,255,0.04) 46%, rgba(35,165,90,0.075)), rgba(13,28,17,0.44);
    border: 1px solid rgba(255,255,255,0.2); border-radius: 26px;
    padding: 38px 44px; position: relative; z-index: 1; overflow: hidden;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.24), inset 1px 0 0 rgba(255,255,255,0.07), inset 0 -1px 0 rgba(255,255,255,0.035), 0 30px 90px rgba(0,0,0,0.3);
    backdrop-filter: blur(34px) saturate(195%) brightness(1.07); -webkit-backdrop-filter: blur(34px) saturate(195%) brightness(1.07);
  }
  .price-card::before {
    content: ''; position: absolute; top: -140px; left: 50%; transform: translateX(-50%);
    width: 500px; height: 300px; background: radial-gradient(ellipse, rgba(35,165,90,0.18), transparent 70%);
    pointer-events: none;
  }
  .price-card .pc-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
  .price-card .pc-name { display: flex; align-items: center; gap: 12px; }
  .price-card .pc-name img { height: 38px; width: auto; max-width: 68px; object-fit: contain; display: block; }
  .price-card .pc-name .nm { font-size: 18px; font-weight: 700; }
  .price-card .pc-name .sn { font-size: 13px; color: var(--text-3); }
  .price-card .pc-price { text-align: right; }
  .price-card .pc-price .amt { font-size: 44px; font-weight: 900; line-height: 1; letter-spacing: -0.02em; }
  .price-card .pc-price .amt.odometer-roll { animation: odometerRoll 0.72s cubic-bezier(0.22,1,0.36,1); }
  .price-card .pc-price .amt small { font-size: 16px; font-weight: 600; color: var(--text-2); }
  .price-card .pc-price .sub { font-size: 13px; color: var(--text-3); margin-top: 4px; }
  .price-card .pc-feats {
    display: grid; grid-template-columns: repeat(2, 1fr); grid-auto-rows: max-content;
    align-content: start; gap: 10px 24px; margin-bottom: 22px;
    transition: height 0.82s cubic-bezier(0.22,1,0.36,1);
  }
  .price-card .pc-feats li { display: flex; align-items: center; gap: 10px; font-size: 14px; color: var(--text-2); list-style: none; }
  .price-card .pc-feats li i { color: var(--green); font-size: 13px; flex-shrink: 0; }
  .price-card .pc-feats li.decrypt-enter { animation: decryptEnter 0.45s ease both; }
  .decrypt-text.decoding {
    color: #74e5a3; font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    text-shadow: 0 0 10px rgba(35,165,90,0.45); letter-spacing: 0.015em;
  }
  .price-card.switching { animation: glassSwitch 0.72s cubic-bezier(0.22,1,0.36,1); }
  .price-card.switching .pc-name .sn,
  .price-card.switching .pc-price .sub,
  .price-card.switching .pc-note,
  .price-card.switching .pay-methods { animation: pricingContentSettle 0.52s cubic-bezier(0.22,1,0.36,1) both; }
  @keyframes odometerRoll {
    0% { opacity: 0.35; transform: translateY(12px); filter: blur(5px); }
    55% { opacity: 1; transform: translateY(-2px); filter: blur(0); }
    100% { transform: translateY(0); }
  }
  @keyframes decryptEnter {
    from { opacity: 0; transform: translateY(6px); filter: blur(3px); }
    to { opacity: 1; transform: translateY(0); filter: blur(0); }
  }
  @keyframes glassSwitch {
    0% { border-color: rgba(255,255,255,0.2); }
    42% { border-color: rgba(85,217,139,0.52); box-shadow: inset 0 1px 0 rgba(255,255,255,0.3), 0 30px 90px rgba(0,0,0,0.3), 0 0 42px rgba(35,165,90,0.13); }
    100% { border-color: rgba(255,255,255,0.2); }
  }
  @keyframes pricingContentSettle {
    from { opacity: 0.42; transform: translateY(5px); filter: blur(2px); }
    to { opacity: 1; transform: translateY(0); filter: blur(0); }
  }
  .price-card .pc-note { font-size: 13px; color: var(--text-3); margin-bottom: 20px; line-height: 1.6; }
  .price-card .pay-methods { display: flex; gap: 14px; flex-wrap: wrap; }
  .price-card .pay-method {
    flex: 1; min-width: 180px; display: flex; align-items: center; gap: 12px;
    background: linear-gradient(135deg, rgba(255,255,255,0.1), rgba(255,255,255,0.025)); border: 1px solid rgba(255,255,255,0.14); border-radius: 14px;
    padding: 14px 18px; cursor: pointer; transition: all 0.2s; color: var(--text);
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.13); backdrop-filter: blur(18px) saturate(175%); -webkit-backdrop-filter: blur(18px) saturate(175%);
  }
  .price-card .pay-method:hover { border-color: var(--green); background: rgba(35,165,90,0.08); }
  .price-card .pay-method.best { border-color: rgba(35,165,90,0.5); }
  .price-card .pay-method i { font-size: 22px; width: 26px; text-align: center; }
  .price-card .pay-method .pm-info { flex: 1; }
  .price-card .pay-method .pm-info b { font-size: 14px; font-weight: 600; display: block; }
  .price-card .pay-method .pm-info span { font-size: 12px; color: var(--text-3); }
  .price-card .pay-method .pm-badge {
    background: rgba(35,165,90,0.2); color: #6fdf9e; font-size: 11px; font-weight: 700;
    padding: 3px 10px; border-radius: 100px;
  }

  footer { border-top: 1px solid rgba(255,255,255,0.05); padding: 28px 0; text-align: center; }
  footer p { color: var(--text-3); font-size: 13px; }
  footer .footer-links { display: flex; gap: 22px; justify-content: center; margin-bottom: 14px; flex-wrap: wrap; }
  footer .footer-links a { color: var(--text-2); font-size: 13px; transition: color 0.15s; }
  footer .footer-links a:hover { color: var(--text); }

  .modal-bg {
    position: fixed; inset: 0; z-index: 1000;
    background: rgba(0,0,0,0.65); display: none;
    align-items: center; justify-content: center; backdrop-filter: blur(4px);
  }
  .modal-bg.show { display: flex; animation: modalBackdropIn 0.24s ease both; }
  .modal-card {
    background: linear-gradient(135deg, rgba(255,255,255,0.16), rgba(255,255,255,0.045) 48%, rgba(35,165,90,0.07)), rgba(12,25,16,0.78); border: 1px solid rgba(255,255,255,0.2); border-radius: 22px;
    padding: 28px; width: 420px; max-width: 90vw; position: relative;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.23), inset 1px 0 0 rgba(255,255,255,0.06), 0 24px 80px rgba(0,0,0,0.6);
    backdrop-filter: blur(36px) saturate(195%) brightness(1.06); -webkit-backdrop-filter: blur(36px) saturate(195%) brightness(1.06);
  }
  .modal-card.pop-in { animation: modalPopIn 0.52s cubic-bezier(0.16,1,0.3,1) both; transform-origin: 50% 56%; }
  @keyframes modalBackdropIn {
    from { opacity: 0; backdrop-filter: blur(0); }
    to { opacity: 1; backdrop-filter: blur(4px); }
  }
  @keyframes modalPopIn {
    0% { opacity: 0; transform: translateY(22px) scale(0.88); filter: blur(8px); }
    58% { opacity: 1; transform: translateY(-3px) scale(1.018); filter: blur(0); }
    100% { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
  }
  .modal-card h2 { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
  .modal-card .modal-sub { color: var(--text-2); font-size: 14px; margin-bottom: 20px; }
  .modal-close {
    position: absolute; top: 18px; right: 20px;
    background: none; border: none; color: var(--text-3); font-size: 20px; cursor: pointer;
  }
  .modal-close:hover { color: var(--text); }
  .pay-opts { display: flex; flex-direction: column; gap: 10px; }
  .pay-opt {
    display: flex; align-items: center; gap: 12px; padding: 14px;
    background: var(--bg-card-hover); border: 1px solid var(--border); border-radius: 12px;
    cursor: pointer; transition: border-color 0.2s; color: var(--text); position: relative;
  }
  .pay-opt:hover { border-color: var(--green); }
  .pay-opt i:first-child { font-size: 24px; width: 28px; text-align: center; }
  .pay-opt .pm-info { flex: 1; }
  .pay-opt .pm-info h3 { font-size: 14px; font-weight: 600; }
  .pay-opt .pm-info p { font-size: 12px; color: var(--text-3); }
  .pay-opt i.fa-arrow-right { font-size: 12px; color: var(--text-3); }
  .modal-ph {
    background: var(--bg-card-hover); border: 1px dashed var(--border); border-radius: 12px;
    padding: 36px; text-align: center; color: var(--text-3); font-size: 13px;
  }
  .modal-ph i { font-size: 28px; margin-bottom: 10px; color: var(--green); }
  .checkout-embed { min-height: 320px; display: flex; align-items: center; justify-content: center; }
  .checkout-embed button { width: 100%; }
  .back-btn {
    margin-top: 16px; width: 100%; border: 1px solid var(--border); background: transparent;
    border-radius: 8px; padding: 9px; font-size: 13px; font-weight: 600; cursor: pointer;
    color: var(--text-2); font-family: inherit;
  }
  .back-btn:hover { border-color: var(--green); }

  @media (max-width: 960px) {
    .feat-grid { grid-template-columns: repeat(2, 1fr); }
  }

  @media (max-width: 640px) {
    nav {
      top: 12px; left: 50%; transform: translateX(-50%);
      width: max-content; max-width: calc(100% - 16px);
    }
    nav .inner {
      height: 42px; padding: 0 8px 0 10px;
    }
    nav.shrunk .inner {
      padding: 0 14px;
    }
    nav .brand {
      padding: 2px 4px;
    }
    nav .brand img {
      height: 18px; max-width: 38px;
    }
    nav .links {
      display: flex; align-items: center; gap: 2px; margin: 0 4px; overflow: hidden;
      max-width: 320px; opacity: 1; pointer-events: auto;
      transition: max-width 0.45s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.35s cubic-bezier(0.4, 0, 0.2, 1), margin 0.45s cubic-bezier(0.4, 0, 0.2, 1);
    }
    nav .links a {
      font-size: 11px; padding: 4px 7px;
    }
    nav .nav-right {
      gap: 5px; max-width: 80px;
    }
    nav .nav-icon-btn {
      width: 28px; height: 28px; font-size: 11.5px;
    }
    nav.shrunk:hover .links,
    nav.shrunk:focus-within .links {
      max-width: 320px; margin: 0 4px; opacity: 1; pointer-events: auto;
    }
    nav.shrunk:hover .nav-right,
    nav.shrunk:focus-within .nav-right {
      max-width: 80px; opacity: 1; pointer-events: auto; overflow: visible;
    }

    .hero { padding: 125px 16px 24px; }
    .hero h1 { font-size: 40px; }
    .hero p { font-size: 16px; }
    .hero .fern-wrap { left: 60%; }
    .hero .fern-float {
      width: 340px; pointer-events: none;
      -webkit-user-select: none; user-select: none; -webkit-touch-callout: none;
    }
    @keyframes fernFloat {
      0%, 100% { transform: translate(-75%, -52%); }
      50% { transform: translate(-75%, -48%); }
    }
    .hero .stats { gap: 24px; }

    .section { padding: 40px 0; }
    .section-t h2 { font-size: 28px; }
    .feat-grid { grid-template-columns: 1fr; }
    .feat-card { padding: 22px 20px; }
    .trial-card { flex-direction: column; text-align: center; padding: 26px 20px; gap: 20px; }
    .trial-card .feat-head { flex-direction: column; text-align: center; }
    .trial-info { text-align: center; }
    .trial-actions { width: 100%; }
    .trial-actions .btn { width: 100%; }
    .cc-banner { padding: 24px 20px; }
    .price-card { padding: 28px 20px; }
    .price-card .pc-feats { grid-template-columns: 1fr; }
    .price-hero h2 { font-size: 32px; }

    .modal-bg { padding: 16px; overflow-y: auto; }
    .modal-card { margin: auto; max-height: 90vh; overflow-y: auto; }
  }
  @media (max-width: 380px) {
    nav .links a { font-size: 10px; padding: 3px 5px; }
    nav .nav-icon-btn { width: 25px; height: 25px; font-size: 10.5px; }
    nav .brand img { height: 16px; }
  }

  @media (hover: none), (pointer: coarse) {
    .hero .fern-float {
      pointer-events: none;
      -webkit-user-select: none; user-select: none; -webkit-touch-callout: none;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .cipher-stream, .encryption-status i, .encryption-status .signal b, .hero .hero-img::before, .price-card.switching, .price-card.switching .pc-name .sn, .price-card.switching .pc-price .sub, .price-card.switching .pc-note, .price-card.switching .pay-methods, .price-card .pc-price .amt.odometer-roll, .price-card .pc-feats li.decrypt-enter, .modal-bg.show, .modal-card.pop-in { animation: none !important; }
    #particle-field { display: none; }
  }
</style>
</head>
<body>

<div class="page-fade" id="page-fade"></div>
<canvas id="particle-field" aria-hidden="true"></canvas>

<nav id="navbar">
  <div class="inner">
    <a href="/" class="brand" aria-label="Fern">
      <img src="/static/assets/fern_icon.png" alt="Fern">
    </a>
    <div class="links">
      <a href="#features">Features</a>
      <a href="#screenshots">Showcase</a>
      <a href="#trial">Trial</a>
      <a href="#pricing">Pricing</a>
    </div>
    <div class="nav-right">
      <a href="/invite" class="nav-icon-btn" aria-label="Discord" title="Join Discord"><i class="fa-brands fa-discord"></i></a>
      {% if logged_in %}
      <a href="/channels/@me" class="nav-icon-btn" aria-label="Open Fern" title="Open Fern"><i class="fa-solid fa-power-off"></i></a>
      {% else %}
      <a href="/login" class="nav-icon-btn" aria-label="Login" title="Login / Open Fern"><i class="fa-solid fa-power-off"></i></a>
      {% endif %}
    </div>
  </div>
</nav>

<section class="hero">
  <div class="glow glow-green"></div>
  <div class="glow glow-teal"></div>
  <div class="glow glow-dim"></div>
  <div class="cipher-field" aria-hidden="true">
    <span class="cipher-stream">A7F2 91C0 E4B8 6D3A 0F11 7BC9</span>
    <span class="cipher-stream">0110 1011 0010 1101 0100 1110</span>
    <span class="cipher-stream">3E91 C2A4 77D0 8B6F 15AC 09E3</span>
    <span class="cipher-stream">1101 0010 1110 0101 1001 0011</span>
  </div>
  <div class="hero-title">
    <div class="fern-wrap">
      <img class="fern-float" src="/static/assets/fern_float.png" alt="">
    </div>
    <h1>Meet <span class="accent">Fern.</span></h1>
  </div>
  <p>The privacy-focused Discord client. Every message you send stays encrypted, only you and your recipient can read it.</p>
  <div class="cta">
    <a class="btn btn-green" href="/login">Try it free</a>
    <button class="btn btn-out" onclick="document.getElementById('features').scrollIntoView({behavior:'smooth'})">Features</button>
  </div>
  <div class="hero-img">
    <img src="/static/assets/Example1.png" alt="Fern client">
  </div>
</section>

<section class="section" id="features">
  <div class="glow glow-dim"></div>
  <div class="wrap">
    <div class="section-t">
      <div class="eyebrow">Features</div>
      <h2>Everything you need from a Discord client</h2>
      <p>Fern packs the essentials into one clean, fast, privacy-first client.</p>
    </div>
    <div class="feat-grid">
      <div class="feat-card">
        <div class="feat-head"><div class="ico"><i class="fa-solid fa-bolt"></i></div><h3>Fast</h3></div>
        <p>Fern is a lightweight client compared to Discord. No matter which browser you use, it won't eat up your RAM, even Chrome.</p>
      </div>
      <div class="feat-card">
        <div class="feat-head"><div class="ico"><i class="fa-solid fa-shield-halved"></i></div><h3>Account safety</h3></div>
        <p>Fern uses an up-to-date, undetected client that simulates Discord like you're using the original. The ban chance is low, but it's never 0%, so if your main account is too big a risk to lose, we recommend using an alt.</p>
      </div>
      <div class="feat-card">
        <div class="feat-head"><div class="ico"><i class="fa-solid fa-palette"></i></div><h3>Custom look</h3></div>
        <p>Fern lets you customize the client in basic ways, but we're not a beauty salon. The core purpose is secure chats, so don't expect a catalog of UI skins.</p>
      </div>
      <div class="feat-card">
        <div class="feat-head"><div class="ico"><i class="fa-solid fa-users"></i></div><h3>Friends & DMs</h3></div>
        <p>Fern identifies other users on the Fern client, they're shown when you open someone's profile, letting you talk in encrypted chats. It works with more than two people too.</p>
      </div>
      <div class="feat-card">
        <div class="feat-head"><div class="ico"><i class="fa-solid fa-lock"></i></div><h3>End-to-End Encryption</h3></div>
        <p>Fern encrypts chats using Fernet, which can't be broken into and is kept up to date. Everything you do on Fern, messages, calls, and shared files, can't be read by anyone, including law enforcement. We believe privacy is a policy for every human.</p>
      </div>
      <div class="feat-card">
        <div class="feat-head"><div class="ico"><i class="fa-solid fa-gift"></i></div><h3>Claim Trial</h3></div>
        <p>Not sure about Fern? We give you a one-time trial upfront. Get a friend and both claim a trial so you can test it out for yourselves.</p>
      </div>
    </div>
    <div class="section-t" style="margin-top:16px;">
      <h2>We stand with your privacy</h2>
    </div>
    <div class="cc-banner">
      <div class="cc-icon"><i class="fa-solid fa-bullhorn"></i></div>
      <div class="cc-body">
        <h3>Our action against EU Chat Control</h3>
        <p>Fern is publicly opposed to mass scanning of private conversations. Read a short summary of what we're doing about it and why it matters.</p>
        <a class="btn btn-green" style="margin-top:18px;display:inline-block;" href="/chat-control">Read the statement</a>
      </div>
    </div>
  </div>
</section>

<section class="section" id="screenshots" style="padding-top:0;">
  <div class="wrap">
    <div class="section-t">
      <div class="eyebrow">Showcase</div>
      <h2>See Fern in action</h2>
      <p>A quick look at what you're getting.</p>
    </div>
    <div class="showcase-box">
      <div class="play">
        <i class="fa-regular fa-circle-play"></i>
        <span>video coming soon</span>
      </div>
    </div>
  </div>
</section>

<section class="section" id="trial">
  <div class="wrap">
    <div class="section-t">
      <div class="eyebrow">Free Trial</div>
      <h2>Fern Trial</h2>
      <p>Experience Fern risk-free with an instant trial. No payment or credit card required.</p>
    </div>
    <div class="trial-card">
      <div class="feat-head">
        <div class="ico"><i class="fa-solid fa-gift"></i></div>
        <div class="trial-info">
          <h3>Claim Trial</h3>
          <p>Instant full access to all client features. Log in and test everything for yourself.</p>
        </div>
      </div>
      <div class="trial-actions">
        <a class="btn btn-green" href="/login">Claim Free Trial <i class="fa-solid fa-arrow-right"></i></a>
      </div>
    </div>
  </div>
</section>

<section class="section" id="pricing">
  <div class="glow glow-green"></div>
  <div class="wrap">
    <div class="section-t">
      <div class="eyebrow">Pricing</div>
    </div>
    <div class="price-hero">
      <div class="badge-ltd"><i class="fa-solid fa-bolt"></i> First 50 users only</div>
      <h2>Pay once. Access forever.</h2>
      <p>One payment. Lifetime license key. All future updates included.</p>
    </div>
    <div class="price-card">
      <div class="plan-switch">
        <button type="button" class="plan-switch-btn" id="switch-month" disabled style="opacity: 0.45; cursor: not-allowed;" title="Monthly payments temporarily disabled">Monthly</button>
        <button type="button" class="plan-switch-btn active" id="switch-lifetime" onclick="switchPlan('lifetime')">Lifetime</button>
      </div>
      <div class="pc-head">
        <div class="pc-name">
          <img src="/static/assets/fern_icon.png" alt="Fern">
          <div>
            <div class="nm" id="pc-nm">Fern</div>
            <div class="sn" id="pc-sn">Lifetime license</div>
          </div>
        </div>
        <div class="pc-price">
          <div class="amt" id="pc-amt">$5</div>
          <div class="sub" id="pc-sub">one-time, never expires</div>
        </div>
      </div>
      <ul class="pc-feats" id="pc-feats">
        <li><i class="fa-solid fa-check"></i> Lifetime updates</li>
        <li><i class="fa-solid fa-check"></i> All features</li>
        <li><i class="fa-solid fa-check"></i> End-to-end encryption</li>
        <li><i class="fa-solid fa-check"></i> Friends & DMs</li>
        <li><i class="fa-solid fa-check"></i> No hidden fees</li>
        <li><i class="fa-solid fa-check"></i> Priority support</li>
      </ul>
      <p class="pc-note" id="pc-note">Limited launch offer for the first 50 users before price increases. Upon purchase you'll receive a unique lifetime license key that never expires.</p>
      <div class="pay-methods" id="pc-pay">
        <div class="pay-method best" onclick="payPlan('lifetime')">
          <i class="fa-brands fa-bitcoin"></i>
          <div class="pm-info"><b>Crypto</b><span>BTC, ETH, LTC and more</span></div>
          <span class="pm-badge">Cheapest</span>
        </div>
        <div class="pay-method" onclick="payPlan('lifetime')">
          <i class="fa-solid fa-credit-card"></i>
          <div class="pm-info"><b>Card</b><span>Debit or credit</span></div>
          <span class="pm-badge">+7% fee</span>
        </div>
      </div>
    </div>
  </div>
</section>

<footer>
  <div class="wrap">
    <div class="footer-links">
      <a href="#features">Features</a>
      <a href="#screenshots">Showcase</a>
      <a href="#trial">Trial</a>
      <a href="#pricing">Pricing</a>
      <a href="/invite">Discord</a>
      <a href="/status">Status</a>
      <a href="/tos">Terms of Service</a>
      <a href="/chat-control">EU Chat Control</a>
    </div>
    <p>&copy; 2026 Fern</p>
  </div>
</footer>

<div class="modal-bg" id="pay-modal">
  <div class="modal-card" id="pay-methods-box">
    <button class="modal-close" onclick="closeModal()">&times;</button>
    <h2 id="pay-plan-title">Get your lifetime license</h2>
    <p class="modal-sub" id="pay-plan-sub">How do you want to pay?</p>
    <div class="pay-opts">
      <div class="pay-opt" onclick="payCard()">
        <i class="fa-solid fa-credit-card"></i>
        <div class="pm-info">
          <h3>Card</h3>
          <p>Debit or credit, a 7% processing fee is tacked on</p>
        </div>
        <i class="fa-solid fa-arrow-right"></i>
      </div>
      <div class="pay-opt" onclick="payCrypto()">
        <i class="fa-brands fa-bitcoin"></i>
        <div class="pm-info">
          <h3>Crypto</h3>
          <p>BTC, ETH, LTC and more, no fees</p>
        </div>
        <i class="fa-solid fa-arrow-right"></i>
      </div>
      <div class="pay-opt" onclick="payTicket()">
        <i class="fa-solid fa-ticket"></i>
        <div class="pm-info">
          <h3>Ticket</h3>
          <p>Manual order, handled in our Discord</p>
        </div>
        <i class="fa-solid fa-arrow-right"></i>
      </div>
    </div>
  </div>
  <div class="modal-card" id="checkout-box" style="display:none">
    <button class="modal-close" onclick="closeModal()">&times;</button>
    <h2>Complete your purchase</h2>
    <p class="modal-sub" id="checkout-plan-sub">$5 lifetime license</p>
    <div class="checkout-embed">
      <div data-aquadratic="cmshwduyi0001nnqblzi7xx9y" id="embed-month" style="display:none" data-accent-color="#23a55a" data-button-text="Pay" data-border-radius="10" data-padding="13"></div>
      <div data-aquadratic="cmshweg6p00012o44pc94vlwo" id="embed-lifetime" style="display:none" data-accent-color="#23a55a" data-button-text="Pay" data-border-radius="10" data-padding="13"></div>
    </div>
    <button class="back-btn" onclick="backToMethods()">Back</button>
  </div>
</div>

<script src="https://aquadratic.com/embed.js"></script>
<script src="https://static.sellauth.com/embed/v3.min.js" defer></script>
<script>
  (function() {
    var canvas = document.getElementById('particle-field');
    if (!canvas || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) return;
    var ctx = canvas.getContext('2d');
    var particles = [];
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    function resizeParticles() {
      canvas.width = Math.floor(innerWidth * dpr);
      canvas.height = Math.floor(innerHeight * dpr);
      canvas.style.width = innerWidth + 'px'; canvas.style.height = innerHeight + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var count = Math.min(130, Math.max(64, Math.floor(innerWidth / 11)));
      particles = Array.from({length: count}, function() {
        return { x: Math.random() * innerWidth, y: Math.random() * innerHeight, r: Math.random() * 1.45 + 0.3, vx: (Math.random() - 0.5) * 0.13, vy: -(Math.random() * 0.17 + 0.035), a: Math.random() * 0.42 + 0.09 };
      });
    }
    function drawParticles() {
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      particles.forEach(function(p) {
        p.x += p.vx; p.y += p.vy;
        if (p.y < -8) { p.y = innerHeight + 8; p.x = Math.random() * innerWidth; }
        if (p.x < -8) p.x = innerWidth + 8;
        if (p.x > innerWidth + 8) p.x = -8;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(91, 221, 145, ' + p.a + ')'; ctx.fill();
      });
      requestAnimationFrame(drawParticles);
    }
    resizeParticles(); drawParticles();
    window.addEventListener('resize', resizeParticles, {passive:true});
  })();


  var modal = document.getElementById('pay-modal');
  var currentPlan = 'lifetime';

  function popIn(card) {
    if (!card) return;
    card.classList.remove('pop-in');
    void card.offsetWidth;
    card.classList.add('pop-in');
  }

  function showModal() {
    var methodsBox = document.getElementById('pay-methods-box');
    methodsBox.style.display = '';
    document.getElementById('checkout-box').style.display = 'none';
    modal.classList.add('show');
    popIn(methodsBox);
  }

  function closeModal() { modal.classList.remove('show'); }

  function backToMethods() {
    document.getElementById('pay-methods-box').style.display = '';
    document.getElementById('checkout-box').style.display = 'none';
  }

  var MONTHLY_FEATS = [
    '<li><i class="fa-solid fa-check"></i> All features</li>',
    '<li><i class="fa-solid fa-check"></i> End-to-end encryption</li>',
    '<li><i class="fa-solid fa-check"></i> Friends & DMs</li>',
    '<li><i class="fa-solid fa-check"></i> Cancel anytime</li>'
  ].join('');
  var LIFETIME_FEATS = [
    '<li><i class="fa-solid fa-check"></i> Lifetime updates</li>',
    '<li><i class="fa-solid fa-check"></i> All features</li>',
    '<li><i class="fa-solid fa-check"></i> End-to-end encryption</li>',
    '<li><i class="fa-solid fa-check"></i> Friends & DMs</li>',
    '<li><i class="fa-solid fa-check"></i> No hidden fees</li>',
    '<li><i class="fa-solid fa-check"></i> Priority support</li>'
  ].join('');

  var planAnimationToken = 0;
  var priceAnimationFrame = 0;
  var reducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var cipherChars = '01ABCDEF#$%&';

  function animatePrice(target, token) {
    var amount = document.getElementById('pc-amt');
    var from = parseInt(amount.textContent.replace(/[^0-9]/g, ''), 10);
    if (!Number.isFinite(from)) from = target;
    cancelAnimationFrame(priceAnimationFrame);
    amount.classList.remove('odometer-roll');
    void amount.offsetWidth;
    amount.classList.add('odometer-roll');
    if (reducedMotion || from === target) { amount.textContent = '$' + target; return; }
    var started = performance.now();
    function tick(now) {
      if (token !== planAnimationToken) return;
      var progress = Math.min(1, (now - started) / 680);
      var eased = 1 - Math.pow(1 - progress, 3);
      amount.textContent = '$' + Math.round(from + (target - from) * eased);
      if (progress < 1) priceAnimationFrame = requestAnimationFrame(tick);
      else amount.textContent = '$' + target;
    }
    priceAnimationFrame = requestAnimationFrame(tick);
  }

  function cipherLine(text, revealed) {
    return Array.from(text).map(function(character, index) {
      if (character === ' ') return ' ';
      if (index < revealed) return character;
      return cipherChars[Math.floor(Math.random() * cipherChars.length)];
    }).join('');
  }

  function animateFeatures(markup, token) {
    var target = document.getElementById('pc-feats');
    if (reducedMotion) { target.style.height = 'auto'; target.innerHTML = markup; return; }
    var startingHeight = target.getBoundingClientRect().height;
    target.style.height = startingHeight + 'px';
    target.style.overflow = 'hidden';
    var source = document.createElement('div');
    source.innerHTML = '<ul>' + markup + '</ul>';
    var labels = Array.from(source.querySelectorAll('li')).map(function(item) { return item.textContent.trim(); });
    target.innerHTML = '';
    labels.forEach(function(label, index) {
      var item = document.createElement('li');
      item.className = 'decrypt-enter';
      item.style.animationDelay = (index * 55) + 'ms';
      var icon = document.createElement('i');
      icon.className = 'fa-solid fa-check';
      var text = document.createElement('span');
      text.className = 'decrypt-text decoding';
      text.textContent = cipherLine(label, 0);
      item.appendChild(icon); item.appendChild(text); target.appendChild(item);
      setTimeout(function() {
        if (token !== planAnimationToken) return;
        var revealed = 0;
        var timer = setInterval(function() {
          if (token !== planAnimationToken) { clearInterval(timer); return; }
          revealed += Math.max(1, Math.ceil(label.length / 11));
          text.textContent = cipherLine(label, revealed);
          if (revealed >= label.length) {
            clearInterval(timer); text.textContent = label; text.classList.remove('decoding');
          }
        }, 38);
      }, index * 85);
    });
    target.style.height = 'auto';
    var endingHeight = target.getBoundingClientRect().height;
    target.style.height = startingHeight + 'px';
    void target.offsetHeight;
    requestAnimationFrame(function() {
      requestAnimationFrame(function() {
        if (token === planAnimationToken) target.style.height = endingHeight + 'px';
      });
    });
    setTimeout(function() {
      if (token !== planAnimationToken) return;
      target.style.height = 'auto';
      target.style.overflow = '';
    }, 860);
  }

  function switchPlan(plan) {
    if (plan === 'month') return;
    var animationToken = ++planAnimationToken;
    currentPlan = plan;
    var isMonth = plan === 'month';
    var priceCard = document.querySelector('.price-card');
    priceCard.classList.remove('switching');
    void priceCard.offsetWidth;
    priceCard.classList.add('switching');
    document.getElementById('switch-month').classList.toggle('active', isMonth);
    document.getElementById('switch-lifetime').classList.toggle('active', !isMonth);
    document.getElementById('pc-sn').textContent = isMonth ? '1 month license' : 'Lifetime license';
    animatePrice(isMonth ? 3 : 5, animationToken);
    document.getElementById('pc-sub').textContent = isMonth ? 'first month, then $5/mo' : 'one-time, never expires';
    animateFeatures(isMonth ? MONTHLY_FEATS : LIFETIME_FEATS, animationToken);
    document.getElementById('pc-note').innerHTML = isMonth
      ? 'Use code <b>FERN</b> at checkout to claim your first month for $3.'
      : 'Limited launch offer for the first 50 users before price increases. Upon purchase you\\'ll receive a unique lifetime license key that never expires.';
    document.getElementById('pc-pay').innerHTML = isMonth
      ? '<div class="pay-method best" onclick="payPlan(\\'month\\')"><i class="fa-brands fa-bitcoin"></i><div class="pm-info"><b>Crypto</b><span>BTC, ETH, LTC and more</span></div><span class="pm-badge">Cheapest</span></div><div class="pay-method" onclick="payPlan(\\'month\\')"><i class="fa-solid fa-credit-card"></i><div class="pm-info"><b>Card</b><span>Debit or credit</span></div><span class="pm-badge">+7% fee</span></div>'
      : '<div class="pay-method best" onclick="payPlan(\\'lifetime\\')"><i class="fa-brands fa-bitcoin"></i><div class="pm-info"><b>Crypto</b><span>BTC, ETH, LTC and more</span></div><span class="pm-badge">Cheapest</span></div><div class="pay-method" onclick="payPlan(\\'lifetime\\')"><i class="fa-solid fa-credit-card"></i><div class="pm-info"><b>Card</b><span>Debit or credit</span></div><span class="pm-badge">+7% fee</span></div>';
  }

  function payPlan(plan) {
    currentPlan = plan;
    var title = document.getElementById('pay-plan-title');
    var sub = document.getElementById('pay-plan-sub');
    if (plan === 'month') {
      title.textContent = 'Get your 1 month license';
      sub.textContent = 'How do you want to pay?';
    } else {
      title.textContent = 'Get your lifetime license';
      sub.textContent = 'How do you want to pay?';
    }
    showModal();
  }

  function payCrypto() {
    closeModal();
    if (window.sellAuth) {
      var cart = currentPlan === 'month'
        ? [{ productId: 824850, variantId: 1415901, quantity: 1 }]
        : [{ productId: 824852, variantId: 1415903, quantity: 1 }];
      sellAuth.open({
        shopId: 258907,
        shopUrl: 'https://fern.mysellauth.com',
        cart: cart,
        theme: 'dark',
        returnUrl: window.location.origin + '/confirm'
      });
    } else {
      startCheckout();
    }
  }

  function payCard() {
    startCheckout();
  }

  function payTicket() {
    window.location.href = '/invite';
  }

  function startCheckout() {
    var isMonth = currentPlan === 'month';
    document.getElementById('pay-methods-box').style.display = 'none';
    var checkoutBox = document.getElementById('checkout-box');
    checkoutBox.style.display = '';
    popIn(checkoutBox);
    document.getElementById('checkout-plan-sub').textContent = isMonth ? '$3 1 month license (code FERN)' : '$5 lifetime license';
    document.getElementById('embed-month').style.display = isMonth ? '' : 'none';
    document.getElementById('embed-lifetime').style.display = isMonth ? 'none' : '';
    var embed = document.querySelector(isMonth ? '#embed-month button' : '#embed-lifetime button');
    if (embed) embed.click();
  }

  document.addEventListener('aquadratic:success', function(e) {
    window.location.href = '/confirm';
  });

  document.addEventListener('sellauth:success', function(e) {
    window.location.href = '/confirm';
  });

  modal.addEventListener('click', function(e) { if (e.target === modal) closeModal(); });
  document.addEventListener('keydown', function(e) { if (e.key === 'Escape') closeModal(); });

  var navbar = document.getElementById('navbar');
  window.addEventListener('scroll', function() {
    if (window.scrollY > 100) navbar.classList.add('shrunk');
    else navbar.classList.remove('shrunk');
  });

  var fade = document.getElementById('page-fade');
  function goTo(url) {
    if (!fade) { location.href = url; return; }
    fade.classList.add('active');
    setTimeout(function() { location.href = url; }, 320);
  }
  document.querySelectorAll('a.nav-icon-btn[href*="login"], a.open-btn[href*="login"], a.btn-green[href*="login"], .btn-green:not([href])').forEach(function(el) {
    el.addEventListener('click', function(e) {
      e.preventDefault();
      goTo('/login');
    });
  });
  window.addEventListener('pageshow', function() {
    if (fade) fade.classList.remove('active');
  });

  var fernImg = document.querySelector('.fern-float');
  if (fernImg && window.matchMedia && window.matchMedia('(min-width: 641px) and (hover: hover) and (pointer: fine)').matches) {
    var fernHit = new Image();
    fernHit.src = fernImg.src;
    fernHit.onload = function() {
      var cv = document.createElement('canvas');
      cv.width = fernHit.naturalWidth;
      cv.height = fernHit.naturalHeight;
      var cvt = cv.getContext('2d');
      cvt.drawImage(fernHit, 0, 0);
      var px = cvt.getImageData(0, 0, cv.width, cv.height).data;
      var hovered = false;
      var lastTouch = 0;
      fernImg.style.pointerEvents = 'none';
      document.addEventListener('mousemove', function(e) {
        var r = fernImg.getBoundingClientRect();
        var ix = Math.floor((e.clientX - r.left) / r.width * cv.width);
        var iy = Math.floor((e.clientY - r.top) / r.height * cv.height);
        var inside = ix >= 0 && iy >= 0 && ix < cv.width && iy < cv.height;
        var alpha = inside ? px[(iy * cv.width + ix) * 4 + 3] : 0;
        if (alpha > 4) lastTouch = e.timeStamp;
        if (!hovered && alpha > 80) {
          hovered = true;
          fernImg.style.pointerEvents = 'auto';
        } else if (hovered && e.timeStamp - lastTouch > 120) {
          hovered = false;
          fernImg.style.pointerEvents = 'none';
        }
      });
      document.addEventListener('mouseleave', function() {
        hovered = false;
        fernImg.style.pointerEvents = 'none';
      });
    };
  }
</script>
</body>
</html>"""

TOS_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fern</title>
<link rel="icon" type="image/png" href="/static/assets/fern_logo_circle.png">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0c130d;
    --bg-card: #121f14;
    --bg-card-hover: #162619;
    --border: #1d2e20;
    --green: #23a55a;
    --green-hover: #1e8b4c;
    --text: #e9f0ea;
    --text-2: #a8b8a9;
    --text-3: #5d6e5e;
    --max-w: 820px;
  }
  body {
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.7;
    -webkit-font-smoothing: antialiased;
  }
  a { color: var(--green); text-decoration: none; }
  a:hover { color: var(--green-hover); }
  .wrap { max-width: var(--max-w); margin: 0 auto; padding: 0 24px; }

  nav {
    position: fixed; top: 16px; left: 50%; transform: translateX(-50%); z-index: 100;
    background: rgba(14, 22, 15, 0.62);
    backdrop-filter: blur(18px) saturate(160%);
    -webkit-backdrop-filter: blur(18px) saturate(160%);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 100px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.35);
  }
  nav .inner { display: flex; align-items: center; height: 48px; padding: 0 18px; }
  nav .brand { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 16px; white-space: nowrap; color: var(--text); }
  nav .brand img { width: 24px; height: 24px; }

  .tos-hero { padding: 120px 24px 48px; text-align: center; position: relative; overflow: hidden; }
  .tos-hero .glow {
    position: absolute; width: 480px; height: 480px; top: -160px; left: 50%;
    transform: translateX(-50%); border-radius: 50%;
    background: radial-gradient(ellipse, rgba(35,165,90,0.22), transparent 70%);
    pointer-events: none;
  }
  .tos-hero h1 { font-size: 36px; font-weight: 800; letter-spacing: -0.03em; margin-bottom: 10px; position: relative; }
  .tos-hero p { color: var(--text-2); font-size: 14px; position: relative; }

  .tos-body { padding: 8px 12px 80px; }
  .tos-section {
    background: var(--bg-card); border: 1px solid var(--border); border-radius: 14px;
    padding: 30px 34px; margin-bottom: 18px;
  }
  .tos-section h2 { font-size: 20px; font-weight: 700; margin-bottom: 12px; line-height: 1.4; }
  .tos-section h2 .num { color: var(--green); margin-right: 10px; }
  .tos-section p { color: var(--text-2); font-size: 14px; margin-bottom: 12px; line-height: 1.7; }
  .tos-section p:last-child { margin-bottom: 0; }
  .tos-section .sub { color: var(--text); font-size: 15px; font-weight: 600; margin: 14px 0 8px; }
  .tos-section ul { list-style: none; margin: 0 0 12px; }
  .tos-section ul:last-child { margin-bottom: 0; }
  .tos-section li {
    color: var(--text-2); font-size: 14px; line-height: 1.7;
    padding: 6px 0 6px 22px; position: relative;
  }
  .tos-section li::before {
    content: ''; position: absolute; left: 3px; top: 15px;
    width: 6px; height: 6px; border-radius: 50%; background: var(--green);
  }

  footer { padding: 32px 0 56px; text-align: center; color: var(--text-3); font-size: 13px; }

  @media (max-width: 600px) {
    .wrap { padding: 0 20px; }
    nav { top: 12px; }
    .tos-hero { padding: 108px 20px 32px; }
    .tos-hero h1 { font-size: 26px; margin-bottom: 8px; }
    .tos-body { padding: 0 8px 56px; }
    .tos-section { padding: 24px 22px; margin-bottom: 14px; }
    .tos-section h2 { font-size: 18px; }
    .tos-section p, .tos-section li { font-size: 14px; }
    footer { padding: 24px 16px 40px; }
  }
</style>
</head>
<body>

<nav>
  <div class="inner">
    <a class="brand" href="/"><img src="/static/assets/fern_logo_circle.png" alt="" onerror="this.style.display='none'"> Fern</a>
  </div>
</nav>

<header class="tos-hero">
  <div class="glow"></div>
  <div class="wrap">
    <h1>Fern Terms of Service &amp; Data Policy</h1>
    <p>Last Updated: August 6, 2026</p>
  </div>
</header>

<div class="tos-body wrap">

  <section class="tos-section">
    <h2><span class="num">1.</span>Acceptance of Terms</h2>
    <p>By accessing or using Fern, you agree to be bound by these Terms of Service. If you do not agree with these terms, you must not use the service.</p>
  </section>

  <section class="tos-section">
    <h2><span class="num">2.</span>Service Description</h2>
    <p>Fern is a privacy-focused platform designed to provide end-to-end encryption for communications and data exchanged over supported third-party platforms, including but not limited to Discord.</p>
    <p>Fern encrypts supported content before transmission and decrypts it only for authorized recipients. Our goal is to improve user privacy while remaining compatible with supported platforms.</p>
  </section>

  <section class="tos-section">
    <h2><span class="num">3.</span>Data Handling</h2>
    <p>We are committed to minimizing the amount of data we collect.</p>
    <h3 style="color:var(--text);font-size:15px;font-weight:600;margin-bottom:6px">Data We May Process</h3>
    <p>Depending on the features you use, Fern may process:</p>
    <ul>
      <li>Account identifiers</li>
      <li>Authentication tokens required for service functionality</li>
      <li>Device and diagnostic information</li>
      <li>Configuration preferences</li>
      <li>Encrypted message metadata required for operation</li>
    </ul>
    <h3 style="color:var(--text);font-size:15px;font-weight:600;margin-bottom:6px">Encryption</h3>
    <p>All sensitive information processed by Fern is encrypted using industry-standard cryptographic methods.</p>
    <p>Fern is designed so that sensitive user content remains encrypted during transmission between supported clients whenever applicable.</p>
  </section>

  <section class="tos-section">
    <h2><span class="num">4.</span>Data Retention</h2>
    <p>Fern only retains data for as long as it is necessary to operate the service.</p>
    <p>Unless otherwise required by law:</p>
    <ul>
      <li>Data remains stored only while required for the service to function.</li>
      <li>Data associated with active sessions may persist for the duration of those sessions.</li>
      <li>Service-related information may remain available while the Fern service is operational.</li>
      <li>When data is no longer required for operational purposes, it will be securely deleted within a reasonable timeframe.</li>
    </ul>
  </section>

  <section class="tos-section">
    <h2><span class="num">5.</span>Third-Party Platforms</h2>
    <p>Fern integrates with third-party platforms such as Discord.</p>
    <p>You acknowledge that:</p>
    <ul>
      <li>Your use of those platforms remains subject to their respective Terms of Service and Privacy Policies.</li>
      <li>Fern is not affiliated with, endorsed by, or operated by Discord or any other supported platform.</li>
      <li>Platform functionality may change without notice.</li>
    </ul>
  </section>

  <section class="tos-section">
    <h2><span class="num">6.</span>User Responsibilities</h2>
    <p>You agree that you will not:</p>
    <ul>
      <li>Use Fern for unlawful purposes.</li>
      <li>Attempt to compromise the security or availability of the service.</li>
      <li>Abuse, reverse engineer, or exploit vulnerabilities within Fern.</li>
      <li>Use Fern to distribute malware, spam, or malicious content.</li>
    </ul>
    <p>Users remain solely responsible for the content they transmit using Fern.</p>
  </section>

  <section class="tos-section">
    <h2><span class="num">7.</span>Purchases</h2>
    <p>All purchases made through Fern are <strong style="color:var(--text)">final</strong>.</p>
    <h3 style="color:var(--text);font-size:15px;font-weight:600;margin-bottom:6px">Refund Policy</h3>
    <p>Unless required by applicable law:</p>
    <ul>
      <li>Purchases are <strong style="color:var(--text)">non-refundable</strong>.</li>
      <li>Partial refunds are not provided.</li>
      <li>Subscription periods are not prorated.</li>
      <li>Accidental purchases do not automatically qualify for a refund.</li>
    </ul>
    <p>If you experience a technical issue preventing access to purchased services, please contact support so we can investigate.</p>
  </section>

  <section class="tos-section">
    <h2><span class="num">8.</span>Service Availability</h2>
    <p>While we strive to maintain reliable uptime, Fern is provided on an "as available" basis.</p>
    <p>We do not guarantee:</p>
    <ul>
      <li>Continuous availability</li>
      <li>Error-free operation</li>
      <li>Compatibility with future versions of supported platforms</li>
    </ul>
    <p>Maintenance, updates, or third-party changes may temporarily affect service functionality.</p>
  </section>

  <section class="tos-section">
    <h2><span class="num">9.</span>Security</h2>
    <p>Fern employs reasonable technical and organizational measures to protect stored and transmitted data.</p>
    <p>However, no online service can guarantee absolute security. Users remain responsible for protecting their own devices, credentials, and authentication methods.</p>
  </section>

  <section class="tos-section">
    <h2><span class="num">10.</span>Limitation of Liability</h2>
    <p>To the maximum extent permitted by law, Fern and its operators shall not be liable for any indirect, incidental, consequential, or special damages arising from:</p>
    <ul>
      <li>Use of the service</li>
      <li>Inability to access the service</li>
      <li>Third-party platform outages</li>
      <li>Data loss caused by circumstances beyond our reasonable control</li>
    </ul>
    <p>Your use of Fern is at your own risk.</p>
  </section>

  <section class="tos-section">
    <h2><span class="num">11.</span>Termination</h2>
    <p>We reserve the right to suspend or terminate access to Fern if these Terms are violated or if continued access poses a security or operational risk.</p>
  </section>

  <section class="tos-section">
    <h2><span class="num">12.</span>Changes to These Terms</h2>
    <p>We may update these Terms of Service from time to time.</p>
    <p>Material changes will become effective upon publication of the updated Terms. Continued use of Fern after changes are published constitutes acceptance of the revised Terms.</p>
  </section>

  <section class="tos-section">
    <h2><span class="num">13.</span>Contact</h2>
    <p>For questions regarding these Terms or our data handling practices, please contact the Fern support team through our official support channels.</p>
  </section>

  <section class="tos-section" style="border-color:rgba(35,165,90,0.35)">
    <h2 style="color:var(--green)">Privacy Summary</h2>
    <ul>
      <li>Sensitive data is encrypted.</li>
      <li>We collect only the information necessary to operate Fern.</li>
      <li>Data is retained only for as long as required to provide the service.</li>
      <li>Fern is designed to enhance privacy when using supported third-party platforms.</li>
      <li>Purchases are final and non-refundable except where required by applicable law.</li>
    </ul>
  </section>

</div>

<footer>
  <div class="wrap">
    <p>&copy; 2026 Fern &nbsp;&middot;&nbsp; <a href="/">Home</a></p>
  </div>
</footer>

</body>
</html>"""


CHAT_CONTROL_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fern</title>
<link rel="icon" href="/static/assets/favicon.ico">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0c130d;
    --bg-card: #121f14;
    --bg-card-hover: #162619;
    --border: #1d2e20;
    --green: #23a55a;
    --green-hover: #1e8b4c;
    --text: #e9f0ea;
    --text-2: #a8b8a9;
    --text-3: #5d6e5e;
    --max-w: 820px;
  }
  body {
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.7;
    -webkit-font-smoothing: antialiased;
  }
  a { color: var(--green); text-decoration: none; }
  a:hover { color: var(--green-hover); }
  .wrap { max-width: var(--max-w); margin: 0 auto; padding: 0 24px; }

  nav {
    position: fixed; top: 16px; left: 50%; transform: translateX(-50%); z-index: 100;
    background: rgba(14, 22, 15, 0.62);
    backdrop-filter: blur(18px) saturate(160%);
    -webkit-backdrop-filter: blur(18px) saturate(160%);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 100px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.35);
  }
  nav .inner { display: flex; align-items: center; height: 48px; padding: 0 18px; }
  nav .brand { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 16px; white-space: nowrap; color: var(--text); }
  nav .brand img { width: 24px; height: 24px; }

  .cc-hero { padding: 120px 24px 48px; text-align: center; position: relative; overflow: hidden; }
  .cc-hero .glow {
    position: absolute; width: 480px; height: 480px; top: -160px; left: 50%;
    transform: translateX(-50%); border-radius: 50%;
    background: radial-gradient(ellipse, rgba(35,165,90,0.22), transparent 70%);
    pointer-events: none;
  }
  .cc-hero h1 { font-size: 38px; font-weight: 900; letter-spacing: -0.03em; margin-bottom: 10px; position: relative; }
  .cc-hero p { color: var(--text-2); font-size: 15px; position: relative; }

  .cc-body { padding: 8px 12px 80px; }
  .cc-section {
    background: var(--bg-card); border: 1px solid var(--border); border-radius: 14px;
    padding: 30px 34px; margin-bottom: 18px;
  }
  .cc-section h2 { font-size: 20px; font-weight: 700; margin-bottom: 12px; line-height: 1.4; }
  .cc-section p { color: var(--text-2); font-size: 14px; margin-bottom: 12px; line-height: 1.7; }
  .cc-section p:last-child { margin-bottom: 0; }
  .cc-cta {
    background: linear-gradient(135deg, rgba(35,165,90,0.1), rgba(45,212,191,0.06));
    border: 1px solid rgba(35,165,90,0.3); border-radius: 14px;
    padding: 30px 34px; margin-bottom: 18px; text-align: center;
  }
  .cc-cta h2 { font-size: 20px; font-weight: 700; margin-bottom: 10px; color: #6fdf9e; }
  .cc-cta p { color: var(--text-2); font-size: 14px; margin-bottom: 20px; }
  .btn {
    border: none; border-radius: 10px; padding: 12px 28px; font-size: 14px;
    font-weight: 600; cursor: pointer; transition: all 0.2s; font-family: inherit;
    display: inline-block;
  }
  .btn-green { background: var(--green); color: #fff; }
  .btn-green:hover { background: var(--green-hover); transform: translateY(-1px); color: #fff; }

  footer { padding: 32px 0 56px; text-align: center; color: var(--text-3); font-size: 13px; }
  footer a { color: var(--text-2); }

  @media (max-width: 600px) {
    .wrap { padding: 0 20px; }
    nav { top: 12px; }
    .cc-hero { padding: 108px 20px 32px; }
    .cc-hero h1 { font-size: 28px; }
    .cc-body { padding: 0 8px 56px; }
    .cc-section { padding: 24px 22px; margin-bottom: 14px; }
    .cc-cta { padding: 24px 22px; }
  }
</style>
</head>
<body>

<nav>
  <div class="inner">
    <a class="brand" href="/"><img src="/static/assets/favicon.ico" alt="" onerror="this.style.display='none'"> Fern</a>
  </div>
</nav>

<header class="cc-hero">
  <div class="glow"></div>
  <h1>Our action against EU Chat Control</h1>
  <p>Why we oppose mass scanning of private conversations.</p>
</header>

<div class="cc-body wrap">

  <section class="cc-section">
    <h2>What is Fern?</h2>
    <p>Fern is an organisation aiming to fight back against EU Chat Control, because we believe it violates the privacy of every human. The world is turning into a surveillance globe, with thousands of cases of innocent people being framed for crimes committed by the rich, who need a scapegoat to put the case to bed. They aim to find the smallest "crime", manipulate the evidence, and bring it all to court. These people are targeted from, you guessed it, chat control. Evidence obtained without permission, but no one will question it, because the accused victim often cannot afford a lawyer. This is a prime example of modern day society.</p>
  </section>

  <section class="cc-section">
    <h2>Our mission</h2>
    <p>Fern is aimed at giving that privacy back while using conglomerate companies who would basically have to shut down to shut us down, creating a major challenge for them. We are going to make sure to operate as long as possible while sending a clear message. Privacy shouldn't be for people who have something to hide, it should be for every person who cares about their privacy and does not want their messages read. Because why should these organisations read our messages, but we can't read theirs? Not very "equal", is it.</p>
  </section>

  <section class="cc-section">
    <h2>The security risk</h2>
    <p>Chat control, regardless of their goals, poses a security risk. As we have seen in the past, these organisations can get hacked, meaning your data gets leaked, but not theirs. They don't care enough about protecting your data because they know not enough people care to protest. This means photos and chats with sensitive information can be shown to a bad actor, putting your safety at risk. Next thing you know, you're getting blackmailed, which could ruin your career.</p>
  </section>

  <section class="cc-section">
    <h2>Join us</h2>
    <p>Encrypt your messages today, fight against chat control.</p>
  </section>

  <div class="cc-cta">
    <h2>Privacy is not negotiable.</h2>
    <p>Encrypt your messages today, fight against chat control.</p>
    <a class="btn btn-green" href="/">Back to Fern</a>
  </div>

  <section class="cc-section" style="text-align:center">
    <p style="color:var(--text-3);font-size:13px;margin-bottom:0">Source: <a href="https://www.euronews.com/my-europe/2026/07/18/why-the-eus-so-called-chat-control-law-has-privacy-experts-up-in-arms" target="_blank" rel="noopener">Euronews: Why the EU's so-called "Chat Control" law has privacy experts up in arms</a></p>
  </section>

</div>

<footer>
  <div class="wrap">
    <p>&copy; 2026 Fern &nbsp;&middot;&nbsp; <a href="/">Home</a> &nbsp;&middot;&nbsp; <a href="/tos">Terms of Service</a></p>
  </div>
</footer>

</body>
</html>"""


CONFIRM_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fern</title>
<link rel="icon" type="image/png" href="/static/assets/fern_logo_circle.png">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0c130d;
    --bg-card: #121f14;
    --bg-card-hover: #162619;
    --border: #1d2e20;
    --green: #23a55a;
    --green-hover: #1e8b4c;
    --text: #e9f0ea;
    --text-2: #a8b8a9;
    --text-3: #5d6e5e;
    --max-w: 820px;
  }
  body {
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.7;
    -webkit-font-smoothing: antialiased;
  }
  a { color: var(--green); text-decoration: none; }
  a:hover { color: var(--green-hover); }
  .wrap { max-width: var(--max-w); margin: 0 auto; padding: 0 24px; }

  nav {
    position: fixed; top: 16px; left: 50%; transform: translateX(-50%); z-index: 100;
    background: rgba(14, 22, 15, 0.62);
    backdrop-filter: blur(18px) saturate(160%);
    -webkit-backdrop-filter: blur(18px) saturate(160%);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 100px;
    box-shadow: 0 6px 24px rgba(0,0,0,0.35);
  }
  nav .inner { display: flex; align-items: center; height: 48px; padding: 0 18px; }
  nav .brand { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 16px; white-space: nowrap; color: var(--text); }
  nav .brand img { width: 24px; height: 24px; }

  .confirm-hero { padding: 120px 24px 40px; text-align: center; position: relative; overflow: hidden; }
  .confirm-hero .glow {
    position: absolute; width: 480px; height: 480px; top: -160px; left: 50%;
    transform: translateX(-50%); border-radius: 50%;
    background: radial-gradient(ellipse, rgba(35,165,90,0.22), transparent 70%);
    pointer-events: none;
  }
  .confirm-hero .check {
    width: 72px; height: 72px; border-radius: 50%; margin: 0 auto 24px;
    background: rgba(35,165,90,0.15); border: 1px solid rgba(35,165,90,0.4);
    display: flex; align-items: center; justify-content: center;
    font-size: 30px; color: #6fdf9e; position: relative;
  }
  .confirm-hero h1 { font-size: 36px; font-weight: 900; letter-spacing: -0.03em; margin-bottom: 10px; position: relative; }
  .confirm-hero p { color: var(--text-2); font-size: 15px; position: relative; }

  .confirm-body { padding: 8px 12px 80px; }
  .confirm-card {
    background: var(--bg-card); border: 1px solid var(--border); border-radius: 14px;
    padding: 30px 34px; margin-bottom: 18px; text-align: center;
  }
  .confirm-card h2 { font-size: 20px; font-weight: 700; margin-bottom: 12px; }
  .confirm-card p { color: var(--text-2); font-size: 14px; margin-bottom: 10px; line-height: 1.7; }
  .confirm-card p:last-child { margin-bottom: 0; }
  .btn {
    border: none; border-radius: 10px; padding: 13px 30px; font-size: 14px;
    font-weight: 600; cursor: pointer; transition: all 0.2s; font-family: inherit;
    display: inline-block;
  }
  .btn-green { background: var(--green); color: #fff; }
  .btn-green:hover { background: var(--green-hover); transform: translateY(-1px); color: #fff; }
  .btn-out { background: rgba(255,255,255,0.03); color: var(--text); border: 1px solid var(--border); }
  .btn-out:hover { border-color: var(--green); background: rgba(35,165,90,0.08); }
  .confirm-cta { display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; margin-top: 8px; }

  footer { padding: 32px 0 56px; text-align: center; color: var(--text-3); font-size: 13px; }
  footer a { color: var(--text-2); }

  @media (max-width: 600px) {
    .wrap { padding: 0 20px; }
    nav { top: 12px; }
    .confirm-hero { padding: 108px 20px 32px; }
    .confirm-hero h1 { font-size: 28px; }
    .confirm-body { padding: 0 8px 56px; }
    .confirm-card { padding: 24px 22px; margin-bottom: 14px; }
  }
</style>
</head>
<body>

<nav>
  <div class="inner">
    <a class="brand" href="/"><img src="/static/assets/fern_logo_circle.png" alt="" onerror="this.style.display='none'"> Fern</a>
  </div>
</nav>

<header class="confirm-hero">
  <div class="glow"></div>
  <div class="wrap">
    <div class="check"><i class="fa-solid fa-check"></i></div>
    <h1>Payment received</h1>
    <p>Thanks for supporting Fern.</p>
  </div>
</header>

<div class="confirm-body wrap">
  <div class="confirm-card">
    <h2>Check your email for your key</h2>
    <p>Your license key is on its way. If you don't see it within a few minutes, check your spam folder.</p>
  </div>
  <div class="confirm-card">
    <h2>Get help from the community</h2>
    <p>Join our Discord server for support, updates and early access to new features.</p>
    <div class="confirm-cta">
      <a class="btn btn-green" href="/invite">Join our Discord</a>
      <a class="btn btn-out" href="/">Back to home</a>
    </div>
  </div>
</div>

<footer>
  <div class="wrap">
    <p>&copy; 2026 Fern &nbsp;&middot;&nbsp; <a href="/">Home</a> &nbsp;&middot;&nbsp; <a href="/tos">Terms of Service</a></p>
  </div>
</footer>

</body>
</html>"""


ADMIN_LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Fern Admin</title>
  <link rel="icon" href="/static/assets/favicon.ico">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg: #101a12;
      --card: #16261a;
      --input: #0e1710;
      --input-focus: #132216;
      --border: rgba(255, 255, 255, 0.14);
      --border-hover: rgba(255, 255, 255, 0.28);
      --green: #23a55a;
      --green-hover: #1e8e4c;
      --text: #e6eee7;
      --text-muted: #8fa290;
      --text-dim: #5c705e;
      --danger: #f87171;
      --danger-bg: rgba(239, 68, 68, 0.1);
      --danger-border: rgba(239, 68, 68, 0.22);
    }

    html, body {
      width: 100%;
      height: 100%;
      background: var(--bg);
      background-image: radial-gradient(850px circle at 50% 22%, #1b3021 0%, #142217 50%, var(--bg) 100%);
      color: var(--text);
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      -webkit-font-smoothing: antialiased;
      overflow-x: hidden;
    }

    body {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 24px;
      position: relative;
    }

    .login-container {
      width: 100%;
      max-width: 400px;
      position: relative;
      z-index: 1;
    }

    .login-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 28px;
      box-shadow: 0 16px 36px rgba(0, 0, 0, 0.4);
    }

    .back-link {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--text-dim);
      font-size: 12px;
      font-weight: 500;
      text-decoration: none;
      margin-bottom: 18px;
      transition: color 0.15s ease;
    }

    .back-link:hover {
      color: var(--text);
    }

    .back-link i {
      font-size: 11px;
    }

    .brand-header {
      margin-bottom: 18px;
    }

    .brand-title {
      font-size: 19px;
      font-weight: 600;
      letter-spacing: -0.3px;
      color: #ffffff;
      margin-bottom: 3px;
    }

    .brand-subtitle {
      font-size: 12.5px;
      color: var(--text-muted);
      font-weight: 400;
      line-height: 1.4;
    }

    .login-error {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--danger);
      background: var(--danger-bg);
      border: 1px solid var(--danger-border);
      padding: 8px 11px;
      border-radius: 8px;
      font-size: 11.5px;
      font-weight: 500;
      margin-bottom: 14px;
    }

    .login-error i {
      font-size: 12px;
      flex-shrink: 0;
    }

    .field-group {
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-bottom: 16px;
    }

    .input-wrap {
      position: relative;
      display: flex;
      align-items: center;
    }

    .field-input {
      width: 100%;
      background: var(--input);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10.5px 36px 10.5px 12px;
      color: #ffffff;
      font-size: 13px;
      font-family: inherit;
      outline: none;
      transition: border-color 0.15s ease, background 0.15s ease, box-shadow 0.15s ease;
    }

    .field-input::placeholder {
      color: var(--text-dim);
      font-size: 12.5px;
    }

    .field-input:hover {
      border-color: var(--border-hover);
    }

    .field-input:focus {
      border-color: var(--green);
      background: var(--input-focus);
      box-shadow: 0 0 0 2px rgba(35, 165, 90, 0.16);
    }

    .input-action-btn {
      position: absolute;
      right: 8px;
      background: transparent;
      border: none;
      color: var(--text-dim);
      font-size: 12.5px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 24px;
      height: 24px;
      border-radius: 4px;
      transition: color 0.15s ease;
    }

    .input-action-btn:hover {
      color: #ffffff;
    }

    .submit-btn {
      width: 100%;
      background: var(--green);
      color: #ffffff;
      border: none;
      border-radius: 8px;
      padding: 11px 16px;
      font-size: 13px;
      font-weight: 600;
      font-family: inherit;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      transition: background 0.15s ease;
    }

    .submit-btn:hover {
      background: var(--green-hover);
    }
  </style>
</head>
<body>
  <div class="login-container">
    <div class="login-card">
      <a href="/" class="back-link">
        <i class="fa-solid fa-arrow-left"></i>
        <span>Back</span>
      </a>
      <div class="brand-header">
        <h1 class="brand-title">Admin Panel</h1>
        <p class="brand-subtitle">Enter your admin master key to continue.</p>
      </div>
      {% if error %}
      <div class="login-error">
        <i class="fa-solid fa-circle-exclamation"></i>
        <span>{{ error }}</span>
      </div>
      {% endif %}
      <form method="post">
        <div class="field-group">
          <div class="input-wrap">
            <input name="admin_key" id="admin_key_input" type="password" class="field-input" placeholder="Admin master key" autocomplete="off" spellcheck="false" required autofocus />
            <button type="button" id="toggle-key-btn" class="input-action-btn" aria-label="Toggle password visibility">
              <i class="fa-regular fa-eye"></i>
            </button>
          </div>
        </div>
        <button type="submit" class="submit-btn">
          <i class="fa-solid fa-lock-open"></i>
          <span>Unlock Admin</span>
        </button>
      </form>
    </div>
  </div>
  <script>
    const btn = document.getElementById('toggle-key-btn');
    const inp = document.getElementById('admin_key_input');
    if (btn && inp) {
      btn.addEventListener('click', () => {
        const isPwd = inp.type === 'password';
        inp.type = isPwd ? 'text' : 'password';
        btn.innerHTML = isPwd ? '<i class="fa-regular fa-eye-slash"></i>' : '<i class="fa-regular fa-eye"></i>';
      });
    }
  </script>
</body>
</html>"""


ADMIN_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Fern</title>
<link rel="icon" href="/static/assets/favicon.ico">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg-1: #061007;
    --bg-2: #0c170f;
    --bg-hover: rgba(255, 255, 255, 0.04);
    --bg-active: rgba(35, 165, 90, 0.12);
    --border: rgba(255, 255, 255, 0.08);
    --border-hover: rgba(255, 255, 255, 0.16);
    --accent: #23a55a;
    --accent-hover: #1e8e4c;
    --text-primary: #f0f6f1;
    --text-normal: #d0ded2;
    --text-secondary: #8e9f90;
    --text-muted: #5e7060;
    --danger: #f87171;
    --danger-bg: rgba(239, 68, 68, 0.1);
    --warn: #fbbf24;
  }
  html, body { height: 100%; }
  body {
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    background: var(--bg-1); color: var(--text-normal); line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    zoom: 1.10;
  }
  a { color: inherit; text-decoration: none; }
  .layout { display: flex; min-height: 100vh; }
  .sidebar {
    width: 220px; flex-shrink: 0; background: #081209;
    border-right: 1px solid var(--border);
    padding: 16px 12px; display: flex; flex-direction: column; gap: 4px;
    position: sticky; top: 0; height: 100vh; overflow-y: auto;
  }
  .sidebar-brand { display: flex; align-items: center; gap: 10px; padding: 6px 10px 18px; }
  .sidebar-brand img { width: 26px; height: 26px; border-radius: 6px; }
  .sidebar-brand h1 { font-size: 17px; font-weight: 700; color: var(--text-primary); }
  .sidebar-brand h1 span { color: var(--accent); }
  .nav-item {
    display: flex; align-items: center; gap: 10px; width: 100%;
    padding: 8.5px 12px; border: none; border-radius: 8px;
    background: transparent; color: var(--text-secondary);
    font-size: 13.5px; font-weight: 500; font-family: inherit; text-align: left;
    cursor: pointer; position: relative;
    transition: background 0.15s, color 0.15s;
  }
  .nav-item i { width: 18px; font-size: 14px; text-align: center; color: var(--text-muted); }
  .nav-item:hover { background: var(--bg-hover); color: var(--text-primary); }
  .nav-item:hover i { color: var(--text-normal); }
  .nav-item.active { background: var(--bg-active); color: var(--accent); font-weight: 600; }
  .nav-item.active i { color: var(--accent); }
  .sidebar-footer { margin-top: auto; padding: 14px 6px 4px; display: flex; flex-direction: column; gap: 6px; }
  .sidebar-footer .btn { text-align: center; justify-content: center; padding: 8px 12px; font-size: 12.5px; }
  .content { flex: 1; min-width: 0; padding: 22px 30px; }
  .content-head { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 18px; }
  .content-head h2 { font-size: 20px; font-weight: 700; color: var(--text-primary); letter-spacing: -0.2px; }
  .pane { display: none; }
  .pane.active { display: block; animation: fadeIn 150ms ease; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; transform: none; } }
  .btn {
    background: var(--accent); color: #fff; border: none; border-radius: 7px;
    padding: 8px 16px; font-size: 13px; font-weight: 600; cursor: pointer;
    font-family: inherit; display: inline-flex; align-items: center; justify-content: center; gap: 6px;
    transition: background 0.15s ease;
  }
  .btn:hover { background: var(--accent-hover); }
  .btn:disabled { opacity: 0.45; cursor: not-allowed; }
  .btn.ghost { background: var(--bg-hover); color: var(--text-secondary); border: 1px solid var(--border); }
  .btn.ghost:hover { background: rgba(255,255,255,0.08); color: var(--text-primary); }
  .btn.danger { background: var(--danger-bg); color: var(--danger); border: 1px solid rgba(248, 113, 113, 0.2); }
  .btn.danger:hover { background: rgba(248, 113, 113, 0.22); }
  .btn.sm { padding: 6px 12px; font-size: 12px; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin-bottom: 18px; }
  .stat { background: var(--bg-2); border: 1px solid var(--border); border-radius: 10px; padding: 14px 18px; }
  .stat .num { font-size: 24px; font-weight: 700; color: var(--text-primary); line-height: 1.2; }
  .stat .lbl { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; font-weight: 600; }
  .live-hourly { display: flex; align-items: flex-end; gap: 4px; height: 64px; margin-top: 14px; }
  .live-hourly .bar-wrap { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 4px; height: 100%; }
  .live-hourly .bar { width: 100%; background: rgba(35,165,90,0.2); border-radius: 3px 3px 0 0; transition: height 0.25s; }
  .live-hourly .bar.hi { background: var(--accent); }
  .live-hourly .bar-lbl { font-size: 9px; color: var(--text-muted); }
  .card { background: var(--bg-2); border: 1px solid var(--border); border-radius: 12px; padding: 18px 22px; margin-bottom: 18px; }
  .card h3 { font-size: 15px; font-weight: 600; color: var(--text-primary); margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }
  .card h3 i { color: var(--accent); font-size: 14px; }
  .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--border); vertical-align: middle; }
  th { color: var(--text-muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
  .mono { font-family: monospace; font-size: 12.5px; color: #8fd4a0; word-break: break-all; }
  .badge { display: inline-block; padding: 2px 7px; border-radius: 5px; font-size: 11px; font-weight: 600; }
  .badge.active { background: rgba(35,165,90,0.15); color: var(--accent); }
  .badge.trial { background: rgba(251,191,36,0.15); color: var(--warn); }
  .badge.inactive { background: var(--danger-bg); color: var(--danger); }
  label { display: block; font-size: 11.5px; color: var(--text-secondary); font-weight: 600; margin: 10px 0 5px; text-transform: uppercase; letter-spacing: 0.4px; }
  input, textarea, select {
    width: 100%; background: var(--bg-1); border: 1px solid var(--border); border-radius: 7px;
    color: var(--text-primary); padding: 9px 12px; font-size: 13.5px; font-family: inherit; outline: none;
    transition: border-color 0.15s ease;
  }
  input:focus, textarea:focus, select:focus { border-color: var(--accent); }
  select { cursor: pointer; }
  textarea { resize: vertical; min-height: 68px; }
  .msg { margin-top: 8px; font-size: 12.5px; min-height: 18px; }
  .msg.ok { color: var(--accent); }
  .msg.err { color: var(--danger); }
  .entry { border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; margin-bottom: 10px; background: var(--bg-1); }
  .entry .head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
  .entry .title { font-weight: 600; font-size: 14px; color: var(--text-primary); }
  .entry .ver { font-size: 11.5px; color: var(--accent); font-weight: 600; }
  .entry .date { font-size: 11.5px; color: var(--text-muted); }
  .entry .body { color: var(--text-secondary); font-size: 13px; white-space: pre-wrap; line-height: 1.45; }
  .entry .del { margin-left: auto; cursor: pointer; color: var(--danger); font-size: 12.5px; border: none; background: none; font-family: inherit; }
  .empty { color: var(--text-muted); font-size: 13px; padding: 12px 0; }
  .table-scroll { overflow-x: auto; }
  @media (max-width: 860px) {
    .layout { flex-direction: column; }
    .sidebar {
      width: 100%; height: auto; position: static; flex-direction: row; align-items: center;
      gap: 4px; padding: 10px; overflow-x: auto; border-right: none; border-bottom: 1px solid var(--border);
    }
    .sidebar-brand { padding: 0 8px; margin-right: auto; flex-shrink: 0; }
    .sidebar-brand h1 { font-size: 15px; }
    .sidebar-brand img { width: 22px; height: 22px; }
    .nav-item { width: auto; padding: 7px 12px; white-space: nowrap; flex-shrink: 0; font-size: 13px; }
    .sidebar-footer { flex-direction: row; margin-top: 0; padding: 0; gap: 6px; flex-shrink: 0; }
    .sidebar-footer .btn { padding: 6px 12px; font-size: 12px; }
    .content { padding: 16px; }
    .grid-2 { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-brand">
      <img src="/static/assets/favicon.ico" alt="">
      <h1>Fern</h1>
    </div>
    <button class="nav-item active" data-nav="overview"><i class="fa-solid fa-gauge-high"></i> Overview</button>
    <button class="nav-item" data-nav="licenses"><i class="fa-solid fa-tags"></i> Licenses</button>
    <button class="nav-item" data-nav="referrals"><i class="fa-solid fa-arrows-rotate"></i> Referrals</button>
    <button class="nav-item" data-nav="trials"><i class="fa-solid fa-flask"></i> Trials</button>
    <button class="nav-item" data-nav="changelog"><i class="fa-solid fa-list-check"></i> Changelog</button>
    <button class="nav-item" data-nav="accounts"><i class="fa-solid fa-users"></i> Accounts</button>
    <button class="nav-item" data-nav="status"><i class="fa-solid fa-heart-pulse"></i> Status</button>
    <div class="sidebar-footer">
      <a class="btn ghost sm" href="/app"><i class="fa-solid fa-up-right-from-square"></i> Open Fern</a>
      <a class="btn danger sm" href="/logout"><i class="fa-solid fa-right-from-bracket"></i> Log out</a>
    </div>
  </aside>

  <main class="content">
    <div class="content-head">
      <h2 data-pane-title>Overview</h2>
      <button class="btn ghost sm" onclick="loadAll()"><i class="fa-solid fa-rotate-right"></i> Refresh</button>
    </div>

    <section class="pane active" data-pane="overview">
      <div class="stats" id="stats"></div>
      <div class="card">
        <h3><i class="fa-solid fa-bolt"></i> Live Activity</h3>
        <div class="stats" id="live-stats"></div>
        <div class="live-hourly" id="live-hourly"></div>
      </div>
      <div class="grid-2">
        <div class="card">
          <h3><i class="fa-solid fa-key"></i> Create License Key</h3>
          <label>Days (0 = lifetime)</label>
          <input type="number" id="days" value="0" min="0">
          <div id="create-msg" class="msg"></div>
          <button class="btn" id="create-btn" onclick="createLicense()">Generate Key</button>
        </div>
        <div class="card">
          <h3><i class="fa-solid fa-bullhorn"></i> Post Changelog</h3>
          <label>Version (optional)</label>
          <input type="text" id="cl-version" placeholder="1.1.0">
          <label>Title</label>
          <input type="text" id="cl-title" placeholder="New change">
          <label>Body</label>
          <textarea id="cl-body" placeholder="What's new in this version..."></textarea>
          <div id="cl-msg" class="msg"></div>
          <button class="btn" id="cl-btn" onclick="postChangelog()">Publish</button>
        </div>
      </div>
    </section>

    <section class="pane" data-pane="licenses">
      <div class="card">
        <h3><i class="fa-solid fa-tags"></i> All Licenses</h3>
        <div class="table-scroll">
          <table>
            <thead><tr><th>Key</th><th>Status</th><th>Expires</th><th>Bound To</th><th>Created</th></tr></thead>
            <tbody id="lic-rows"></tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="pane" data-pane="referrals">
      <div class="card">
        <h3><i class="fa-solid fa-arrows-rotate"></i> Referrals</h3>
        <div class="table-scroll">
          <table>
            <thead><tr><th>Inviter</th><th>Invitee</th><th>Status</th><th>Created</th></tr></thead>
            <tbody id="ref-rows"></tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="pane" data-pane="trials">
      <div class="card">
        <h3><i class="fa-solid fa-flask"></i> Trials</h3>
        <div class="table-scroll">
          <table>
            <thead><tr><th>User ID</th><th>Claimed</th><th>License Key</th></tr></thead>
            <tbody id="trial-rows"></tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="pane" data-pane="changelog">
      <div class="card">
        <h3><i class="fa-solid fa-list-check"></i> Published Changelog</h3>
        <div id="cl-list"><p class="empty">Loading...</p></div>
      </div>
    </section>

    <section class="pane" data-pane="accounts">
      <div class="stats" id="acc-stats"></div>
      <div class="card">
        <h3><i class="fa-solid fa-users"></i> Accounts</h3>
        <div class="table-scroll">
          <table>
            <thead><tr><th>User ID</th><th>Key</th><th>Type</th><th>Expires</th><th>Status</th></tr></thead>
            <tbody id="acc-rows"></tbody>
          </table>
        </div>
      </div>
    </section>

    <section class="pane" data-pane="status">
      <div class="card">
        <h3><i class="fa-solid fa-tower-broadcast"></i> Banner</h3>
        <label>Level</label>
        <select id="st-banner-level">
          <option value="info">Info</option>
          <option value="warn">Warning</option>
          <option value="critical">Critical</option>
        </select>
        <label>Message (empty = no banner)</label>
        <textarea id="st-banner-text" placeholder="Scheduled maintenance tonight at 02:00 UTC..."></textarea>
      </div>
      <div class="card">
        <h3><i class="fa-solid fa-gauge-high"></i> Service Overrides</h3>
        <div class="table-scroll">
          <table>
            <thead><tr><th>Service</th><th>Override</th><th>Note</th></tr></thead>
            <tbody id="st-rows"></tbody>
          </table>
        </div>
        <div id="st-msg" class="msg"></div>
        <button class="btn" onclick="saveStatus()"><i class="fa-solid fa-floppy-disk"></i> Save Status</button>
      </div>
    </section>
  </main>
</div>

<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<script src="/static/js/bundles/admin.bundle.js?v=107"></script>
</body>
</html>"""


STATUS_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Fern</title>
<link rel="icon" href="/static/assets/favicon.ico">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0c130d; --bg-card: #121f14; --bg-hover: #1d2e20; --border: #1d2e20;
    --green: #23a55a; --text: #e9f0ea; --text-2: #a8b8a9; --text-3: #5d6e5e;
    --warn: #f0b232; --danger: #ed4245;
  }
  body { font-family: 'Inter', system-ui, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; min-height: 100vh; }
  a { color: inherit; text-decoration: none; }
  .wrap { max-width: 720px; margin: 0 auto; padding: 0 20px; }
  header { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 28px 0 20px; }
  .brand { display: flex; align-items: center; gap: 10px; font-size: 18px; font-weight: 800; }
  .brand img { width: 30px; height: 30px; border-radius: 8px; }
  .brand span { color: var(--green); }
  .back { color: var(--text-2); font-size: 13px; font-weight: 600; border: 1px solid var(--border); border-radius: 100px; padding: 7px 14px; transition: all .2s; }
  .back:hover { border-color: var(--green); color: var(--text); }
  .hero { text-align: center; padding: 30px 0 26px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--text-3); flex-shrink: 0; }
  .dot.up { background: var(--green); box-shadow: 0 0 8px rgba(35,165,90,0.7); animation: pulse 2s infinite; }
  .dot.degraded { background: var(--warn); box-shadow: 0 0 8px rgba(240,178,50,0.6); }
  .dot.down { background: var(--danger); box-shadow: 0 0 8px rgba(237,66,69,0.7); }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .4; } }
  .hero h1 { font-size: 28px; font-weight: 800; margin-top: 18px; letter-spacing: -0.02em; }
  .hero p { color: var(--text-2); font-size: 15px; margin-top: 6px; }
  #updated { color: var(--text-3); font-size: 12px; margin-top: 14px; }
  .banner { display: none; border-radius: 12px; padding: 14px 16px; font-size: 14px; margin-bottom: 18px; border: 1px solid var(--border); }
  .banner.show { display: flex; align-items: flex-start; gap: 10px; }
  .banner i { margin-top: 2px; }
  .banner.info { background: rgba(35,165,90,0.1); border-color: rgba(35,165,90,0.35); color: #6fdf9e; }
  .banner.warn { background: rgba(240,178,50,0.1); border-color: rgba(240,178,50,0.4); color: #ffd166; }
  .banner.critical { background: rgba(237,66,69,0.12); border-color: rgba(237,66,69,0.45); color: #ff8a8c; }
  .card { background: var(--bg-card); border: 1px solid var(--border); border-radius: 14px; padding: 18px 20px; margin-bottom: 12px; display: flex; align-items: center; gap: 14px; }
  .card .ico { width: 40px; height: 40px; border-radius: 10px; background: rgba(255,255,255,0.04); display: flex; align-items: center; justify-content: center; font-size: 17px; color: var(--text-2); flex-shrink: 0; }
  .card .body { flex: 1; min-width: 0; }
  .card .name { font-size: 15px; font-weight: 700; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .card .name .pill { font-size: 11px; font-weight: 700; padding: 2px 9px; border-radius: 100px; }
  .pill.up { background: rgba(35,165,90,0.15); color: var(--green); }
  .pill.degraded { background: rgba(240,178,50,0.15); color: var(--warn); }
  .pill.down { background: rgba(237,66,69,0.15); color: var(--danger); }
  .card .note { color: var(--text-3); font-size: 12px; margin-top: 3px; }
  .card .latency { text-align: right; color: var(--text-3); font-size: 12px; flex-shrink: 0; }
  .card .latency b { color: var(--text-2); font-size: 13px; font-weight: 600; }
  .discord-cta { margin-top: 18px; text-align: center; }
  .btn { display: inline-flex; align-items: center; gap: 9px; background: var(--green); color: #fff; border-radius: 10px; padding: 12px 26px; font-size: 15px; font-weight: 700; transition: background .2s; }
  .btn:hover { background: #1e8b4c; }
  footer { text-align: center; color: var(--text-3); font-size: 12px; padding: 26px 0 34px; }
  @media (max-width: 520px) {
    .hero h1 { font-size: 23px; }
    .card .latency { display: none; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <a href="/" class="brand"><img src="/static/assets/favicon.ico" alt=""><span>Fern</span></a>
    <a href="/" class="back"><i class="fa-solid fa-arrow-left"></i>&nbsp; Back</a>
  </header>

  <div class="hero">
    <h1>Service status</h1>
    <p>Live health of the Fern API, UI and Discord.</p>
    <div id="updated"></div>
  </div>

  <div class="banner" id="banner"></div>
  <div id="services"></div>

  <div class="discord-cta">
    <a class="btn" href="/invite"><i class="fa-brands fa-discord"></i> Join our Discord</a>
  </div>

  <footer>&copy; 2026 Fern &middot; Status updates every 30s</footer>
</div>

<script src="/static/js/bundles/status.bundle.js?v=105"></script>
</body>
</html>"""


@app.before_request
def require_login():
    if request.endpoint in {
        "landing", "login", "static", "index", "tos", "tos_page", "chat_control_page",
        "confirm_page", "invite", "status_page", "api_status", "api_version",
        "api_changelog", "api_safety_score", "qr_login_generate", "qr_login_status",
        "qr_login_image", "admin_panel", "api_admin_status", "api_admin_data",
        "api_admin_live", "api_admin_accounts", "api_admin_create_license",
        "api_admin_changelog", "api_admin_changelog_delete"
    } or request.path in {"/tos", "/chat-control", "/confirm", "/invite", "/status"} or request.path.startswith(
        ("/static/", "/socket.io/", "/qr/", "/api/qr-login/", "/api/validate-license", "/api/upload/", "/api/status", "/api/version", "/api/changelog", "/api/safety-score", "/admin", "/api/admin/")
    ):
        return
    if not authenticated():
        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("login", next=request.path), code=302)

def _sync_token_to_backend(token: str, licence: str) -> None:
    return
    try:
        requests.put(
            f"{BACKEND_BASE_URL}/admin/token",
            json={"token": token, "licence": licence},
            timeout=5,
        )
    except Exception:
        pass

@app.route("/login", methods=["GET", "POST"])
def login():
    error = request.args.get("error", "")
    next_path = request.args.get("next") or DEFAULT_ROUTE

    auth_id = session.get("auth_id")
    token = db.get_document(db_conn, "tokens", auth_id)
    if auth_id and token:
        return redirect(DEFAULT_ROUTE, code=302)

    confirm_qr = request.args.get("confirm_qr")
    if confirm_qr:
        with _qr_lock:
            qr_sess = _qr_sessions.get(confirm_qr)
        if qr_sess and qr_sess.token:
            token = qr_sess.token
            try:
                user = discord_api_request("/users/@me", token, method="GET")
                if user.get("bot"):
                    error = "Bot tokens are not allowed."
                else:
                    user_id = str(user.get("id", ""))
                    license_key = None
                    try:
                        resp = http_requests.get(f"{LICENSE_SERVER_URL}/api/user-status?user_id={user_id}", timeout=5)
                        if resp.status_code == 200:
                            data = resp.json()
                            if data.get("is_fern_user") and data.get("license_key"):
                                val = validate_license_with_server(data["license_key"], user_id)
                                if val.get("valid"):
                                    license_key = data["license_key"]
                    except Exception as e:
                        logger.warning("Error checking user-status on QR login: %s", e)

                    if not license_key:
                        trial_result = claim_trial_from_server(user_id)
                        if trial_result.get("success"):
                            license_key = trial_result["license_key"]
                            session["is_trial"] = True
                        else:
                            error = trial_result.get("error", "No active license found. Please sign in with token and license key.")

                    if license_key:
                        auth_id = secrets.token_urlsafe(32)
                        _token_to_auth[token] = auth_id
                        db.add_document(db_conn, "tokens", auth_id, token)
                        #
                        if hasattr(db_conn, 'commit'):
                            db_conn.commit()
                            print(f"[DEBUG] Committed token for auth_id={auth_id}")
                        #

                        session["auth_id"] = auth_id
                        session["licence"] = license_key
                        if license_key.startswith("TRIAL-"):
                            session["is_trial"] = True
                        db.add_document(db_conn, "licences", auth_id, license_key)
                        session.permanent = True
                        if auth_id in _onliners:
                            _onliners[auth_id].stop()
                        onliner = BackgroundOnliner(token, auth_id)
                        onliner.start()
                        _onliners[auth_id] = onliner
                        _sync_token_to_backend(token, license_key)
                        with _qr_lock:
                            _qr_sessions.pop(confirm_qr, None)
                        if not next_path.startswith("/") or next_path.startswith("/login"):
                            next_path = DEFAULT_ROUTE
                        return redirect(next_path, code=302)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    error = "Token is invalid."
                else:
                    error = f"Error from Discord ({exc.code})."
            except urllib.error.URLError:
                error = "No connection made to Discord."
        else:
            error = "QR login session expired or invalid."

        if error:
            return redirect(url_for("login", error=error, next=next_path), code=302)

    if request.method == "POST":
        token = (request.form.get("bot_token") or "").strip()
        if token.lower().startswith("bot "):
            token = token[4:].strip()
        if not token:
            error = "Missing token."
        else:
            try:
                user = discord_api_request("/users/@me", token, method="GET")
                if user.get("bot"):
                    error = "Bot tokens are not allowed."
                else:
                    user_id = str(user.get("id", ""))
                    license_key = (request.form.get("license_key") or "").strip()
                    if not license_key:
                        trial_result = claim_trial_from_server(user_id)
                        if trial_result.get("success"):
                            license_key = trial_result["license_key"]
                            session["is_trial"] = True
                        else:
                            error = trial_result.get("error", "Trial unavailable. Please enter a license key.")
                    if license_key:
                        license_result = validate_license_with_server(license_key, user_id)
                        if not license_result.get("valid"):
                            error = license_result.get("message", "License invalid.")
                        else:
                            auth_id = secrets.token_urlsafe(32)
                            _token_to_auth[token] = auth_id
                            db.add_document(db_conn, "tokens", auth_id, token)
                            session["auth_id"] = auth_id
                            session["licence"] = license_key
                            if license_key.startswith("TRIAL-") or license_result.get("trial"):
                                session["is_trial"] = True
                            db.add_document(db_conn, "licences", auth_id, license_key)
                            session.permanent = True
                            if auth_id in _onliners:
                                _onliners[auth_id].stop()
                            onliner = BackgroundOnliner(token, auth_id)
                            onliner.start()
                            _onliners[auth_id] = onliner
                            _sync_token_to_backend(token, license_key)
                            if not next_path.startswith("/") or next_path.startswith("/login"):
                                next_path = DEFAULT_ROUTE
                            return redirect(next_path, code=302)
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    error = "Token is invalid."
                else:
                    error = f"Error from Discord ({exc.code})."
            except urllib.error.URLError:
                error = "No connection made to Discord."

    return render_template_string(LOGIN_TEMPLATE, error=error, next_path=next_path)

@app.route("/logout")
def logout():
    auth_id = session.get("auth_id")
    if auth_id:
        token = db.delete_document(db_conn, "tokens", auth_id)
        if token:
            _token_to_auth.pop(token, None)
        # Detener onliner al hacer logout
        onliner = _onliners.pop(auth_id, None)
        if onliner:
            onliner.stop()
    session.clear()
    error = request.args.get("error", "")
    if error:
        return redirect(url_for("login", error=error), code=302)
    return redirect(url_for("login"), code=302)

def _clean_expired_qr():
    now = time.time()
    with _qr_lock:
        expired = [sid for sid, s in list(_qr_sessions.items()) if now - (getattr(s, "_created_at", 0)) > _QR_TTL]
        for sid in expired:
            _qr_sessions.pop(sid, None)

@app.route("/api/qr-login/generate")
def qr_login_generate():
    _clean_expired_qr()
    sid = secrets.token_urlsafe(16)
    qr_session = RemoteAuthSession(sid)
    with _qr_lock:
        _qr_sessions[sid] = qr_session
    client = QrAuthClient(qr_session)
    client.start()
    return jsonify({"session_id": sid})

@app.route("/api/qr-login/status/<session_id>")
def qr_login_status(session_id):
    _clean_expired_qr()
    with _qr_lock:
        s = _qr_sessions.get(session_id)
    if not s:
        return jsonify({"status": "expired"})
    if s.token:
        return jsonify({"status": "confirmed"})
    if s.user_id:
        avatar_url = f"https://cdn.discordapp.com/avatars/{s.user_id}/{s.avatar_hash}.png" if s.avatar_hash else ""
        return jsonify({"status": "scanned", "user_id": s.user_id, "username": s.username, "avatar_url": avatar_url})
    if s.fingerprint:
        qr_url = f"https://discord.com/ra/{s.fingerprint}"
        return jsonify({"status": "pending", "qr_url": qr_url})
    if s.error:
        return jsonify({"status": "error", "error": s.error})
    return jsonify({"status": "waiting"})

@app.route("/api/qr-login/qr/<session_id>")
def qr_login_image(session_id):
    with _qr_lock:
        s = _qr_sessions.get(session_id)
    if not s or not s.fingerprint:
        return jsonify({"error": "not_found"}), 404
    
    qr_data = f"https://discord.com/ra/{s.fingerprint}"
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=8,
        border=2,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#e9f0ea", back_color="#0e1710").convert("RGBA")
    
    logo_path = os.path.join(BASE_DIR, "static/assets/fern_icon.png")
    if os.path.exists(logo_path):
        try:
            logo = PilImage.open(logo_path).convert("RGBA")
            qr_w, qr_h = img.size
            target_size = int(qr_w * 0.32)
            logo_w, logo_h = logo.size
            scale = target_size / max(logo_w, logo_h)
            new_w = max(1, int(logo_w * scale))
            new_h = max(1, int(logo_h * scale))
            logo = logo.resize((new_w, new_h), PilImage.Resampling.LANCZOS)

            pad = 12
            canvas_size = target_size + pad * 2
            square_logo = PilImage.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
            offset_x = (canvas_size - new_w) // 2
            offset_y = (canvas_size - new_h) // 2
            square_logo.paste(logo, (offset_x, offset_y), logo)
            alpha = square_logo.getchannel("A")
            outer_dark_mask = alpha.filter(ImageFilter.MaxFilter(9))
            border_mask = alpha.filter(ImageFilter.MaxFilter(5))
            inner_mask = alpha

            dark_color = (14, 23, 16, 255)
            border_color = (233, 240, 234, 255)

            badge = PilImage.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
            dark_layer = PilImage.new("RGBA", (canvas_size, canvas_size), dark_color)
            border_layer = PilImage.new("RGBA", (canvas_size, canvas_size), border_color)

            badge.paste(dark_layer, (0, 0), outer_dark_mask)
            badge.paste(border_layer, (0, 0), border_mask)
            badge.paste(dark_layer, (0, 0), inner_mask)

            pos = ((qr_w - canvas_size) // 2, (qr_h - canvas_size) // 2)
            img.paste(badge, pos, badge)
        except Exception as e:
            logger.warning("Could not overlay logo on QR: %s", e)
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue(), 200, {"Content-Type": "image/png", "Cache-Control": "no-cache, no-store, must-revalidate"}

def _fetch_url(url):
    identity = _get_identity()
    dl_headers = identity.browse_headers()
    dl_headers["accept-encoding"] = "gzip, deflate, br"
    resp = requests.get(url, headers=dl_headers, timeout=30)
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")

@app.route("/api/proxy")
def api_proxy():
    url = request.args.get("url")
    if not url:
        return "", 400
    try:
        data, ctype = _fetch_url(url)
        return data, 200, {
            "Content-Type": ctype,
            "Content-Length": str(len(data)),
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=86400",
        }
    except requests.RequestException:
        return "", 502

@app.route("/api/download")
def api_download():
    url = request.args.get("url")
    name = request.args.get("name", "download")
    if not url:
        return jsonify({"error": "missing url"}), 400
    try:
        data, ctype = _fetch_url(url)
        return data, 200, {
            "Content-Disposition": f'attachment; filename="{name}"',
            "Content-Type": ctype,
            "Content-Length": str(len(data)),
        }
    except requests.RequestException:
        return jsonify({"error": "download failed"}), 502

@app.route("/api/session")
def api_session():
    return jsonify({"authenticated": True})

@app.route("/api/me")
def api_me():
    try:
        token = bot_token()
        try:
            user = discord_api_request("/users/@me", token, method="GET")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                session.clear()
                return jsonify({"error": "expired_session"}), 401
            return jsonify({"error": "discord_api_failed"}), 502
        except urllib.error.URLError:
            return jsonify({"error": "discord_unavailable"}), 502
        _wait_for_presences()
        return jsonify(user_payload(user))
    except Exception as e:
        logger.error(e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/guilds")
def api_guilds():
    token = bot_token()
    try:
        guilds = discord_api_request("/users/@me/guilds", token, method="GET")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        return jsonify([])
    except urllib.error.URLError:
        return jsonify([])
    return jsonify([guild_payload(g) for g in guilds])

@app.route("/api/dms")
def api_dms():
    try:
        token = bot_token()
        _wait_for_presences()
        try:
            channels = discord_api_request("/users/@me/channels", token, method="GET")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                session.clear()
                return jsonify({"items": [], "reason": "session_expired"}), 401
            return jsonify({"items": [], "reason": "discord_api_error"})
        except urllib.error.URLError:
            return jsonify({"items": [], "reason": "discord_unavailable"})
        dm_channels = [c for c in channels if c.get("type") in (1, 3)]
        dm_channels.sort(key=lambda c: c.get("last_message_id") or "", reverse=True)
        return jsonify({"items": [dm_payload(c) for c in dm_channels], "reason": ""})
    except Exception as e:
        logger.error(e)
        return jsonify({"items": [], "reason": str(e)}), 500

@app.route("/api/friends", methods=["GET", "POST"])
def api_friends():
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401
    _wait_for_presences()

    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        user_id = str(body.get("user_id") or "").strip() or None
        username = str(body.get("username") or "").strip() or None
        discriminator = str(body.get("discriminator") or "").strip() or None
        captcha = str(body.get("captcha") or "").strip() or None
        rqtoken = str(body.get("rqtoken") or "").strip() or None

        if user_id and not username:
            return jsonify({"error": "use username (and discriminator) to send friend requests"}), 400
        if not username:
            return jsonify({"error": "missing_username"}), 400

        success, extra_data, error = send_friend_request(
            token,
            username=username,
            discriminator=discriminator,
            captcha=captcha,
            rqtoken=rqtoken,
        )
        # logger.info("send_friend_request result: success=%s error=%s extra=%s", success, error, extra_data)

        if not success:
            if error == "captcha_required":
                return (
                    jsonify(
                        {
                            "error": "captcha_required",
                            "message": "Complete this action on the original Discord client.",
                        }
                    ),
                    428,
                )
            if error == "user_not_found":
                return jsonify({"error": "user_not_found"}), 404
            if error == "rate_limited":
                return jsonify({"error": "rate_limited"}), 429
            if error == "expired_session":
                session.clear()
                return jsonify({"error": "expired_session"}), 401
            if error == "permission_denied":
                return jsonify({"error": "permission_denied"}), 403
            return jsonify({"error": error}), 400

        relationships = get_relationships(token)
        return jsonify(relationships), 201

    relationships = get_relationships(token)
    return jsonify(relationships)

@app.route("/api/friends/<friend_id>", methods=["DELETE", "POST"])
def api_manage_friend(friend_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401

    if request.method == "DELETE":
        try:
            discord_api_request(f"/users/@me/relationships/{friend_id}", token, method="DELETE")
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                session.clear()
                return jsonify({"error": "expired_session"}), 401
            if exc.code == 404:
                return jsonify({"error": "relationship_not_found"}), 404
            return jsonify({"error": f"discord_error_{exc.code}", "detail": str(exc)}), exc.code
        return jsonify(get_relationships(token))

    success, error = accept_friend_request(token, friend_id)
    if not success:
        if error == "expired_session":
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if error == "relationship_not_found":
            return jsonify({"error": "relationship_not_found"}), 404
        if error == "user_not_found":
            return jsonify({"error": "user_not_found"}), 404
        if error == "rate_limited":
            return jsonify({"error": "rate_limited"}), 429
        if error == "permission_denied":
            return jsonify({"error": "permission_denied"}), 403
        return jsonify({"error": error}), 400
    return jsonify(get_relationships(token))

@app.route("/api/friends/<friend_id>/block", methods=["POST", "DELETE"])
def api_block_unblock(friend_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401

    if request.method == "POST":
        method = "PUT"
        json_data = {"type": 2}
    else:  # DELETE
        method = "DELETE"
        json_data = None

    try:
        discord_api_request(
            f"/users/@me/relationships/{friend_id}",
            token,
            method=method,
            json_data=json_data,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 404:
            return jsonify({"error": "relationship_not_found"}), 404
        if exc.code == 400:
            return jsonify({"error": "bad_request"}), 400
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code
    return jsonify(get_relationships(token))

@app.route("/api/read/<channel_id>", methods=["GET", "POST"])
def api_read_cursor(channel_id):
    auth_id = session.get("auth_id")
    if not auth_id:
        return jsonify({"error": "no_session"}), 401
    if request.method == "GET":
        db_row = db.get_document(db_conn, "read_cursors", auth_id)
        if not isinstance(db_row, dict):
            db_row = {}
        return jsonify({
            "channel_id": channel_id,
            "last_message_id": db_row.get(channel_id, ""),
        })
    body = request.get_json(silent=True) or {}
    last_message_id = str(body.get("last_message_id", ""))
    user_cursors = db.get_document(db_conn, "read_cursors", auth_id)
    if not user_cursors or not isinstance(user_cursors, dict):
        db.add_document(db_conn, "read_cursors", auth_id, {})
        user_cursors = {}
    user_cursors[channel_id] = last_message_id
    db.add_document(db_conn, "read_cursors", auth_id, user_cursors)
    return jsonify({"ok": True})

@app.route("/api/ring/<channel_id>", methods=["POST"])
def api_ring_channel(channel_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401
    auth_id = session.get("auth_id", "")
    licence = session.get("licence") or db.get_document(db_conn, "licences", auth_id) or "ADMIN"
    if licence:
        _sync_token_to_backend(token, licence)
    try:
        payload = {"licence": licence, "auth_id": auth_id, "token": token}
        resp = requests.post(
            f"{BACKEND_BASE_URL}/ring/discord/{channel_id}",
            cookies=request.cookies,
            json=payload,
            timeout=20,
        )
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))
    except Exception as e:
        logger.error("Ring backend request failed: %s", e)
        return jsonify({"error": str(e)}), 502

@app.route("/api/leave-call", methods=["POST"])
def api_leave_call():
    licence = session.get("licence") or db.get_document(db_conn, "licences", session.get("auth_id", "")) or "ADMIN"
    try:
        requests.post(f"{BACKEND_BASE_URL}/call/leave/discord/{licence}", cookies=request.cookies, timeout=5)
    except Exception:
        pass
    return jsonify({"status": "ok"})

@app.route("/api/upload/<channel_id>", methods=["POST"])
def api_upload(channel_id):
    try:
        if "file" not in request.files:
            return jsonify({"error": "no_file"}), 400
        f = request.files["file"]
        data = f.read()
        resp = requests.post(
            f"{BACKEND_BASE_URL}/upload/discord/{channel_id}",
            cookies=request.cookies,
            files={"file": (f.filename or "file", data, f.mimetype or "application/octet-stream")},
            timeout=60,
        )
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("Content-Type", "application/json"))
    except requests.RequestException as e:
        logger.error(e)
        return jsonify({"error": "upload_failed"}), 502
    except Exception as e:
        logger.error(e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/messages/<dm_id>", methods=["GET", "POST"])
def api_messages(dm_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401

    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        content = str(body.get("content") or "").strip()
        if not content:
            return jsonify({"error": "empty_message"}), 400
        if len(content) > 2000:
            return jsonify({"error": "message_too_long"}), 400

        try:
            result = discord_api_request(
                f"/channels/{dm_id}/messages",
                token,
                method="POST",
                json_data={"content": content},
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                session.clear()
                return jsonify({"error": "expired_session"}), 401
            if exc.code == 403:
                return (
                    jsonify(
                        {
                            "error": "permission_denied",
                            "detail": "No tienes permiso para enviar mensajes a este canal.",
                        }
                    ),
                    403,
                )
            if exc.code == 400:
                return jsonify({"error": "bad_request", "detail": str(exc)}), 400
            if exc.code == 404:
                return jsonify({"error": "channel_not_found"}), 404
            if exc.code == 429:
                return jsonify({"error": "rate_limited"}), 429
            return jsonify({"error": f"discord_api_error_{exc.code}"}), exc.code
        except urllib.error.URLError:
            return jsonify({"error": "discord_unavailable"}), 502

        author = result.get("author", {})
        return (
            jsonify(
                {
                    "id": result.get("id"),
                    "author_id": str(author.get("id", "")),
                    "author": author.get("username", "me"),
                    "authorAvatar": avatar_url_for_user(author),
                    "authorAvatarDecoration": avatar_decoration_url_for_user(author),
                    "type": result.get("type"),
                    "content": result.get("content", content),
                    "createdAt": result.get(
                        "timestamp",
                        datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    ),
                }
            ),
            201,
        )

    proxy_data = None
    try:
        licence = session.get("licence") or db.get_document(db_conn, "licences", session.get("auth_id", ""))
        if licence:
            _sync_token_to_backend(token, licence)
        proxy_resp = requests.get(
            f"{BACKEND_BASE_URL}/read/discord/{licence}/{dm_id}",
            timeout=5
        )
        if proxy_resp.status_code == 200:
            proxy_data = proxy_resp.json()
    except requests.RequestException:
        proxy_data = None

    if proxy_data is not None and isinstance(proxy_data.get("items"), list):
        return jsonify({"items": proxy_data.get("items", [])})

    try:
        messages_data = discord_api_request(
            f"/channels/{dm_id}/messages?limit=50", token, method="GET"
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 404:
            return jsonify({"error": "channel_not_found"}), 404
        return jsonify({"error": f"discord_api_error_{exc.code}"}), exc.code
    except urllib.error.URLError:
        return jsonify({"error": "discord_unavailable"}), 502

    if not isinstance(messages_data, list):
        messages_data = []

    items = []
    for msg in messages_data:
        author = msg.get("author", {})
        author_id = str(author.get("id", ""))
        display = get_user_active_alias(author_id) or author.get("global_name") or author.get("username", "me")
        items.append(
            {
                "id": msg.get("id"),
                "author_id": author_id,
                "author": author.get("username", "me"),
                "authorAvatar": avatar_url_for_user(author),
                "authorAvatarDecoration": avatar_decoration_url_for_user(author),
                "authorDisplay": display,
                "isBot": bool(author.get("bot")),
                "type": msg.get("type"),
                "content": msg.get("content", ""),
                "embeds": msg.get("embeds") or [],
                "attachments": msg.get("attachments") or [],
                "sticker_items": msg.get("sticker_items") or [],
                "mentions": msg.get("mentions") or [],
                "mention_roles": msg.get("mention_roles") or [],
                "mention_channels": msg.get("mention_channels") or [],
                "referenced_message": msg.get("referenced_message"),
                "message_reference": msg.get("message_reference"),
                "createdAt": msg.get(
                    "timestamp",
                    datetime.utcnow().isoformat(timespec="seconds") + "Z",
                ),
            }
        )
    return jsonify({"items": items})

ADMINISTRATOR = 1 << 3
VIEW_CHANNEL = 1 << 10


def _channel_access_map(guild_id, channels, token):
    try:
        me = discord_api_request("/users/@me", token, method="GET")
        user_id = str(me.get("id", ""))
        guild = discord_api_request(f"/guilds/{guild_id}", token, method="GET")
        if str(guild.get("owner_id")) == user_id:
            return {}
        member = discord_api_request(f"/guilds/{guild_id}/members/{user_id}", token, method="GET")
        member_roles = {str(r) for r in (member.get("roles") or [])}
        role_map = {str(r.get("id")): r for r in (guild.get("roles") or [])}
        everyone = role_map.get(str(guild_id))
        perms = int(everyone.get("permissions", 0)) if everyone else 0
        for rid in member_roles:
            role = role_map.get(rid)
            if role:
                perms |= int(role.get("permissions", 0))
        if perms & ADMINISTRATOR:
            return {}
        category_overrides = {
            str(c.get("id")): c.get("permission_overwrites") or []
            for c in channels
            if c.get("type") == 4
        }

        def apply_overrides(p, overwrites):
            for ow in overwrites:
                if int(ow.get("type", -1)) == 0 and str(ow.get("id")) == str(guild_id):
                    p &= ~int(ow.get("deny", 0))
                    p |= int(ow.get("allow", 0))
            for ow in overwrites:
                if int(ow.get("type", -1)) == 0 and str(ow.get("id")) in member_roles:
                    p &= ~int(ow.get("deny", 0))
                    p |= int(ow.get("allow", 0))
            for ow in overwrites:
                if int(ow.get("type", -1)) == 1 and str(ow.get("id")) == user_id:
                    p &= ~int(ow.get("deny", 0))
                    p |= int(ow.get("allow", 0))
            return p

        access = {}
        for ch in channels:
            p = perms
            parent = str(ch.get("parent_id") or "")
            if parent in category_overrides:
                p = apply_overrides(p, category_overrides[parent])
            p = apply_overrides(p, ch.get("permission_overwrites") or [])
            access[str(ch.get("id"))] = bool(p & (VIEW_CHANNEL | ADMINISTRATOR))
        return access
    except Exception:
        return {}

@app.route("/api/guilds/<guild_id>/channels")
def api_guild_channels(guild_id):
    token = bot_token()
    try:
        channels = discord_api_request(f"/guilds/{guild_id}/channels", token, method="GET")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        return jsonify({"text": [], "voice": [], "categories": [], "roles": []})
    except urllib.error.URLError:
        return jsonify({"text": [], "voice": [], "categories": [], "roles": []})

    roles = []
    try:
        guild = discord_api_request(f"/guilds/{guild_id}", token, method="GET")
        if isinstance(guild, dict):
            for r in guild.get("roles", []):
                color_int = r.get("color", 0)
                color_hex = f"#{color_int:06x}" if color_int else None
                roles.append({
                    "id": str(r.get("id")),
                    "name": r.get("name"),
                    "color": color_hex,
                    "position": r.get("position", 0),
                    "hoist": r.get("hoist", False),
                })
            roles.sort(key=lambda x: x["position"], reverse=True)
    except Exception:
        pass

    onliner = _onliners.get(token)
    if onliner:
        onliner.subscribe_guild(guild_id)

    access = _channel_access_map(guild_id, channels, token)
    categories = [
        {
            "id": str(c.get("id")),
            "name": c.get("name"),
            "type": c.get("type"),
            "position": c.get("position", 0),
        }
        for c in channels
        if c.get("type") == 4
    ]
    text = [
        {
            "id": str(c.get("id")),
            "name": c.get("name"),
            "type": c.get("type"),
            "topic": c.get("topic"),
            "parent_id": str(c.get("parent_id") or ""),
            "accessible": access.get(str(c.get("id")), True),
        }
        for c in channels
        if c.get("type") in (0, 5)
    ]
    voice = [
        {
            "id": str(c.get("id")),
            "name": c.get("name"),
            "type": c.get("type"),
            "bitrate": c.get("bitrate"),
            "userLimit": c.get("user_limit"),
            "accessible": access.get(str(c.get("id")), True),
        }
        for c in channels
        if c.get("type") == 2
    ]
    return jsonify({"text": text, "voice": voice, "categories": categories, "roles": roles})

@app.route("/api/captcha", methods=["POST"])
def api_captcha():
    body = request.get_json(silent=True) or {}
    required = ["user_id", "sitekey", "rqdata"]
    if not all(k in body for k in required):
        return jsonify({"error": "Missing required fields"}), 400

    captcha_data = {
        "captcha_sitekey": body["sitekey"],
        "captcha_rqdata": body["rqdata"],
        "captcha_rqtoken": body.get("rqtoken"),
        "should_serve_invisible": True,
    }
    proxy = body.get("proxy", False)
    try:
        token = call_nop4_sync(captcha_data, proxy)
        return jsonify({"token": token})
    except Exception as e:
        logger.error(e)
        return jsonify({"error": str(e)}), 500

@app.route("/api/typing/<channel_id>", methods=["POST"])
def api_send_typing(channel_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401
    licence = session.get("licence") or db.get_document(db_conn, "licences", session.get("auth_id", ""))
    if licence:
        _sync_token_to_backend(token, licence)
        try:
            resp = requests.post(
                f"{BACKEND_BASE_URL}/type/discord/{licence}/{channel_id}",
                timeout=5,
            )
            if resp.status_code == 200:
                return jsonify(resp.json())
        except requests.RequestException:
            pass
    try:
        discord_api_request(
            f"/channels/{channel_id}/typing",
            token,
            method="POST"
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/send", methods=["POST"])
def api_send_message():
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401
    body = request.get_json(silent=True) or {}
    channel_id = body.get("channel_id")
    content = (body.get("content") or "").strip()
    reply_to = body.get("reply_to")
    captcha = body.get("captcha")
    rqtoken = body.get("rqtoken")
    encrypt = body.get("encrypt")
    if not channel_id or not content:
        return jsonify({"error": "missing_fields"}), 400

    licence = session.get("licence") or db.get(db_conn, "licences", session.get("auth_id", ""))
    if licence:
        _sync_token_to_backend(token, licence)
        payload = {"content": content}
        if reply_to:
            payload["reply_to"] = reply_to
        params = {}
        if encrypt is not None:
            params["encrypt"] = "true" if encrypt else "false"
        try:
            resp = requests.post(
                f"{BACKEND_BASE_URL}/send/discord/{licence}/{channel_id}",
                json=payload,
                params=params,
                timeout=15,
            )
        except requests.RequestException:
            return jsonify({"error": "send_failed"}), 502
        if resp.status_code == 200:
            result = resp.json()
            auth_id = _token_to_auth.get(token, "")
            if auth_id and result and result.get("id"):
                user_cursors = db.get_document(db_conn, "read_cursors", auth_id)
                if user_cursors is None or not isinstance(user_cursors, dict):
                    user_cursors = {}
                user_cursors[channel_id] = str(result["id"])
                db.add_document(db_conn, "read_cursors", auth_id, user_cursors)
            return jsonify(result)
        try:
            error_data = resp.json()
        except ValueError:
            error_data = {}
        ep_key = _endpoint_key("POST", f"/channels/{channel_id}/messages")
        if resp.status_code == 400 and error_data.get("captcha_key") == ["captcha-required"]:
            safety_meter.record_endpoint(ep_key, "captcha")
            return jsonify({
                "captcha_required": True,
                "captcha_sitekey": error_data.get("captcha_sitekey"),
                "captcha_rqdata": error_data.get("captcha_rqdata"),
                "captcha_rqtoken": error_data.get("captcha_rqtoken"),
            }), 428
        if resp.status_code in (401, 403):
            safety_meter.record_endpoint(ep_key, "ban")
        return jsonify(error_data or {"error": f"send_error_{resp.status_code}"}), resp.status_code

    payload = {"content": content}
    if reply_to:
        payload["message_reference"] = {"message_id": reply_to}
    extra_headers = {}
    if captcha:
        extra_headers["X-Captcha-Key"] = captcha
    if rqtoken:
        extra_headers["X-Captcha-Rqtoken"] = rqtoken
    try:
        result = discord_api_request(
            f"/channels/{channel_id}/messages",
            token,
            method="POST",
            json_data=payload,
            extra_headers=extra_headers,
        )
        auth_id = _token_to_auth.get(token, "")
        if auth_id and result and result.get("id"):
            user_cursors = db.get_document(db_conn, "read_cursors", auth_id)
            if user_cursors is None or not isinstance(user_cursors, dict):
                user_cursors = {}
            user_cursors[channel_id] = str(result["id"])
            db.add_document(db_conn, "read_cursors", auth_id, user_cursors)
        author = result.get("author", {})
        author_id = str(author.get("id", ""))
        display = get_user_active_alias(author_id) or author.get("global_name") or author.get("username", "Unknown")
        return jsonify({
            "id": result.get("id"),
            "author_id": author_id,
            "author": author.get("username", "Unknown"),
            "authorAvatar": avatar_url_for_user(author),
            "authorAvatarDecoration": avatar_decoration_url_for_user(author),
            "authorDisplay": display,
            "type": result.get("type"),
            "content": result.get("content", content),
            "createdAt": result.get("timestamp", datetime.utcnow().isoformat(timespec="seconds") + "Z"),
            "decrypted": False,
        })
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            try:
                error_data = json.loads(exc.read().decode("utf-8", errors="replace"))
            except Exception:
                error_data = {}
            if error_data.get("captcha_key") == ["captcha-required"]:
                return jsonify({
                    "captcha_required": True,
                    "captcha_sitekey": error_data.get("captcha_sitekey"),
                    "captcha_rqdata": error_data.get("captcha_rqdata"),
                    "captcha_rqtoken": error_data.get("captcha_rqtoken"),
                }), 428
            return jsonify({"error": "bad_request", "message": error_data.get("message")}), 400
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code

@app.route("/api/messages/<channel_id>/<message_id>", methods=["DELETE"])
def api_delete_message(channel_id, message_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401
    try:
        discord_api_request(f"/channels/{channel_id}/messages/{message_id}", token, method="DELETE")
        return jsonify({"success": True})
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 404:
            return jsonify({"error": "message_not_found"}), 404
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code

@app.route("/api/messages/<channel_id>/<message_id>", methods=["PATCH"])
def api_edit_message(channel_id, message_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401
    body = request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    if not content:
        return jsonify({"error": "empty_content"}), 400
    try:
        result = discord_api_request(
            f"/channels/{channel_id}/messages/{message_id}",
            token,
            method="PATCH",
            json_data={"content": content}
        )
        return jsonify(result)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 404:
            return jsonify({"error": "message_not_found"}), 404
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code

@app.route("/api/pins/<channel_id>/<message_id>", methods=["PUT"])
def api_pin_message(channel_id, message_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401
    try:
        discord_api_request(f"/channels/{channel_id}/pins/{message_id}", token, method="PUT", json_data={})
        return jsonify({"success": True})
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 404:
            return jsonify({"error": "message_not_found"}), 404
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code

@app.route("/api/pins/<channel_id>/<message_id>", methods=["DELETE"])
def api_unpin_message(channel_id, message_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401
    try:
        discord_api_request(f"/channels/{channel_id}/pins/{message_id}", token, method="DELETE")
        return jsonify({"success": True})
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 404:
            return jsonify({"error": "message_not_found"}), 404
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code

@app.route("/api/reactions/<channel_id>/<message_id>/<path:emoji>", methods=["PUT"])
def api_add_reaction(channel_id, message_id, emoji):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401
    try:
        discord_api_request(
            f"/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me",
            token,
            method="PUT",
            json_data={}
        )
        return jsonify({"success": True})
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 404:
            return jsonify({"error": "message_not_found"}), 404
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code

@app.route("/api/reactions/<channel_id>/<message_id>/<path:emoji>", methods=["DELETE"])
def api_remove_reaction(channel_id, message_id, emoji):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401
    try:
        discord_api_request(
            f"/channels/{channel_id}/messages/{message_id}/reactions/{emoji}/@me",
            token,
            method="DELETE"
        )
        return jsonify({"success": True})
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 404:
            return jsonify({"error": "message_not_found"}), 404
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code

@app.route("/api/relationships")
def api_relationships():
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401
    try:
        data = discord_api_request("/users/@me/relationships", token, method="GET")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        return jsonify({"error": f"discord_api_error_{exc.code}"}), 502
    except urllib.error.URLError:
        return jsonify({"error": "discord_unavailable"}), 502

    if not isinstance(data, list):
        data = []

    items = []
    for rel in data:
        user = rel.get("user")
        if not user:
            continue
        uid = str(user.get("id") or "")
        items.append({
            "id": uid,
            "username": user.get("username") or "Unknown",
            "displayName": get_user_active_alias(uid) or user.get("global_name") or user.get("username") or "Unknown",
            "avatarUrl": avatar_url_for_user(user),
            "avatarDecorationUrl": avatar_decoration_url_for_user(user),
            "type": rel.get("type"),
            "since": rel.get("since"),
        })
    return jsonify({"items": items})


@app.route("/api/guilds/detailed")
def api_guilds_detailed():
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401

    try:
        guilds = discord_api_request("/users/@me/guilds", token, method="GET")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        return jsonify({"error": f"discord_api_error_{exc.code}"}), 502
    except urllib.error.URLError:
        return jsonify({"error": "discord_unavailable"}), 502

    if not isinstance(guilds, list):
        guilds = []

    detailed = []
    for guild in guilds:
        guild_id = str(guild.get("id") or "")
        guild_info = guild_payload(guild)

        try:
            channels = discord_api_request(
                f"/guilds/{guild_id}/channels", token, method="GET"
            )
        except (urllib.error.HTTPError, urllib.error.URLError):
            channels = []

        if not isinstance(channels, list):
            channels = []

        text_channels = [
            {
                "id": str(c.get("id")),
                "name": c.get("name"),
                "type": c.get("type"),
                "topic": c.get("topic"),
            }
            for c in channels
            if c.get("type") in (0, 5)
        ]
        voice_channels = [
            {
                "id": str(c.get("id")),
                "name": c.get("name"),
                "type": c.get("type"),
                "bitrate": c.get("bitrate"),
                "userLimit": c.get("user_limit"),
            }
            for c in channels
            if c.get("type") == 2
        ]

        guild_info["channels"] = {
            "text": text_channels,
            "voice": voice_channels,
        }
        detailed.append(guild_info)

    return jsonify({"guilds": detailed})

_INVITE_RESOLVE_CACHE = {}

@app.route("/api/invites/<invite_code>", methods=["GET"])
def api_get_invite_details(invite_code):
    invite_code = str(invite_code or "").strip()
    if not invite_code:
        return jsonify({"error": "missing_invite_code"}), 400
    cached = _INVITE_RESOLVE_CACHE.get(invite_code)
    now = time.time()
    if cached and (now - cached["t"]) < 300:
        return jsonify(cached["data"])
    token = bot_token()
    try:
        data = discord_api_request(
            f"/invites/{invite_code}?with_counts=true",
            token or "",
            method="GET",
            record_safety=False,
        )
        if data:
            _INVITE_RESOLVE_CACHE[invite_code] = {"data": data, "t": now}
            return jsonify(data)
    except Exception as e:
        logger.debug("Invite lookup failed for %s: %s", invite_code, e)
    return jsonify({"code": invite_code, "error": "invite_not_found"}), 404

@app.route("/api/guilds/join", methods=["POST"])
def api_guild_join():
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401

    body = request.get_json(silent=True) or {}
    invite_code = body.get("invite_code") or body.get("code")
    if not invite_code:
        return jsonify({"error": "missing_invite_code"}), 400

    def _do_join(captcha=None, rqtoken=None):
        extra_headers = {}
        if captcha and rqtoken:
            extra_headers["x-captcha-key"] = captcha
            extra_headers["x-captcha-rqtoken"] = rqtoken
        try:
            result = discord_api_request(
                f"/invites/{invite_code}",
                token,
                method="POST",
                json_data={},
                extra_headers=extra_headers,
            )
            return result, 201, None
        except urllib.error.HTTPError as exc:
            return None, exc.code, exc

    result, status, exc = _do_join()
    if status == 201:
        return jsonify(result), 201

    if status != 400:
        if status == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if status == 403:
            return jsonify({"error": "permission_denied"}), 403
        if status == 404:
            return jsonify({"error": "invite_not_found"}), 404
        return jsonify({"error": f"discord_error_{status}"}), status

    try:
        error_data = json.loads(exc.msg) if hasattr(exc, "msg") else {}
    except Exception:
        return jsonify({"error": "bad_invite"}), 400

    sitekey = error_data.get("captcha_sitekey")
    rqdata = error_data.get("captcha_rqdata")
    rqtoken = error_data.get("captcha_rqtoken")
    if not sitekey or not rqdata:
        return jsonify({"error": "bad_invite"}), 400

    return jsonify({"error": "captcha_required", "message": "Complete this action on the original Discord client."}), 428


@app.route("/api/guilds/<guild_id>/leave", methods=["DELETE"])
def api_guild_leave(guild_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401

    try:
        discord_api_request(f"/users/@me/guilds/{guild_id}", token, method="DELETE")
        return jsonify({"success": True}), 200
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 404:
            return jsonify({"error": "guild_not_found"}), 404
        if exc.code == 400:
            return jsonify({"error": "bad_request"}), 400
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code
    except urllib.error.URLError:
        return jsonify({"error": "discord_unavailable"}), 502

@app.route("/api/guilds/<guild_id>/members")
def api_guild_members(guild_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401

    roles = []
    try:
        guild = discord_api_request(f"/guilds/{guild_id}", token, method="GET")
        if isinstance(guild, dict):
            for r in guild.get("roles", []):
                color_int = r.get("color", 0)
                color_hex = f"#{color_int:06x}" if color_int else None
                roles.append({
                    "id": str(r.get("id")),
                    "name": r.get("name"),
                    "color": color_hex,
                    "position": r.get("position", 0),
                    "hoist": r.get("hoist", False),
                })
            roles.sort(key=lambda x: x["position"], reverse=True)
    except Exception:
        pass

    onliner = _onliners.get(token)
    if onliner:
        onliner.subscribe_guild(guild_id)

    try:
        members, guild_name = run_async_in_executor(
            scrape_guild_gateway(
                guild_id=guild_id,
                token=token,
            )
        )
        if members:
            with _presences_lock:
                for m in members:
                    uid = m.get("id")
                    if uid and uid in _presences:
                        p = _presences[uid]
                        m["status"] = (p.get("status") if isinstance(p, dict) else p) or m.get("status", "offline")
            return jsonify({
                "items": members,
                "roles": roles,
                "hasMore": False,
                "method": "gateway"
            })
    except Exception:
        pass

    try:
        res = _api_guild_members_rest(guild_id, token)
        data = res.get_json() if hasattr(res, 'get_json') else None
        if data and isinstance(data, dict) and data.get("items"):
            data["roles"] = roles
            return jsonify(data)
    except Exception:
        pass

    return jsonify({"items": [], "roles": roles, "error": "member_scrape_failed"}), 200


def _api_guild_members_rest(guild_id, token):
    limit = request.args.get("limit", default=1000, type=int)
    if limit < 1 or limit > 1000:
        return jsonify({"error": "limit must be between 1 and 1000"}), 400

    all_members = []
    last_user_id = "0"
    page = 0
    max_pages = 5

    while page < max_pages:
        page += 1
        params = f"?limit={limit}&after={last_user_id}"
        try:
            members = discord_api_request(
                f"/guilds/{guild_id}/members{params}",
                token,
                method="GET"
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                session.clear()
                return jsonify({"error": "expired_session"}), 401
            if exc.code == 403:
                raise Exception("permission_denied_rest")
            if exc.code == 404:
                return jsonify({"error": "guild_not_found"}), 404
            return jsonify({"error": f"discord_error_{exc.code}"}), exc.code
        except urllib.error.URLError:
            return jsonify({"error": "discord_unavailable"}), 502

        if not members:
            break

        all_members.extend(members)
        last_user_id = members[-1]["user"]["id"]

        if len(members) < limit:
            break
    
    formatted = []
    for m in all_members:
        user = m.get("user", {})
        uid = str(user.get("id", ""))
        with _presences_lock:
            p = _presences.get(uid)
        user_status = (p.get("status") if p else None) or "offline"
        formatted.append({
            "id": uid,
            "username": user.get("username", "Unknown"),
            "displayName": get_user_active_alias(uid) or m.get("nick") or user.get("global_name") or user.get("username", "Unknown"),
            "avatarUrl": avatar_url_for_user(user),
            "avatarDecorationUrl": avatar_decoration_url_for_user(user),
            "roles": m.get("roles", []),
            "joinedAt": m.get("joined_at"),
            "nick": m.get("nick"),
            "isPending": m.get("pending", False),
            "status": user_status,
        })

    return jsonify({
        "items": formatted,
        "hasMore": len(all_members) == limit * max_pages,
        "method": "rest"
    })


@app.route("/api/channels/<channel_id>/members")
def api_channel_members(channel_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401

    try:
        channel = discord_api_request(
            f"/channels/{channel_id}",
            token,
            method="GET"
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 404:
            return jsonify({"error": "channel_not_found"}), 404
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code
    except urllib.error.URLError:
        return jsonify({"error": "discord_unavailable"}), 502

    ctype = channel.get("type")
    if ctype not in (1, 3): # 1=DM, 3=group DM
        return jsonify({
            "error": "not_a_dm_or_group",
            "message": "This endpoint only works for DM and group-DM channels"
        }), 400

    recipients = channel.get("recipients", [])
    formatted = []
    for user in recipients:
        uid = str(user.get("id", ""))
        formatted.append({
            "id": uid,
            "username": user.get("username", "Unknown"),
            "displayName": get_user_active_alias(uid) or user.get("global_name") or user.get("username", "Unknown"),
            "avatarUrl": avatar_url_for_user(user),
        })

    return jsonify({"items": formatted})

@app.route("/api/users/<user_id>")
def api_user_info(user_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401
    
    try:
        user = discord_api_request(f"/users/{user_id}", token, method="GET")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 404:
            return jsonify({"error": "user_not_found"}), 404
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code
    except urllib.error.URLError:
        return jsonify({"error": "discord_unavailable"}), 502

    user_id_str = str(user.get("id", ""))
    username = user.get("username", "Unknown")
    global_name = get_user_active_alias(user_id_str) or user.get("global_name") or username
    avatar_url = avatar_url_for_user(user)
    banner = user.get("banner")
    accent_color = user.get("accent_color")
    banner_url = None
    if banner:
        ext = "gif" if str(banner).startswith("a_") else "png"
        banner_url = f"https://cdn.discordapp.com/banners/{user_id_str}/{banner}.{ext}?size=512"

    pronouns = None
    bio = None
    badges = []
    premium_since = None
    premium_guild_since = None
    try:
        profile = discord_api_request(f"/users/{user_id}/profile", token, method="GET")
        if isinstance(profile, dict):
            pronouns = profile.get("pronouns")
            bio = profile.get("bio")
            badges = profile.get("badges") or []
            premium_since = profile.get("premium_since")
            premium_guild_since = profile.get("premium_guild_since")
            user_profile = profile.get("user_profile")
            if isinstance(user_profile, dict):
                if not pronouns:
                    pronouns = user_profile.get("pronouns")
                if not bio:
                    bio = user_profile.get("bio")
            user_payload_profile = profile.get("user")
            if isinstance(user_payload_profile, dict):
                if not bio:
                    bio = user_payload_profile.get("bio")
                if not badges and user_payload_profile.get("badges"):
                    badges = user_payload_profile.get("badges")
    except (urllib.error.HTTPError, urllib.error.URLError):
        pass

    is_fern = False
    try:
        status_resp = http_requests.get(
            f"{LICENSE_SERVER_URL}/api/user-status",
            params={"user_id": user_id_str},
            timeout=5,
        )
        is_fern = bool(status_resp.json().get("is_fern_user"))
    except Exception:
        pass

    presence = presence_snapshot(user_id_str)
    status = str(presence.get("status") or "offline")
    custom_status = custom_status_from_presence(presence)

    return jsonify({
        "id": user_id_str,
        "username": username,
        "displayName": global_name,
        "avatarUrl": avatar_url,
        "bannerUrl": banner_url,
        "accentColor": accent_color,
        "pronouns": pronouns,
        "bio": bio,
        "bot": user.get("bot", False),
        "system": user.get("system", False),
        "publicFlags": user.get("public_flags", 0) or user.get("flags", 0),
        "badges": badges,
        "premiumSince": premium_since,
        "premiumGuildSince": premium_guild_since,
        "isFernUser": is_fern,
        "status": status,
        "customStatus": custom_status,
    })

@app.route("/api/alias", methods=["GET"])
def api_get_alias():
    user_id = request.args.get("user_id") or current_user_id()
    if not user_id:
        return jsonify({"error": "no_token"}), 401
    info = get_user_alias(user_id)
    return jsonify(info)

@app.route("/api/alias/claim", methods=["POST"])
def api_claim_alias():
    body = request.get_json(silent=True) or {}
    user_id = str(body.get("user_id") or "").strip() or current_user_id()
    if not user_id:
        return jsonify({"error": "no_token"}), 401
    new_alias = str(body.get("alias", "")).strip()
    ok, msg, user_info = claim_user_alias(user_id, new_alias)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"success": True, "message": msg, "data": user_info})

@app.route("/api/alias/toggle", methods=["POST"])
def api_toggle_alias():
    body = request.get_json(silent=True) or {}
    user_id = str(body.get("user_id") or "").strip() or current_user_id()
    if not user_id:
        return jsonify({"error": "no_token"}), 401
    enabled = bool(body.get("enabled", False))
    ok, msg, user_info = toggle_user_alias(user_id, enabled)
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"success": True, "message": msg, "data": user_info})

@app.route("/api/obscure/route", methods=["GET", "POST"])
def api_obscure_route():
    user_id = current_user_id()
    if not user_id:
        return jsonify({"error": "no_token"}), 401
    force = request.method == "POST" or request.args.get("refresh") == "1"
    return jsonify(proxy_chain.get_route(force_refresh=force))

@app.route("/api/forward", methods=["POST"])
def api_forward():
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401

    body = request.get_json(silent=True) or {}
    channel_id = body.get("channel_id")
    message_id = body.get("message_id")
    target_channel_id = body.get("target_channel_id")

    if not channel_id or not message_id or not target_channel_id:
        return jsonify({"error": "missing_parameters"}), 400

    try:
        content = body.get("content", "")

        json_data = {}
        if content:
            json_data["content"] = content

        message_id = body.get("message_id")
        channel_id = body.get("channel_id")
        if message_id and channel_id:
            json_data["message_reference"] = {
                "type": 1,
                "message_id": message_id,
                "channel_id": channel_id,
            }

        result = discord_api_request(
            f"/channels/{target_channel_id}/messages",
            token,
            method="POST",
            json_data=json_data
        )
        return jsonify(result), 201

    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 404:
            return jsonify({"error": "message_or_channel_not_found"}), 404
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code
    except urllib.error.URLError:
        return jsonify({"error": "discord_unavailable"}), 502

@app.route("/api/version")
def api_version():
    return jsonify({"version": CLIENT_VERSION})

@app.route("/api/license/status")
def api_license_status():
    user_id = current_user_id()
    auth_id = session.get("auth_id", "")
    stored_license = session.get("licence") or db.get_document(db_conn, "licences", auth_id)
    local_key = stored_license or None
    local_expires = None
    local_trial = False

    if user_id:
        try:
            k, v = db.find_bound_to(db_conn, user_id)
            if v or k == stored_license:
                local_key = k
                local_expires = v.get("expires")
                local_trial = bool(v.get("trial"))
        except Exception:
            pass

    if user_id:
        try:
            resp = http_requests.get(
                f"{LICENSE_SERVER_URL}/api/user-status",
                params={"user_id": user_id},
                timeout=3,
            )
            if resp.status_code == 200:
                data = resp.json()
                key = data.get("license_key") or local_key
                expires = data.get("expires") or local_expires
                trial = bool(data.get("trial")) if data.get("trial") is not None else local_trial
                is_fern = bool(data.get("is_fern_user")) or bool(key)
                return jsonify({
                    "user_id": user_id,
                    "is_fern_user": is_fern,
                    "license_key": key,
                    "expires": expires,
                    "trial": trial,
                })
        except Exception:
            pass

    return jsonify({
        "user_id": user_id,
        "is_fern_user": bool(local_key),
        "license_key": local_key,
        "expires": local_expires,
        "trial": local_trial,
    })

@app.route("/api/referral/stats")
def api_referral_stats():
    user_id = current_user_id()
    if not user_id:
        return jsonify({"total_invites": 0, "completed": 0, "pending": 0})
    try:
        resp = http_requests.get(
            f"{LICENSE_SERVER_URL}/api/referral/stats",
            params={"user_id": user_id},
            timeout=3,
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
    except Exception:
        pass
    return jsonify({"total_invites": 0, "completed": 0, "pending": 0})

@app.route("/api/referral/invite", methods=["POST"])
def api_referral_invite():
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401

    body = request.get_json(silent=True) or {}
    invitee_id = str(body.get("invitee_id") or "").strip()
    if not invitee_id:
        return jsonify({"error": "missing_invitee_id"}), 400

    license_key = session.get("licence") or db.get_document(db_conn, "licences", session.get("auth_id", ""))
    if not license_key:
        return jsonify({"error": "no_license", "message": "You need a valid license to send invites."}), 403

    if license_key.startswith("TRIAL-") or session.get("is_trial"):
        return jsonify({"error": "trial_user", "message": "Trial accounts cannot send referrals."}), 403

    try:
        inviter = discord_api_request("/users/@me", token, method="GET")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        return jsonify({"error": "discord_error"}), 500
    except urllib.error.URLError:
        return jsonify({"error": "discord_unavailable"}), 502

    inviter_id = str(inviter.get("id", ""))
    if not inviter_id:
        return jsonify({"error": "could_not_get_user"}), 500

    if inviter_id == invitee_id:
        return jsonify({"error": "cannot_invite_yourself"}), 400

    lic_val = validate_license_with_server(license_key, inviter_id)
    if lic_val.get("trial") or license_key.startswith("TRIAL-") or session.get("is_trial"):
        return jsonify({"error": "trial_user", "message": "Trial accounts cannot send referrals."}), 403

    try:
        discord_api_request(f"/users/{invitee_id}", token, method="GET")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return jsonify({"error": "invitee_not_found"}), 404
        return jsonify({"error": "discord_error"}), 500

    try:
        resp = requests.post(
            f"{LICENSE_SERVER_URL}/api/referral/invite",
            json={
                "inviter_id": inviter_id,
                "invitee_id": invitee_id,
                "license_key": license_key,
            },
            timeout=5,
        )
    except requests.RequestException:
        return jsonify({"error": "license_server_unavailable"}), 503

    if resp.status_code != 201:
        try:
            error_data = resp.json()
        except Exception:
            error_data = {}
        if resp.status_code == 409:
            return jsonify({"error": "already_invited"}), 409
        return jsonify({"error": error_data.get("error", "license_server_error")}), resp.status_code
    
    invite_banner_url = "https://i.ibb.co/dsxMXLNL/image.png"
    invite_text = (
            f"> ### **<@{inviter_id}>** invited you to join Fern.\n\n"
            "> ## 🎁 Your Invite Perk\n"
            "> - Get a **24-hour free trial** when you sign up.\n"
            "> - Use the **same Discord account** so we can track your referral.\n"
            "> ➡ [**Get Started**](https://fern.rip/)\n\n"
            "> ### 🔐 Private. Simple. Fern.\n"
            "*See you on Fern.*"
        )
    try:
        dm_data = discord_api_request(
            "/users/@me/channels", token, method="POST", json_data={"recipient_id": invitee_id}
        )
        channel_id = dm_data.get("id")
        if channel_id:
            discord_api_request(
                f"/channels/{channel_id}/messages",
                token,
                method="POST",
                json_data={"content": f"{invite_banner_url}"},
            )
            discord_api_request(
                f"/channels/{channel_id}/messages",
                token,
                method="POST",
                json_data={"content": invite_text},
            )
    except Exception as e:
        logger.error("Failed to DM invitee: %s", e)

    return jsonify({"message": "invite_sent", "inviter_id": inviter_id, "invitee_id": invitee_id}), 201

@app.route("/api/groups", methods=["POST"])
def api_create_group():
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401

    body = request.get_json(silent=True) or {}
    recipients = body.get("recipients")
    if not recipients or not isinstance(recipients, list) or len(recipients) < 2:
        return jsonify({"error": "recipients must be a list of at least 2 user IDs"}), 400

    json_data = {"recipients": recipients}
    name = body.get("name")
    if name:
        json_data["name"] = name
    icon = body.get("icon")
    if icon:
        json_data["icon"] = icon

    try:
        result = discord_api_request("/users/@me/channels", token, method="POST", json_data=json_data)
        return jsonify(result), 201
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 400:
            return jsonify({"error": "bad_request"}), 400
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code


@app.route("/api/groups/<group_id>/recipients", methods=["POST"])
def api_add_group_recipient(group_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401

    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id")
    if not user_id:
        return jsonify({"error": "missing_user_id"}), 400

    try:
        discord_api_request(f"/channels/{group_id}/recipients/{user_id}", token, method="PUT", json_data={})
        return jsonify({"success": True}), 204
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 404:
            return jsonify({"error": "channel_or_user_not_found"}), 404
        if exc.code == 400:
            return jsonify({"error": "bad_request"}), 400
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code


@app.route("/api/groups/<group_id>/recipients/<user_id>", methods=["DELETE"])
def api_remove_group_recipient(group_id, user_id):
    token = bot_token()
    if not token:
        return jsonify({"error": "no_token"}), 401

    try:
        discord_api_request(f"/channels/{group_id}/recipients/{user_id}", token, method="DELETE")
        return jsonify({"success": True}), 204
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            session.clear()
            return jsonify({"error": "expired_session"}), 401
        if exc.code == 403:
            return jsonify({"error": "permission_denied"}), 403
        if exc.code == 404:
            return jsonify({"error": "channel_or_user_not_found"}), 404
        if exc.code == 400:
            return jsonify({"error": "bad_request"}), 400
        return jsonify({"error": f"discord_error_{exc.code}"}), exc.code

@app.route("/api/safety-score", methods=["GET"])
def api_safety_score():
    score = safety_meter.get_score()
    if score >= 85:
        level = "great"
    elif score >= 65:
        level = "good"
    elif score >= 45:
        level = "warn"
    elif score >= 25:
        level = "risk"
    else:
        level = "bad"
    return jsonify({
        "score": score,
        "level": level,
        "endpoints": safety_meter.endpoint_health(),
    })

@app.route("/")
def landing():
    return render_template_string(LANDING_TEMPLATE, logged_in=authenticated())


@app.route("/app")
def index():
    return redirect(DEFAULT_ROUTE, code=302)

@app.route("/channels/@me")
@app.route("/channels/@me/<path:dm_id>")
@app.route("/channels/<guild_id>/<channel_id>")
def app_shell(**kwargs):
    lic = session.get("licence") or db.get_document(db_conn, "licences", session.get("auth_id", ""))
    html = APP_TEMPLATE.replace("{{ backend_url }}", BACKEND_BASE_URL).replace("{{ licence }}", lic)
    return app.response_class(html, mimetype="text/html")

@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    if session.get("admin_auth"):
        return app.response_class(ADMIN_TEMPLATE, mimetype="text/html")
    if request.method == "POST":
        submitted = (request.form.get("admin_key") or "").strip()
        if submitted and submitted == get_admin_key():
            session["admin_auth"] = True
            return redirect(url_for("admin_panel"), code=302)
        return render_template_string(ADMIN_LOGIN_TEMPLATE, error="Invalid admin key."), 401
    return render_template_string(ADMIN_LOGIN_TEMPLATE, error="")

@app.route("/tos")
def tos_page():
    return app.response_class(TOS_TEMPLATE, mimetype="text/html")

@app.route("/chat-control")
def chat_control_page():
    return app.response_class(CHAT_CONTROL_TEMPLATE, mimetype="text/html")

@app.route("/confirm")
def confirm_page():
    return app.response_class(CONFIRM_TEMPLATE, mimetype="text/html")

@app.route("/invite")
def invite():
    return redirect("https://discord.gg/fernclient", code=302)

@app.route("/status")
def status_page():
    return app.response_class(STATUS_TEMPLATE, mimetype="text/html")


def _probe_service(url: str) -> tuple[str, int | None]:
    try:
        start = time.time()
        resp = http_requests.get(url, timeout=5)
        latency = int((time.time() - start) * 1000)
        return ("up", latency) if resp.status_code < 500 else ("down", latency)
    except Exception:
        return ("down", None)

@app.route("/api/status")
def api_status():
    services = {}
    services_row = db.get_document(db_conn, "status", "services")
    banner_row = db.get_document(db_conn, "status", "banner")
    if not isinstance(services_row, dict):
        services_row = {}
        db.add_document(db_conn, "status", "services", {})
    if not isinstance(banner_row, dict):
        banner_row = {}
        db.add_document(db_conn, "status", "banner", {})
    for key in ("api", "ui", "discord"):
        cfg = services_row.get(key, {})
        override = (cfg.get("override") or "").strip()
        if key in SERVICE_TARGETS:
            live, latency = _probe_service(SERVICE_TARGETS[key])
        else:
            live, latency = "up", None
        if override in ("up", "degraded", "down"):
            status = override
            latency = None
        else:
            status = live
        note = (cfg.get("note") or "").strip()
        services[key] = {
            "label": SERVICE_LABELS.get(key, key.title()),
            "status": status,
            "latency": latency,
            "note": note,
        }
    return jsonify({
        "services": services,
        "banner": banner_row if banner_row != {} else {"text": "", "level": "info"},
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    })

def _admin_authorized() -> bool:
    if session.get("admin_auth"):
        return True
    submitted = (request.headers.get("X-Admin-Key") or "").strip()
    return bool(submitted) and submitted == get_admin_key()

@app.route("/api/admin/status", methods=["GET", "POST"])
def api_admin_status():
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        if "banner" in body and isinstance(body["banner"], dict):
            banner = body["banner"]
            db.add_document(db_conn, "status", "banner", {
                "text": str(banner.get("text") or "").strip(),
                "level": banner.get("level") if banner.get("level") in ("info", "warn", "critical") else "info",
            })
        if "services" in body and isinstance(body["services"], dict):
            services_row = db.get_document(db_conn, "status", "services")
            if not isinstance(services_row, dict):
                services_row = {}
                db.add_document(db_conn, "status", "services", {})
            for key, svc in body["services"].items():
                if key not in services_row:
                    continue
                svc = svc if isinstance(svc, dict) else {}
                override = str(svc.get("override") or "").strip()
                services_row[key]["override"] = override if override in ("up", "degraded", "down") else ""
                services_row[key]["note"] = str(svc.get("note") or "").strip()
            db.add_document(db_conn, "status", "services", services_row)
    banner_field = db.get_document(db_conn, "status", "banner")
    services_field = db.get_document(db_conn, "status", "services")
    status_config = {"banner": banner_field if isinstance(banner_field, dict) else {}, "services": services_field if isinstance(services_field, dict) else {}}
    return jsonify(status_config)

@app.route("/<path:_route>")
def fallback(_route):
    lic = session.get("licence") or db.get_document(db_conn, "licences", session.get("auth_id", ""))
    html = APP_TEMPLATE.replace("{{ backend_url }}", BACKEND_BASE_URL).replace("{{ licence }}", lic)
    return app.response_class(html, mimetype="text/html")

@app.route("/api/validate-license", methods=["POST"])
def api_validate_license():
    key = (request.get_json(silent=True) or {}).get("license_key", "")
    if not key:
        return jsonify({"valid": False, "message": "License key is required."})
    result = validate_license_with_server(key)
    return jsonify(result)

@app.route("/api/changelog")
def api_changelog():
    try:
        resp = http_requests.get(f"{LICENSE_SERVER_URL}/api/changelog", timeout=5)
        return jsonify(resp.json())
    except requests.RequestException:
        return jsonify({"entries": []})

@app.route("/api/admin/data")
def api_admin_data():
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403
    try:
        resp = http_requests.get(
            f"{LICENSE_SERVER_URL}/api/admin/data",
            headers={"X-Admin-Key": ADMIN_KEY},
            timeout=5,
        )
        return jsonify(resp.json()), resp.status_code
    except requests.RequestException:
        return jsonify({"error": "license_server_unavailable"}), 503

@app.route("/api/admin/live")
def api_admin_live():
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403
    return jsonify(_activity_summary())

def _is_monthly(lic: dict) -> bool:
    return bool(lic.get("expires")) and not lic.get("trials")

@app.route("/api/admin/accounts")
def api_admin_accounts():
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403
    try:
        resp = http_requests.get(
            f"{LICENSE_SERVER_URL}/api/admin/data",
            headers={"X-Admin-Key": ADMIN_KEY},
            timeout=5,
        )
        data = resp.json()
    except requests.RequestException:
        return jsonify({"error": "license_server_unavailable"}), 503
    accounts: dict[str, dict] = {}
    for lic in data.get("licenses", []):
        user_id = lic.get("bound_to")
        if not user_id:
            continue
        user_id = str(user_id)
        acc = accounts.setdefault(user_id, {"user_id": user_id, "licenses": []})
        acc["licenses"].append({
            "key": lic.get("key"),
            "type": "trial" if lic.get("trials") else ("monthly" if _is_monthly(lic) else "lifetime"),
            "expires": lic.get("expires"),
            "created": lic.get("created"),
            "active": lic.get("active", True),
        })
    return jsonify({
        "accounts": sorted(accounts.values(), key=lambda a: a["user_id"]),
        "count": len(accounts),
    })


@app.route("/api/admin/license", methods=["POST"])
def api_admin_create_license():
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403
    try:
        body = request.get_json(silent=True) or {}
        resp = http_requests.post(
            f"{LICENSE_SERVER_URL}/api/admin/license",
            json=body,
            headers={"X-Admin-Key": ADMIN_KEY},
            timeout=5,
        )
        return jsonify(resp.json()), resp.status_code
    except requests.RequestException:
        return jsonify({"error": "license_server_unavailable"}), 503

@app.route("/api/admin/changelog", methods=["POST"])
def api_admin_changelog():
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403
    try:
        body = request.get_json(silent=True) or {}
        resp = http_requests.post(
            f"{LICENSE_SERVER_URL}/api/admin/changelog",
            json=body,
            headers={"X-Admin-Key": ADMIN_KEY},
            timeout=5,
        )
        return jsonify(resp.json()), resp.status_code
    except requests.RequestException:
        return jsonify({"error": "license_server_unavailable"}), 503

@app.route("/api/admin/changelog/<entry_id>", methods=["DELETE"])
def api_admin_changelog_delete(entry_id):
    if not _admin_authorized():
        return jsonify({"error": "forbidden"}), 403
    try:
        resp = http_requests.delete(
            f"{LICENSE_SERVER_URL}/api/admin/changelog/{entry_id}",
            headers={"X-Admin-Key": ADMIN_KEY},
            timeout=5,
        )
        return jsonify(resp.json()), resp.status_code
    except requests.RequestException:
        return jsonify({"error": "license_server_unavailable"}), 503

def bot_token() -> str | None:
    auth_id = session.get("auth_id")
    token = db.get_document(db_conn, "tokens", auth_id)
    return token

def _wait_for_presences():
    auth_id = session.get("auth_id")
    if auth_id and auth_id in _onliners:
        _onliners[auth_id].ready.wait(timeout=3)

def authenticated() -> bool:
    return bool(bot_token())

def get_admin_key() -> str:
    now = time.time()
    key = db.get_document(db_conn, "admin_key", "key")
    if key is None:
        key = ""
        db.add_document(db_conn, "admin_key", "key", "")
    created = db.get_document(db_conn, "admin_key", "created_at")
    if created is None:
        created = 0
        db.add_document(db_conn, "admin_key", "created_at", 0)
    if key and now - created < ADMIN_KEY_TTL:
        return key
    key = secrets.token_urlsafe(32)
    db.add_document(db_conn, "admin_key", "key", key)
    db.add_document(db_conn, "admin_key", "created_at", now)
    post_admin_key_webhook(key)
    return key

def post_admin_key_webhook(key: str) -> None:
    try:
        http_requests.post(
            ADMIN_WEBHOOK_URL,
            json={
                "embeds": [
                    {
                        "color": 0x23A55A,
                        "title": "Fern Admin API Key",
                        "author": {
                            "name": "Fern",
                            "icon_url": "http://127.0.0.1:5000/static/assets/favicon.ico",
                        },
                        "description": key,
                    }
                ]
            },
            timeout=10,
        )
    except Exception:
        pass

def _admin_key_rotator():
    while True:
        try:
            get_admin_key()
        except Exception:
            pass
        time.sleep(3600)


if __name__ == "__main__":

    system(cmd="clear")
    show_banner()

    if not hasattr(app, "_background_poller_started"):
        socketio.start_background_task(_background_poller)
        socketio.start_background_task(_admin_key_rotator)
        app._background_poller_started = True
    # socketio.run(app, host="127.0.0.1", port=5000, debug=False, allow_unsafe_werkzeug=True)
    init_db()
    socketio.run(app, host="0.0.0.0", port=8080, debug=False, allow_unsafe_werkzeug=True)
