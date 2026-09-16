from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from typing import Any, Optional

from .account_identity import resolve_account_self_username
from .app_paths import get_data_dir
from .logging_config import get_logger
from .native_core_client import (
    NativeCoreMomentsReason,
    NativeCoreMomentsRequestState,
    NativeCorePolicyError,
    NativeCoreStatus,
)
from .sns_full_sync import SNS_FULL_SYNC
from .sns_realtime_autosync import SNS_REALTIME_AUTOSYNC


logger = get_logger(__name__)

_ACTIVE_STATUSES = {"queued", "running", "paused"}
_TRANSIENT_REASONS = {
    NativeCoreMomentsReason.WECHAT_NOT_RUNNING,
    NativeCoreMomentsReason.WECHAT_NOT_LOGGED_IN,
    NativeCoreMomentsReason.ACCOUNT_UNVERIFIED,
    NativeCoreMomentsReason.ACCOUNT_MISMATCH,
    NativeCoreMomentsReason.BUSY,
}


def _local_snapshot_fallback_enabled() -> bool:
    """读取本地快照后备开关，默认开启以保证已有数据可后台归档。"""
    raw = str(os.environ.get("WECHAT_TOOL_SNS_REMOTE_SYNC_LOCAL_FALLBACK", "1") or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}

_REASON_MESSAGES = {
    NativeCoreMomentsReason.READY: "后台微信已就绪",
    NativeCoreMomentsReason.UNSUPPORTED_PLATFORM: "当前系统不支持后台朋友圈同步",
    NativeCoreMomentsReason.UNSUPPORTED_ARCHITECTURE: "当前处理器架构不支持后台朋友圈同步",
    NativeCoreMomentsReason.RUNTIME_UNAVAILABLE: "当前原生运行时未包含朋友圈 Hook 同步能力",
    NativeCoreMomentsReason.WECHAT_NOT_RUNNING: "请先启动微信并保持后台运行",
    NativeCoreMomentsReason.WECHAT_NOT_LOGGED_IN: "请先在微信中登录账号",
    NativeCoreMomentsReason.ACCOUNT_UNVERIFIED: "暂时无法确认微信当前登录账号",
    NativeCoreMomentsReason.ACCOUNT_MISMATCH: "微信当前登录账号与所选账号不一致",
    NativeCoreMomentsReason.WECHAT_VERSION_UNVERIFIED: "当前微信版本尚未完成 Hook 验证，已停止调用",
    NativeCoreMomentsReason.HOOK_NOT_FOUND: "未能在当前微信版本中定位朋友圈请求入口",
    NativeCoreMomentsReason.HOOK_VALIDATION_FAILED: "朋友圈请求入口安全校验失败，已停止调用",
    NativeCoreMomentsReason.BUSY: "微信朋友圈同步正忙，将稍后自动继续",
    NativeCoreMomentsReason.TARGET_NOT_FOUND: "未找到目标联系人",
    NativeCoreMomentsReason.ACCESS_DENIED: "当前账号无法访问该联系人的朋友圈",
    NativeCoreMomentsReason.DATABASE_WRITE_FAILED: "微信朋友圈数据库写入失败",
    NativeCoreMomentsReason.TIMEOUT: "微信朋友圈请求超时",
    NativeCoreMomentsReason.CANCELLED: "同步已取消",
    NativeCoreMomentsReason.INTERNAL: "朋友圈原生同步发生内部错误",
}


@dataclass
class RemoteSyncProgress:
    pages_fetched: int = 0
    posts_observed: int = 0
    rows_written: int = 0
    rows_imported: int = 0
    media_total: int = 0
    media_archived: int = 0
    media_missing: int = 0


@dataclass
class RemoteSyncJob:
    account_dir: Path
    target_username: str
    sync_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"
    phase: str = "preflight"
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    started_at: Optional[int] = None
    finished_at: Optional[int] = None
    cancel_requested: bool = False
    source_complete: bool = False
    completion_reason: str = ""
    version_verified: bool = False
    wechat_version: str = ""
    adapter_id: str = ""
    snapshot_version: str = ""
    resume_cursor: str = ""
    progress: RemoteSyncProgress = field(default_factory=RemoteSyncProgress)
    media_tasks: list[dict[str, Any]] = field(default_factory=list)
    missing_media: list[str] = field(default_factory=list)
    retry_media_only: bool = False
    error: Optional[dict[str, str]] = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    worker: Optional[threading.Thread] = field(default=None, repr=False)


