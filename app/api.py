import json
import logging
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from aiogram import Bot
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from .config import settings
from .db import connect, create_pending_user, init_db, row_dict, upsert_user, user_by_telegram_id
from .security import validate_init_data

app = FastAPI(title="Bar Mini App API")
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


def current_user(init_data: str | None):
    try:
        user = validate_init_data(init_data or "")
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    telegram_id = int(user.get("id", 0))
    full_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "Новый пользователь"
    username = str(user.get("username") or "")
    if telegram_id in settings.admin_ids:
        return upsert_user(telegram_id, full_name, "admin", username)
    existing = user_by_telegram_id(telegram_id)
    if existing:
        if username and existing["username"] != username:
            return upsert_user(telegram_id, full_name, username=username)
        return existing
    return create_pending_user(telegram_id, full_name, "", "bartender", username)


def approved_user(init_data: str | None):
    user = current_user(init_data)
    if user["status"] != "approved":
        raise HTTPException(403, "Доступ откроется после одобрения администратора")
    return user


def admin_user(init_data: str | None):
    user = approved_user(init_data)
    if user["role"] != "admin":
        raise HTTPException(403, "Доступ только для администратора")
    return user


@app.get("/api/me")
def me(x_telegram_init_data: str | None = Header(default=None)):
    return row_dict(current_user(x_telegram_init_data))


