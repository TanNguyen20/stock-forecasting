"""Lưu trữ SQLite: giá lịch sử, danh mục theo dõi, cảnh báo, nhật ký cảnh báo."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .market import now_vn
from .models import Alert, AlertKind, Channel, Quote

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL, high REAL, low REAL, close REAL NOT NULL, volume REAL,
    source TEXT,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS quotes (
    symbol TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    price REAL NOT NULL,
    ref_price REAL NOT NULL,
    volume REAL,
    avg_volume20 REAL,
    updated_at TEXT NOT NULL,
    source TEXT
);
CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS symbols (
    symbol TEXT PRIMARY KEY,
    exchange TEXT,
    type TEXT,
    name TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    threshold REAL NOT NULL,
    channels TEXT NOT NULL,
    note TEXT DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    armed INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_triggered_at TEXT
);
CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER,
    symbol TEXT NOT NULL,
    message TEXT NOT NULL,
    value REAL,
    channels TEXT,
    status TEXT,
    triggered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_log_time ON alert_log (triggered_at DESC);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    endpoint TEXT PRIMARY KEY,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    user_agent TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
"""

PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Repository:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        # Mỗi thao tác một kết nối: an toàn khi Streamlit và worker cùng truy cập.
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------- Khóa-giá trị (trạng thái worker...) ----------
    def get_meta(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def setdefault_meta(self, key: str, value: str) -> str:
        """Ghi `value` nếu khóa chưa có; trả về giá trị đang lưu (an toàn khi nhiều luồng cùng ghi)."""
        with self._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)", (key, value))
            return conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()[0]

    # ---------- Giá lịch sử ----------
    def upsert_prices(self, symbol: str, df: pd.DataFrame, source: str) -> int:
        """df: index là ngày (DatetimeIndex), cột open/high/low/close/volume."""
        if df.empty:
            return 0
        rows = [
            (symbol, idx.strftime("%Y-%m-%d"), r.open, r.high, r.low, r.close, r.volume, source)
            for idx, r in df[PRICE_COLUMNS].iterrows()
        ]
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO prices (symbol, date, open, high, low, close, volume, source)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def replace_prices(self, symbol: str, df: pd.DataFrame, source: str) -> int:
        with self._conn() as conn:
            conn.execute("DELETE FROM prices WHERE symbol = ?", (symbol,))
        return self.upsert_prices(symbol, df, source)

    def get_prices(self, symbol: str, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        sql = "SELECT date, open, high, low, close, volume FROM prices WHERE symbol = ?"
        params: list = [symbol]
        if start:
            sql += " AND date >= ?"
            params.append(start.isoformat())
        if end:
            sql += " AND date <= ?"
            params.append(end.isoformat())
        with self._conn() as conn:
            df = pd.read_sql_query(sql + " ORDER BY date", conn, params=params, parse_dates=["date"])
        return df.set_index("date")

    def price_range(self, symbol: str) -> tuple[date | None, date | None, int]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MIN(date), MAX(date), COUNT(*) FROM prices WHERE symbol = ?", (symbol,)
            ).fetchone()
        to_date = lambda s: date.fromisoformat(s) if s else None  # noqa: E731
        return to_date(row[0]), to_date(row[1]), row[2]

    def stored_symbols(self) -> list[str]:
        with self._conn() as conn:
            return [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM prices ORDER BY symbol")]

    # ---------- Giá hiện tại ----------
    def upsert_quote(self, q: Quote) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO quotes (symbol, date, price, ref_price, volume, avg_volume20, updated_at, source)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (q.symbol, q.date.isoformat(), q.price, q.ref_price, q.volume, q.avg_volume20,
                 q.updated_at.isoformat(), q.source),
            )

    def get_quotes(self, symbols: Iterable[str] | None = None) -> dict[str, Quote]:
        sql, params = "SELECT * FROM quotes", []
        symbols = list(symbols) if symbols is not None else None
        if symbols is not None:
            if not symbols:
                return {}
            sql += f" WHERE symbol IN ({','.join('?' * len(symbols))})"
            params = symbols
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {
            r["symbol"]: Quote(
                symbol=r["symbol"], date=date.fromisoformat(r["date"]), price=r["price"],
                ref_price=r["ref_price"], volume=r["volume"] or 0, avg_volume20=r["avg_volume20"] or 0,
                updated_at=datetime.fromisoformat(r["updated_at"]), source=r["source"] or "",
            )
            for r in rows
        }

    # ---------- Danh mục theo dõi ----------
    def watchlist(self) -> list[str]:
        with self._conn() as conn:
            return [r[0] for r in conn.execute("SELECT symbol FROM watchlist ORDER BY symbol")]

    def add_watch(self, symbol: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist (symbol, added_at) VALUES (?, ?)",
                (symbol.upper(), now_vn().isoformat()),
            )

    def remove_watch(self, symbol: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))

    # ---------- Danh sách mã ----------
    def upsert_symbols(self, df: pd.DataFrame) -> int:
        rows = list(df[["symbol", "exchange", "type", "name"]].itertuples(index=False, name=None))
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO symbols (symbol, exchange, type, name) VALUES (?, ?, ?, ?)", rows
            )
        return len(rows)

    def get_symbols(self) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql_query("SELECT * FROM symbols ORDER BY symbol", conn)

    # ---------- Cảnh báo ----------
    @staticmethod
    def _row_to_alert(r: sqlite3.Row) -> Alert:
        return Alert(
            id=r["id"], symbol=r["symbol"], kind=AlertKind(r["kind"]), threshold=r["threshold"],
            channels=tuple(Channel(c) for c in r["channels"].split(",") if c),
            note=r["note"] or "", active=bool(r["active"]), armed=bool(r["armed"]),
            created_at=_dt(r["created_at"]), last_triggered_at=_dt(r["last_triggered_at"]),
        )

    def add_alert(self, alert: Alert) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO alerts (symbol, kind, threshold, channels, note, active, armed, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                (alert.symbol.upper(), alert.kind.value, alert.threshold,
                 ",".join(c.value for c in alert.channels), alert.note, int(alert.active),
                 now_vn().isoformat()),
            )
            return int(cur.lastrowid)

    def list_alerts(self, active_only: bool = False) -> list[Alert]:
        sql = "SELECT * FROM alerts" + (" WHERE active = 1" if active_only else "") + " ORDER BY symbol, id"
        with self._conn() as conn:
            return [self._row_to_alert(r) for r in conn.execute(sql)]

    def set_alert_active(self, alert_id: int, active: bool) -> None:
        with self._conn() as conn:
            # Bật lại thì cũng nạp lại (armed) để có thể bắn lần nữa.
            conn.execute("UPDATE alerts SET active = ?, armed = 1 WHERE id = ?", (int(active), alert_id))

    def set_alert_armed(self, alert_id: int, armed: bool, triggered_at: datetime | None = None) -> None:
        with self._conn() as conn:
            if triggered_at:
                conn.execute(
                    "UPDATE alerts SET armed = ?, last_triggered_at = ? WHERE id = ?",
                    (int(armed), triggered_at.isoformat(), alert_id),
                )
            else:
                conn.execute("UPDATE alerts SET armed = ? WHERE id = ?", (int(armed), alert_id))

    def delete_alert(self, alert_id: int) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))

    # ---------- Đăng ký Web Push ----------
    def add_push_subscription(self, endpoint: str, p256dh: str, auth: str, user_agent: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent, created_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(endpoint) DO UPDATE SET"
                " p256dh = excluded.p256dh, auth = excluded.auth, user_agent = excluded.user_agent",
                (endpoint, p256dh, auth, user_agent, now_vn().isoformat()),
            )

    def remove_push_subscription(self, endpoint: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))

    def push_subscriptions(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM push_subscriptions ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def log_alert(self, alert_id: int | None, symbol: str, message: str, value: float | None,
                  channels: str, status: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO alert_log (alert_id, symbol, message, value, channels, status, triggered_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (alert_id, symbol, message, value, channels, status, now_vn().isoformat()),
            )

    def alert_log(self, limit: int = 200) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql_query(
                "SELECT triggered_at, symbol, message, channels, status FROM alert_log"
                " ORDER BY id DESC LIMIT ?",
                conn, params=[limit],
            )

