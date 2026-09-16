from __future__ import annotations

from pathlib import Path
import sqlite3
from unittest import mock

from wechat_decrypt_tool.routers import sns as sns_router
from wechat_decrypt_tool.sns_export_service import (
    _collect_sns_remote_media_tasks,
    _sns_remote_media_task_id,
)
from wechat_decrypt_tool.sns_remote_sync import SnsRemoteSyncManager


def _create_manager(tmp_path: Path) -> SnsRemoteSyncManager:
    return SnsRemoteSyncManager(tmp_path / "remote-sync.sqlite3")


def test_remote_sync_job_persists_and_resumes_after_restart(tmp_path: Path) -> None:
    account_dir = tmp_path / "decrypted" / "account-a"
    account_dir.mkdir(parents=True)
    first = _create_manager(tmp_path)
    with (
        mock.patch.object(first, "_spawn_locked") as spawn,
        mock.patch.object(first, "_publish"),
    ):
        started, reused = first.start(account_dir, "wxid_friend")

    assert reused is False
    assert spawn.call_count == 1
    assert started["targetUsername"] == "wxid_friend"
    first._jobs[started["syncId"]].resume_cursor = "opaque-page-7"
    first._jobs[started["syncId"]].media_tasks = [
        {
            "kind": "image",
            "url": "https://example.invalid/private.jpg",
            "key": "sensitive-key",
            "token": "sensitive-token",
        }
    ]
    with first._mu:
        first._persist_locked(first._jobs[started["syncId"]])
    with sqlite3.connect(str(first.state_path)) as conn:
        persisted_payload = conn.execute(
            "SELECT payload_json FROM sns_remote_sync_jobs WHERE sync_id = ?",
            (started["syncId"],),
        ).fetchone()[0]
    assert "sensitive-key" not in persisted_payload
    assert "sensitive-token" not in persisted_payload

    restored = _create_manager(tmp_path)
    with mock.patch.object(restored, "_spawn_locked") as resume:
        restored.start_service()

    current = restored.get_latest(account_dir)
    assert current is not None
    assert current["syncId"] == started["syncId"]
    assert current["status"] == "paused"
    assert current["targetUsername"] == "wxid_friend"
    assert current["checkpointAvailable"] is True
    assert restored._jobs[started["syncId"]].resume_cursor == "opaque-page-7"
    assert resume.call_count == 1


def test_remote_sync_requires_native_source_end_marker(tmp_path: Path) -> None:
    account_dir = tmp_path / "decrypted" / "account-b"
    account_dir.mkdir(parents=True)
    manager = _create_manager(tmp_path)
    events: list[str] = []
    with (
        mock.patch.object(manager, "_spawn_locked"),
        mock.patch.object(manager, "_publish", side_effect=lambda _job, event: events.append(event)),
    ):
        started, _ = manager.start(account_dir, "wxid_friend")
        job = manager._jobs[started["syncId"]]
        with (
            mock.patch.object(manager, "_native_fetch"),
            mock.patch.object(manager, "_sync_target_snapshot") as local_sync,
            mock.patch.object(manager, "_archive_media") as archive,
        ):
            manager._run_job(job)

    current = manager.get(started["syncId"])
    assert current is not None
    assert current["status"] == "error"
    assert current["error"]["code"] == "source_end_not_confirmed"
    assert local_sync.call_count == 0
    assert archive.call_count == 0
    assert events[-1] == "remote_sync_error"


