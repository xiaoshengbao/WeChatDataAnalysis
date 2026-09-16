from __future__ import annotations

import ctypes
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
import threading

import pytest

from wechat_decrypt_tool import native_core_client as native
from wechat_decrypt_tool.native_core_client import (
    NativeCoreClient,
    NativeCoreMomentsCompletionReason,
    NativeCoreMomentsReason,
    NativeCoreMomentsRequestState,
    NativeCoreProtocolError,
    NativeCoreStatus,
)


class _FakeFunction:
    def __init__(self, callback: Callable[..., Any]) -> None:
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args: Any) -> Any:
        return self.callback(*args)


def _dereference(pointer: Any, value_type: type[ctypes.Structure]) -> Any:
    return ctypes.cast(pointer, ctypes.POINTER(value_type)).contents


def _write_fixed(
    value: ctypes.Structure,
    field_name: str,
    field_size: int,
    payload: bytes,
) -> None:
    assert len(payload) <= field_size
    field = getattr(type(value), field_name)
    ctypes.memmove(ctypes.addressof(value) + field.offset, payload, len(payload))


class _MomentsStub:
    def __init__(self) -> None:
        self.capability_calls: list[tuple[bytes, bytes, int]] = []
        self.begin_calls: list[tuple[bytes, bytes, bytes, bytes, int, int, int]] = []
        self.poll_calls: list[int] = []
        self.cancel_calls: list[int] = []
        self.close_calls: list[int] = []
        self.library = SimpleNamespace(
            wce_wechat_moments_sync_get_capability=_FakeFunction(self._capability),
            wce_wechat_moments_sync_begin=_FakeFunction(self._begin),
            wce_wechat_moments_sync_poll=_FakeFunction(self._poll),
            wce_wechat_moments_sync_cancel=_FakeFunction(self._cancel),
            wce_wechat_moments_sync_close=_FakeFunction(self._close),
            wce_status_message=_FakeFunction(lambda _status: b"status"),
        )

    def _capability(
        self, _client: Any, options_pointer: Any, result_pointer: Any
    ) -> int:
        options = _dereference(
            options_pointer, native._WceWechatMomentsSyncCapabilityOptions
        )
        result = _dereference(
            result_pointer, native._WceWechatMomentsSyncCapability
        )
        self.capability_calls.append(
            (
                bytes(options.account_utf8),
                bytes(options.account_directory_utf8),
                int(options.flags),
            )
        )
        result.reason = int(NativeCoreMomentsReason.READY)
        result.platform_supported = 1
        result.ready = 1
        result.version_verified = 1
        result.wechat_process_id = 4321
        _write_fixed(result, "actual_wechat_version", 32, b"4.1.12.26\0")
        _write_fixed(result, "adapter_id", 64, b"win-4.1.12.26-x64\0")
        return int(NativeCoreStatus.OK)

    def _begin(
        self, _client: Any, options_pointer: Any, output_pointer: Any
    ) -> int:
        options = _dereference(
            options_pointer, native._WceWechatMomentsSyncBeginOptions
        )
        self.begin_calls.append(
            (
                bytes(options.account_utf8),
                bytes(options.account_directory_utf8),
                bytes(options.target_username_utf8),
                bytes(options.resume_cursor_utf8 or b""),
                int(options.flags),
                int(options.scene),
                int(options.page_size),
            )
        )
        ctypes.cast(output_pointer, ctypes.POINTER(ctypes.c_uint64))[0] = 91
        return int(NativeCoreStatus.OK)

    def _poll(
        self, _client: Any, options_pointer: Any, result_pointer: Any
    ) -> int:
        options = _dereference(
            options_pointer, native._WceWechatMomentsSyncRequestOptions
        )
        result = _dereference(result_pointer, native._WceWechatMomentsSyncPollResult)
        self.poll_calls.append(int(options.request_handle))
        result.state = int(NativeCoreMomentsRequestState.SUCCEEDED)
        result.reason = int(NativeCoreMomentsReason.READY)
        result.terminal_status = int(NativeCoreStatus.OK)
        result.completion_reason = int(NativeCoreMomentsCompletionReason.SOURCE_END)
        result.source_complete = 1
        result.version_verified = 1
        result.has_more = 0
        result.pages_fetched = 4
        result.posts_observed = 87
        result.rows_written = 87
        result.oldest_tid = 123
        return int(NativeCoreStatus.OK)

    def _cancel(self, _client: Any, options_pointer: Any) -> int:
        options = _dereference(
            options_pointer, native._WceWechatMomentsSyncRequestOptions
        )
        self.cancel_calls.append(int(options.request_handle))
        return int(NativeCoreStatus.OK)

    def _close(self, _client: Any, options_pointer: Any) -> int:
        options = _dereference(
            options_pointer, native._WceWechatMomentsSyncRequestOptions
        )
        self.close_calls.append(int(options.request_handle))
        return int(NativeCoreStatus.OK)


def _client(stub: _MomentsStub, *, supported: bool = True) -> NativeCoreClient:
    client = object.__new__(NativeCoreClient)
    client._supports_wechat_moments_sync = supported
    client._library = stub.library
    client._lock = threading.RLock()
    client._closed = False
    client._handle = ctypes.c_void_p(0x1234)
    client._moments_sync_handles = set()
    return client


