"""Freeze/archive gate + pre-append validation for wish comments (pure)."""

MAX_AUTHOR_LEN = 50
MAX_CONTENT_LEN = 500

WRITABLE_STATUSES = ("open", "released")


def writable(status: str) -> bool:
    """Appends are allowed only while open or released.

    claimed -> frozen; fulfilled -> archived forever (archive beats unfreeze).
    """
    return status in WRITABLE_STATUSES


def block_reason(status: str) -> str | None:
    """409 reason code when the thread is not writable. fulfilled wins over frozen."""
    if status == "fulfilled":
        return "archived"
    if status == "claimed":
        return "frozen"
    return None


def validate(author: str, content: str) -> list[str]:
    """Return error codes; empty list means the append may proceed. No DB side effects."""
    errors: list[str] = []
    author = (author or "").strip()
    content = (content or "").strip()
    if not author:
        errors.append("author_empty")
    elif len(author) > MAX_AUTHOR_LEN:
        errors.append("author_too_long")
    if not content:
        errors.append("content_empty")
    elif len(content) > MAX_CONTENT_LEN:
        errors.append("content_too_long")
    return errors
