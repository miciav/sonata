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
    encoded = json.dumps(
        _canonical(payload),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
