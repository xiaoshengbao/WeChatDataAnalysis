from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .logging_config import get_logger
from .native_core_client import NativeCoreFeature


logger = get_logger(__name__)


@dataclass
class ManagedSnsNativeOperation:
    client: Any
    native_core_operation: Any = None

    @property
    def uses_native_core_authorization(self) -> bool:
        return self.native_core_operation is not None

    def refresh_authorization(self) -> None:
        if not self.uses_native_core_authorization:
            return
        from .native_core_lease import refresh_native_core_lease

        refresh_native_core_lease(
            self.client, NativeCoreFeature.WECHAT_MOMENTS_REFRESH
        )

    def close(self) -> None:
        operation = self.native_core_operation
        self.native_core_operation = None
        if operation is not None:
            operation.close()

    def __enter__(self) -> ManagedSnsNativeOperation:
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()


def managed_sns_native_operation() -> ManagedSnsNativeOperation:
    """Prefer the fused native-core ABI, then use our standalone companion."""

    operation = None
    fused_error: Exception | None = None
    try:
        from .native_core_broker import managed_native_core_operation
        from .native_core_client import get_native_core_client

        operation = managed_native_core_operation()
        client = get_native_core_client()
        if client.supports_wechat_moments_sync:
            return ManagedSnsNativeOperation(client, operation)
    except Exception as exc:
        fused_error = exc
    if operation is not None:
        operation.close()

    from .sns_native_companion import get_sns_native_companion_client

    try:
        return ManagedSnsNativeOperation(get_sns_native_companion_client())
    except Exception:
        if fused_error is not None:
            logger.info(
                "[sns.native] fused runtime unavailable; standalone companion also failed "
                "fused_error_type=%s",
                type(fused_error).__name__,
            )
        raise


__all__ = ["ManagedSnsNativeOperation", "managed_sns_native_operation"]
