"""
Простое хранилище отслеживаемых товаров на SQLite.
Каждый пользователь Telegram видит только свои товары.
"""
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Optional

DB_PATH = "tracked_items.db"


def init_db(db_path: str = DB_PATH) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                name TEXT,
                last_price REAL,
                last_checked TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                price REAL,
                checked_at TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE
            )
            """
        )
        conn.commit()


def add_history_entry(item_id: int, price: Optional[float], db_path: str = DB_PATH) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO price_history (item_id, price, checked_at) VALUES (?, ?, ?)",
            (item_id, price, datetime.utcnow().isoformat()),
        )
        conn.commit()


def get_history(item_id: int, db_path: str = DB_PATH, limit: int = 30) -> list[sqlite3.Row]:
    """Последние записи истории цены, от новых к старым."""
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM price_history WHERE item_id = ? "
            "ORDER BY checked_at DESC LIMIT ?",
            (item_id, limit),
        )
        return cur.fetchall()


def add_item(user_id: int, url: str, name: Optional[str] = None,
             price: Optional[float] = None, db_path: str = DB_PATH) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        cur = conn.execute(
            "INSERT INTO items (user_id, url, name, last_price, last_checked) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, url, name, price, datetime.utcnow().isoformat()),
        )
        conn.commit()
        item_id = cur.lastrowid
    add_history_entry(item_id, price, db_path=db_path)
    return item_id


def list_items(user_id: int, db_path: str = DB_PATH) -> list[sqlite3.Row]:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM items WHERE user_id = ? ORDER BY id", (user_id,)
        )
        return cur.fetchall()


def get_item(item_id: int, user_id: int, db_path: str = DB_PATH) -> Optional[sqlite3.Row]:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM items WHERE id = ? AND user_id = ?", (item_id, user_id)
        )
        return cur.fetchone()


def update_price(item_id: int, price: Optional[float], db_path: str = DB_PATH) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "UPDATE items SET last_price = ?, last_checked = ? WHERE id = ?",
            (price, datetime.utcnow().isoformat(), item_id),
        )
        conn.commit()
    add_history_entry(item_id, price, db_path=db_path)


def remove_item(item_id: int, user_id: int, db_path: str = DB_PATH) -> bool:
    with closing(sqlite3.connect(db_path)) as conn:
        cur = conn.execute(
            "DELETE FROM items WHERE id = ? AND user_id = ?", (item_id, user_id)
        )
        conn.commit()
        return cur.rowcount > 0


def all_items(db_path: str = DB_PATH) -> list[sqlite3.Row]:
    """Все товары всех пользователей — нужно для ежедневного обновления."""
    with closing(sqlite3.connect(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM items ORDER BY id")
        return cur.fetchall()
