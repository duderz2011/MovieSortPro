"""Local UI preferences and optional Windows DPAPI-protected API credentials.

Secrets are never saved in plaintext. On non-Windows systems they are not saved.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _crypt(raw: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise OSError("Windows DPAPI not available")
    # DPAPI binds the encrypted bytes to the Windows user profile.
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_wchar_p if protect else ctypes.POINTER(ctypes.c_wchar_p),
                   ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                   ctypes.POINTER(_Blob)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    source = ctypes.create_string_buffer(raw)
    in_blob = _Blob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    out_blob = _Blob()
    if not fn(ctypes.byref(in_blob), None, None, None, None, 0x01, ctypes.byref(out_blob)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def seal(value: str) -> str:
    if not value or os.name != "nt":
        return ""
    try:
        return base64.b64encode(_crypt(value.encode("utf-8"), True)).decode("ascii")
    except (OSError, ValueError):
        return ""


def unseal(value: str) -> str:
    if not value or os.name != "nt":
        return ""
    try:
        return _crypt(base64.b64decode(value, validate=True), False).decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        return ""


def read_preferences(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def write_preferences(path: Path, prefs: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    pending.replace(path)
