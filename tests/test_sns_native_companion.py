from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from wechat_decrypt_tool.native_core_client import NativeCoreMomentsReason
from wechat_decrypt_tool.sns_native_companion import SnsNativeCompanionClient
from wechat_decrypt_tool import sns_native_transport


ROOT = Path(__file__).resolve().parents[1]
NATIVE_ROOT = ROOT / "native" / "wechat-sns-native"


class _ManagedOperation:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Client:
    def __init__(self, supported: bool) -> None:
        self.supports_wechat_moments_sync = supported


def test_transport_prefers_fused_abi(monkeypatch: pytest.MonkeyPatch) -> None:
    from wechat_decrypt_tool import native_core_broker, native_core_client
    from wechat_decrypt_tool import sns_native_companion

    lease = _ManagedOperation()
    fused = _Client(True)
    monkeypatch.setattr(
        native_core_broker, "managed_native_core_operation", lambda: lease
    )
    monkeypatch.setattr(native_core_client, "get_native_core_client", lambda: fused)
    monkeypatch.setattr(
        sns_native_companion,
        "get_sns_native_companion_client",
        lambda: pytest.fail("standalone companion should not be selected"),
    )

    with sns_native_transport.managed_sns_native_operation() as selected:
        assert selected.client is fused
        assert selected.uses_native_core_authorization is True
        assert lease.closed is False

    assert lease.closed is True


def test_transport_falls_back_when_fused_abi_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wechat_decrypt_tool import native_core_broker, native_core_client
    from wechat_decrypt_tool import sns_native_companion

    lease = _ManagedOperation()
    fused = _Client(False)
    standalone = _Client(True)
    monkeypatch.setattr(
        native_core_broker, "managed_native_core_operation", lambda: lease
    )
    monkeypatch.setattr(native_core_client, "get_native_core_client", lambda: fused)
    monkeypatch.setattr(
        sns_native_companion,
        "get_sns_native_companion_client",
        lambda: standalone,
    )

    with sns_native_transport.managed_sns_native_operation() as selected:
        assert selected.client is standalone
        assert selected.uses_native_core_authorization is False

    assert lease.closed is True


@pytest.mark.skipif(
    sys.platform != "darwin"
    or shutil.which("make") is None
    or shutil.which("clang++") is None,
    reason="native macOS contract test requires Apple clang and make",
)
def test_macos_companion_builds_and_reports_fail_closed_capability(
    tmp_path: Path,
) -> None:
    build_dir = tmp_path / "native-build"
    subprocess.run(
        ["make", "-C", str(NATIVE_ROOT), f"BUILD_DIR={build_dir}", "all"],
        check=True,
        capture_output=True,
        text=True,
    )
    library = build_dir / "libwechat_sns_native.dylib"
    probe = build_dir / "wechat-sns-probe"
    assert library.is_file()
    assert probe.is_file()

    completed = subprocess.run(
        [str(probe), "probe", str(tmp_path.resolve())],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload["abiVersion"] == 1
    assert payload["status"] == 0
    assert payload["ready"] in {0, 1}
    assert payload["versionVerified"] in {0, 1}
    assert payload["reason"] in {reason.value for reason in NativeCoreMomentsReason}
    if payload["ready"] == 0:
        assert payload["reason"] != NativeCoreMomentsReason.READY.value

    with SnsNativeCompanionClient(library) as client:
        capability = client.get_wechat_moments_sync_capability(
            "probe",
            tmp_path.resolve(),
            allow_unverified_version=True,
        )
    assert capability.platform_supported is True
    if capability.ready:
        assert capability.reason is NativeCoreMomentsReason.READY
    else:
        assert capability.reason is not NativeCoreMomentsReason.READY


def test_adapter_for_observed_macos_binary_is_not_prematurely_verified() -> None:
    records = [
        json.loads(line)
        for line in (
            NATIVE_ROOT / "adapters" / "wechat_sns_adapters.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    observed = next(
        record
        for record in records
        if record["wechatVersion"] == "4.1.5"
        and record["architecture"] == "arm64"
    )
    assert observed["binarySha256"] == (
        "20f355ca7269a51fbc29e4b50ec4e74bdcac53f47e93dbc49245231f4f202b6d"
    )
    assert observed["status"] == "research_required"
    assert observed["requestEntryPattern"] == ""
    assert observed["responseCursorPattern"] == ""
    assert observed["databaseWritePattern"] == ""
