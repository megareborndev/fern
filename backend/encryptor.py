import secrets
import base64
import hashlib
import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

encryptor_version = "1"

class DiscordCrypto:
    @staticmethod
    async def generate_random_key():
        return secrets.token_hex(32)
    @staticmethod
    async def get_crypto_key(key_str: str):
        key_data = key_str.encode("utf-8")
        hash_bytes = hashlib.sha256(key_data).digest()
        return hash_bytes
    @staticmethod
    async def decrypt_message(ciphertext: str, key_str: str) -> str:
        try:
            if not ciphertext.startswith(f"ENC:v{encryptor_version}:"):
                raise ValueError("Unsupported format version")

            parts = ciphertext.split(":")
            if len(parts) != 4:
                raise ValueError("Invalid ciphertext format")

            iv_b64 = parts[2]
            ciphertext_b64 = parts[3]

            iv = base64.b64decode(iv_b64)
            encrypted = base64.b64decode(ciphertext_b64)

            key = await DiscordCrypto.get_crypto_key(key_str)
            aesgcm = AESGCM(key)

            plaintext = aesgcm.decrypt(iv, encrypted, None)
            return plaintext.decode("utf-8")

        except Exception as err:
            return 500
    @staticmethod
    def encrypt_audio_chunk(plaintext: bytes, key_str: str) -> bytes:
        key_data = key_str.encode("utf-8")
        key = hashlib.sha256(key_data).digest()
        iv = os.urandom(12)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(iv, plaintext, None)
        return iv + ciphertext

    @staticmethod
    def decrypt_audio_chunk(encrypted: bytes, key_str: str) -> bytes:
        if len(encrypted) < 13:
            raise ValueError("Invalid encrypted audio chunk")

        key_data = key_str.encode("utf-8")
        key = hashlib.sha256(key_data).digest()
        iv = encrypted[:12]
        ciphertext = encrypted[12:]
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(iv, ciphertext, None)
    @staticmethod
    async def encrypt_message(plaintext: str, key_str: str) -> str:
        try:
            key = await DiscordCrypto.get_crypto_key(key_str)
            iv = os.urandom(12)
            aesgcm = AESGCM(key)

            ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)

            iv_base64 = base64.b64encode(iv).decode("utf-8")
            ciphertext_base64 = base64.b64encode(ciphertext).decode("utf-8")

            return f"ENC:v{encryptor_version}:{iv_base64}:{ciphertext_base64}"

        except Exception as err:
            print(f"Crypto encrypt error: {err}")
            raise