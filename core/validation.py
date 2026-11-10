"""Small, shared limits for untrusted API and metadata input."""
import json
import math


def validate_metadata(value):
    try:
        size = len(json.dumps(value, allow_nan=False).encode('utf-8'))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError('metadata must contain finite JSON values') from exc
    if size > 32768:
        raise ValueError('metadata must be at most 32768 UTF-8 bytes')
    return value


def validate_filters(value):
    if len(value) > 20:
        raise ValueError('At most 20 metadata filters are supported')
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or len(key) > 200:
            raise ValueError('Filter keys must contain 1–200 characters')
        if isinstance(item, list):
            valid = 1 <= len(item) <= 100 and all(isinstance(v, str) and len(v) <= 1000 for v in item)
        elif isinstance(item, str):
            valid = len(item) <= 1000
        else:
            valid = isinstance(item, (bool, int)) or isinstance(item, float) and math.isfinite(item)
        if not valid:
            raise ValueError('Filter values must be strings, finite numbers, booleans, or 1–100 strings')
    return value