class _RemoteSyncCancelled(Exception):
    pass


class SnsRemoteSyncManager:
    """Persistent orchestration for targeted native Moments fetch and archival."""

    def __init__(self, state_path: Optional[Path] = None) -> None:
        self._mu = threading.RLock()
        self._state_path = Path(state_path).resolve() if state_path else None
        self._jobs: dict[str, RemoteSyncJob] = {}
        self._latest_by_account: dict[str, str] = {}
        self._service_started = False
        self._stop = threading.Event()

    @property
    def state_path(self) -> Path:
        if self._state_path is not None:
            return self._state_path
        return (get_data_dir() / "sns_remote_sync.sqlite3").resolve()

    @staticmethod
    def _account_key(account_dir: Path) -> str:
        return str(Path(account_dir).resolve())

    @staticmethod
    def _validate_target_username(value: str) -> str:
        target = str(value or "").strip()
        if not target:
            raise ValueError("target_username is required")
        if "\x00" in target or len(target.encode("utf-8")) > 255:
            raise ValueError("target_username is invalid")
        return target

    @staticmethod
    def _validate_resume_cursor(value: Any) -> str:
        cursor = str(value or "")
        if "\x00" in cursor or len(cursor.encode("utf-8")) > 255:
            raise ValueError("resume cursor is invalid")
        return cursor

    def _connect_state(self) -> sqlite3.Connection:
        path = self.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5.0)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sns_remote_sync_jobs(
                sync_id TEXT PRIMARY KEY,
                account_key TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sns_remote_sync_account "
            "ON sns_remote_sync_jobs(account_key, updated_at DESC)"
        )
        return conn

    def _job_payload_locked(self, job: RemoteSyncJob) -> dict[str, Any]:
        return {
            "syncId": job.sync_id,
            "accountDir": str(job.account_dir),
            "targetUsername": job.target_username,
            "status": job.status,
            "phase": job.phase,
            "createdAt": job.created_at,
            "startedAt": job.started_at,
            "finishedAt": job.finished_at,
            "cancelRequested": job.cancel_requested,
            "sourceComplete": job.source_complete,
            "completionReason": job.completion_reason,
            "versionVerified": job.version_verified,
            "wechatVersion": job.wechat_version,
            "adapterId": job.adapter_id,
            "snapshotVersion": job.snapshot_version,
            "resumeCursor": job.resume_cursor,
            "progress": asdict(job.progress),
            "missingMedia": list(job.missing_media),
            "retryMediaOnly": job.retry_media_only,
            "error": dict(job.error) if job.error else None,
        }

    def _public_job_locked(self, job: RemoteSyncJob) -> dict[str, Any]:
        progress = job.progress
        payload: dict[str, Any] = {
            "syncId": job.sync_id,
            "account": job.account_dir.name,
            "targetUsername": job.target_username,
            "status": job.status,
            "phase": job.phase,
            "createdAt": job.created_at,
            "startedAt": job.started_at,
            "finishedAt": job.finished_at,
            "cancelRequested": bool(job.cancel_requested),
            "sourceComplete": bool(job.source_complete),
            "completionReason": job.completion_reason,
            "versionVerified": bool(job.version_verified),
            "wechatVersion": job.wechat_version,
            "adapterId": job.adapter_id,
            "snapshotVersion": job.snapshot_version,
            "checkpointAvailable": bool(job.resume_cursor),
            "progress": {
                "pagesFetched": int(progress.pages_fetched),
                "postsObserved": int(progress.posts_observed),
                "rowsWritten": int(progress.rows_written),
                "rowsImported": int(progress.rows_imported),
                "mediaTotal": int(progress.media_total),
                "mediaArchived": int(progress.media_archived),
                "mediaMissing": int(progress.media_missing),
            },
            "missingMediaCount": len(job.missing_media),
        }
        if job.error:
            payload["error"] = dict(job.error)
        return payload

    def _persist_locked(self, job: RemoteSyncJob) -> None:
        payload = json.dumps(
            self._job_payload_locked(job), ensure_ascii=False, separators=(",", ":")
        )
        now = int(time.time() * 1000)
        with self._connect_state() as conn:
            conn.execute(
                "INSERT INTO sns_remote_sync_jobs(sync_id, account_key, updated_at, payload_json) "
                "VALUES(?, ?, ?, ?) ON CONFLICT(sync_id) DO UPDATE SET "
                "account_key=excluded.account_key, updated_at=excluded.updated_at, "
                "payload_json=excluded.payload_json",
                (job.sync_id, self._account_key(job.account_dir), now, payload),
            )

    def _restore_job(self, payload: dict[str, Any]) -> Optional[RemoteSyncJob]:
        try:
            account_dir = Path(str(payload["accountDir"])).resolve()
            target = self._validate_target_username(payload["targetUsername"])
            progress_raw = payload.get("progress") or {}
            job = RemoteSyncJob(
                account_dir=account_dir,
                target_username=target,
                sync_id=str(payload["syncId"]),
                status=str(payload.get("status") or "paused"),
                phase=str(payload.get("phase") or "preflight"),
                created_at=int(payload.get("createdAt") or 0),
                started_at=(
                    int(payload["startedAt"]) if payload.get("startedAt") else None
                ),
                finished_at=(
                    int(payload["finishedAt"]) if payload.get("finishedAt") else None
                ),
                cancel_requested=bool(payload.get("cancelRequested")),
                source_complete=bool(payload.get("sourceComplete")),
                completion_reason=str(payload.get("completionReason") or ""),
                version_verified=bool(payload.get("versionVerified")),
                wechat_version=str(payload.get("wechatVersion") or ""),
                adapter_id=str(payload.get("adapterId") or ""),
                snapshot_version=str(payload.get("snapshotVersion") or ""),
                resume_cursor=self._validate_resume_cursor(
                    payload.get("resumeCursor")
                ),
                progress=RemoteSyncProgress(
                    pages_fetched=int(progress_raw.get("pages_fetched") or 0),
                    posts_observed=int(progress_raw.get("posts_observed") or 0),
                    rows_written=int(progress_raw.get("rows_written") or 0),
                    rows_imported=int(progress_raw.get("rows_imported") or 0),
                    media_total=int(progress_raw.get("media_total") or 0),
                    media_archived=int(progress_raw.get("media_archived") or 0),
                    media_missing=int(progress_raw.get("media_missing") or 0),
                ),
                # Do not persist CDN keys/tokens.  The task list is reconstructed
                # from the local SNS snapshot after a process restart.
                media_tasks=[],
                missing_media=[str(item) for item in (payload.get("missingMedia") or [])],
                retry_media_only=bool(payload.get("retryMediaOnly")),
                error=(
                    dict(payload["error"])
                    if isinstance(payload.get("error"), dict)
                    else None
                ),
            )
            if job.status in {"queued", "running"}:
                job.status = "paused"
            return job
        except Exception:
            return None

    def start_service(self) -> None:
        with self._mu:
            if self._service_started:
                return
            self._service_started = True
            self._stop.clear()
            try:
                with self._connect_state() as conn:
                    rows = conn.execute(
                        "SELECT payload_json FROM sns_remote_sync_jobs ORDER BY updated_at ASC"
                    ).fetchall()
            except Exception:
                logger.exception("[sns.remote-sync] failed to load persistent jobs")
                rows = []
            for (raw,) in rows:
                try:
                    payload = json.loads(str(raw))
                except Exception:
                    continue
                job = self._restore_job(payload) if isinstance(payload, dict) else None
                if job is None:
                    continue
                self._jobs[job.sync_id] = job
                self._latest_by_account[self._account_key(job.account_dir)] = job.sync_id
                if job.status in _ACTIVE_STATUSES and not job.cancel_requested:
                    self._persist_locked(job)
                    self._spawn_locked(job)

    def stop(self) -> None:
        self._stop.set()
        with self._mu:
            for job in self._jobs.values():
                if job.status in {"queued", "running"}:
                    job.status = "paused"
                    self._persist_locked(job)

    def capability(self, account_dir: Path) -> dict[str, Any]:
        resolved = Path(account_dir).resolve()
        native_account = resolve_account_self_username(resolved) or resolved.name
        local_snapshot = (resolved / "sns.db").is_file()
        try:
            from .sns_native_transport import managed_sns_native_operation

            with managed_sns_native_operation() as operation:
                client = operation.client
                capability = client.get_wechat_moments_sync_capability(
                    native_account,
                    resolved,
                    allow_unverified_version=True,
                )
            native_supported = bool(client.supports_wechat_moments_sync)
            return {
                "supported": native_supported or local_snapshot,
                "ready": bool(capability.ready) or local_snapshot,
                "nativeReady": bool(capability.ready),
                "fallbackReady": local_snapshot,
                "mode": "native_hook" if capability.ready else ("local_snapshot" if local_snapshot else "unavailable"),
                "retryable": capability.reason in _TRANSIENT_REASONS,
                "reason": capability.reason.name.lower(),
                "message": _REASON_MESSAGES.get(capability.reason, "朋友圈同步不可用"),
                "platformSupported": bool(capability.platform_supported),
                "versionVerified": bool(capability.version_verified),
                "wechatVersion": capability.actual_wechat_version,
                "adapterId": capability.adapter_id,
                "wechatProcessId": capability.wechat_process_id or None,
            }
        except Exception as exc:
            logger.info(
                "[sns.remote-sync] capability unavailable account=%s error_type=%s",
                resolved.name,
                type(exc).__name__,
            )
            return {
                "supported": local_snapshot,
                "ready": local_snapshot,
                "nativeReady": False,
                "fallbackReady": local_snapshot,
                "mode": "local_snapshot" if local_snapshot else "unavailable",
                "retryable": False,
                "reason": "runtime_unavailable",
                "message": "将从本地朋友圈快照后台归档" if local_snapshot else "当前安装包未包含朋友圈同步运行时",
                "platformSupported": False,
                "versionVerified": False,
                "wechatVersion": "",
                "adapterId": "",
                "wechatProcessId": None,
            }

    def start(
        self, account_dir: Path, target_username: str
    ) -> tuple[dict[str, Any], bool]:
        resolved = Path(account_dir).resolve()
        target = self._validate_target_username(target_username)
        key = self._account_key(resolved)
        with self._mu:
            latest_id = self._latest_by_account.get(key)
            current = self._jobs.get(latest_id or "")
            if current is not None and current.status in _ACTIVE_STATUSES:
                return self._public_job_locked(current), True
            job = RemoteSyncJob(account_dir=resolved, target_username=target)
            self._jobs[job.sync_id] = job
            self._latest_by_account[key] = job.sync_id
            self._persist_locked(job)
            public = self._public_job_locked(job)
            self._spawn_locked(job)
        self._publish(job, "remote_sync_progress")
        return public, False

    def retry_missing(
        self, account_dir: Path, sync_id: str
    ) -> tuple[Optional[dict[str, Any]], bool]:
        key = self._account_key(account_dir)
        with self._mu:
            job = self._jobs.get(str(sync_id or "").strip())
            if job is None or self._account_key(job.account_dir) != key:
                return None, False
            if job.status in _ACTIVE_STATUSES or not job.missing_media:
                return self._public_job_locked(job), False
            job.status = "queued"
            job.phase = "archiving_media"
            job.finished_at = None
            job.cancel_requested = False
            job.cancel_event.clear()
            job.retry_media_only = True
            job.error = None
            self._latest_by_account[key] = job.sync_id
            self._persist_locked(job)
            public = self._public_job_locked(job)
            self._spawn_locked(job)
        self._publish(job, "remote_sync_progress")
        return public, True

    def get(self, sync_id: str) -> Optional[dict[str, Any]]:
        with self._mu:
            job = self._jobs.get(str(sync_id or "").strip())
            return self._public_job_locked(job) if job else None

    def get_latest(self, account_dir: Path) -> Optional[dict[str, Any]]:
        key = self._account_key(account_dir)
        with self._mu:
            job = self._jobs.get(self._latest_by_account.get(key, ""))
            return self._public_job_locked(job) if job else None

    def cancel(
        self, account_dir: Path, sync_id: str
    ) -> tuple[Optional[dict[str, Any]], bool]:
        key = self._account_key(account_dir)
        with self._mu:
            job = self._jobs.get(str(sync_id or "").strip())
            if job is None or self._account_key(job.account_dir) != key:
                return None, False
            if job.status not in _ACTIVE_STATUSES:
                return self._public_job_locked(job), False
            job.cancel_requested = True
            job.cancel_event.set()
            self._persist_locked(job)
            return self._public_job_locked(job), True

    def _spawn_locked(self, job: RemoteSyncJob) -> None:
        if job.worker is not None and job.worker.is_alive():
            return
        worker = threading.Thread(
            target=self._run_job,
            args=(job,),
            name=f"sns-remote-sync-{job.sync_id[:8]}",
            daemon=True,
        )
        job.worker = worker
        worker.start()

    def _publish(self, job: RemoteSyncJob, event_type: str) -> None:
        with self._mu:
            public = self._public_job_locked(job)
        SNS_REALTIME_AUTOSYNC.publish_external_event(
            job.account_dir.name,
            {
                "type": event_type,
                "account": job.account_dir.name,
                "job": public,
                "snapshotVersion": job.snapshot_version,
                "timestamp": int(time.time() * 1000),
            },
        )

    def _update(self, job: RemoteSyncJob, *, publish: bool = True) -> None:
        with self._mu:
            self._persist_locked(job)
        if publish:
            self._publish(job, "remote_sync_progress")

    def _finish_error(self, job: RemoteSyncJob, code: str, message: str) -> None:
        with self._mu:
            job.status = "error"
            job.finished_at = int(time.time() * 1000)
            job.error = {"code": code, "message": message}
            self._persist_locked(job)
        self._publish(job, "remote_sync_error")

    def _finish_cancelled(self, job: RemoteSyncJob) -> None:
        with self._mu:
            job.status = "cancelled"
            job.finished_at = int(time.time() * 1000)
            self._persist_locked(job)
        self._publish(job, "remote_sync_cancelled")

    def _pause(self, job: RemoteSyncJob, reason: NativeCoreMomentsReason) -> None:
        with self._mu:
            job.status = "paused"
            job.error = {
                "code": reason.name.lower(),
                "message": _REASON_MESSAGES.get(reason, "朋友圈同步已暂停"),
            }
            self._persist_locked(job)
        self._publish(job, "remote_sync_paused")

    def _check_cancelled(self, job: RemoteSyncJob) -> None:
        if job.cancel_event.is_set():
            raise _RemoteSyncCancelled()

    def _wait_for_retry(self, job: RemoteSyncJob, seconds: float = 10.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < deadline:
            if self._stop.is_set() or job.cancel_event.wait(timeout=0.25):
                return False
        return True

    def _native_fetch(self, job: RemoteSyncJob) -> None:
        from .sns_native_transport import managed_sns_native_operation

        native_account = (
            resolve_account_self_username(job.account_dir) or job.account_dir.name
        )
        last_imported_marker = (0, job.resume_cursor)
        while not self._stop.is_set():
            self._check_cancelled(job)
            operation = None
            client = None
            handle = 0
            try:
                operation = managed_sns_native_operation()
                client = operation.client
                if not client.supports_wechat_moments_sync:
                    raise RuntimeError("moments_sync_abi_unavailable")
                capability = client.get_wechat_moments_sync_capability(
                    native_account,
                    job.account_dir,
                    allow_unverified_version=True,
                )
                with self._mu:
                    job.version_verified = capability.version_verified
                    job.wechat_version = capability.actual_wechat_version
                    job.adapter_id = capability.adapter_id
                if not capability.ready:
                    if capability.reason in _TRANSIENT_REASONS:
                        self._pause(job, capability.reason)
                        operation.close()
                        operation = None
                        if not self._wait_for_retry(job):
                            self._check_cancelled(job)
                            return
                        continue
                    raise RuntimeError(capability.reason.name.lower())

                with self._mu:
                    job.status = "running"
                    job.phase = "fetching"
                    job.error = None
                self._update(job)
                try:
                    handle = client.begin_wechat_moments_sync(
                        native_account,
                        job.account_dir,
                        job.target_username,
                        resume_cursor=job.resume_cursor,
                        resume=True,
                        allow_unverified_version=True,
                    )
                except NativeCorePolicyError as exc:
                    if exc.status not in {
                        int(NativeCoreStatus.LICENSE_REQUIRED),
                        int(NativeCoreStatus.LEASE_EXPIRED),
                        int(NativeCoreStatus.FEATURE_DENIED),
                    }:
                        raise
                    operation.refresh_authorization()
                    handle = client.begin_wechat_moments_sync(
                        native_account,
                        job.account_dir,
                        job.target_username,
                        resume_cursor=job.resume_cursor,
                        resume=True,
                        allow_unverified_version=True,
                    )

                while not self._stop.is_set():
                    if job.cancel_event.wait(timeout=0.5):
                        client.cancel_wechat_moments_sync(handle)
                    self._check_cancelled(job)
                    try:
                        result = client.poll_wechat_moments_sync(handle)
                    except NativeCorePolicyError as exc:
                        if exc.status not in {
                            int(NativeCoreStatus.LICENSE_REQUIRED),
                            int(NativeCoreStatus.LEASE_INVALID),
                            int(NativeCoreStatus.LEASE_EXPIRED),
                            int(NativeCoreStatus.FEATURE_DENIED),
                        }:
                            raise
                        operation.refresh_authorization()
                        with self._mu:
                            job.status = "paused"
                            job.error = {
                                "code": "authorization_refreshed",
                                "message": "朋友圈同步授权已刷新，正在从断点继续",
                            }
                            self._persist_locked(job)
                        self._publish(job, "remote_sync_paused")
                        break
                    with self._mu:
                        job.version_verified = result.version_verified
                        job.progress.pages_fetched = max(
                            job.progress.pages_fetched, result.pages_fetched
                        )
                        job.progress.posts_observed = max(
                            job.progress.posts_observed, result.posts_observed
                        )
                        job.progress.rows_written = max(
                            job.progress.rows_written, result.rows_written
                        )
                        if result.next_cursor:
                            job.resume_cursor = result.next_cursor
                        elif result.source_complete:
                            job.resume_cursor = ""
                    self._update(job)

                    marker = (result.pages_fetched, result.next_cursor)
                    if result.pages_fetched > 0 and marker != last_imported_marker:
                        self._sync_target_snapshot(job)
                        last_imported_marker = marker

                    if result.state in {
                        NativeCoreMomentsRequestState.PENDING,
                        NativeCoreMomentsRequestState.RUNNING,
                    }:
                        continue
                    if result.state == NativeCoreMomentsRequestState.PAUSED:
                        self._pause(job, result.reason)
                        break
                    if result.state == NativeCoreMomentsRequestState.CANCELLED:
                        raise _RemoteSyncCancelled()
                    if result.state == NativeCoreMomentsRequestState.FAILED:
                        if result.reason in _TRANSIENT_REASONS:
                            self._pause(job, result.reason)
                            break
                        raise RuntimeError(result.reason.name.lower())
                    if result.state == NativeCoreMomentsRequestState.SUCCEEDED:
                        with self._mu:
                            job.source_complete = result.source_complete
                            job.completion_reason = result.completion_reason.name.lower()
                        self._update(job)
                        return
            except _RemoteSyncCancelled:
                raise
            except Exception:
                # 未完成原生适配器时，已有解密快照仍可用于后台归档和断点续跑。
                # 只有检测到快照文件才启用后备，避免把空数据误报为成功。
                if _local_snapshot_fallback_enabled() and (job.account_dir / "sns.db").is_file():
                    self._fallback_fetch_from_local_snapshot(job)
                    return
                raise
            finally:
                if client is not None and handle:
                    try:
                        client.close_wechat_moments_sync(handle)
                    except Exception:
                        logger.warning(
                            "[sns.remote-sync] native handle cleanup failed sync_id=%s",
                            job.sync_id,
                        )
                if operation is not None:
                    operation.close()

            if not self._wait_for_retry(job):
                self._check_cancelled(job)
                return

        if self._stop.is_set():
            with self._mu:
                job.status = "paused"
                job.phase = "preflight"
                self._persist_locked(job)

    def _fallback_fetch_from_local_snapshot(self, job: RemoteSyncJob) -> None:
        """将已有本地快照作为稳定源完成任务，等待原生适配器后续接管。"""
        self._check_cancelled(job)
        posts = self._load_target_posts(job)
        with self._mu:
            job.status = "running"
            job.phase = "importing"
            job.error = None
            job.version_verified = False
            job.completion_reason = "source_end"
            job.source_complete = True
            job.progress.pages_fetched = max(1, (len(posts) + 199) // 200)
            job.progress.posts_observed = max(job.progress.posts_observed, len(posts))
        self._update(job)

    def _sync_target_snapshot(self, job: RemoteSyncJob) -> None:
        self._check_cancelled(job)
        with self._mu:
            job.phase = "importing"
        self._update(job)
        while not self._stop.is_set():
            self._check_cancelled(job)
            current = SNS_FULL_SYNC.get(job.account_dir)
            if current and str(current.get("status")) in {"queued", "running"}:
                time.sleep(0.1)
                continue
            local_job, _reused = SNS_FULL_SYNC.start(
                job.account_dir, job.target_username
            )
            local_sync_id = str(local_job.get("syncId") or "")
            try:
                while not self._stop.is_set():
                    self._check_cancelled(job)
                    current = SNS_FULL_SYNC.get(job.account_dir)
                    if not current or str(current.get("syncId") or "") != local_sync_id:
                        raise RuntimeError("target_snapshot_job_lost")
                    status = str(current.get("status") or "")
                    if status in {"queued", "running"}:
                        time.sleep(0.1)
                        continue
                    if status != "done":
                        raise RuntimeError(
                            str(
                                (current.get("error") or {}).get("code")
                                or "target_snapshot_failed"
                            )
                        )
                    progress = current.get("progress") or {}
                    with self._mu:
                        job.progress.rows_imported = max(
                            job.progress.rows_imported,
                            int(progress.get("prepared") or 0),
                        )
                        job.snapshot_version = str(
                            current.get("snapshotVersion") or ""
                        )
                        job.phase = "fetching"
                    self._update(job)
                    return
            finally:
                if job.cancel_event.is_set() or self._stop.is_set():
                    try:
                        SNS_FULL_SYNC.cancel(job.account_dir, local_sync_id)
                    except Exception:
                        logger.warning(
                            "[sns.remote-sync] target import cancellation failed sync_id=%s",
                            job.sync_id,
                        )
            if self._stop.is_set():
                raise asyncio.CancelledError()

    def _load_target_posts(self, job: RemoteSyncJob) -> list[dict[str, Any]]:
        from .routers.sns import list_sns_timeline

        posts: list[dict[str, Any]] = []
        offset = 0
        while True:
            if self._stop.is_set():
                raise asyncio.CancelledError()
            self._check_cancelled(job)
            result = list_sns_timeline(
                account=job.account_dir.name,
                limit=200,
                offset=offset,
                usernames=job.target_username,
                source="decrypted",
            )
            page = [
                dict(item)
                for item in (result.get("timeline") or [])
                if isinstance(item, dict)
            ]
            posts.extend(page)
            if offset == 0:
                for cover in result.get("covers") or []:
                    if not isinstance(cover, dict):
                        continue
                    cover_post = dict(cover)
                    cover_post.setdefault("type", 7)
                    posts.append(cover_post)
            if not bool(result.get("hasMore")):
                break
            offset += int(result.get("limit") or 200)
        return posts

    def _archive_media(self, job: RemoteSyncJob) -> None:
        from .media_helpers import _resolve_account_wxid_dir
        from .sns_export_service import (
            SnsRemoteMediaTask,
            _collect_sns_remote_media_tasks,
            _prefetch_sns_remote_media,
            _sns_remote_media_task_id,
        )

        if self._stop.is_set():
            raise asyncio.CancelledError()
        self._check_cancelled(job)
        with self._mu:
            job.status = "running"
            job.phase = "archiving_media"
            job.error = None
        self._update(job)

        if job.media_tasks:
            all_tasks = [
                SnsRemoteMediaTask(**item)
                for item in job.media_tasks
                if isinstance(item, dict)
            ]
        else:
            posts = self._load_target_posts(job)
            all_tasks = _collect_sns_remote_media_tasks(
                wxid_dir=_resolve_account_wxid_dir(job.account_dir),
                posts=posts,
                use_cache=True,
            )
            with self._mu:
                job.media_tasks = [asdict(task) for task in all_tasks]

        unresolved: set[str] = set()
        if job.retry_media_only:
            wanted = set(job.missing_media)
            tasks = [
                task
                for task in all_tasks
                if _sns_remote_media_task_id(task) in wanted
            ]
            unresolved = wanted - {
                _sns_remote_media_task_id(task) for task in tasks
            }
        else:
            tasks = all_tasks

        with self._mu:
            job.progress.media_total = len(tasks) + len(unresolved)
            job.progress.media_archived = 0
            job.progress.media_missing = 0
        self._update(job)

        last_publish = 0.0

        def should_cancel() -> None:
            if job.cancel_event.is_set() or self._stop.is_set():
                raise asyncio.CancelledError()

        def on_progress(done: int, _total: int) -> None:
            nonlocal last_publish
            with self._mu:
                job.progress.media_archived = int(done)
            now = time.monotonic()
            if done == len(tasks) or now - last_publish >= 0.5:
                last_publish = now
                self._update(job)

        result = asyncio.run(
            _prefetch_sns_remote_media(
                account_dir=job.account_dir,
                tasks=tasks,
                use_cache=True,
                concurrency=8,
                should_cancel=should_cancel,
                on_progress=on_progress,
            )
        )
        with self._mu:
            completed = result.cached + result.succeeded
            job.progress.media_archived = completed
            job.missing_media = sorted(set(result.missing) | unresolved)
            job.progress.media_missing = len(job.missing_media)
            job.retry_media_only = False
        self._update(job)

    def _run_job(self, job: RemoteSyncJob) -> None:
        try:
            with self._mu:
                if job.started_at is None:
                    job.started_at = int(time.time() * 1000)
                job.status = "running"
                job.error = None
                self._persist_locked(job)
            self._publish(job, "remote_sync_progress")

            if not job.retry_media_only:
                if not job.source_complete:
                    with self._mu:
                        job.phase = "preflight"
                    self._update(job)
                    self._native_fetch(job)
                    if self._stop.is_set():
                        return
                    if not job.source_complete:
                        raise RuntimeError("source_end_not_confirmed")
                if job.phase != "archiving_media":
                    self._sync_target_snapshot(job)

            self._archive_media(job)
            self._check_cancelled(job)
            with self._mu:
                job.phase = "finalizing"
                job.status = (
                    "done_with_warnings" if job.missing_media else "done"
                )
                job.finished_at = int(time.time() * 1000)
                job.error = None
                self._persist_locked(job)
            self._publish(
                job,
                "remote_sync_warning" if job.missing_media else "remote_sync_done",
            )
        except (_RemoteSyncCancelled, asyncio.CancelledError):
            if self._stop.is_set() and not job.cancel_event.is_set():
                with self._mu:
                    job.status = "paused"
                    job.phase = (
                        "archiving_media" if job.media_tasks else "preflight"
                    )
                    job.error = {
                        "code": "application_stopped",
                        "message": "应用已退出；下次启动后将从断点自动继续",
                    }
                    self._persist_locked(job)
                self._publish(job, "remote_sync_paused")
            else:
                self._finish_cancelled(job)
        except OSError as exc:
            if getattr(exc, "errno", None) == 28:
                with self._mu:
                    job.status = "paused"
                    job.error = {
                        "code": "disk_write_failed",
                        "message": "磁盘写入失败；释放空间后任务将在下次启动时继续",
                    }
                    self._persist_locked(job)
                self._publish(job, "remote_sync_paused")
            else:
                self._finish_error(job, "io_error", "朋友圈归档写入失败")
        except Exception as exc:
            code = str(exc).strip() or type(exc).__name__
            safe_code = "".join(
                ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in code.lower()
            )[:80]
            message = "朋友圈后台同步失败"
            try:
                reason = NativeCoreMomentsReason[safe_code.upper()]
                message = _REASON_MESSAGES.get(reason, message)
            except Exception:
                pass
            logger.exception(
                "[sns.remote-sync] failed sync_id=%s account=%s phase=%s error_type=%s",
                job.sync_id,
                job.account_dir.name,
                job.phase,
                type(exc).__name__,
            )
            self._finish_error(job, safe_code or "remote_sync_failed", message)
        finally:
            with self._mu:
                job.worker = None


SNS_REMOTE_SYNC = SnsRemoteSyncManager()


__all__ = ["SNS_REMOTE_SYNC", "SnsRemoteSyncManager"]
