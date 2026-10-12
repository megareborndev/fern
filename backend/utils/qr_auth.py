from __future__ import annotations

import base64
import json
import logging
import threading
import time
from typing import Callable

import requests
import websocket
from Crypto.Cipher import PKCS1_OAEP
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA

logger = logging.getLogger(__name__)

WS_ENDPOINT = "wss://remote-auth-gateway.discord.gg/?v=2"
LOGIN_ENDPOINT = "https://discord.com/api/v9/users/@me/remote-auth/login"


class RemoteAuthSession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.fingerprint: str | None = None
        self.user_id: str | None = None
        self.username: str | None = None
        self.discriminator: str | None = None
        self.avatar_hash: str | None = None
        self.token: str | None = None
        self.error: str | None = None
        self._created_at: float = time.time()
        self._done = threading.Event()

    def wait(self, timeout: float | None = 120) -> bool:
        return self._done.wait(timeout)

    def set_done(self) -> None:
        self._done.set()


class QrAuthClient:
    def __init__(self, session: RemoteAuthSession) -> None:
        self.session = session
        self.key = RSA.generate(2048)
        self.cipher = PKCS1_OAEP.new(self.key, hashAlgo=SHA256)
        self._heartbeat_interval: float | None = None
        self._last_heartbeat: float = 0.0
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None

    @property
    def public_key(self) -> str:
        pub = self.key.publickey().export_key().decode("utf-8")
        return "".join(pub.split("\n")[1:-1])

    def decrypt(self, data: bytes) -> bytes:
        return self.cipher.decrypt(data)

    def decrypt_b64(self, encrypted_b64: str) -> bytes:
        return self.decrypt(base64.b64decode(encrypted_b64))

    def _send(self, op: str, **data) -> None:
        payload = {"op": op}
        payload.update(data)
        if self._ws:
            self._ws.send(json.dumps(payload))

    def _heartbeat_loop(self) -> None:
        while not self.session._done.is_set():
            now = time.time()
            if self._heartbeat_interval and now - self._last_heartbeat >= self._heartbeat_interval:
                self._send("heartbeat")
                self._last_heartbeat = now
            time.sleep(1)

    def on_open(self, ws: websocket.WebSocketApp) -> None:
        logger.info("[qr] WebSocket opened")

    def on_message(self, ws: websocket.WebSocketApp, message: str) -> None:
        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            return
        op = data.get("op")
        logger.info(f"[qr] received op={op}")

        if op == "hello":
            self._heartbeat_interval = data.get("heartbeat_interval", 41250) / 1000.0
            self._last_heartbeat = time.time()
            threading.Thread(target=self._heartbeat_loop, daemon=True).start()
            self._send("init", encoded_public_key=self.public_key)

        elif op == "nonce_proof":
            nonce_b64 = data.get("encrypted_nonce", "")
            decrypted = self.decrypt_b64(nonce_b64)
            proof = base64.urlsafe_b64encode(
                SHA256.new(data=decrypted).digest()
            ).decode().rstrip("=")
            self._send("nonce_proof", proof=proof)

        elif op == "pending_remote_init":
            fingerprint = data.get("fingerprint", "")
            self.session.fingerprint = fingerprint

        elif op == "pending_ticket":
            encrypted = data.get("encrypted_user_payload", "")
            payload = self.decrypt_b64(encrypted).decode()
            parts = payload.split(":")
            if len(parts) >= 4:
                self.session.user_id = parts[0]
                self.session.discriminator = parts[1]
                self.session.avatar_hash = parts[2]
                self.session.username = parts[3]

        elif op == "pending_login":
            ticket = data.get("ticket", "")
            try:
                encrypted_token = self._exchange_ticket(ticket)
                if encrypted_token:
                    token = self.decrypt_b64(encrypted_token).decode()
                    self.session.token = token
                else:
                    self.session.error = "Failed to exchange ticket"
            except Exception as e:
                logger.exception("[qr] ticket exchange failed")
                self.session.error = str(e)
            self.session.set_done()
            if self._ws:
                self._ws.close()

        elif op == "cancel":
            self.session.error = "Cancelled by user"
            self.session.set_done()

    def on_error(self, ws: websocket.WebSocketApp, error) -> None:
        logger.error(f"[qr] WS error: {error}")
        if not self.session._done.is_set():
            self.session.error = str(error)
            self.session.set_done()

    def on_close(self, ws: websocket.WebSocketApp, close_status, close_msg) -> None:
        logger.info(f"[qr] WS closed: {close_status} {close_msg}")

    def _exchange_ticket(self, ticket: str) -> str | None:
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": "https://discord.com",
            "Referer": "https://discord.com/login",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
            ),
        }
        try:
            r = requests.post(LOGIN_ENDPOINT, json={"ticket": ticket}, headers=headers, timeout=15)
            if r.status_code == 200:
                return r.json().get("encrypted_token")
            logger.warning(f"[qr] ticket exchange returned {r.status_code}: {r.text[:200]}")
            return None
        except requests.RequestException as e:
            logger.error(f"[qr] ticket exchange request failed: {e}")
            return None

    def run(self) -> None:
        self._ws = websocket.WebSocketApp(
            WS_ENDPOINT,
            header={"Origin": "https://discord.com"},
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
        )
        self._ws.run_forever()

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, daemon=True, name=f"qr-auth-{self.session.session_id[:8]}")
        self._thread.start()
