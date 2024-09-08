from __future__ import annotations


def decode_text(file_bytes: bytes) -> str | None:
    try:
        return file_bytes.decode('utf-8')
    except UnicodeDecodeError:
        try:
            return file_bytes.decode('utf-16')
        except UnicodeDecodeError:
            return None
