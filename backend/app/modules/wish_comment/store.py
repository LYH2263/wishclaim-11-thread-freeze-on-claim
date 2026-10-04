"""Persistence for wish comment floors. Caller owns the connection/transaction."""


def add_comment(c, wish_id: int, author: str, content: str, now_iso: str) -> dict:
    """Append a floor. Floor numbers are per-wish monotonic and never reused after deletion."""
    row = c.execute(
        "SELECT COALESCE(MAX(floor), 0) + 1 AS next_floor FROM comments WHERE wish_id=?",
        (wish_id,),
    ).fetchone()
    floor = row["next_floor"]
    cur = c.execute(
        "INSERT INTO comments(wish_id, floor, author, content, created_at) VALUES (?,?,?,?,?)",
        (wish_id, floor, author, content, now_iso),
    )
    return {
        "id": cur.lastrowid,
        "wish_id": wish_id,
        "floor": floor,
        "author": author,
        "content": content,
        "created_at": now_iso,
    }


def delete_comment(c, wish_id: int, comment_id: int, author: str) -> bool:
    """Hard-delete one's own floor in any status. False if missing or not the author."""
    cur = c.execute(
        "DELETE FROM comments WHERE id=? AND wish_id=? AND author=?",
        (comment_id, wish_id, author),
    )
    return cur.rowcount == 1


def list_comments(c, wish_id: int) -> list[dict]:
    return [
        dict(r)
        for r in c.execute(
            "SELECT id, wish_id, floor, author, content, created_at "
            "FROM comments WHERE wish_id=? ORDER BY floor ASC",
            (wish_id,),
        )
    ]
