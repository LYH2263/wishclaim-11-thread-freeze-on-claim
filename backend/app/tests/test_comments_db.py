import threading
from datetime import datetime, timedelta, timezone

import pytest

from app import seed
from app.db import connect
from app.engines.claim_lock import lock_payload
from app.modules import wish_comment
from app.modules.wish_comment import projection
from app.modules.wish_comment.storage import list_comments

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
MAX = 500


@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()
    return tmp_path


def _set_claim(wid, claimer, now=NOW, ttl_seconds=3600):
    c = connect()
    p = lock_payload(claimer, now, ttl_seconds)
    c.execute("UPDATE wishes SET status=?, claimer=?, claimed_at=?, expires_at=? WHERE id=?",
              (p["status"], p["claimer"], p["claimed_at"], p["expires_at"], wid))
    c.commit(); c.close()


def _release(wid):
    c = connect()
    c.execute("UPDATE wishes SET status='released', claimer=NULL, claimed_at=NULL, expires_at=NULL WHERE id=?", (wid,))
    c.commit(); c.close()


def _fulfill(wid):
    c = connect()
    c.execute("UPDATE wishes SET status='fulfilled' WHERE id=?", (wid,))
    c.commit(); c.close()


def test_floors_increment_per_wish(db):
    c = connect()
    for i in range(3):
        row = wish_comment.add_comment(c, 1, f"a{i}", f"msg{i}", NOW, MAX)
        assert row["floor"] == i + 1
    other = wish_comment.add_comment(c, 2, "b", "first", NOW, MAX)
    c.close()
    assert other["floor"] == 1


def test_rejected_writes_never_persist(db):
    _set_claim(1, "alice")  # live lock -> frozen
    c = connect()
    with pytest.raises(wish_comment.CommentError) as ei:
        wish_comment.add_comment(c, 1, "bob", "should fail", NOW, MAX)
    assert ei.value.reason == "frozen" and ei.value.status_code == 409
    assert projection.counts(c) == {}
    for bad in ("", "   ", "x" * (MAX + 1)):
        with pytest.raises(wish_comment.CommentError):
            wish_comment.add_comment(c, 2, bad, "bob", NOW, MAX)
    with pytest.raises(wish_comment.CommentError) as ei:
        wish_comment.add_comment(c, 2, "ok content", "  ", NOW, MAX)
    assert ei.value.reason == "empty_author"
    assert list_comments(c, 1) == [] and list_comments(c, 2) == []
    c.close()


def test_preview_does_not_write(db):
    c = connect()
    v = wish_comment.preview(c, 1, "   ", "bob", NOW, MAX)
    assert v["ok"] is False and v["reason"] == "empty_content"
    v = wish_comment.preview(c, 1, "hi", "bob", NOW, MAX)
    assert v["ok"] is True and v["state"] == "writable"
    assert list_comments(c, 1) == []
    c.close()


def test_freeze_release_refreeze_archive_lifecycle(db):
    c = connect()
    wish_comment.add_comment(c, 1, "bob", "open msg", NOW, MAX)
    c.close()

    _set_claim(1, "alice")
    c = connect()
    with pytest.raises(wish_comment.CommentError) as ei:
        wish_comment.add_comment(c, 1, "bob", "while claimed", NOW, MAX)
    assert ei.value.reason == "frozen"
    # history stays readable and reports frozen
    payload = wish_comment.get_thread(c, 1, NOW, MAX)
    assert payload["state"] == "frozen" and len(payload["comments"]) == 1
    c.close()

    _release(1)  # release unfreezes
    c = connect()
    wish_comment.add_comment(c, 1, "carol", "after release", NOW, MAX)
    c.close()

    _set_claim(1, "alice")
    _fulfill(1)  # archive: permanent even though the thread was unfrozen before
    c = connect()
    with pytest.raises(wish_comment.CommentError) as ei:
        wish_comment.add_comment(c, 1, "bob", "after fulfill", NOW, MAX)
    assert ei.value.reason == "archived" and ei.value.status_code == 409
    payload = wish_comment.get_thread(c, 1, NOW, MAX)
    assert payload["state"] == "archived"
    assert [m["floor"] for m in payload["comments"]] == [1, 2]
    c.close()


def test_expired_lock_writing_releases_lock_in_same_tx(db):
    # Seed wish 4 is a claimed lock expired back in 2020.
    c = connect()
    row = wish_comment.add_comment(
        c, 4, "bob", "late comment",
        datetime(2026, 1, 1, tzinfo=timezone.utc), MAX)
    assert row["floor"] == 1
    w = c.execute("SELECT status, claimer, claimed_at, expires_at FROM wishes WHERE id=4").fetchone()
    assert w["status"] == "open" and w["claimer"] is None
    assert w["claimed_at"] is None and w["expires_at"] is None
    assert list_comments(c, 4)[0]["floor"] == 1
    c.close()


def test_counts_single_source_across_three_views(db):
    c = connect()
    wish_comment.add_comment(c, 1, "a", "x", NOW, MAX)
    wish_comment.add_comment(c, 1, "b", "y", NOW, MAX)
    wish_comment.add_comment(c, 2, "c", "z", NOW, MAX)
    wall = projection.counts(c)
    detail = projection.counts(c, wish_ids=[1])
    c.close()

    _set_claim(1, "alice")
    c = connect()
    mine = projection.counts(c, claimer="alice")
    assert wall[1] == detail[1] == mine[1] == 2
    assert wall[2] == 1
    assert projection.counts(c, claimer="nobody") == {}
    c.close()


def test_missing_wish_is_404(db):
    c = connect()
    with pytest.raises(wish_comment.CommentError) as ei:
        wish_comment.preview(c, 999, "hi", "bob", NOW, MAX)
    assert ei.value.reason == "not_found" and ei.value.status_code == 404
    with pytest.raises(wish_comment.CommentError) as ei:
        wish_comment.get_thread(c, 999, NOW, MAX)
    assert ei.value.reason == "not_found" and ei.value.status_code == 404
    with pytest.raises(wish_comment.CommentError) as ei:
        wish_comment.add_comment(c, 999, "hi", "bob", NOW, MAX)
    assert ei.value.reason == "not_found"
    c.close()


def test_concurrent_appends_get_distinct_floors(db):
    errors = []

    def worker(i):
        try:
            c = connect()  # own connection per thread
            for j in range(5):
                wish_comment.add_comment(c, 1, f"t{i}", f"{i}-{j}", NOW, MAX)
            c.close()
        except Exception as e:  # pragma: no cover - surfaces in assertion
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert errors == []
    c = connect()
    floors = [m["floor"] for m in list_comments(c, 1)]
    c.close()
    assert sorted(floors) == list(range(1, 11))
