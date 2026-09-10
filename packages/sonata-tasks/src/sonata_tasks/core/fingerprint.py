"""Deterministic fingerprints of task configuration."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence, Set
from pathlib import Path
from typing import Any


def _canonical(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("fingerprint values must be finite")
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        return {"bytes_hex": bytes(value).hex()}
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("fingerprint mappings must use string keys")
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, Set) and not isinstance(value, (str, bytes)):
        encoded = [_canonical(item) for item in value]
        return sorted(encoded, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical(item) for item in value]
    raise TypeError(f"unsupported fingerprint value {type(value).__qualname__}")


def fingerprint_digest(payload: Mapping[str, Any]) -> str:
    """Return a ``sha256:`` digest of ``payload`` under a canonical encoding.

    Nested mappings are key-sorted, sets are put in a deterministic order, and
    paths and bytes are normalised, so payloads that are equal as
    configuration hash identically. Values the payload cannot represent
    faithfully — non-finite floats, non-string mapping keys, unknown types —
    raise ``ValueError`` or ``TypeError`` instead of hashing ambiguously.
    """
    encoded = json.dumps(
        _canonical(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def semantic_key(namespace: str, payload: Mapping[str, Any]) -> str:
    """Build a stable, opaque key for configuration captured by a callable.

    Keeping the payload behind a digest prevents credentials and large request
    bodies from leaking into journals while retaining deterministic invalidation.
    """
    if not namespace:
        raise ValueError("semantic key namespace must not be empty")
    return f"{namespace}:{fingerprint_digest(payload)}"
