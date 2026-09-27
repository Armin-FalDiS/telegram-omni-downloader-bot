import sqlite3
from dataclasses import dataclass

APPROVED = "approved"
WAITING = "waiting"


@dataclass(frozen=True)
class Member:
    id: int
    name: str
    username: str | None

    @property
    def label(self) -> str:
        return f"{self.name} (@{self.username})" if self.username else self.name


class AccessList:
    def __init__(self, path: str, admin_id: int):
        self._admin_id = admin_id
        self._db = sqlite3.connect(path)
        with self._db:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS members ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL, username TEXT, status TEXT NOT NULL)"
            )

    def is_admin(self, user_id: int) -> bool:
        return user_id == self._admin_id

    def is_approved(self, user_id: int) -> bool:
        if self.is_admin(user_id):
            return True
        row = self._db.execute("SELECT status FROM members WHERE id = ?", (user_id,)).fetchone()
        return row is not None and row[0] == APPROVED

    def add_to_waitlist(self, member: Member) -> bool:
        with self._db:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO members (id, name, username, status) VALUES (?, ?, ?, ?)",
                (member.id, member.name, member.username, WAITING),
            )
        return cursor.rowcount == 1

    def waitlist(self) -> list[Member]:
        rows = self._db.execute(
            "SELECT id, name, username FROM members WHERE status = ? ORDER BY rowid", (WAITING,)
        ).fetchall()
        return [Member(*row) for row in rows]

    def approve(self, user_id: int) -> bool:
        with self._db:
            cursor = self._db.execute(
                "UPDATE members SET status = ? WHERE id = ? AND status = ?", (APPROVED, user_id, WAITING)
            )
        return cursor.rowcount == 1
