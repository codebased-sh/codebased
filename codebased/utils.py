from __future__ import annotations

import hashlib


def get_content_hash(content: bytes) -> str:
    return hashlib.sha1(content).hexdigest()
