import asyncio
import base64
import json
import logging
import socket
import struct
import time
from dataclasses import dataclass
from typing import Callable

import aiohttp

try:
    from nacl.secret import SecretBox
except ImportError:
    SecretBox = None

logger = logging.getLogger("discord-voice-manager")

VOICE_GATEWAY_URL = "wss://{endpoint}/?v=8"
UDP_DISCOVERY_PACKET_SIZE = 74
SAMPLES_PER_FRAME = 960
RTP_VERSION = 0x80
RTP_PAYLOAD_TYPE = 0x78


def xor_crypt(data: bytes, key: bytes) -> bytes:
    if not key:
        return data
    return bytes(value ^ key[index % len(key)] for index, value in enumerate(data))


@dataclass
class VoiceCredentials:
    user_id: str
    session_id: str
    token: str


class VoiceConnection:
    def __init__(
        self,
        guild_id: str | None,
        channel_id: str,
        endpoint: str,
        credentials: VoiceCredentials,
        custom_key: bytes,
    ):
        self.guild_id = str(guild_id) if guild_id is not None else None
        self.channel_id = str(channel_id)
        self.endpoint = endpoint.removeprefix("wss://").removeprefix("https://").rstrip("/")
        self.credentials = credentials
        self.custom_key = custom_key

        self.ws = None
        self.udp = None
        self.ssrc = 0
        self.sequence = 0
        self.timestamp = 0
        self.ip = None
        self.port = None
        self.mode = None
        self.secret_key = None
        self.ready = asyncio.Event()
        self.closed = asyncio.Event()
        self.audio_callback: Callable[[bytes], None] | None = None
        self._heartbeat_task = None
        self._udp_task = None
        self._ws_task = None

    async def connect(self):
        session = aiohttp.ClientSession()
        try:
            self.ws = await session.ws_connect(VOICE_GATEWAY_URL.format(endpoint=self.endpoint))
            await self._send_ws({
                "op": 0,
                "d": {
                    "server_id": self.guild_id,
                    "user_id": self.credentials.user_id,
                    "session_id": self.credentials.session_id,
                    "token": self.credentials.token,
                },
            })
            self._ws_task = asyncio.create_task(self._ws_loop(session))
        except Exception:
            await session.close()
            raise

    async def wait_ready(self, timeout: float | None = 20.0):
        await asyncio.wait_for(self.ready.wait(), timeout=timeout)

    async def send_audio(self, opus_frame: bytes):
        if not self.ready.is_set() or self.udp is None or self.ip is None or self.port is None:
            raise RuntimeError("voice connection is not ready")

        custom_encrypted = xor_crypt(opus_frame, self.custom_key)
        rtp_header = self._build_rtp_header()
        discord_encrypted = self._encrypt_discord_payload(rtp_header, custom_encrypted)
        packet = rtp_header + discord_encrypted
        await asyncio.get_running_loop().sock_sendto(self.udp, packet, (self.ip, self.port))

    def on_audio(self, callback: Callable[[bytes], None]):
        self.audio_callback = callback

    async def close(self):
        self.closed.set()
        for task in (self._heartbeat_task, self._udp_task, self._ws_task):
            if task:
                task.cancel()
        if self.ws is not None:
            await self.ws.close()
        if self.udp is not None:
            self.udp.close()

    async def _ws_loop(self, session: aiohttp.ClientSession):
        try:
            async for message in self.ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_ws_message(json.loads(message.data))
                elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        finally:
            self.closed.set()
            await session.close()

    async def _handle_ws_message(self, message: dict):
        op = message.get("op")
        data = message.get("d") or {}

        if op == 2:
            self.ssrc = int(data["ssrc"])
            self.ip = data.get("ip") or self.endpoint.split(":", 1)[0]
            self.port = int(data["port"])
            modes = data.get("modes") or []
            self.mode = "xsalsa20_poly1305" if "xsalsa20_poly1305" in modes else (modes[0] if modes else None)
            await self._open_udp()
        elif op == 4:
            self.mode = data.get("mode") or self.mode
            self.secret_key = self._parse_secret_key(data.get("secret_key"))
            self.ready.set()
            logger.info("Voice ready for channel %s using mode %s", self.channel_id, self.mode)
        elif op == 8:
            interval = (data.get("heartbeat_interval") or 5000) / 1000
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval))

    async def _heartbeat_loop(self, interval: float):
        while not self.closed.is_set():
            await self._send_ws({"op": 3, "d": int(time.time() * 1000)})
            await asyncio.sleep(interval)

    async def _send_ws(self, payload: dict):
        if self.ws is not None:
            await self.ws.send_str(json.dumps(payload))

    async def _open_udp(self):
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setblocking(False)
        self.udp.bind(("0.0.0.0", 0))
        self._udp_task = asyncio.create_task(self._udp_loop())

        packet = bytearray(UDP_DISCOVERY_PACKET_SIZE)
        struct.pack_into(">H", packet, 0, 1)
        struct.pack_into(">H", packet, 2, UDP_DISCOVERY_PACKET_SIZE)
        struct.pack_into(">I", packet, 4, self.ssrc)
        await asyncio.get_running_loop().sock_sendto(self.udp, bytes(packet), (self.ip, self.port))

    async def _udp_loop(self):
        while not self.closed.is_set():
            packet, _ = await asyncio.get_running_loop().sock_recvfrom(self.udp, 4096)
            await self._handle_udp_packet(packet)

    async def _handle_udp_packet(self, packet: bytes):
        if len(packet) == UDP_DISCOVERY_PACKET_SIZE:
            address = packet[8:72].split(b"\x00", 1)[0].decode("ascii", errors="ignore")
            port = struct.unpack_from(">H", packet, 72)[0]
            await self._send_ws({
                "op": 1,
                "d": {
                    "protocol": "udp",
                    "data": {
                        "address": address,
                        "port": port,
                        "mode": self.mode,
                    },
                },
            })
            return

        if not self.ready.is_set() or len(packet) <= 12:
            return

        rtp_header = packet[:12]
        encrypted_payload = packet[12:]
        try:
            discord_payload = self._decrypt_discord_payload(rtp_header, encrypted_payload)
            custom_decrypted = xor_crypt(discord_payload, self.custom_key)
        except Exception as exc:
            logger.debug("Could not decrypt received voice packet: %s", exc)
            return

        if self.audio_callback:
            self.audio_callback(custom_decrypted)

    def _build_rtp_header(self) -> bytes:
        self.sequence = (self.sequence + 1) & 0xFFFF
        self.timestamp = (self.timestamp + SAMPLES_PER_FRAME) & 0xFFFFFFFF
        return struct.pack(">BBHII", RTP_VERSION, RTP_PAYLOAD_TYPE, self.sequence, self.timestamp, self.ssrc)

    def _encrypt_discord_payload(self, rtp_header: bytes, payload: bytes) -> bytes:
        if self.mode != "xsalsa20_poly1305":
            raise RuntimeError(f"Unsupported voice encryption mode: {self.mode}")
        if SecretBox is None:
            raise RuntimeError("PyNaCl is required for Discord voice encryption")
        nonce = rtp_header + b"\x00" * 12
        return SecretBox(bytes(self.secret_key)).encrypt(payload, nonce).ciphertext

    def _decrypt_discord_payload(self, rtp_header: bytes, payload: bytes) -> bytes:
        if self.mode != "xsalsa20_poly1305":
            raise RuntimeError(f"Unsupported voice encryption mode: {self.mode}")
        if SecretBox is None:
            raise RuntimeError("PyNaCl is required for Discord voice encryption")
        nonce = rtp_header + b"\x00" * 12
        return SecretBox(bytes(self.secret_key)).decrypt(payload, nonce)

    @staticmethod
    def _parse_secret_key(secret_key):
        if isinstance(secret_key, list):
            return bytes(secret_key)
        if isinstance(secret_key, str):
            try:
                return base64.b64decode(secret_key)
            except Exception:
                return secret_key.encode("utf-8")
        return bytes(secret_key)


