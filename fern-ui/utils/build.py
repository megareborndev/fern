# cyberbeach.cc #sexy

from __future__ import annotations

import hashlib
import json as jsonlib
import random
import re
import threading
import time
from base64 import b64encode
from difflib import SequenceMatcher
from uuid import uuid4

from httpz import Client as HttpzClient

# from .config import BUILD_NUM
BUILD_NUM = 72112
DEFAULT_TIMEZONE = "UTC"

def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()

def _electron_ua(client_version: str, chromium_ver: str, electron_ver: str) -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        f"discord/{client_version} Chrome/{chromium_ver} "
        f"Electron/{electron_ver} Safari/537.36"
    )

_CHROME_FP: dict = {
    "browser": "chrome",
    "h2_fingerprint": "1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p",
    "h2_fingerprint_hash": "52d84b11737d980aef856699f885ca86",
    "ja3": "771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,35-65281-11-18-43-10-45-16-0-17613-5-23-27-13-65037-51,4588-29-23-24,0",
    "ja3_hash": "2e3999cda23d9bff7ce0e5fbe42ee2f2",
    "source": "manual",
    "source_name": "chrome138-electron",
    "user_agent": _electron_ua("1.0.9243", "138.0.7204.251", "37.6.0"),
}
_DISCORD_OS_VERSION = "10.0.26200"
_DISCORD_OS_SDK_VERSION = "26200"
_DISCORD_SEC_CH_UA_PLATFORM = '"Windows"'
_DISCORD_NATIVE_BUILD_NUMBER = random.randint(84800, 84934)
_ELECTRON_CHROME_PAIRS: list[tuple[str, str]] = [
    ("130.0.6723.137", "33.4.11"), # electron 33 -> chromium 130
    ("132.0.6834.210", "34.5.8"),  # etc
    ("134.0.6998.205", "35.7.5"),
    ("136.0.7103.149", "36.9.2"),
    ("138.0.7204.251", "37.6.0"),
]

def _sec_ch_ua(chromium_major: int) -> str:
    brand = '"Not/A)Brand";v="99"' if chromium_major >= 140 else '"Not)A;Brand";v="8"'
    return f'{brand}, "Chromium";v="{chromium_major}"'

_ELECTRON_PAIRS_FALLBACK: dict[int, tuple[str, str]] = {
    33: ("130.0.6723.191", "33.4.11"),
    34: ("132.0.6834.210", "34.5.8"),
    35: ("134.0.6998.205", "35.7.5"),
    36: ("136.0.7103.177", "36.9.5"),
    37: ("138.0.7204.251", "37.10.3"),
    38: ("140.0.7339.249", "38.8.6"),
    39: ("142.0.7444.265", "39.8.10"),
    40: ("144.0.7559.236", "40.10.6"),
    41: ("146.0.7680.216", "41.10.1"),
    42: ("148.0.7778.280", "42.6.1"),
    43: ("150.0.7871.47", "43.1.0"),
}
_ELECTRON_RELEASES_URL = "https://releases.electronjs.org/releases.json"
_ELECTRON_PAIRS_TTL_SECONDS = 6 * 3600

_CHANNEL_CONFIG: dict[str, dict] = {
    "discord-ptb": {
        "release_channel": "ptb",
        "domain": "ptb.discord.com",
        "electron_majors": (33, 37),
        "fallback_version": "1.0.1202",
        "fallback_build": 575486,
        "native_build_band": (85700, 85850),
    },
    "discord-canary": {
        "release_channel": "canary",
        "domain": "canary.discord.com",
        "electron_majors": (38, 42),
        "fallback_version": "1.0.1032",
        "fallback_build": 575552,
        "native_build_band": (85700, 85850),
    },
}

_IOS_FP: dict = {
    "browser": "safari",
    "h2_fingerprint": "2:0;3:100;4:2097152;9:1|10420225|0|m,s,a,p",
    "h2_fingerprint_hash": "c52879e43202aeb92740be6e8c86ea96",
    "ja3": "771,4865-4866-4867-49196-49195-52393-49200-49199-52392-49162-49161-49172-49171-157-156-53-47-49160-49170-10,0-23-65281-10-11-16-5-13-18-51-45-43-27-21,29-23-24-25,0",
    "ja3_hash": "773906b0efdefa24a7f2b8eb6985bf37",
    "source": "manual",
    "source_name": "safari184_ios",
    "user_agent": "",
}

