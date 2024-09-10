from __future__ import annotations
import chardet


def decode_text(file_bytes: bytes) -> str:
    # Try UTF-8 first
    try:
        return file_bytes.decode('utf-8')
    except UnicodeDecodeError:
        pass

    # Use chardet to detect encoding
    detected = chardet.detect(file_bytes)
    if detected['encoding']:
        try:
            return file_bytes.decode(detected['encoding'])
        except UnicodeDecodeError:
            pass

    # If chardet fails, try some common encodings
    for encoding in ['windows-1252', 'iso-8859-1']:
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue

    # If all else fails, use 'replace' error handling with UTF-8
    return file_bytes.decode('utf-8', errors='replace')