class DiscordVoiceManager:
    def __init__(self):
        self.credentials: VoiceCredentials | None = None
        self.connections: dict[str, VoiceConnection] = {}

    def set_credentials(self, user_id: str, session_id: str, token: str):
        self.credentials = VoiceCredentials(str(user_id), str(session_id), str(token))

    async def join_voice_channel(
        self,
        guild_id: str | None,
        channel_id: str,
        endpoint: str,
        custom_key: bytes,
    ) -> VoiceConnection:
        if self.credentials is None:
            raise RuntimeError("credentials are not set")
        channel_id = str(channel_id)
        if channel_id in self.connections:
            raise RuntimeError(f"already connected to channel {channel_id}")

        connection = VoiceConnection(
            guild_id=guild_id,
            channel_id=channel_id,
            endpoint=endpoint,
            credentials=self.credentials,
            custom_key=custom_key,
        )
        self.connections[channel_id] = connection
        try:
            await connection.connect()
            await connection.wait_ready()
        except Exception:
            self.connections.pop(channel_id, None)
            await connection.close()
            raise
        return connection

    async def send_audio(self, channel_id: str, opus_frame: bytes):
        await self.connections[str(channel_id)].send_audio(opus_frame)

    def on_audio(self, channel_id: str, callback: Callable[[bytes], None]):
        self.connections[str(channel_id)].on_audio(callback)

    async def leave_channel(self, channel_id: str):
        connection = self.connections.pop(str(channel_id), None)
        if connection:
            await connection.close()

    async def close_all(self):
        for channel_id in list(self.connections):
            await self.leave_channel(channel_id)