_IOS_DEVICES: list[str] = [
    "iPhone14,2",
    "iPhone14,3",
    "iPhone14,4",
    "iPhone14,5",
    "iPhone14,7",
    "iPhone14,8",
    "iPhone15,2",
    "iPhone15,3",
    "iPhone15,4",
    "iPhone15,5",
    "iPhone16,1",
    "iPhone16,2",
    "iPhone17,1",
    "iPhone17,2",
    "iPhone17,3",
    "iPhone17,4",
]

_IOS_RUNTIMES: list[tuple[str, str, str]] = [
    ("18.5", "24.5.0", "3826.500.131"),
    ("18.4.1", "24.4.0", "3826.400.120"),
    ("18.4", "24.4.0", "3826.400.120"),
]
_IOS_SENTRY_PUBLIC_KEY = "06e00b7472364e1986bc684e14371271"
_IOS_DESIGN_ID = 2
_IOS_APP_MAJOR = 311
_IOS_APP_BUILD = 91117

def _ios_app_version() -> tuple[str, int]:
    steps = random.randint(0, 6)
    major = _IOS_APP_MAJOR - steps
    build = _IOS_APP_BUILD - sum(random.randint(250, 360) for _ in range(steps))
    build -= random.randint(0, 90)
    return f"{major}.0", build

_BUILD_TTL_SECONDS = 1800

def _scrape_build_number(domain: str) -> str | None:
    try:
        client = HttpzClient(impersonate="chrome148", timeout=30)
        page = client.get(f"https://{domain}/login").text
        assets = re.compile(
            r'<script defer src="/assets/((?:[a-zA-Z0-9]+\.)?[a-zA-Z0-9]+\.js)', re.I
        ).findall(page)
        for asset in reversed(assets):
            body = client.get(f"https://{domain}/assets/{asset}").text.replace(" ", "")
            if 'buildNumber:"' not in body:
                continue
            return body.split('buildNumber:"')[1].split('"')[0]
    except Exception:
        return None
    return None

def _fetch_host_version(channel: str) -> str | None:
    try:
        client = HttpzClient(impersonate="chrome148", timeout=30)
        manifest = client.get(
            "https://discord.com/api/updates/distributions/app/manifests/latest"
            f"?channel={channel}&platform=win&arch=x64"
        ).json()
        hv = manifest.get("full", {}).get("host_version")
        if isinstance(hv, list) and len(hv) == 3:
            return f"{hv[0]}.{hv[1]}.{hv[2]}"
    except Exception:
        return None
    return None

class _ChannelBuildInfo:
    def __init__(
        self,
        *,
        channel: str,
        domain: str,
        fallback_version: str,
        fallback_build: int,
    ) -> None:
        self.channel = channel
        self.domain = domain
        self.fallback_version = fallback_version
        self.fallback_build = fallback_build
        self._lock = threading.Lock()
        self._version: str | None = None
        self._build: int | None = None
        self._ts: float = 0.0

    def get(self) -> tuple[str, int]:
        if self._version is not None and (time.time() - self._ts) <= _BUILD_TTL_SECONDS:
            return self._version, self._build
        with self._lock:
            if (
                self._version is not None
                and (time.time() - self._ts) <= _BUILD_TTL_SECONDS
            ):
                return self._version, self._build
            version = _fetch_host_version(self.channel) or self.fallback_version
            build = _scrape_build_number(self.domain) or self.fallback_build
            self._version, self._build, self._ts = version, int(build), time.time()
            return self._version, self._build

def _fetch_electron_pairs() -> dict[int, tuple[str, str]] | None:
    try:
        client = HttpzClient(impersonate="chrome148", timeout=40)
        releases = client.get(_ELECTRON_RELEASES_URL).json()
        best: dict[int, tuple] = {}
        for entry in releases:
            ver = str(entry.get("version", ""))
            chrome = str(entry.get("chrome", ""))
            if not re.fullmatch(r"\d+\.\d+\.\d+", ver):  # stable releases only
                continue
            if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", chrome):
                continue
            major = int(ver.split(".")[0])
            key = tuple(int(x) for x in ver.split("."))
            if major not in best or key > best[major][0]:
                best[major] = (key, chrome, ver)
        pairs = {m: (v[1], v[2]) for m, v in best.items()}
        return pairs or None
    except Exception:
        return None


