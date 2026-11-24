"""Serialize the AI scans so face recognition and OCR never run at the same time.

Each scan already caps its own runtime threads, but the caps are per runtime: running
both scans together multiplied the load (the process sat at ~90% CPU on a 24 core
machine while scanning). This gate allows a single AI scan at a time; the second one
waits for its turn.
"""

from __future__ import annotations

import functools
import threading

_GATE = threading.Semaphore(1)


def serialized_scan(func):
    """Run *func* while holding the single AI-scan slot."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        _GATE.acquire()
        try:
            return func(*args, **kwargs)
        finally:
            _GATE.release()

    return wrapper
