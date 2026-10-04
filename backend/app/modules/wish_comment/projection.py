"""Read projections: the single source of floor counts for all three surfaces."""
from app.modules.wish_comment.gate import writable
from app.modules.wish_comment.store import list_comments


def counts_by_wish(c, wish_ids: list[int] | None = None) -> dict[int, dict]:
    """Map wish_id -> {count, max_floor} via one GROUP BY query.

    Wall card badge, Mine annotation and detail thread all read from here.
    """
    if wish_ids is not None:
        if not wish_ids:
            return {}
        marks = ",".join("?" for _ in wish_ids)
        rows = c.execute(
            f"SELECT wish_id, COUNT(*) AS count, MAX(floor) AS max_floor "
            f"FROM comments WHERE wish_id IN ({marks}) GROUP BY wish_id",
            list(wish_ids),
        )
    else:
        rows = c.execute(
            "SELECT wish_id, COUNT(*) AS count, MAX(floor) AS max_floor "
            "FROM comments GROUP BY wish_id"
        )
    return {r["wish_id"]: {"count": r["count"], "max_floor": r["max_floor"]} for r in rows}


def thread(c, wish_id: int, status: str) -> dict:
    comments = list_comments(c, wish_id)
    return {
        "wish_id": wish_id,
        "status": status,
        "writable": writable(status),
        "count": len(comments),
        "comments": comments,
    }