@app.post("/api/onboarding")
async def onboarding(payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    telegram_user = validate_init_data(x_telegram_init_data or "")
    telegram_id = int(telegram_user.get("id", 0))
    if telegram_id in settings.admin_ids:
        return row_dict(upsert_user(telegram_id, payload.get("full_name", "Администратор"), "admin"))
    existing = user_by_telegram_id(telegram_id)
    full_name = str(payload.get("full_name", "")).strip()
    role = payload.get("role")
    if not full_name or role not in {"waiter", "bartender", "admin"}:
        raise HTTPException(400, "Заполните ФИО и роль")
    user = create_pending_user(telegram_id, full_name, "", role, str(telegram_user.get("username") or ""))
    notification_sent = False
    if settings.bot_token and settings.admin_ids:
        bot = Bot(settings.bot_token)
        try:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Одобрить", callback_data=f"access:approved:{user['id']}"), InlineKeyboardButton(text="Отклонить", callback_data=f"access:rejected:{user['id']}")]])
            for admin_id in settings.admin_ids:
                await bot.send_message(admin_id, f"Новая заявка на доступ\nФИО: {full_name}\nРоль: {role}", reply_markup=keyboard)
            notification_sent = True
        except Exception:
            logging.exception("Не удалось уведомить администраторов о заявке пользователя %s", telegram_id)
        try:
            await bot.session.close()
        except Exception:
            logging.exception("Не удалось закрыть сессию Telegram после заявки пользователя %s", telegram_id)
    return {**row_dict(user), "notification_sent": notification_sent}


@app.get("/api/recipes")
def recipes(q: str = "", x_telegram_init_data: str | None = Header(default=None)):
    user = current_user(x_telegram_init_data)
    with connect() as db:
        active_filter = "" if user["role"] == "admin" else " AND is_active = 1"
        rows = db.execute(f"SELECT * FROM recipes WHERE (title LIKE ? OR ingredients LIKE ?){active_filter} ORDER BY sort_order, title", (f"%{q}%", f"%{q}%")).fetchall()
    result = []
    for row in rows:
        item = row_dict(row)
        item["ingredients"] = json.loads(item["ingredients"])
        result.append(item)
    return result


@app.get("/api/schedule")
def schedule(x_telegram_init_data: str | None = Header(default=None)):
    user = approved_user(x_telegram_init_data)
    with connect() as db:
        rows = db.execute("SELECT * FROM schedules WHERE user_id = ? AND work_date >= date('now', '-7 day') ORDER BY work_date", (user["id"],)).fetchall()
    return [row_dict(row) for row in rows]


@app.get("/api/calendar")
def public_calendar(month: str = "", x_telegram_init_data: str | None = Header(default=None)):
    approved_user(x_telegram_init_data)
    month = month or date.today().strftime("%Y-%m")
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(400, "Месяц должен быть в формате YYYY-MM") from exc
    with connect() as db:
        rows = db.execute("SELECT s.work_date, s.user_id, u.full_name FROM schedules s JOIN users u ON u.id = s.user_id WHERE substr(s.work_date, 1, 7) = ? AND u.is_active = 1 AND u.status = 'approved' ORDER BY s.work_date, u.full_name", (month,)).fetchall()
    return [row_dict(row) for row in rows]


@app.get("/api/staff")
def staff(x_telegram_init_data: str | None = Header(default=None)):
    approved_user(x_telegram_init_data)
    with connect() as db:
        rows = db.execute("SELECT id, telegram_id, username, full_name, role, created_at FROM users WHERE is_active = 1 AND status = 'approved' ORDER BY CASE role WHEN 'admin' THEN 1 WHEN 'waiter' THEN 2 WHEN 'bartender' THEN 3 ELSE 4 END, full_name").fetchall()
    return [row_dict(row) for row in rows]


@app.get("/api/admin/overview")
def admin_overview(x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    with connect() as db:
        recipes = db.execute("SELECT * FROM recipes ORDER BY is_active DESC, sort_order, title").fetchall()
        users = db.execute("SELECT id, telegram_id, full_name, role, is_active, created_at FROM users ORDER BY CASE role WHEN 'admin' THEN 1 WHEN 'waiter' THEN 2 WHEN 'bartender' THEN 3 ELSE 4 END, full_name").fetchall()
        requests = db.execute("SELECT r.*, u.full_name FROM day_off_requests r JOIN users u ON u.id = r.user_id ORDER BY r.created_at DESC LIMIT 50").fetchall()
        swaps = db.execute("SELECT r.*, u.full_name AS requester_name, t.full_name AS target_name FROM shift_swap_requests r JOIN users u ON u.id = r.requester_id LEFT JOIN users t ON t.id = r.target_user_id ORDER BY r.created_at DESC LIMIT 50").fetchall()
        shifts = db.execute("SELECT s.*, u.full_name FROM shifts s JOIN users u ON u.id = s.user_id ORDER BY s.created_at DESC LIMIT 50").fetchall()
        schedules = db.execute("SELECT s.*, u.full_name FROM schedules s JOIN users u ON u.id = s.user_id WHERE s.work_date >= date('now', '-7 day') ORDER BY s.work_date, u.full_name").fetchall()
    recipe_items = []
    for row in recipes:
        item = row_dict(row)
        item["ingredients"] = json.loads(item["ingredients"])
        recipe_items.append(item)
    return {"recipes": recipe_items, "users": [row_dict(row) for row in users], "requests": [row_dict(row) for row in requests], "swaps": [row_dict(row) for row in swaps], "shifts": [row_dict(row) for row in shifts], "schedules": [row_dict(row) for row in schedules]}


@app.patch("/api/admin/users/{user_id}/role")
def change_user_role(user_id: int, payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    role = payload.get("role")
    if role not in {None, "admin", "waiter", "bartender"}:
        raise HTTPException(400, "Недопустимая роль")
    with connect() as db:
        target = db.execute("SELECT telegram_id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(404, "Сотрудник не найден")
        if target["telegram_id"] in settings.admin_ids and role != "admin":
            raise HTTPException(400, "Основного администратора нельзя понизить через приложение")
        if role is None:
            db.execute("UPDATE users SET role = '', status = 'rejected', is_active = 0 WHERE id = ?", (user_id,))
        else:
            db.execute("UPDATE users SET role = ?, status = 'approved', is_active = 1 WHERE id = ?", (role, user_id))
    return {"ok": True}


@app.get("/api/admin/calendar")
def admin_calendar(month: str = "", x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    month = month or date.today().strftime("%Y-%m")
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(400, "Месяц должен быть в формате YYYY-MM") from exc
    with connect() as db:
        rows = db.execute("SELECT s.id, s.user_id, s.work_date, u.full_name FROM schedules s JOIN users u ON u.id = s.user_id WHERE substr(s.work_date, 1, 7) = ? ORDER BY s.work_date, u.full_name", (month,)).fetchall()
    return [row_dict(row) for row in rows]


@app.post("/api/admin/calendar/day")
def set_calendar_day(payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    try:
        work_date = date.fromisoformat(str(payload["work_date"]))
        user_ids = [int(value) for value in payload.get("user_ids", [])]
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(400, "Укажите дату и список сотрудников") from exc
    with connect() as db:
        db.execute("DELETE FROM schedules WHERE work_date = ?", (work_date.isoformat(),))
        db.executemany("INSERT INTO schedules(user_id, work_date, shift_type, created_at) VALUES (?, ?, 'work', ?)", [(user_id, work_date.isoformat(), datetime.utcnow().isoformat()) for user_id in user_ids])
    return {"ok": True}


@app.post("/api/admin/recipes")
def create_recipe(payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    title = str(payload.get("title", "")).strip()
    recipe_text = str(payload.get("recipe_text", "")).strip()
    if not title or not recipe_text:
        raise HTTPException(400, "Название и способ приготовления обязательны")
    ingredients = payload.get("ingredients", [])
    if isinstance(ingredients, str):
        ingredients = [line.strip() for line in ingredients.splitlines() if line.strip()]
    now = datetime.utcnow().isoformat()
    with connect() as db:
        cursor = db.execute("INSERT INTO recipes(title, category, ingredients, recipe_text, notes, photo_url, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (title, payload.get("category", "Коктейль"), json.dumps(ingredients, ensure_ascii=False), recipe_text, payload.get("notes", ""), payload.get("photo_url", ""), int(payload.get("sort_order", 0)), now, now))
    return {"id": cursor.lastrowid}


@app.put("/api/admin/recipes/{recipe_id}")
def update_recipe(recipe_id: int, payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    ingredients = payload.get("ingredients", [])
    if isinstance(ingredients, str):
        ingredients = [line.strip() for line in ingredients.splitlines() if line.strip()]
    with connect() as db:
        exists = db.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "Техкарта не найдена")
        db.execute("UPDATE recipes SET title=?, category=?, ingredients=?, recipe_text=?, notes=?, photo_url=?, sort_order=?, updated_at=? WHERE id=?", (payload.get("title", ""), payload.get("category", "Коктейль"), json.dumps(ingredients, ensure_ascii=False), payload.get("recipe_text", ""), payload.get("notes", ""), payload.get("photo_url", ""), int(payload.get("sort_order", 0)), datetime.utcnow().isoformat(), recipe_id))
    return {"ok": True}


@app.delete("/api/admin/recipes/{recipe_id}")
def delete_recipe(recipe_id: int, x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    with connect() as db:
        db.execute("UPDATE recipes SET is_active = 0, updated_at = ? WHERE id = ?", (datetime.utcnow().isoformat(), recipe_id))
    return {"ok": True}


@app.patch("/api/admin/recipes/{recipe_id}/archive")
def archive_recipe(recipe_id: int, payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    archived = bool(payload.get("archived", True))
    with connect() as db:
        exists = db.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "Техкарта не найдена")
        db.execute("UPDATE recipes SET is_active = ?, updated_at = ? WHERE id = ?", (0 if archived else 1, datetime.utcnow().isoformat(), recipe_id))
    return {"ok": True, "archived": archived}


@app.post("/api/admin/schedule")
def set_schedule(payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    try:
        user_id = int(payload["user_id"])
        work_date = date.fromisoformat(str(payload["work_date"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(400, "Укажите user_id и дату YYYY-MM-DD") from exc
    shift_type = payload.get("shift_type", "day")
    if shift_type not in {"day", "night", "off"}:
        raise HTTPException(400, "Недопустимый тип смены")
    with connect() as db:
        db.execute("INSERT INTO schedules(user_id, work_date, shift_type, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(user_id, work_date) DO UPDATE SET shift_type=excluded.shift_type", (user_id, work_date.isoformat(), shift_type, datetime.utcnow().isoformat()))
    return {"ok": True}


@app.patch("/api/admin/day-off/{request_id}")
async def process_day_off_api(request_id: int, payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    status = payload.get("status")
    if status not in {"approved", "rejected"}:
        raise HTTPException(400, "Статус должен быть approved или rejected")
    with connect() as db:
        request = db.execute("SELECT r.*, u.telegram_id FROM day_off_requests r JOIN users u ON u.id = r.user_id WHERE r.id = ?", (request_id,)).fetchone()
        if not request:
            raise HTTPException(404, "Заявка не найдена")
        db.execute("UPDATE day_off_requests SET status=?, admin_comment=?, processed_at=? WHERE id=?", (status, payload.get("admin_comment", ""), datetime.utcnow().isoformat(), request_id))
    if settings.bot_token:
        bot = Bot(settings.bot_token)
        try:
            await bot.send_message(request["telegram_id"], f"Заявка на выходной {request['request_date']} {'одобрена' if status == 'approved' else 'отклонена'}.")
        finally:
            await bot.session.close()
    return {"ok": True}


@app.patch("/api/admin/shifts/{shift_id}")
def review_shift(shift_id: int, payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    status = payload.get("status", "reviewed")
    if status not in {"submitted", "reviewed", "needs_attention"}:
        raise HTTPException(400, "Недопустимый статус отчёта")
    with connect() as db:
        db.execute("UPDATE shifts SET status=? WHERE id=?", (status, shift_id))
    return {"ok": True}


@app.post("/api/day-off")
async def day_off(request_date: str = Form(...), x_telegram_init_data: str | None = Header(default=None)):
    user = approved_user(x_telegram_init_data)
    with connect() as db:
        cursor = db.execute("INSERT INTO day_off_requests(user_id, request_date, created_at) VALUES (?, ?, ?)", (user["id"], request_date, datetime.utcnow().isoformat()))
        request_id = cursor.lastrowid
    if settings.bot_token and settings.admin_ids:
        bot = Bot(settings.bot_token)
        try:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Одобрить", callback_data=f"dayoff:approved:{request_id}"), InlineKeyboardButton(text="Отклонить", callback_data=f"dayoff:rejected:{request_id}")]])
            for admin_id in settings.admin_ids:
                await bot.send_message(admin_id, f"Заявка на выходной\nБармен: {user['full_name']}\nДата: {request_date}", reply_markup=keyboard)
        finally:
            await bot.session.close()
    return {"ok": True}


@app.post("/api/shift-swap")
async def shift_swap(request_date: str = Form(...), target_user_id: int | None = Form(default=None), comment: str = Form(""), x_telegram_init_data: str | None = Header(default=None)):
    user = approved_user(x_telegram_init_data)
    try:
        date.fromisoformat(request_date)
    except ValueError as exc:
        raise HTTPException(400, "Некорректная дата") from exc
    with connect() as db:
        cursor = db.execute("INSERT INTO shift_swap_requests(requester_id, target_user_id, request_date, comment, created_at) VALUES (?, ?, ?, ?, ?)", (user["id"], target_user_id, request_date, comment, datetime.utcnow().isoformat()))
        request_id = cursor.lastrowid
    return {"ok": True, "id": request_id}


@app.post("/api/shifts")
async def create_shift(cash_amount: float = Form(0), card_amount: float = Form(0), comment: str = Form(""), photo_types: str = Form("[]"), photos: list[UploadFile] = File(default=[]), x_telegram_init_data: str | None = Header(default=None)):
    user = approved_user(x_telegram_init_data)
    required_photo_types = {"bar_station", "sink", "tea_surface", "coffee_station", "fridge_1", "fridge_2", "mini_fridge"}
    submitted_photo_types = set(json.loads(photo_types))
    if len(photos) != len(required_photo_types) or submitted_photo_types != required_photo_types:
        raise HTTPException(400, "Нужно сделать все семь фотографий закрытия смены")
    now = datetime.utcnow().isoformat()
    saved_paths: list[tuple[str, str]] = []
    with connect() as db:
        cursor = db.execute("INSERT INTO shifts(user_id, report_date, cash_amount, card_amount, comment, created_at) VALUES (?, ?, ?, ?, ?, ?)", (user["id"], date.today().isoformat(), cash_amount, card_amount, comment, now))
        shift_id = cursor.lastrowid
        types = json.loads(photo_types)
        for index, photo in enumerate(photos):
            suffix = Path(photo.filename or "photo.jpg").suffix or ".jpg"
            target = settings.storage_path / f"shift-{shift_id}-{uuid.uuid4().hex}{suffix}"
            target.write_bytes(await photo.read())
            db.execute("INSERT INTO shift_photos(shift_id, photo_type, photo_path, created_at) VALUES (?, ?, ?, ?)", (shift_id, types[index] if index < len(types) else "other", str(target), now))
            saved_paths.append((types[index] if index < len(types) else "other", str(target)))
    if settings.bot_token and settings.admin_ids:
        bot = Bot(settings.bot_token)
        try:
            text = (f"Новый отчёт о закрытии смены\nБармен: {user['full_name']}\n"
                    f"Дата: {date.today().strftime('%d.%m.%Y')}\n"
                    f"Наличные: {cash_amount:.2f}\nБезналичные: {card_amount:.2f}\n"
                    f"Комментарий: {comment or '—'}")
            for admin_id in settings.admin_ids:
                await bot.send_message(admin_id, text)
                for photo_type, path in saved_paths:
                    await bot.send_photo(admin_id, FSInputFile(path), caption=f"{photo_type}: фото закрытия")
        finally:
            await bot.session.close()
    return {"ok": True, "shift_id": shift_id}

@app.get("/api/order-items")
def order_items(x_telegram_init_data: str | None = Header(default=None)):
    user = approved_user(x_telegram_init_data)
    if user["role"] != "bartender": return []
    with connect() as db:
        rows = db.execute("SELECT s.id AS supplier_id, s.name AS supplier_name, i.id, i.name FROM suppliers s JOIN supplier_items i ON i.supplier_id=s.id WHERE s.is_active=1 AND i.is_active=1 ORDER BY s.name, i.name").fetchall()
    return [row_dict(row) for row in rows]

@app.post("/api/orders")
async def create_order(payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    user = approved_user(x_telegram_init_data); order_type = "bar" if user["role"] == "bartender" else "hall"; comment = str(payload.get("comment", "")).strip(); items = payload.get("items", []) if order_type == "bar" else []
    if order_type == "hall" and not comment: raise HTTPException(400, "Заполните заказ")
    if order_type == "bar":
        items = [{"name": str(i.get("name", "")), "quantity": int(i.get("quantity", 0)), "supplier": str(i.get("supplier", ""))} for i in items if int(i.get("quantity", 0)) > 0]
        if not items: raise HTTPException(400, "Добавьте хотя бы одну позицию")
    with connect() as db: cursor = db.execute("INSERT INTO orders(user_id, order_type, comment, items, created_at) VALUES (?, ?, ?, ?, ?)", (user["id"], order_type, comment, json.dumps(items, ensure_ascii=False), datetime.utcnow().isoformat()))
    if settings.bot_token and settings.admin_ids:
        bot = Bot(settings.bot_token)
        try:
            if order_type == "hall":
                messages = [f"Заказ ЗАЛ\nОт: {user['full_name']}\n\n{comment}"]
            else:
                grouped = {}
                for item in items: grouped.setdefault(item["supplier"], []).append(item)
                messages = [f"Заказ БАР — {supplier}\nОт: {user['full_name']}\n\n" + "\n".join(f"• {item['name']}: {item['quantity']}" for item in supplier_items) for supplier, supplier_items in grouped.items()]
            for admin_id in settings.admin_ids:
                for text in messages: await bot.send_message(admin_id, text)
        finally: await bot.session.close()
    return {"ok": True, "id": cursor.lastrowid}

@app.get("/api/admin/suppliers")
def admin_suppliers(x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    with connect() as db:
        suppliers = [row_dict(r) for r in db.execute("SELECT * FROM suppliers ORDER BY name").fetchall()]; items = [row_dict(r) for r in db.execute("SELECT * FROM supplier_items ORDER BY name").fetchall()]
    return {"suppliers": suppliers, "items": items}

@app.post("/api/admin/suppliers")
def add_supplier(payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data); name = str(payload.get("name", "")).strip()
    if not name: raise HTTPException(400, "Укажите поставщика")
    with connect() as db: cursor = db.execute("INSERT INTO suppliers(name, created_at) VALUES (?, ?)", (name, datetime.utcnow().isoformat()))
    return {"id": cursor.lastrowid}

@app.post("/api/admin/supplier-items")
def add_supplier_item(payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data); name = str(payload.get("name", "")).strip(); supplier_id = int(payload.get("supplier_id", 0))
    if not name or not supplier_id: raise HTTPException(400, "Укажите поставщика и позицию")
    with connect() as db: cursor = db.execute("INSERT INTO supplier_items(supplier_id, name, created_at) VALUES (?, ?, ?)", (supplier_id, name, datetime.utcnow().isoformat()))
    return {"id": cursor.lastrowid}

@app.patch("/api/admin/suppliers/{supplier_id}")
def update_supplier(supplier_id: int, payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data); active = int(bool(payload.get("is_active", True)))
    with connect() as db:
        current = db.execute("SELECT name FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
        if not current: raise HTTPException(404, "Категория не найдена")
        name = str(payload.get("name") or current["name"]).strip()
        db.execute("UPDATE suppliers SET name = ?, is_active = ? WHERE id = ?", (name, active, supplier_id))
    return {"ok": True}

@app.delete("/api/admin/suppliers/{supplier_id}")
def delete_supplier(supplier_id: int, x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    with connect() as db: db.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))
    return {"ok": True}

@app.patch("/api/admin/supplier-items/{item_id}")
def update_supplier_item(item_id: int, payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data); active = int(bool(payload.get("is_active", True)))
    with connect() as db:
        current = db.execute("SELECT name FROM supplier_items WHERE id = ?", (item_id,)).fetchone()
        if not current: raise HTTPException(404, "Позиция не найдена")
        name = str(payload.get("name") or current["name"]).strip()
        db.execute("UPDATE supplier_items SET name = ?, is_active = ? WHERE id = ?", (name, active, item_id))
    return {"ok": True}

@app.delete("/api/admin/supplier-items/{item_id}")
def delete_supplier_item(item_id: int, x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    with connect() as db: db.execute("DELETE FROM supplier_items WHERE id = ?", (item_id,))
    return {"ok": True}