class _ElectronPairsCache:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pairs: dict[int, tuple[str, str]] | None = None
        self._ts: float = 0.0

    def get(self) -> dict[int, tuple[str, str]]:
        if (
            self._pairs is not None
            and (time.time() - self._ts) <= _ELECTRON_PAIRS_TTL_SECONDS
        ):
            return self._pairs
        with self._lock:
            if (
                self._pairs is not None
                and (time.time() - self._ts) <= _ELECTRON_PAIRS_TTL_SECONDS
            ):
                return self._pairs
            self._pairs = _fetch_electron_pairs() or _ELECTRON_PAIRS_FALLBACK
            self._ts = time.time()
            return self._pairs

_electron_pairs_cache = _ElectronPairsCache()

def _electron_pairs() -> dict[int, tuple[str, str]]:
    return _electron_pairs_cache.get()

def _older_version(ceiling: str) -> str:
    try:
        major, minor, patch = ceiling.split(".")
        top = int(patch)
    except Exception:
        return ceiling
    low = max(1, top - 40)
    return f"{major}.{minor}.{random.randint(low, top)}"

def _older_build(ceiling: int) -> int:
    ceiling = int(ceiling)
    return random.randint(max(1, ceiling - 2500), ceiling)


def _pick_desktop_runtime(client_version: str) -> tuple[str, str, str]:
    chromium_ver, electron_ver = random.choice(_ELECTRON_CHROME_PAIRS)
    chromium_major = int(chromium_ver.split(".", 1)[0])
    ua = _electron_ua(client_version, chromium_ver, electron_ver)
    return ua, electron_ver, _sec_ch_ua(chromium_major)

def generate_similar_ja3_and_h2(
    base_fp: dict = _CHROME_FP, min_similarity: float = 0.80
) -> dict:
    ver, ciphers, exts, curves, pfmt = base_fp["ja3"].split(",")
    ext_list = [e for e in exts.split("-") if e != "41"]
    curve_list = curves.split("-")

    def _make_ja3() -> str:
        e = ext_list[:]
        random.shuffle(e)
        c = curve_list[:]
        if len(c) > 1:
            tail = c[1:]
            random.shuffle(tail)
            c = c[:1] + tail
        return ",".join([ver, ciphers, "-".join(e), "-".join(c), pfmt])

    new_ja3 = base_fp["ja3"]
    for _ in range(300):
        cand = _make_ja3()
        if (
            cand != base_fp["ja3"]
            and SequenceMatcher(None, cand, base_fp["ja3"]).ratio() >= min_similarity
        ):
            new_ja3 = cand
            break
    if new_ja3 == base_fp["ja3"] and len(ext_list) >= 2:
        e = ext_list[:]
        i = random.randrange(len(e) - 1)
        e[i], e[i + 1] = e[i + 1], e[i]
        new_ja3 = ",".join([ver, ciphers, "-".join(e), curves, pfmt])

    settings, window, prio, horder = base_fp["h2_fingerprint"].split("|")
    new_h2 = base_fp["h2_fingerprint"]
    for _ in range(300):
        try:
            w = str(int(window) + random.randint(-1, 1)) if window.isdigit() else window
        except Exception:
            w = window
        cand = "|".join([settings, w, prio, horder])
        if (
            cand != base_fp["h2_fingerprint"]
            and SequenceMatcher(None, cand, base_fp["h2_fingerprint"]).ratio()
            >= min_similarity
        ):
            new_h2 = cand
            break

    out = dict(base_fp)
    if base_fp["ja3"] == _CHROME_FP["ja3"]:
        out["browser"] = "chrome"
    else:
        out["browser"] = "ios"
    out["ja3"] = new_ja3
    out["ja3_hash"] = _md5(new_ja3)
    out["h2_fingerprint"] = new_h2
    out["h2_fingerprint_hash"] = _md5(new_h2)
    out["source"] = "generated"
    return out

