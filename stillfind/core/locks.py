# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Daniele Sisto
"""Blocchi tra processi: un solo agente, un solo lavoratore, una sola finestra alla volta."""
from __future__ import annotations

import sys
from pathlib import Path


class ProcessLock:
    """Lock di file, rilasciato dal sistema anche se il processo termina bruscamente."""

    def __init__(self, handle):
        self._handle = handle

    @classmethod
    def acquire(cls, path, *, blocking: bool = False) -> "ProcessLock | None":
        """Il lock da tenere aperto, o None se un altro processo lo possiede (a meno di `blocking`)."""
        path = Path(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        f = path.open("a+")
        try:
            if sys.platform.startswith("win"):
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except OSError:
            f.close()
            if blocking:
                raise
            return None
        return cls(f)

    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "ProcessLock":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
