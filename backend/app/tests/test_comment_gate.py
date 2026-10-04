from datetime import datetime, timedelta, timezone

from app.modules.wish_comment.gate import validate_text, write_gate

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
MAX = 500


def test_open_is_writable():
    g = write_gate("open", None, NOW)
    assert g["state"] == "writable" and g["writable"] is True


def test_released_is_writable():
    g = write_gate("released", None, NOW)
    assert g["state"] == "writable" and g["writable"] is True


def test_claimed_live_is_frozen():
    g = write_gate("claimed", (NOW + timedelta(hours=1)).isoformat(), NOW)
    assert g["state"] == "frozen" and g["writable"] is False and g["reason"] == "frozen"


def test_claimed_expired_is_writable():
    g = write_gate("claimed", (NOW - timedelta(minutes=1)).isoformat(), NOW)
    assert g["state"] == "writable" and g["reason"] == "ttl_expired"


def test_claimed_expiry_boundary_is_writable():
    # <= like claim_lock.release_if_expired
    g = write_gate("claimed", NOW.isoformat(), NOW)
    assert g["state"] == "writable" and g["reason"] == "ttl_expired"


def test_fulfilled_is_archived_first():
    # Archive wins even though the lock timestamp is already expired.
    g = write_gate("fulfilled", (NOW - timedelta(days=1)).isoformat(), NOW)
    assert g["state"] == "archived" and g["writable"] is False and g["reason"] == "archived"


def test_claimed_without_expiry_is_defensively_frozen():
    g = write_gate("claimed", None, NOW)
    assert g["state"] == "frozen" and g["writable"] is False


def test_unknown_status_not_writable():
    g = write_gate("weird", None, NOW)
    assert g["writable"] is False and g["reason"] == "bad_status"


def test_validate_ok_normalizes_whitespace():
    v = validate_text("  hello  ", "  bob ", MAX)
    assert v["ok"] is True and v["content"] == "hello" and v["author"] == "bob"
    assert v["length"] == 5


def test_validate_empty_content_variants():
    for bad in (None, "", "   ", "\t\n "):
        v = validate_text(bad, "bob", MAX)
        assert v["ok"] is False and v["reason"] == "empty_content"


def test_validate_length_boundary():
    assert validate_text("x" * 500, "bob", MAX)["ok"] is True
    v = validate_text("x" * 501, "bob", MAX)
    assert v["ok"] is False and v["reason"] == "too_long" and v["length"] == 501


def test_validate_empty_author():
    for bad in ("", "   "):
        v = validate_text("hello", bad, MAX)
        assert v["ok"] is False and v["reason"] == "empty_author"


def test_validate_counts_codepoints():
    v = validate_text("字" * 500, "bob", MAX)
    assert v["ok"] is True and v["length"] == 500
