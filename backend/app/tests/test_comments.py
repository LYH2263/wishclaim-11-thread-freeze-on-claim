import pytest

from app import seed
from app.db import connect
from app.modules import wish_comment as wc
from app.modules.wish_comment import gate
from app import main


# ---------- pure gate ----------

def test_writable_statuses():
    assert gate.writable("open") and gate.writable("released")
    assert not gate.writable("claimed") and not gate.writable("fulfilled")


def test_block_reason_archive_beats_freeze():
    assert gate.block_reason("claimed") == "frozen"
    assert gate.block_reason("fulfilled") == "archived"
    assert gate.block_reason("open") is None


@pytest.mark.parametrize("author,content", [("", "x"), ("   ", "x"), ("a", ""), ("a", "   \n  ")])
def test_validate_rejects_empty(author, content):
    errors = gate.validate(author, content)
    assert errors


def test_validate_length_limits():
    assert "author_too_long" in gate.validate("a" * 51, "x")
    assert "content_too_long" in gate.validate("a", "x" * 501)
    assert gate.validate("小林", "你好") == []


# ---------- store + projection on a temp DB ----------

@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()
    yield


def _add(wish_id, author, content, ts="2026-02-01T00:00:00+00:00"):
    c = connect()
    out = wc.add_comment(c, wish_id, author, content, ts)
    c.commit(); c.close()
    return out


def test_floor_numbers_monotonic_and_not_reused(db):
    f1 = _add(3, "a", "one")
    f2 = _add(3, "a", "two")
    f3 = _add(3, "b", "three")
    assert [f1["floor"], f2["floor"], f3["floor"]] == [1, 2, 3]
    c = connect()
    assert wc.delete_comment(c, 3, f2["id"], "a")
    c.commit(); c.close()
    f4 = _add(3, "c", "four")
    assert f4["floor"] == 4  # floor 2 is not reused
    c = connect()
    assert wc.counts_by_wish(c)[3] == {"count": 3, "max_floor": 4}
    c.close()


def test_delete_rejects_other_author(db):
    f = _add(3, "alice", "mine")
    c = connect()
    assert not wc.delete_comment(c, 3, f["id"], "bob")
    c.close()


def test_projection_count_matches_thread(db):
    _add(3, "a", "x"); _add(3, "b", "y")
    c = connect()
    t = wc.thread(c, 3, "open")
    counts = wc.counts_by_wish(c)
    c.close()
    assert t["count"] == 2 and t["writable"] is True
    assert counts[3]["count"] == t["count"]
    assert [m["floor"] for m in t["comments"]] == [1, 2]


# ---------- end-to-end via route functions ----------

def _new_wish(title="测试愿望"):
    return main.create_wish(main.WishIn(title=title, note=""))["id"]


def test_precheck_does_not_write(db):
    wid = _new_wish()
    r = main.precheck_comment(wid, main.CommentIn(author="  ", content=""))
    assert r["ok"] is False and "content_empty" in r["errors"]
    rows = main.list_comments(wid)
    assert rows["count"] == 0


def test_append_freeze_unfreeze_archive_flow(db):
    wid = _new_wish()
    main.add_comment(wid, main.CommentIn(author="alice", content="open 可写"))

    main.claim(wid, main.ClaimIn(claimer="alice"))
    with pytest.raises(Exception) as frozen:
        main.add_comment(wid, main.CommentIn(author="bob", content="冻结期应拒"))
    assert frozen.value.status_code == 409 and frozen.value.detail == "frozen"

    main.release(wid)
    main.add_comment(wid, main.CommentIn(author="bob", content="释放后解冻"))

    main.claim(wid, main.ClaimIn(claimer="alice"))
    main.fulfill(wid)
    with pytest.raises(Exception) as archived:
        main.add_comment(wid, main.CommentIn(author="bob", content="归档后不得再写"))
    assert archived.value.status_code == 409 and archived.value.detail == "archived"


def test_empty_content_422_without_write(db):
    wid = _new_wish()
    with pytest.raises(Exception) as exc:
        main.add_comment(wid, main.CommentIn(author="alice", content="   "))
    assert exc.value.status_code == 422
    assert main.list_comments(wid)["count"] == 0


def test_author_delete_in_any_status_and_three_way_pin(db):
    wid = _new_wish()
    m1 = main.add_comment(wid, main.CommentIn(author="alice", content="first"))
    m2 = main.add_comment(wid, main.CommentIn(author="bob", content="second"))

    # non-author cannot delete, even while open
    with pytest.raises(Exception) as denied:
        main.delete_comment(wid, m1["id"], author="bob")
    assert denied.value.status_code == 403

    # claim then fulfill: author can still delete own floor while archived
    main.claim(wid, main.ClaimIn(claimer="alice"))
    main.fulfill(wid)
    main.delete_comment(wid, m2["id"], author="bob")

    # three surfaces pinned to the same count
    thread = main.list_comments(wid)
    wall = [r for r in main.list_wishes() if r["id"] == wid][0]
    # alice claimed and fulfilled, so mine(alice) carries this wish
    mine = [r for r in main.mine(claimer="alice") if r["id"] == wid][0]
    assert thread["count"] == wall["comment_count"] == mine["comment_count"] == 1


def test_ttl_release_unfreezes_thread(db):
    # seeded wish id=4 is claimed by "ghost" with a 2020 expiry
    t = main.list_comments(4)  # sweep runs inside and releases the expired lock
    assert t["writable"] is True and t["status"] == "open"
