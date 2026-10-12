import asyncio
import json
import random
import uuid
from typing import Optional

import websockets

from constants import (
    GATEWAY_URL,
    GATEWAY_DISPATCH,
    GATEWAY_HEARTBEAT,
    GATEWAY_IDENTIFY,
    GATEWAY_REQUEST_GUILD_MEMBERS,
    GATEWAY_SEARCH_RECENT_MEMBERS,
    GATEWAY_HELLO,
    DISCORD_CLIENT_UA,
)


class GatewayScraper:

    def __init__(
        self,
        token: str,
        guild_id: str,
        proxy_url: Optional[str] = None,
        chunk_delay_min: float = 0.3,
        chunk_delay_max: float = 0.8,
    ):
        self.token = token
        self.guild_id = guild_id
        self.proxy_url = proxy_url
        self.chunk_delay_min = chunk_delay_min
        self.chunk_delay_max = chunk_delay_max

        self._ws = None
        self._heartbeat_interval = None
        self._heartbeat_task = None
        self._session_id = None
        self._sequence = None
        self._scraped_members = []
        self._scraped_ids = set()
        self._guild_name = None
        self._guild_roles = []
        self._presences = {}
        self._done_event = asyncio.Event()
        self._seq_lock = asyncio.Lock()

    async def _send_heartbeat(self) -> None:
        while self._ws and not self._done_event.is_set():
            try:
                await asyncio.sleep(self._heartbeat_interval * 0.001 * random.uniform(0.95, 1.0))
                if self._done_event.is_set():
                    break

                async with self._seq_lock:
                    seq = self._sequence

                await self._ws.send(json.dumps({
                    "op": GATEWAY_HEARTBEAT,
                    "d": seq,
                }))
            except asyncio.CancelledError:
                break
            except Exception:
                break

    async def connect(self) -> bool:
        try:
            self._ws = await websockets.connect(
                GATEWAY_URL,
                max_size=2 ** 23,
                ping_interval=None,
            )

            hello_data = await self._ws.recv()
            hello = json.loads(hello_data)
            if hello.get("op") == GATEWAY_HELLO:
                self._heartbeat_interval = hello["d"]["heartbeat_interval"]
                self._heartbeat_task = asyncio.create_task(self._send_heartbeat())

                launch_id = str(uuid.uuid4())
                launch_sig = str(uuid.uuid4())
                hb_session = str(uuid.uuid4())

                identify_payload = {
                    "op": GATEWAY_IDENTIFY,
                    "d": {
                        "token": self.token,
                        "capabilities": 8189,
                        "properties": {
                            "os": "Windows",
                            "browser": "Discord Client",
                            "release_channel": "stable",
                            "client_version": "1.0.9210",
                            "os_version": "10.0.19044",
                            "os_arch": "x64",
                            "app_arch": "x64",
                            "system_locale": "en-US",
                            "browser_user_agent": DISCORD_CLIENT_UA,
                            "browser_version": "35.3.0",
                            "client_build_number": 455964,
                            "native_build_number": 69976,
                            "client_event_source": None,
                            "client_launch_id": launch_id,
                            "launch_signature": launch_sig,
                            "client_heartbeat_session_id": hb_session,
                            "client_app_state": "focused",
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
                }

                await self._ws.send(json.dumps(identify_payload))

                while True:
                    msg = await self._ws.recv()
                    payload = json.loads(msg)
                    op = payload.get("op")
                    seq = payload.get("s")

                    if seq is not None:
                        async with self._seq_lock:
                            self._sequence = seq

                    if op == GATEWAY_DISPATCH:
                        event_type = payload.get("t")
                        if event_type == "READY":
                            self._session_id = payload["d"]["session_id"]
                            self._self_user_id = payload["d"]["user"]["id"]
                            for g in payload["d"].get("guilds", []):
                                if str(g.get("id")) == str(self.guild_id):
                                    self._guild_name = g.get("name", self.guild_id)
                                    break

                        if event_type == "GUILD_CREATE":
                            if str(payload["d"].get("id")) == str(self.guild_id):
                                self._guild_name = payload["d"].get("name", self.guild_id)
                                raw_roles = payload["d"].get("roles", [])
                                self._guild_roles = []
                                for r in raw_roles:
                                    c_int = r.get("color", 0)
                                    c_hex = f"#{c_int:06x}" if c_int else None
                                    self._guild_roles.append({
                                        "id": str(r.get("id")),
                                        "name": r.get("name"),
                                        "color": c_hex,
                                        "position": r.get("position", 0),
                                        "hoist": r.get("hoist", False),
                                    })
                        if event_type == "READY_SUPPLEMENTAL":
                            d = payload.get("d", {})
                            merged = d.get("merged_presences", {})
                            for g_pres_list in merged.get("guilds", []):
                                for p in g_pres_list:
                                    uid = str(p.get("user_id") or "")
                                    st = p.get("status")
                                    if uid and st:
                                        self._presences[uid] = st
                            for p in merged.get("friends", []):
                                uid = str(p.get("user_id") or "")
                                st = p.get("status")
                                if uid and st:
                                    self._presences[uid] = st
                            return True

            return False

        except Exception:
            return False

    async def request_members(self) -> list[dict]:
        if not self._ws:
            return []

        try:
            nonce = str(uuid.uuid4())[:32]
            continuation_token = None
            total_pages = 0
            max_pages = 50

            for _ in range(max_pages):
                if self._done_event.is_set():
                    break

                total_pages += 1
                payload = {
                    "op": GATEWAY_SEARCH_RECENT_MEMBERS,
                    "d": {
                        "guild_id": self.guild_id,
                        "query": "",
                        "continuation_token": continuation_token,
                        "nonce": nonce,
                    },
                }

                await self._ws.send(json.dumps(payload))

                new_count = 0
                got_chunk = False
                for _ in range(20):
                    try:
                        msg = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
                        data = json.loads(msg)
                    except asyncio.TimeoutError:
                        break

                    op = data.get("op")
                    seq = data.get("s")
                    if seq is not None:
                        async with self._seq_lock:
                            self._sequence = seq

                    event_type = data.get("t")
                    d = data.get("d", {})

                    if event_type == "GUILD_MEMBERS_CHUNK":
                        got_chunk = True
                        members = d.get("members", [])
                        raw_presences = {}
                        for p in d.get("presences", []):
                            p_u = p.get("user", {})
                            if p_u and p_u.get("id"):
                                raw_presences[str(p_u.get("id"))] = p.get("status", "offline")

                        for member in members:
                            user = member.get("user", {})
                            uid = str(user.get("id") or "")
                            if uid and uid not in self._scraped_ids:
                                self._scraped_ids.add(uid)
                                username = user.get("username", "Unknown")
                                global_name = user.get("global_name")
                                nick = member.get("nick")
                                display_name = nick or global_name or username
                                avatar_hash = user.get("avatar")
                                avatar_url = None
                                if avatar_hash:
                                    ext = "gif" if str(avatar_hash).startswith("a_") else "png"
                                    avatar_url = f"https://cdn.discordapp.com/avatars/{uid}/{avatar_hash}.{ext}?size=128"
                                deco_data = user.get("avatar_decoration_data")
                                deco_url = None
                                if deco_data and isinstance(deco_data, dict):
                                    asset = deco_data.get("asset")
                                    if asset:
                                        deco_url = f"https://cdn.discordapp.com/avatar-decoration-presets/{asset}.png?size=128"

                                status = self._presences.get(uid) or raw_presences.get(uid, "offline")
                                self._scraped_members.append({
                                    "id": uid,
                                    "username": username,
                                    "displayName": display_name,
                                    "avatarUrl": avatar_url,
                                    "avatarDecorationUrl": deco_url,
                                    "roles": member.get("roles", []),
                                    "joinedAt": member.get("joined_at"),
                                    "nick": nick,
                                    "isPending": member.get("pending", False),
                                    "status": status,
                                })
                                new_count += 1

                        if members:
                            continuation_token = members[0].get("user", {}).get("id")
                        break

                if not got_chunk or new_count == 0:
                    break

                await asyncio.sleep(random.uniform(self.chunk_delay_min, self.chunk_delay_max))

            return self._scraped_members

        except Exception:
            return self._scraped_members

    async def disconnect(self) -> None:
        self._done_event.set()

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

    async def scrape(self) -> tuple[list[dict], str]:
        try:
            connected = await self.connect()
            if not connected:
                return [], self.guild_id

            members = await self.request_members()
            guild_name = self._guild_name or self.guild_id
            return members, guild_name

        finally:
            await self.disconnect()


async def scrape_guild_gateway(
    guild_id: str,
    token: str,
    proxy_url: Optional[str] = None,
    chunk_delay_min: float = 0.3,
    chunk_delay_max: float = 0.8,
) -> tuple[list[dict], str]:
    scraper = GatewayScraper(
        token=token,
        guild_id=guild_id,
        proxy_url=proxy_url,
        chunk_delay_min=chunk_delay_min,
        chunk_delay_max=chunk_delay_max,
    )
    return await scraper.scrape()