def test_moments_sync_ctypes_layout_is_stable() -> None:
    assert ctypes.sizeof(native._WceWechatMomentsSyncCapabilityOptions) == 32
    assert ctypes.sizeof(native._WceWechatMomentsSyncCapability) == 120
    assert ctypes.sizeof(native._WceWechatMomentsSyncBeginOptions) == 56
    assert ctypes.sizeof(native._WceWechatMomentsSyncRequestOptions) == 24
    assert ctypes.sizeof(native._WceWechatMomentsSyncPollResult) == 320


def test_moments_sync_capability_begin_poll_cancel_and_close(tmp_path: Path) -> None:
    stub = _MomentsStub()
    client = _client(stub)
    account_dir = tmp_path.resolve()

    capability = client.get_wechat_moments_sync_capability(
        "wxid_owner", account_dir
    )
    handle = client.begin_wechat_moments_sync(
        "wxid_owner",
        account_dir,
        "wxid_friend",
        resume_cursor="opaque-page-3",
        scene=7,
        page_size=20,
    )
    result = client.poll_wechat_moments_sync(handle)
    client.cancel_wechat_moments_sync(handle)
    client.close_wechat_moments_sync(handle)

    assert capability.ready is True
    assert capability.reason is NativeCoreMomentsReason.READY
    assert capability.actual_wechat_version == "4.1.12.26"
    assert capability.adapter_id == "win-4.1.12.26-x64"
    assert handle == 91
    assert result.state is NativeCoreMomentsRequestState.SUCCEEDED
    assert result.completion_reason is NativeCoreMomentsCompletionReason.SOURCE_END
    assert result.source_complete is True
    assert result.has_more is False
    assert result.next_cursor == ""
    assert result.pages_fetched == 4
    assert result.posts_observed == 87
    assert result.rows_written == 87
    assert stub.capability_calls == [(b"wxid_owner", str(account_dir).encode(), 1)]
    assert stub.begin_calls == [
        (
            b"wxid_owner",
            str(account_dir).encode(),
            b"wxid_friend",
            b"opaque-page-3",
            3,
            7,
            20,
        )
    ]
    assert stub.poll_calls == [91]
    assert stub.cancel_calls == [91]
    assert stub.close_calls == [91]
    assert client._moments_sync_handles == set()


def test_moments_sync_capability_reports_missing_optional_runtime(tmp_path: Path) -> None:
    client = _client(_MomentsStub(), supported=False)

    capability = client.get_wechat_moments_sync_capability(
        "wxid_owner", tmp_path.resolve()
    )

    assert capability.ready is False
    assert capability.reason is NativeCoreMomentsReason.RUNTIME_UNAVAILABLE


def test_moments_sync_rejects_noncanonical_fixed_utf8(tmp_path: Path) -> None:
    stub = _MomentsStub()
    original = stub._capability

    def malformed(_client: Any, options_pointer: Any, result_pointer: Any) -> int:
        rc = original(_client, options_pointer, result_pointer)
        result = _dereference(
            result_pointer, native._WceWechatMomentsSyncCapability
        )
        _write_fixed(result, "adapter_id", 64, b"adapter\0garbage")
        return rc

    stub.library.wce_wechat_moments_sync_get_capability = _FakeFunction(malformed)

    with pytest.raises(NativeCoreProtocolError, match="adapter_id"):
        _client(stub).get_wechat_moments_sync_capability(
            "wxid_owner", tmp_path.resolve()
        )


def test_moments_sync_rejects_success_without_source_end(tmp_path: Path) -> None:
    stub = _MomentsStub()
    original = stub._poll

    def malformed(_client: Any, options_pointer: Any, result_pointer: Any) -> int:
        rc = original(_client, options_pointer, result_pointer)
        result = _dereference(result_pointer, native._WceWechatMomentsSyncPollResult)
        result.source_complete = 0
        return rc

    stub.library.wce_wechat_moments_sync_poll = _FakeFunction(malformed)
    client = _client(stub)
    handle = client.begin_wechat_moments_sync(
        "wxid_owner", tmp_path.resolve(), "wxid_friend"
    )

    with pytest.raises(NativeCoreProtocolError, match="source-end marker"):
        client.poll_wechat_moments_sync(handle)


def test_moments_sync_returns_opaque_next_cursor_for_resume(tmp_path: Path) -> None:
    stub = _MomentsStub()

    def running(_client: Any, options_pointer: Any, result_pointer: Any) -> int:
        options = _dereference(
            options_pointer, native._WceWechatMomentsSyncRequestOptions
        )
        stub.poll_calls.append(int(options.request_handle))
        result = _dereference(result_pointer, native._WceWechatMomentsSyncPollResult)
        result.state = int(NativeCoreMomentsRequestState.RUNNING)
        result.reason = int(NativeCoreMomentsReason.READY)
        result.terminal_status = int(NativeCoreStatus.OK)
        result.completion_reason = int(NativeCoreMomentsCompletionReason.NONE)
        result.source_complete = 0
        result.version_verified = 1
        result.has_more = 1
        result.pages_fetched = 1
        _write_fixed(result, "next_cursor", 256, b"opaque-page-2\0")
        return int(NativeCoreStatus.OK)

    stub.library.wce_wechat_moments_sync_poll = _FakeFunction(running)
    client = _client(stub)
    handle = client.begin_wechat_moments_sync(
        "wxid_owner", tmp_path.resolve(), "wxid_friend"
    )

    result = client.poll_wechat_moments_sync(handle)

    assert result.state is NativeCoreMomentsRequestState.RUNNING
    assert result.has_more is True
    assert result.next_cursor == "opaque-page-2"
