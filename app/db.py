import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import settings


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                full_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'bartender',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'Коктейль',
                ingredients TEXT NOT NULL DEFAULT '[]',
                recipe_text TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                photo_url TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                report_date TEXT NOT NULL,
                cash_amount REAL NOT NULL DEFAULT 0,
                card_amount REAL NOT NULL DEFAULT 0,
                comment TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'submitted',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shift_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
                photo_type TEXT NOT NULL,
                photo_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                work_date TEXT NOT NULL,
                shift_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(user_id, work_date)
            );
            CREATE TABLE IF NOT EXISTS day_off_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                request_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                admin_comment TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                processed_at TEXT
            );
            """
        )
        if db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 0:
            now = datetime.utcnow().isoformat()
            demo = [
                ("Негрони", "Коктейль", json.dumps(["Джин — 30 мл", "Кампари — 30 мл", "Красный вермут — 30 мл"]), "Смешать со льдом в рокс-стакане. Украсить апельсином.", "", now, now),
                ("Маргарита", "Коктейль", json.dumps(["Текила — 50 мл", "Трипл-сек — 25 мл", "Лайм — 25 мл", "Сироп агавы — 10 мл"]), "Шейк со льдом. Подать в бокале с соляным ободком.", "", now, now),
            ]
            db.executemany("INSERT INTO recipes(title, category, ingredients, recipe_text, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", demo)


def upsert_user(telegram_id: int, full_name: str, role: str | None = None) -> sqlite3.Row:
    with connect() as db:
        existing = db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if existing:
            if role:
                db.execute("UPDATE users SET full_name = ?, role = ? WHERE telegram_id = ?", (full_name, role, telegram_id))
            else:
                db.execute("UPDATE users SET full_name = ? WHERE telegram_id = ?", (full_name, telegram_id))
        else:
            db.execute("INSERT INTO users(telegram_id, full_name, role, created_at) VALUES (?, ?, ?, ?)", (telegram_id, full_name, role or "bartender", datetime.utcnow().isoformat()))
        return db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()


def user_by_telegram_id(telegram_id: int) -> sqlite3.Row | None:
    with connect() as db:
        return db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)
