from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes


class CredentialError(RuntimeError):
    pass


class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def protect_text(value: str) -> str:
    data = value.encode("utf-8")
    if sys.platform != "win32":
        raise CredentialError("secure credential storage is only available on Windows")
    encrypted = _crypt_protect(data)
    return base64.b64encode(encrypted).decode("ascii")


def unprotect_text(value: str) -> str:
    if sys.platform != "win32":
        raise CredentialError("secure credential storage is only available on Windows")
    try:
        encrypted = base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise CredentialError("bad credential payload") from exc
    return _crypt_unprotect(encrypted).decode("utf-8")


def _make_blob(data: bytes) -> tuple[DATA_BLOB, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    blob = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _crypt_protect(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, _buffer = _make_blob(data)
    out_blob = DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise CredentialError(f"CryptProtectData failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _crypt_unprotect(data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_blob, _buffer = _make_blob(data)
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise CredentialError(f"CryptUnprotectData failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)
