from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
import threading
from typing import Any

from .native_core_client import (
    NativeCoreClient,
    NativeCoreComponentMissingError,
    NativeCoreProtocolError,
    NativeCoreUnavailableError,
    _WceWechatMomentsSyncBeginOptions,
    _WceWechatMomentsSyncCapability,
    _WceWechatMomentsSyncCapabilityOptions,
    _WceWechatMomentsSyncPollResult,
    _WceWechatMomentsSyncRequestOptions,
)


ENV_SNS_NATIVE_LIBRARY = "WECHAT_TOOL_SNS_NATIVE_LIBRARY"
SNS_NATIVE_ABI_VERSION = 1

_singleton_lock = threading.RLock()
_singleton: SnsNativeCompanionClient | None = None
_singleton_path: Path | None = None


def _library_name() -> str:
    if sys.platform == "darwin":
        return "libwechat_sns_native.dylib"
    if sys.platform.startswith("win"):
        return "wechat_sns_native.dll"
    return "libwechat_sns_native.so"


def _source_build_candidate() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "native"
        / "wechat-sns-native"
        / "build"
        / _library_name()
    )


def resolve_sns_native_library() -> Path:
    explicit = str(os.environ.get(ENV_SNS_NATIVE_LIBRARY, "") or "").strip()
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise NativeCoreComponentMissingError(
                f"Configured WeChat SNS native companion is missing: {path}"
            )
        return path

    package_candidate = Path(__file__).resolve().parent / "native" / _library_name()
    candidates = (package_candidate, _source_build_candidate())
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise NativeCoreComponentMissingError(
        "WeChat SNS native companion is not installed. Expected one of: "
        + ", ".join(str(candidate) for candidate in candidates)
    )


class SnsNativeCompanionClient(NativeCoreClient):
    """Small standalone loader for the Moments-only ABI.

    The companion intentionally reuses the validated ctypes methods on
    ``NativeCoreClient`` but does not create or pass a wechatdb-native client
    handle. The C companion owns only its SNS request map and local bridge.
    """

    def __init__(self, library_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._closed = False
        self._path = (
            Path(library_path).resolve()
            if library_path is not None
            else resolve_sns_native_library()
        )
        try:
            self._library = ctypes.CDLL(str(self._path))
        except OSError as exc:
            raise NativeCoreUnavailableError(
                f"Cannot load WeChat SNS native companion: {self._path}"
            ) from exc
        self._configure_companion_abi()
        actual_abi = int(self._library.wcs_wechat_sns_abi_version())
        if actual_abi != SNS_NATIVE_ABI_VERSION:
            raise NativeCoreProtocolError(
                "WeChat SNS native companion ABI mismatch: "
                f"expected {SNS_NATIVE_ABI_VERSION}, got {actual_abi}."
            )
        # NativeCoreClient's Moments methods only require a non-null opaque
        # value. The standalone companion deliberately ignores this pointer.
        self._handle = ctypes.c_void_p(1)
        self._supports_wechat_moments_sync = True
        self._moments_sync_handles: set[int] = set()

    def _configure_companion_abi(self) -> None:
        library = self._library
        required = (
            "wcs_wechat_sns_abi_version",
            "wce_status_message",
            "wce_wechat_moments_sync_get_capability",
            "wce_wechat_moments_sync_begin",
            "wce_wechat_moments_sync_poll",
            "wce_wechat_moments_sync_cancel",
            "wce_wechat_moments_sync_close",
        )
        missing = [name for name in required if not hasattr(library, name)]
        if missing:
            raise NativeCoreProtocolError(
                "WeChat SNS native companion is missing ABI symbols: "
                + ", ".join(missing)
            )
        library.wcs_wechat_sns_abi_version.argtypes = []
        library.wcs_wechat_sns_abi_version.restype = ctypes.c_uint32
        library.wce_status_message.argtypes = [ctypes.c_int32]
        library.wce_status_message.restype = ctypes.c_char_p
        library.wce_wechat_moments_sync_get_capability.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WceWechatMomentsSyncCapabilityOptions),
            ctypes.POINTER(_WceWechatMomentsSyncCapability),
        ]
        library.wce_wechat_moments_sync_get_capability.restype = ctypes.c_int32
        library.wce_wechat_moments_sync_begin.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WceWechatMomentsSyncBeginOptions),
            ctypes.POINTER(ctypes.c_uint64),
        ]
        library.wce_wechat_moments_sync_begin.restype = ctypes.c_int32
        library.wce_wechat_moments_sync_poll.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WceWechatMomentsSyncRequestOptions),
            ctypes.POINTER(_WceWechatMomentsSyncPollResult),
        ]
        library.wce_wechat_moments_sync_poll.restype = ctypes.c_int32
        library.wce_wechat_moments_sync_cancel.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WceWechatMomentsSyncRequestOptions),
        ]
        library.wce_wechat_moments_sync_cancel.restype = ctypes.c_int32
        library.wce_wechat_moments_sync_close.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_WceWechatMomentsSyncRequestOptions),
        ]
        library.wce_wechat_moments_sync_close.restype = ctypes.c_int32

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            for request_handle in tuple(self._moments_sync_handles):
                try:
                    self.close_wechat_moments_sync(request_handle)
                except Exception:
                    pass
            self._moments_sync_handles.clear()
            self._closed = True
            self._handle = ctypes.c_void_p()

    def __enter__(self) -> SnsNativeCompanionClient:
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()


def get_sns_native_companion_client() -> SnsNativeCompanionClient:
    global _singleton, _singleton_path
    path = resolve_sns_native_library()
    with _singleton_lock:
        if (
            _singleton is None
            or _singleton._closed
            or _singleton_path is None
            or _singleton_path != path
        ):
            if _singleton is not None:
                _singleton.close()
            _singleton = SnsNativeCompanionClient(path)
            _singleton_path = path
        return _singleton


def close_sns_native_companion_client() -> None:
    global _singleton, _singleton_path
    with _singleton_lock:
        if _singleton is not None:
            _singleton.close()
        _singleton = None
        _singleton_path = None


__all__ = [
    "ENV_SNS_NATIVE_LIBRARY",
    "SNS_NATIVE_ABI_VERSION",
    "SnsNativeCompanionClient",
    "close_sns_native_companion_client",
    "get_sns_native_companion_client",
    "resolve_sns_native_library",
]