def test_remote_sync_finishes_with_warnings_and_retries_only_missing_media(
    tmp_path: Path,
) -> None:
    account_dir = tmp_path / "decrypted" / "account-c"
    account_dir.mkdir(parents=True)
    manager = _create_manager(tmp_path)
    events: list[str] = []
    with (
        mock.patch.object(manager, "_spawn_locked"),
        mock.patch.object(manager, "_publish", side_effect=lambda _job, event: events.append(event)),
    ):
        started, _ = manager.start(account_dir, "wxid_friend")
        job = manager._jobs[started["syncId"]]

        def native_fetch(_job) -> None:
            _job.source_complete = True
            _job.completion_reason = "source_end"

        def archive_media(_job) -> None:
            _job.media_tasks = [
                {
                    "kind": "image",
                    "url": "https://example.invalid/missing.jpg",
                    "key": "",
                    "token": "",
                    "expected_width": 0,
                    "expected_height": 0,
                    "require_original": True,
                }
            ]
            _job.missing_media = [
                "image|https://example.invalid/missing.jpg"
            ]
            _job.progress.media_total = 1
            _job.progress.media_missing = 1

        with (
            mock.patch.object(manager, "_native_fetch", side_effect=native_fetch),
            mock.patch.object(manager, "_sync_target_snapshot"),
            mock.patch.object(manager, "_archive_media", side_effect=archive_media),
        ):
            manager._run_job(job)

        warning = manager.get(started["syncId"])
        assert warning is not None
        assert warning["status"] == "done_with_warnings"
        assert warning["sourceComplete"] is True
        assert warning["missingMediaCount"] == 1
        assert events[-1] == "remote_sync_warning"

        retry, accepted = manager.retry_missing(account_dir, started["syncId"])

    assert accepted is True
    assert retry is not None
    assert retry["status"] == "queued"
    assert manager._jobs[started["syncId"]].retry_media_only is True


def test_restart_in_media_phase_does_not_repeat_completed_native_fetch(
    tmp_path: Path,
) -> None:
    account_dir = tmp_path / "decrypted" / "account-media-resume"
    account_dir.mkdir(parents=True)
    manager = _create_manager(tmp_path)
    with (
        mock.patch.object(manager, "_spawn_locked"),
        mock.patch.object(manager, "_publish"),
    ):
        started, _ = manager.start(account_dir, "wxid_friend")
        job = manager._jobs[started["syncId"]]
        job.source_complete = True
        job.completion_reason = "source_end"
        job.phase = "archiving_media"
        with (
            mock.patch.object(manager, "_native_fetch") as native_fetch,
            mock.patch.object(manager, "_sync_target_snapshot") as local_sync,
            mock.patch.object(manager, "_archive_media") as archive,
        ):
            manager._run_job(job)

    current = manager.get(started["syncId"])
    assert current is not None
    assert current["status"] == "done"
    assert native_fetch.call_count == 0
    assert local_sync.call_count == 0
    assert archive.call_count == 1


def test_comment_images_are_part_of_stable_media_archive_queue() -> None:
    tasks = _collect_sns_remote_media_tasks(
        wxid_dir=None,
        posts=[
            {
                "id": "post-1",
                "comments": [
                    {
                        "images": [
                            {
                                "url": "https://example.invalid/comment.jpg?token=secret",
                                "key": "comment-key",
                                "token": "comment-token",
                                "width": 1200,
                                "height": 800,
                            }
                        ]
                    }
                ],
            }
        ],
        use_cache=False,
    )

    assert len(tasks) == 1
    task = tasks[0]
    assert task.kind == "image"
    assert task.require_original is True
    assert task.expected_width == 1200
    assert task.expected_height == 800
    assert _sns_remote_media_task_id(task).startswith("image|")


def test_remote_sync_api_surface_is_registered() -> None:
    routes = {
        (method, route.path)
        for route in sns_router.router.routes
        for method in (route.methods or set())
    }
    assert ("GET", "/api/sns/remote-sync/capability") in routes
    assert ("POST", "/api/sns/remote-sync") in routes
    assert ("GET", "/api/sns/remote-sync/status") in routes
    assert ("GET", "/api/sns/remote-sync/{sync_id}") in routes
    assert ("DELETE", "/api/sns/remote-sync/{sync_id}") in routes
    assert ("POST", "/api/sns/remote-sync/{sync_id}/retry-missing") in routes
