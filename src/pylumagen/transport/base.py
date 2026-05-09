"""Abstract transport interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable


class LumagenTransport(ABC):
    """Bidirectional byte transport to a Lumagen.

    A concrete subclass must:

    * Call the ``on_data`` callback (passed to :meth:`set_data_callback`)
      whenever inbound bytes arrive. Bytes should be passed through
      verbatim — line splitting and parsing are the protocol layer's job.
    * Raise :class:`~pylumagen.exceptions.LumagenConnectionError` from
      :meth:`connect` on unrecoverable connection failure.
    * Implement :meth:`disconnect` idempotently — repeated calls must be safe.
    """

    def __init__(self) -> None:
        self._on_data: Callable[[bytes], None] | None = None

    def set_data_callback(self, callback: Callable[[bytes], None]) -> None:
        """Register the inbound-data callback.

        Called before :meth:`connect`. The callback must be cheap; do any
        real work on a separate task.
        """
        self._on_data = callback

    @abstractmethod
    async def connect(self) -> None:
        """Establish the underlying transport connection."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down the transport. Must be idempotent."""

    @abstractmethod
    async def write(self, data: bytes) -> None:
        """Send ``data`` to the Lumagen. Raises on connection loss."""

    @property
    @abstractmethod
    def connected(self) -> bool:
        """``True`` while the transport is open."""

    # --- Helpers for concrete transports ---

    def _emit(self, data: bytes) -> None:
        """Forward inbound bytes to the registered callback, if any."""
        if self._on_data is not None and data:
            self._on_data(data)
