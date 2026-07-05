from __future__ import annotations

import base64
import concurrent.futures
import ctypes
import hashlib
import json
import os
import platform
import re
import socket
import subprocess
import urllib.request
from ctypes import wintypes
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path


APP_ID = "summer-camp-planner"
APP_DATA_NAME = "SummerCampPlanner"
LICENSE_FILE_NAME = "license.dat"
LEGACY_LICENSE_FILE_NAME = "license.json"
USED_KEYS_FILE_NAME = "activation_registry.json"
ACTIVATION_WINDOW_DAYS = 1
PUBLIC_KEY_HEX = "f1647f4977fc1d0acdfbae1c53182047be1ead3ff12290cd6e2c2c54b5bbd045"
CHINA_TZ = timezone(timedelta(hours=8))
P = 2**255 - 19
Q = 2**252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, P - 2, P)) % P
I = pow(2, (P - 1) // 4, P)
NTP_SERVERS_CN = [
    "ntp.aliyun.com",
    "ntp1.aliyun.com",
    "ntp2.aliyun.com",
    "ntp3.aliyun.com",
    "ntp.tencent.com",
    "ntp.ntsc.ac.cn",
    "time.edu.cn",
    "cn.pool.ntp.org",
]
NTP_SERVERS_GLOBAL = [
    "time.windows.com",
    "time.cloudflare.com",
    "time.google.com",
    "pool.ntp.org",
]
TIME_URLS_CN = [
    "http://connectivitycheck.platform.hicloud.com/generate_204",
    "http://connect.rom.miui.com/generate_204",
    "http://www.baidu.com",
    "http://www.qq.com",
    "http://www.163.com",
    "http://www.taobao.com",
    "http://www.aliyun.com",
    "https://www.baidu.com",
    "https://www.qq.com",
]
TIME_URLS_GLOBAL = [
    "http://www.msftconnecttest.com/connecttest.txt",
    "http://www.gstatic.com/generate_204",
    "http://www.microsoft.com",
    "http://www.cloudflare.com",
    "https://www.microsoft.com",
]
TIME_API_URLS = [
    "https://api.m.taobao.com/rest/api3.do?api=mtop.common.getTimestamp",
    "http://api.m.taobao.com/rest/api3.do?api=mtop.common.getTimestamp",
    "https://acs.m.taobao.com/gw/mtop.common.getTimestamp/",
    "http://acs.m.taobao.com/gw/mtop.common.getTimestamp/",
    "https://f.m.suning.com/api/ct.do",
    "https://quan.suning.com/getSysTime.do",
    "http://quan.suning.com/getSysTime.do",
]


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def app_data_dir() -> Path:
    if sys_platform() == "darwin":
        base = Path.home() / "Library" / "Application Support"
        path = base / APP_DATA_NAME
        path.mkdir(parents=True, exist_ok=True)
        return path
    base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    path = base / APP_DATA_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def sys_platform() -> str:
    import sys

    return sys.platform


def license_path() -> Path:
    return app_data_dir() / LICENSE_FILE_NAME


def legacy_license_path() -> Path:
    return app_data_dir() / LEGACY_LICENSE_FILE_NAME


def used_keys_path() -> Path:
    return app_data_dir() / USED_KEYS_FILE_NAME


def license_diagnostics_path() -> Path:
    return app_data_dir() / "license_diagnostics.log"


def write_license_diagnostic(message: str) -> None:
    try:
        line = f"[{datetime.now(CHINA_TZ).isoformat(timespec='seconds')}] {message}\n"
        license_diagnostics_path().open("a", encoding="utf-8").write(line)
    except Exception:
        pass


def current_install_dir() -> Path:
    import sys

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def normalize_install_dir(install_dir: str | Path | None = None) -> str:
    if sys_platform() == "darwin":
        try:
            return str(app_data_dir().resolve(strict=False)).rstrip("\\/")
        except OSError:
            return str(app_data_dir().absolute()).rstrip("\\/")
    path = Path(install_dir).expanduser() if install_dir is not None else current_install_dir()
    try:
        path = path.resolve(strict=False)
    except OSError:
        path = path.absolute()
    return str(path).rstrip("\\/").casefold()


def _decode_part(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def decode_key_payload(key: str) -> dict:
    parts = key.strip().split(".")
    if len(parts) != 3:
        raise ValueError("密钥格式不正确")
    payload_raw = _decode_part(parts[1])
    payload = json.loads(payload_raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("密钥内容不正确")
    return payload


def _recover_x(y: int, sign: int) -> int | None:
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P)
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = (x * I) % P
    if (x * x - x2) % P != 0:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


def _point_decode(data: bytes) -> tuple[int, int] | None:
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little") & ((1 << 255) - 1)
    sign = data[31] >> 7
    x = _recover_x(y, sign)
    if x is None:
        return None
    return x, y


def _point_add(p1: tuple[int, int], p2: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = p1
    x2, y2 = p2
    den = pow(1 + D * x1 * x2 * y1 * y2, P - 2, P)
    x3 = (x1 * y2 + x2 * y1) * den % P
    den = pow(1 - D * x1 * x2 * y1 * y2, P - 2, P)
    y3 = (y1 * y2 + x1 * x2) * den % P
    return x3, y3


def _point_mul(scalar: int, point: tuple[int, int]) -> tuple[int, int]:
    result = (0, 1)
    addend = point
    while scalar:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        scalar >>= 1
    return result


def _point_encode(point: tuple[int, int]) -> bytes:
    x, y = point
    data = bytearray(y.to_bytes(32, "little"))
    data[31] |= (x & 1) << 7
    return bytes(data)


def _verify_signature(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(signature) != 64:
        return False
    a = _point_decode(public_key)
    r = _point_decode(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= Q:
        return False
    base = (
        15112221349535400772501151409588531511454012693041857206046113283949847762202,
        46316835694926478169428394003475163141307993866256225615783033603165251855960,
    )
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % Q
    left = _point_mul(s, base)
    right = _point_add(r, _point_mul(h, a))
    return _point_encode(left) == _point_encode(right)


def _fetch_ntp_datetime(server: str, timeout: float = 3.0) -> datetime:
    packet = b"\x1b" + 47 * b"\0"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (server, 123))
        data, _ = sock.recvfrom(48)
    if len(data) < 48:
        raise RuntimeError("NTP 响应过短")
    seconds = int.from_bytes(data[40:44], "big")
    fraction = int.from_bytes(data[44:48], "big")
    timestamp = seconds - 2208988800 + fraction / 2**32
    if timestamp < 946684800:
        raise RuntimeError("NTP 时间异常")
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _fetch_http_datetime(url: str, timeout: float = 4.0) -> datetime:
    headers = {
        "User-Agent": "SummerCampPlanner/1.0",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    last_error: Exception | None = None
    for method in ("HEAD", "GET"):
        try:
            request = urllib.request.Request(url, method=method, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                header = response.headers.get("Date")
            if not header:
                raise RuntimeError("响应没有 Date 头")
            dt = parsedate_to_datetime(header)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error or "无法读取 Date 头"))


def _fetch_time_api_datetime(url: str, timeout: float = 4.0) -> datetime:
    request = urllib.request.Request(url, headers={"User-Agent": "SummerCampPlanner/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(4096)
    text = raw.decode("utf-8", errors="ignore")

    def parse_candidate(candidate: object) -> datetime | None:
        clean = str(candidate or "").strip()
        if not clean:
            return None
        match = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?[ T](\d{1,2}):(\d{1,2}):(\d{1,2})", clean)
        if match:
            y, m, d, hh, mm, ss = [int(part) for part in match.groups()]
            return datetime(y, m, d, hh, mm, ss, tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
        for digits in re.findall(r"\d{13,}", clean):
            timestamp = int(digits[:13]) / 1000
            if timestamp >= 946684800:
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        for digits in re.findall(r"(?<!\d)\d{10}(?!\d)", clean):
            timestamp = int(digits)
            if timestamp >= 946684800:
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        digits = re.sub(r"\D", "", clean)
        if len(digits) >= 14 and digits.startswith("20"):
            return datetime(
                int(digits[:4]),
                int(digits[4:6]),
                int(digits[6:8]),
                int(digits[8:10]),
                int(digits[10:12]),
                int(digits[12:14]),
                tzinfo=timezone(timedelta(hours=8)),
            ).astimezone(timezone.utc)
        return None

    parsed = parse_candidate(text)
    if parsed is not None:
        return parsed

    json_text = text.strip()
    if not json_text.startswith(("{", "[")):
        match = re.search(r"[\w.$]+\((.*)\)\s*;?\s*$", json_text, re.S)
        if match:
            json_text = match.group(1).strip()
    data = json.loads(json_text)
    candidates: list[object] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, (str, int, float)):
            candidates.append(value)

    walk(data)
    for candidate in candidates:
        parsed = parse_candidate(candidate)
        if parsed is not None:
            return parsed
    raise RuntimeError("时间 API 没有返回可解析时间")


def fetch_network_datetime(timeout: int = 6) -> datetime:
    errors: list[str] = []
    ntp_timeout = max(1.5, min(float(timeout), 3.0))
    http_timeout = max(2.0, min(float(timeout), 5.0))

    def collect(tasks: list[tuple[str, str]]) -> datetime | None:
        workers = min(32, max(1, len(tasks)))
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        future_map = {}
        try:
            for kind, target in tasks:
                if kind == "ntp":
                    future = executor.submit(_fetch_ntp_datetime, target, ntp_timeout)
                    label = f"NTP {target}"
                elif kind == "http":
                    future = executor.submit(_fetch_http_datetime, target, http_timeout)
                    label = f"HTTP {target}"
                else:
                    future = executor.submit(_fetch_time_api_datetime, target, http_timeout)
                    label = f"API {target}"
                future_map[future] = label
            deadline = max(float(timeout), ntp_timeout, http_timeout) + 1.0
            try:
                for future in concurrent.futures.as_completed(future_map, timeout=deadline):
                    label = future_map[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        errors.append(f"{label}: {exc}")
                    else:
                        for pending in future_map:
                            if pending is not future:
                                pending.cancel()
                        return result
            except concurrent.futures.TimeoutError:
                errors.append("部分授时源响应超时")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        return None

    primary = (
        [("api", url) for url in TIME_API_URLS]
        + [("http", url) for url in TIME_URLS_CN]
        + [("ntp", server) for server in NTP_SERVERS_CN]
    )
    result = collect(primary)
    if result is not None:
        return result

    fallback = [("http", url) for url in TIME_URLS_GLOBAL] + [("ntp", server) for server in NTP_SERVERS_GLOBAL]
    result = collect(fallback)
    if result is not None:
        return result

    raise RuntimeError("无法联网校对时间，请检查网络后重试。\n\n" + "\n".join(errors[-3:]))


def fetch_network_date(timeout: int = 6) -> date:
    return fetch_network_datetime(timeout=timeout).astimezone(CHINA_TZ).date()


def _parse_date(value: object, field_name: str) -> date:
    text = str(value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{field_name} 日期不正确") from exc


def _parse_issued_at(payload: dict) -> datetime:
    issued_at = str(payload.get("issued_at") or "").strip()
    if issued_at:
        text = issued_at.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("密钥签发时间不正确") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CHINA_TZ)
        return dt.astimezone(CHINA_TZ)
    issued_day = _parse_date(payload.get("issued"), "密钥签发")
    return datetime.combine(issued_day, time.min, tzinfo=CHINA_TZ)


def _parse_iso_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CHINA_TZ)
    return dt.astimezone(CHINA_TZ)


def _read_key_parts(key: str) -> tuple[dict, bytes, bytes]:
    key = key.strip()
    if not key.startswith("SCP."):
        raise ValueError("密钥格式不正确")
    parts = key.split(".")
    if len(parts) != 3:
        raise ValueError("密钥格式不正确")
    try:
        payload_raw = _decode_part(parts[1])
        signature = _decode_part(parts[2])
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("密钥内容无法读取") from exc
    if not isinstance(payload, dict):
        raise ValueError("密钥内容不正确")
    return payload, payload_raw, signature


def _verify_key_payload(payload: dict, payload_raw: bytes, signature: bytes) -> None:
    if payload.get("app") != APP_ID:
        raise ValueError("密钥不属于本软件")
    _parse_date(payload.get("expires"), "密钥到期")
    _parse_issued_at(payload)
    public_key = bytes.fromhex(PUBLIC_KEY_HEX)
    if not _verify_signature(public_key, payload_raw, signature):
        raise ValueError("密钥签名无效")


def _key_payload(key: str) -> dict:
    payload, payload_raw, signature = _read_key_parts(key)
    _verify_key_payload(payload, payload_raw, signature)
    return payload


def _validate_key_with_now(key: str, now: datetime, *, for_activation: bool) -> tuple[dict, str]:
    payload = _key_payload(key)
    now_cn = now.astimezone(CHINA_TZ)
    expire_day = _parse_date(payload.get("expires"), "密钥到期")
    if expire_day < now_cn.date():
        raise ValueError(f"密钥已过期：有效期至 {expire_day.isoformat()}，联网日期 {now_cn.date().isoformat()}")
    if for_activation:
        issued_at = _parse_issued_at(payload)
        if now_cn < issued_at - timedelta(minutes=10):
            raise ValueError("密钥签发时间晚于联网时间，请检查密钥是否正确")
        activation_expires = str(payload.get("activation_expires") or "").strip()
        if activation_expires:
            deadline_day = _parse_date(activation_expires, "首次激活到期")
        else:
            deadline_day = issued_at.date() + timedelta(days=ACTIVATION_WINDOW_DAYS)
        deadline = datetime.combine(deadline_day, time.max, tzinfo=CHINA_TZ)
        if now_cn > deadline:
            raise ValueError(
                "密钥已超过首次激活时间："
                f"签发于 {issued_at.strftime('%Y-%m-%d %H:%M')}，"
                f"需在 {deadline_day.isoformat()} 24:00 前首次激活"
            )
    name = str(payload.get("name") or "用户")
    return payload, f"{name}，有效期至 {expire_day.isoformat()}"


def validate_key(key: str, check_time: bool = True) -> tuple[bool, str]:
    try:
        del check_time
        now = fetch_network_datetime()
        _, message = _validate_key_with_now(key, now, for_activation=False)
        return True, message
    except Exception as exc:
        return False, str(exc)


def validate_key_for_activation(key: str, check_time: bool = True) -> tuple[bool, str]:
    try:
        del check_time
        now = fetch_network_datetime()
        _, message = _validate_key_with_now(key, now, for_activation=True)
        return True, message
    except Exception as exc:
        return False, str(exc)


def validate_key_for_install(
    key: str,
    install_dir: str | Path | None = None,
    check_time: bool = True,
    network_now: datetime | None = None,
) -> tuple[bool, str]:
    key = key.strip()
    if not key:
        return False, "请先填写安装密钥"
    try:
        del check_time
        now = network_now or fetch_network_datetime()
        current_key = load_license_key(install_dir)
        if current_key == key:
            _, message = _validate_key_with_now(key, now, for_activation=False)
            return True, f"本机已激活，可继续安装：{message}"
        _, message = _validate_key_with_now(key, now, for_activation=True)
        return True, message
    except Exception as exc:
        return False, str(exc)


def _blob_from_bytes(data: bytes) -> tuple[DATA_BLOB, ctypes.Array[ctypes.c_char] | None]:
    if not data:
        return DATA_BLOB(0, None), None
    buffer = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _blob_to_bytes(blob: DATA_BLOB) -> bytes:
    try:
        if not blob.pbData:
            return b""
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        if blob.pbData:
            ctypes.windll.kernel32.LocalFree(blob.pbData)


def _activation_entropy(install_dir: str | Path | None = None) -> bytes:
    del install_dir
    return hashlib.sha256(f"{APP_ID}|windows-user-license-v3".encode("utf-8")).digest()


def _legacy_activation_entropy(install_dir: str | Path | None = None) -> bytes:
    normalized = normalize_install_dir(install_dir)
    return hashlib.sha256(f"{APP_ID}|{normalized}".encode("utf-8")).digest()


def _activation_entropy_candidates(install_dir: str | Path | None = None) -> list[bytes]:
    candidates: list[bytes] = [_activation_entropy(install_dir)]
    legacy_dirs: list[str | Path | None] = [install_dir, current_install_dir()]
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        legacy_dirs.append(Path(local_app) / "SummerCampPlanner")
    seen: set[bytes] = set()
    for legacy_dir in legacy_dirs:
        try:
            entropy = _legacy_activation_entropy(legacy_dir)
        except Exception:
            continue
        if entropy not in seen:
            seen.add(entropy)
            candidates.append(entropy)
    return candidates


def protect_bytes(data: bytes, entropy: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("授权加密需要 Windows DPAPI")
    in_blob, in_buffer = _blob_from_bytes(data)
    entropy_blob, entropy_buffer = _blob_from_bytes(entropy)
    out_blob = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "SummerCampPlanner license",
        ctypes.byref(entropy_blob),
        None,
        None,
        0x01,
        ctypes.byref(out_blob),
    )
    _ = (in_buffer, entropy_buffer)
    if not ok:
        raise ctypes.WinError()
    return _blob_to_bytes(out_blob)


def unprotect_bytes(data: bytes, entropy: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("授权解密需要 Windows DPAPI")
    in_blob, in_buffer = _blob_from_bytes(data)
    entropy_blob, entropy_buffer = _blob_from_bytes(entropy)
    out_blob = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        0x01,
        ctypes.byref(out_blob),
    )
    _ = (in_buffer, entropy_buffer)
    if not ok:
        raise ctypes.WinError()
    return _blob_to_bytes(out_blob)


def _activation_hash(key: str) -> str:
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()


def _stable_user_hash() -> str:
    if sys_platform() == "darwin":
        parts = [
            os.environ.get("USER") or "",
            str(Path.home()),
            platform.node(),
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8", errors="ignore")).hexdigest()
    parts = [
        os.environ.get("USERDOMAIN") or "",
        os.environ.get("USERNAME") or "",
        os.environ.get("COMPUTERNAME") or platform.node(),
    ]
    return hashlib.sha256("|".join(parts).casefold().encode("utf-8", errors="ignore")).hexdigest()


def _load_used_keys() -> dict:
    path = used_keys_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _remember_used_key(key: str, payload: dict) -> None:
    data = _load_used_keys()
    data[_activation_hash(key)] = {
        "name": str(payload.get("name") or "用户"),
        "expires": str(payload.get("expires") or ""),
        "activated_at": datetime.now().isoformat(timespec="seconds"),
    }
    used_keys_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_key_used(key: str) -> bool:
    return _activation_hash(key) in _load_used_keys()


def _keychain_account(install_dir: str | Path | None = None) -> str:
    digest = hashlib.sha256(normalize_install_dir(install_dir).encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{APP_ID}:{digest}"


def _read_keychain_activation(install_dir: str | Path | None = None) -> dict:
    cmd = [
        "security",
        "find-generic-password",
        "-s",
        APP_ID,
        "-a",
        _keychain_account(install_dir),
        "-w",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("未找到 macOS Keychain 授权记录")
    try:
        data = json.loads(result.stdout.strip())
    except Exception as exc:
        raise RuntimeError("macOS Keychain 授权内容无法读取") from exc
    if not isinstance(data, dict):
        raise RuntimeError("macOS Keychain 授权内容不正确")
    return data


def _write_keychain_activation(data: dict, install_dir: str | Path | None = None) -> None:
    account = _keychain_account(install_dir)
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    delete_cmd = ["security", "delete-generic-password", "-s", APP_ID, "-a", account]
    subprocess.run(delete_cmd, capture_output=True, text=True, check=False)
    add_cmd = [
        "security",
        "add-generic-password",
        "-s",
        APP_ID,
        "-a",
        account,
        "-w",
        raw,
        "-U",
    ]
    result = subprocess.run(add_cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("写入 macOS Keychain 授权失败：" + (result.stderr.strip() or result.stdout.strip()))


def _read_activation(install_dir: str | Path | None = None) -> dict:
    if sys_platform() == "darwin":
        return _read_keychain_activation(install_dir)
    path = license_path()
    if not path.exists():
        legacy = legacy_license_path()
        if legacy.exists():
            raise RuntimeError("检测到旧版授权文件，请重新运行新版安装器激活")
        raise RuntimeError(f"未找到授权文件：{path}")
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        protected = _decode_part(str(wrapper.get("protected") or ""))
    except Exception as exc:
        raise RuntimeError("授权文件内容无法读取") from exc
    errors: list[str] = []
    for entropy in _activation_entropy_candidates(install_dir):
        try:
            raw = unprotect_bytes(protected, entropy)
            data = json.loads(raw.decode("utf-8"))
            break
        except Exception as exc:
            errors.append(str(exc))
    else:
        raise RuntimeError("授权文件无法读取，可能不是在本机或当前安装目录激活的：" + "；".join(errors[-2:]))
    if not isinstance(data, dict):
        raise RuntimeError("授权文件内容不正确")
    return data


def _write_activation(key: str, payload: dict, install_dir: str | Path | None = None, network_now: datetime | None = None) -> None:
    normalized_install_dir = normalize_install_dir(install_dir)
    network_now = network_now or fetch_network_datetime()
    data = {
        "version": 2,
        "app": APP_ID,
        "key": key.strip(),
        "key_hash": _activation_hash(key),
        "payload": payload,
        "install_dir": normalized_install_dir,
        "user_hash": _stable_user_hash(),
        "windows_user": os.environ.get("USERNAME") or "",
        "computer_name": platform.node(),
        "activated_at_network": network_now.astimezone(CHINA_TZ).isoformat(),
        "activated_at_local": datetime.now(CHINA_TZ).isoformat(timespec="seconds"),
    }
    if sys_platform() == "darwin":
        data["mac_user"] = os.environ.get("USER") or ""
        _write_keychain_activation(data, install_dir)
        legacy_license_path().unlink(missing_ok=True)
        license_path().unlink(missing_ok=True)
        return
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    protected = protect_bytes(raw, _activation_entropy(install_dir))
    wrapper = {
        "version": 2,
        "app": APP_ID,
        "protected": base64.urlsafe_b64encode(protected).decode("ascii").rstrip("="),
    }
    license_path().write_text(json.dumps(wrapper, ensure_ascii=False, indent=2), encoding="utf-8")
    legacy_license_path().unlink(missing_ok=True)


def load_license_key(install_dir: str | Path | None = None) -> str:
    try:
        data = _read_activation(install_dir)
        return str(data.get("key") or "").strip()
    except Exception:
        return ""


def save_license(key: str, install_dir: str | Path | None = None) -> None:
    ok, message = activate_license(key, install_dir=install_dir, check_time=True)
    if not ok:
        raise RuntimeError(message)


def activate_license(
    key: str,
    install_dir: str | Path | None = None,
    check_time: bool = True,
    network_now: datetime | None = None,
) -> tuple[bool, str]:
    key = key.strip()
    if not key:
        return False, "请先填写安装密钥"
    try:
        current_key = load_license_key(install_dir)
        del check_time
        now = network_now or fetch_network_datetime()
        if current_key == key:
            payload, message = _validate_key_with_now(key, now, for_activation=False)
            _write_activation(key, payload, install_dir=install_dir, network_now=now)
            _remember_used_key(key, payload)
            return True, f"本机已激活，已更新安装授权：{message}"
        if _is_key_used(key) and not license_path().exists():
            return False, "这个安装密钥已经在本电脑使用过，不能重复激活。请联系作者获取新的安装密钥。"

        payload, message = _validate_key_with_now(key, now, for_activation=True)
        _write_activation(key, payload, install_dir=install_dir, network_now=now)
        _remember_used_key(key, payload)
        return True, f"激活成功：{message}"
    except Exception as exc:
        return False, str(exc)


def validate_saved_license(
    install_dir: str | Path | None = None,
    check_time: bool = True,
    network_now: datetime | None = None,
) -> tuple[bool, str]:
    try:
        del check_time

        def fail(message: str) -> tuple[bool, str]:
            write_license_diagnostic(f"validate_saved_license failed: {message}")
            return False, message

        data = _read_activation(install_dir)
        if data.get("app") != APP_ID:
            return fail("授权文件不属于本软件")
        expected_install_dir = normalize_install_dir(install_dir)
        if str(data.get("install_dir") or "") != expected_install_dir:
            return fail("授权文件绑定的安装目录与当前目录不一致")
        saved_user_hash = str(data.get("user_hash") or "")
        if saved_user_hash and saved_user_hash != _stable_user_hash():
            return fail("授权文件绑定的 Windows 用户与当前用户不一致")
        key = str(data.get("key") or "").strip()
        if network_now is not None:
            now = network_now
        else:
            try:
                now = fetch_network_datetime()
            except Exception as exc:
                write_license_diagnostic(f"network time failed: {exc}")
                return False, "无法联网同步时间，请联网后重新打开"
        _, message = _validate_key_with_now(key, now, for_activation=False)
        return True, message
    except Exception as exc:
        write_license_diagnostic(f"validate_saved_license failed: {exc}")
        return False, str(exc)
