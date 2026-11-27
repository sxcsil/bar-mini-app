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
                status TEXT NOT NULL DEFAULT 'approved',
                phone TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT '',
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
            CREATE TABLE IF NOT EXISTS shift_swap_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_id INTEGER NOT NULL REFERENCES users(id),
                target_user_id INTEGER REFERENCES users(id),
                request_date TEXT NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                processed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS suppliers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS supplier_items (id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE, name TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), order_type TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '', items TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS shift_hour_confirmations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), work_date TEXT NOT NULL, hours REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, processed_at TEXT, UNIQUE(user_id, work_date));
            CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reservations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), reservation_date TEXT NOT NULL, reservation_time TEXT NOT NULL, guest_name TEXT NOT NULL, guest_count INTEGER, phone TEXT NOT NULL DEFAULT '', table_name TEXT NOT NULL DEFAULT '', service_fee INTEGER, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS user_action_limits (user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, action TEXT NOT NULL, last_at TEXT NOT NULL, PRIMARY KEY(user_id, action));
            CREATE TABLE IF NOT EXISTS shift_hour_reminders (user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, work_date TEXT NOT NULL, sent_at TEXT NOT NULL, PRIMARY KEY(user_id, work_date));
            """
        )
        day_off_columns = {row[1] for row in db.execute("PRAGMA table_info(day_off_requests)")}
        if "request_end_date" not in day_off_columns:
            db.execute("ALTER TABLE day_off_requests ADD COLUMN request_end_date TEXT")
        if "request_end_date" not in day_off_columns:
            db.execute("UPDATE day_off_requests SET request_end_date = request_date WHERE request_end_date IS NULL")
        columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        if "status" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'")
        if "phone" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
        if "username" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN username TEXT NOT NULL DEFAULT ''")
        imported_marker = db.execute("SELECT value FROM app_settings WHERE key = 'spreadsheet_recipes_imported_v1'").fetchone()
        if not imported_marker:
            # Seed the spreadsheet only on a fresh database. Existing recipe
            # state (including archived and permanently deleted entries) wins.
            if db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 0:
                import_file = Path(__file__).with_name("recipe_import.json")
                if import_file.exists():
                    try:
                        imported = json.loads(import_file.read_text(encoding="utf-8"))
                        now = datetime.utcnow().isoformat()
                        for recipe in imported:
                            db.execute("INSERT INTO recipes(title, category, ingredients, recipe_text, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (recipe["title"], recipe["category"], json.dumps(recipe["ingredients"], ensure_ascii=False), recipe.get("recipe_text", ""), recipe.get("notes", ""), now, now))
                    except (OSError, ValueError, KeyError):
                        pass
            db.execute("INSERT INTO app_settings(key, value) VALUES ('spreadsheet_recipes_imported_v1', '1')")
        if db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 0:
            now = datetime.utcnow().isoformat()
            demo = [
                ("Негрони", "Коктейль", json.dumps(["Джин — 30 мл", "Кампари — 30 мл", "Красный вермут — 30 мл"]), "Смешать со льдом в рокс-стакане. Украсить апельсином.", "", now, now),
                ("Маргарита", "Коктейль", json.dumps(["Текила — 50 мл", "Трипл-сек — 25 мл", "Лайм — 25 мл", "Сироп агавы — 10 мл"]), "Шейк со льдом. Подать в бокале с соляным ободком.", "", now, now),
            ]
            db.executemany("INSERT INTO recipes(title, category, ingredients, recipe_text, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", demo)


def claim_action(user_id: int, action: str, cooldown_seconds: int) -> bool:
    """Atomically reserve a per-user action slot; safe across concurrent requests."""
    now = datetime.utcnow()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT last_at FROM user_action_limits WHERE user_id=? AND action=?", (user_id, action)).fetchone()
        if row:
            try:
                elapsed = (now - datetime.fromisoformat(row["last_at"])).total_seconds()
            except ValueError:
                elapsed = cooldown_seconds
            if elapsed < cooldown_seconds:
                return False
            db.execute("UPDATE user_action_limits SET last_at=? WHERE user_id=? AND action=?", (now.isoformat(), user_id, action))
        else:
            db.execute("INSERT INTO user_action_limits(user_id, action, last_at) VALUES (?, ?, ?)", (user_id, action, now.isoformat()))
        return True


def upsert_user(telegram_id: int, full_name: str, role: str | None = None, username: str = "") -> sqlite3.Row:
    with connect() as db:
        existing = db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if existing:
            if role:
                db.execute("UPDATE users SET full_name = ?, role = ?, username = ?, status = 'approved', is_active = 1 WHERE telegram_id = ?", (full_name, role, username, telegram_id))
            else:
                db.execute("UPDATE users SET full_name = ?, username = ? WHERE telegram_id = ?", (full_name, username, telegram_id))
        else:
            db.execute("INSERT INTO users(telegram_id, full_name, role, username, created_at) VALUES (?, ?, ?, ?, ?)", (telegram_id, full_name, role or "bartender", username, datetime.utcnow().isoformat()))
        return db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()


def create_pending_user(telegram_id: int, full_name: str, phone: str, role: str, username: str = "") -> sqlite3.Row:
    with connect() as db:
        db.execute(
            "INSERT INTO users(telegram_id, full_name, phone, role, username, status, is_active, created_at) VALUES (?, ?, ?, ?, ?, 'pending', 1, ?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET full_name=excluded.full_name, phone=excluded.phone, role=excluded.role, username=excluded.username, status='pending', is_active=1, created_at=excluded.created_at",
            (telegram_id, full_name, phone, role, username, datetime.utcnow().isoformat()),
        )
        return db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()


def user_by_telegram_id(telegram_id: int) -> sqlite3.Row | None:
    with connect() as db:
        return db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)
