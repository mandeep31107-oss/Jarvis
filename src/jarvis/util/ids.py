"""Identifier helpers."""

from __future__ import annotations

import itertools
import secrets
import time

_counter = itertools.count(1)


def new_id(prefix: str = "id") -> str:
    """A sortable, collision-resistant identifier: ``prefix-<ms>-<rand>``."""
    return f"{prefix}-{int(time.time() * 1000):x}-{secrets.token_hex(4)}"


def short_id(prefix: str = "t") -> str:
    """A short human-friendly id used for task/action references in the UI."""
    return f"{prefix}{next(_counter):04d}-{secrets.token_hex(2)}"
