"""Validation helpers shared by GUI and tests."""

from urllib.parse import urlparse


def is_valid_portal_url(url: str) -> bool:
    """Return True for valid http/https URLs with a non-empty host."""
    try:
        parsed = urlparse((url or "").strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def is_valid_portal_name(name: str) -> bool:
    """
    Portal names must be non-empty and must not contain formatting separators
    that break Organization_list.txt serialization.
    """
    value = (name or "").strip()
    if not value:
        return False
    return ":" not in value and "\n" not in value and "\r" not in value