class ClientIdentity:

    def __init__(
        self,
        *,
        name: str,
        ua: str,
        sec_ch_ua: str,
        sec_ch_ua_platform: str,
        browser_version: str,
        client_version: str,
        release_channel: str,
        api_domain: str,
        os_version: str,
        os_sdk_version: str,
        client_build_number: int,
        native_build_number: int,
        lang: str,
        tls_data: dict,
    ) -> None:
        self.name = name
        self.ua = ua
        self.sec_ch_ua = sec_ch_ua
        self.sec_ch_ua_platform = sec_ch_ua_platform
        self.browser_version = browser_version
        self.client_version = client_version
        self.release_channel = release_channel
        self.api_domain = api_domain
        self.os_version = os_version
        self.os_sdk_version = os_sdk_version
        self.client_build_number = client_build_number
        self.native_build_number = native_build_number
        self.lang = lang
        self.tls_data = tls_data
    @property
    def origin(self) -> str:
        return f"https://{self.api_domain}"

    @property
    def api_base(self) -> str:
        return f"{self.origin}/api/v9"

    def super_properties(self, session_id: str | None = None) -> dict:
        props = {
            "os": "Windows",
            "browser": "Discord Client",
            "release_channel": self.release_channel,
            "client_version": self.client_version,
            "os_version": self.os_version,
            "os_arch": "x64",
            "app_arch": "x64",
            "system_locale": self.lang,
            "has_client_mods": False,
            "client_launch_id": str(uuid4()),
            "browser_user_agent": self.ua,
            "browser_version": self.browser_version,
            "os_sdk_version": self.os_sdk_version,
            "client_build_number": int(self.client_build_number),
            "native_build_number": self.native_build_number,
            "client_event_source": None,
        }
        if session_id is not None:
            props["client_heartbeat_session_id"] = session_id
        props["launch_signature"] = str(uuid4())
        props["client_app_state"] = "focused"
        return props

    def trackers(self, session_id: str | None = None) -> tuple[str, dict]:
        props = self.super_properties(session_id)
        encoded = b64encode(
            jsonlib.dumps(props, separators=(",", ":")).encode()
        ).decode()
        return encoded, props

    def session_headers(self) -> dict:
        return {
            "User-Agent": self.ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": f"{self.lang},en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Referer": f"{self.origin}/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

    def browse_headers(self) -> dict:
        return {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-encoding": "gzip, deflate, br",
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": self.sec_ch_ua_platform,
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "none",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": self.ua,
        }

    def api_headers(
        self,
        fingerprint: str,
        *,
        session_id: str | None = None,
        timezone: str = DEFAULT_TIMEZONE,
        referer: str | None = None,
    ) -> dict:
        trackers, _ = self.trackers(session_id)
        return {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": f"{self.lang},en;q=0.9",
            "Content-Type": "application/json",
            "Origin": self.origin,
            "Referer": referer or f"{self.origin}/register",
            "Sec-CH-UA": self.sec_ch_ua,
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": self.sec_ch_ua_platform,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": self.ua,
            "X-Debug-Options": "bugReporterEnabled",
            "X-Discord-Locale": self.lang,
            "X-Discord-Timezone": timezone,
            "X-Fingerprint": fingerprint,
            "X-Super-Properties": trackers,
            "Priority": "u=1, i",
        }

    def authed_headers(
        self,
        token: str,
        *,
        session_id: str | None = None,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> dict:
        trackers, _ = self.trackers(session_id)
        return {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": f"{self.lang},en;q=0.9",
            "Authorization": token,
            "Content-Type": "application/json",
            "Origin": self.origin,
            "Referer": f"{self.origin}/channels/@me",
            "Sec-CH-UA": self.sec_ch_ua,
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": self.sec_ch_ua_platform,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": self.ua,
            "X-Debug-Options": "bugReporterEnabled",
            "X-Discord-Locale": self.lang,
            "X-Discord-Timezone": timezone,
            "X-Super-Properties": trackers,
            "Priority": "u=1, i",
        }

class IOSClientIdentity(ClientIdentity):

    def __init__(
        self,
        *,
        device: str,
        device_vendor_id: str,
        cfnetwork: str,
        darwin: str,
        sentry_public_key: str,
        design_id: int,
        **base,
    ) -> None:
        super().__init__(**base)
        self.device = device
        self.device_vendor_id = device_vendor_id
        self.cfnetwork = cfnetwork
        self.darwin = darwin
        self.sentry_public_key = sentry_public_key
        self.design_id = design_id

    def super_properties(self, session_id: str | None = None) -> dict:
        props = {
            "os": "iOS",
            "browser": "Discord iOS",
            "device": self.device,
            "system_locale": self.lang,
            "has_client_mods": False,
            "client_version": self.client_version,
            "release_channel": self.release_channel,
            "device_vendor_id": self.device_vendor_id,
            "design_id": self.design_id,
            "browser_user_agent": "",
            "browser_version": "",
            "os_version": self.os_version,
            "client_build_number": int(self.client_build_number),
            "client_event_source": None,
            "client_launch_id": str(uuid4()),
        }
        if session_id is not None:
            props["client_heartbeat_session_id"] = session_id

        props["launch_signature"] = str(time.time_ns())
        props["client_app_state"] = "active"
        return props

    def _sentry_release(self) -> str:
        return (
            f"discord_ios@{self.client_version}."
            f"{self.client_build_number}+{self.client_build_number}"
        )

    def _baggage(self, trace_id: str) -> str:
        release = self._sentry_release().replace("@", "%40").replace("+", "%2B")
        return (
            f"sentry-environment={self.release_channel},"
            f"sentry-public_key={self.sentry_public_key},"
            f"sentry-release={release},"
            f"sentry-trace_id={trace_id}"
        )

    @staticmethod
    def _trace_ids() -> tuple[str, str]:
        return f"{random.getrandbits(128):032x}", f"{random.getrandbits(64):016x}"

    def session_headers(self) -> dict:
        return {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": f"{self.lang},en;q=0.9",
            "User-Agent": self.ua,
        }

    def browse_headers(self) -> dict:
        return self.session_headers()

    def api_headers(
        self,
        fingerprint: str,
        *,
        session_id: str | None = None,
        timezone: str = DEFAULT_TIMEZONE,
        referer: (
            str | None
        ) = None,
    ) -> dict:
        trackers, _ = self.trackers(session_id)
        trace_id, span_id = self._trace_ids()
        return {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": f"{self.lang},en;q=0.9",
            "baggage": self._baggage(trace_id),
            "sentry-trace": f"{trace_id}-{span_id}-0",
            "Content-Type": "application/json",
            "User-Agent": self.ua,
            "X-Debug-Options": "bugReporterEnabled",
            "X-Discord-Locale": self.lang,
            "X-Discord-Timezone": timezone,
            "X-Fingerprint": fingerprint,
            "X-Super-Properties": trackers,
        }

    def authed_headers(
        self,
        token: str,
        *,
        session_id: str | None = None,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> dict:
        trackers, _ = self.trackers(session_id)
        trace_id, span_id = self._trace_ids()
        return {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": f"{self.lang},en;q=0.9",
            "Authorization": token,
            "baggage": self._baggage(trace_id),
            "sentry-trace": f"{trace_id}-{span_id}-0",
            "Content-Type": "application/json",
            "User-Agent": self.ua,
            "X-Debug-Options": "bugReporterEnabled",
            "X-Discord-Locale": self.lang,
            "X-Discord-Timezone": timezone,
            "X-Super-Properties": trackers,
        }

class BaseClient:

    name = "base"

    def build(
        self, *, lang: str | None = None, min_similarity: float = 0.85
    ) -> ClientIdentity:
        raise NotImplementedError

    @staticmethod
    def _lang(lang: str | None) -> str:
        return lang or random.choice(["en-US", "en-GB"])


class DiscordElectronClient(BaseClient):

    name = "discord-electron"

    def build(
        self, *, lang: str | None = None, min_similarity: float = 0.85
    ) -> ClientIdentity:
        client_version = f"1.0.{random.randint(9230, 9238)}"
        ua, browser_version, sec_ch_ua = _pick_desktop_runtime(client_version)
        return ClientIdentity(
            name=self.name,
            ua=ua,
            sec_ch_ua=sec_ch_ua,
            sec_ch_ua_platform=_DISCORD_SEC_CH_UA_PLATFORM,
            browser_version=browser_version,
            client_version=client_version,
            release_channel="stable",
            api_domain="discord.com",
            os_version=_DISCORD_OS_VERSION,
            os_sdk_version=_DISCORD_OS_SDK_VERSION,
            client_build_number=int(BUILD_NUM),
            native_build_number=_DISCORD_NATIVE_BUILD_NUMBER,
            lang=self._lang(lang),
            tls_data=generate_similar_ja3_and_h2(min_similarity=min_similarity),
        )


class _DesktopChannelClient(BaseClient):

    config: dict = {}
    _buildinfo: _ChannelBuildInfo | None = None

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.config:
            cls._buildinfo = _ChannelBuildInfo(
                channel=cls.config["release_channel"],
                domain=cls.config["domain"],
                fallback_version=cls.config["fallback_version"],
                fallback_build=cls.config["fallback_build"],
            )

    def build(
        self, *, lang: str | None = None, min_similarity: float = 0.85
    ) -> ClientIdentity:
        cfg = self.config
        assert self._buildinfo is not None
        version_ceiling, build_ceiling = self._buildinfo.get()

        pairs = _electron_pairs()
        lo, hi = cfg["electron_majors"]
        majors = [m for m in range(lo, hi + 1) if m in pairs] or list(pairs)
        chromium_full, electron_full = pairs[random.choice(majors)]
        chromium_major = int(chromium_full.split(".")[0])

        client_version = _older_version(version_ceiling)
        ua = _electron_ua(client_version, chromium_full, electron_full)
        native_lo, native_hi = cfg["native_build_band"]
        return ClientIdentity(
            name=self.name,
            ua=ua,
            sec_ch_ua=_sec_ch_ua(chromium_major),
            sec_ch_ua_platform=_DISCORD_SEC_CH_UA_PLATFORM,
            browser_version=electron_full,
            client_version=client_version,
            release_channel=cfg["release_channel"],
            api_domain=cfg["domain"],
            os_version=_DISCORD_OS_VERSION,
            os_sdk_version=_DISCORD_OS_SDK_VERSION,
            client_build_number=_older_build(build_ceiling),
            native_build_number=random.randint(native_lo, native_hi),
            lang=self._lang(lang),
            tls_data=generate_similar_ja3_and_h2(min_similarity=min_similarity),
        )


class DiscordPtbClient(_DesktopChannelClient):

    name = "discord-ptb"
    config = _CHANNEL_CONFIG["discord-ptb"]


class DiscordCanaryClient(_DesktopChannelClient):

    name = "discord-canary"
    config = _CHANNEL_CONFIG["discord-canary"]


class DiscordIOSClient(BaseClient):

    name = "discord-ios"

    def build(
        self, *, lang: str | None = None, min_similarity: float = 0.85
    ) -> ClientIdentity:
        device = random.choice(_IOS_DEVICES)
        os_version, darwin, cfnetwork = random.choice(_IOS_RUNTIMES)
        client_version, build = _ios_app_version()
        ua = f"Discord/{build} CFNetwork/{cfnetwork} Darwin/{darwin}"
        return IOSClientIdentity(
            name=self.name,
            ua=ua,
            sec_ch_ua="",
            sec_ch_ua_platform='"iOS"',
            browser_version="",
            client_version=client_version,
            release_channel="stable",
            api_domain="discord.com",
            os_version=os_version,
            os_sdk_version="",
            client_build_number=build,
            native_build_number=0,
            lang=self._lang(lang),
            tls_data=generate_similar_ja3_and_h2(
                _IOS_FP, min_similarity=min_similarity
            ),
            device=device,
            device_vendor_id=str(uuid4()).upper(),
            cfnetwork=cfnetwork,
            darwin=darwin,
            sentry_public_key=_IOS_SENTRY_PUBLIC_KEY,
            design_id=_IOS_DESIGN_ID,
        )

CLIENTS: dict[str, type[BaseClient]] = {
    DiscordElectronClient.name: DiscordElectronClient,
    DiscordPtbClient.name: DiscordPtbClient,
    DiscordCanaryClient.name: DiscordCanaryClient,
    DiscordIOSClient.name: DiscordIOSClient,
}

DEFAULT_CLIENT = DiscordElectronClient.name

def make_client(
    name: str = DEFAULT_CLIENT,
    *,
    lang: str | None = None,
    min_similarity: float = 0.85,
) -> ClientIdentity:
    try:
        client_cls = CLIENTS[name]
    except KeyError:
        raise ValueError(
            f"unknown client {name!r}; registered: {sorted(CLIENTS)}"
        ) from None
    return client_cls().build(lang=lang, min_similarity=min_similarity)