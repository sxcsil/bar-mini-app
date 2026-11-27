import json
import logging
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from .config import settings
from .db import claim_action, connect, create_pending_user, init_db, row_dict, upsert_user, user_by_telegram_id
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


def telegram_bot() -> Bot:
    session = AiohttpSession(proxy=settings.bot_proxy) if settings.bot_proxy else AiohttpSession()
    return Bot(settings.bot_token, session=session)


def format_reservation_notice(booking_date: date, booking_time: str, guest_name: str, guest_count: int | None, phone: str, table_name: str, service_fee: int | None, birthday_discount: bool = False) -> str:
    lines = [f"Бронь на {booking_date.strftime('%d.%m.%Y')} в {booking_time}", f"Имя: {guest_name}"]
    if guest_count is not None:
        lines.append(f"Гостей: {guest_count}")
    if phone:
        lines.append(f"Телефон: {phone}")
    if table_name:
        lines.append(f"Стол: {table_name}")
    if service_fee:
        lines.append("Сервисный сбор 10%")
    if birthday_discount:
        lines.append("Скидка на день рождения: 10%")
    return "\n".join(lines)


def reservation_bool(value: object) -> int:
    return int(value is True or str(value).lower() in {"1", "true", "yes", "да", "on"})


def reservation_daily_text(booking_date: date, bookings) -> str:
    if not bookings:
        return f"На {booking_date.strftime('%d.%m.%Y')} броней нет."
    lines = [f"Брони на {booking_date.strftime('%d.%m.%Y')}", ""]
    for booking in bookings:
        lines.append(format_reservation_notice(booking_date, booking["reservation_time"], booking["guest_name"], booking["guest_count"], booking["phone"], booking["table_name"], booking["service_fee"], bool(booking["birthday_discount"])))
        lines.append("")
    return "\n".join(lines).strip()


def reservation_calendar(month: str, x_telegram_init_data: str | None):
    approved_user(x_telegram_init_data)
    month = month or date.today().strftime("%Y-%m")
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(400, "Месяц должен быть в формате YYYY-MM") from exc
    with connect() as db:
        rows = db.execute("SELECT r.*, u.full_name AS created_by FROM reservations r JOIN users u ON u.id=r.user_id WHERE substr(r.reservation_date,1,7)=? ORDER BY r.reservation_date,r.reservation_time,r.id", (month,)).fetchall()
    return [row_dict(row) for row in rows]


@app.get("/api/reservations")
def get_reservations(month: str = "", x_telegram_init_data: str | None = Header(default=None)):
    return reservation_calendar(month, x_telegram_init_data)


@app.post("/api/reservations")
async def add_reservation(payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    user = approved_user(x_telegram_init_data)
    try:
        booking_date = date.fromisoformat(str(payload.get("reservation_date", "")))
        booking_time = datetime.strptime(str(payload.get("reservation_time", "")), "%H:%M").time()
    except ValueError as exc:
        raise HTTPException(400, "Укажите дату и время брони") from exc
    guest_name = str(payload.get("guest_name", "")).strip()
    if not guest_name:
        raise HTTPException(400, "Укажите имя")
    if not claim_action(user["id"], "reservation", 5):
        raise HTTPException(429, "Подождите 5 секунд перед добавлением брони")
    guest_count = payload.get("guest_count")
    try:
        guest_count = int(guest_count) if guest_count not in (None, "") else None
        if guest_count is not None and guest_count < 1:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Количество гостей должно быть положительным числом") from exc
    fee = reservation_bool(payload.get("service_fee"))
    birthday_discount = reservation_bool(payload.get("birthday_discount"))
    with connect() as db:
        cursor = db.execute("INSERT INTO reservations(user_id,reservation_date,reservation_time,guest_name,guest_count,phone,table_name,service_fee,birthday_discount,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (user["id"], booking_date.isoformat(), booking_time.strftime("%H:%M"), guest_name, guest_count, str(payload.get("phone", "")).strip(), str(payload.get("table_name", "")).strip(), fee, birthday_discount, datetime.utcnow().isoformat()))
        recipients = db.execute("SELECT telegram_id FROM users WHERE is_active=1 AND status='approved' ORDER BY id").fetchall()
    notification_sent = 0
    if settings.bot_token:
        bot = telegram_bot()
        text = "Новая бронь добавлена.\n" + format_reservation_notice(booking_date, booking_time.strftime("%H:%M"), guest_name, guest_count, str(payload.get("phone", "")).strip(), str(payload.get("table_name", "")).strip(), fee, bool(birthday_discount))
        try:
            for recipient in recipients:
                try:
                    await bot.send_message(recipient["telegram_id"], text)
                    notification_sent += 1
                except Exception:
                    logging.exception("Не удалось отправить уведомление о брони пользователю %s (бронь %s)", recipient["telegram_id"], cursor.lastrowid)
        finally:
            await bot.session.close()
    else:
        logging.error("Бронь %s сохранена, но уведомления не отправлены: BOT_TOKEN не настроен", cursor.lastrowid)
    return {"ok": True, "id": cursor.lastrowid, "notification_sent": notification_sent}


@app.post("/api/reservations/daily-copy")
async def send_reservation_daily_copy(payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    user = approved_user(x_telegram_init_data)
    try:
        booking_date = date.fromisoformat(str(payload.get("reservation_date", "")))
    except ValueError as exc:
        raise HTTPException(400, "Укажите дату броней") from exc
    if not settings.bot_token:
        raise HTTPException(503, "Отправка в Telegram не настроена")
    with connect() as db:
        bookings = db.execute("SELECT reservation_time,guest_name,guest_count,phone,table_name,service_fee,birthday_discount FROM reservations WHERE reservation_date=? ORDER BY reservation_time,id", (booking_date.isoformat(),)).fetchall()
    bot = telegram_bot()
    try:
        sent = await bot.send_message(user["telegram_id"], reservation_daily_text(booking_date, bookings))
        return {"ok": True, "message_id": sent.message_id, "count": len(bookings)}
    except Exception as exc:
        logging.exception("Не удалось отправить дневную копию броней пользователю %s", user["telegram_id"])
        raise HTTPException(502, "Не удалось отправить сообщение в Telegram") from exc
    finally:
        await bot.session.close()


@app.put("/api/reservations/{reservation_id}")
async def update_reservation(reservation_id: int, payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    approved_user(x_telegram_init_data)
    try:
        booking_date = date.fromisoformat(str(payload.get("reservation_date", "")))
        booking_time = datetime.strptime(str(payload.get("reservation_time", "")), "%H:%M").time()
    except ValueError as exc:
        raise HTTPException(400, "Укажите дату и время брони") from exc
    guest_name = str(payload.get("guest_name", "")).strip()
    if not guest_name:
        raise HTTPException(400, "Укажите имя")
    guest_count = payload.get("guest_count")
    try:
        guest_count = int(guest_count) if guest_count not in (None, "") else None
        if guest_count is not None and guest_count < 1:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Количество гостей должно быть положительным числом") from exc
    fee = reservation_bool(payload.get("service_fee"))
    birthday_discount = reservation_bool(payload.get("birthday_discount"))
    with connect() as db:
        cursor = db.execute("UPDATE reservations SET reservation_date=?,reservation_time=?,guest_name=?,guest_count=?,phone=?,table_name=?,service_fee=?,birthday_discount=? WHERE id=?", (booking_date.isoformat(), booking_time.strftime("%H:%M"), guest_name, guest_count, str(payload.get("phone", "")).strip(), str(payload.get("table_name", "")).strip(), fee, birthday_discount, reservation_id))
        if not cursor.rowcount:
            raise HTTPException(404, "Бронь не найдена")
        recipients = db.execute("SELECT telegram_id FROM users WHERE is_active=1 AND status='approved' ORDER BY id").fetchall()
    if settings.bot_token:
        bot = telegram_bot()
        text = "Изменение брони. Актуальные данные:\n" + format_reservation_notice(booking_date, booking_time.strftime("%H:%M"), guest_name, guest_count, str(payload.get("phone", "")).strip(), str(payload.get("table_name", "")).strip(), fee, bool(birthday_discount))
        try:
            for recipient in recipients:
                try:
                    await bot.send_message(recipient["telegram_id"], text)
                except Exception:
                    logging.exception("Не удалось отправить изменение брони %s пользователю %s", reservation_id, recipient["telegram_id"])
        finally:
            await bot.session.close()
    return {"ok": True}




@app.delete("/api/reservations/{reservation_id}")
def delete_reservation(reservation_id: int, x_telegram_init_data: str | None = Header(default=None)):
    approved_user(x_telegram_init_data)
    with connect() as db:
        cursor = db.execute("DELETE FROM reservations WHERE id=?", (reservation_id,))
        if not cursor.rowcount:
            raise HTTPException(404, "Бронь не найдена")
    return {"ok": True}


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
    if existing and existing["status"] == "approved":
        raise HTTPException(409, "Доступ уже одобрен")
    if existing and not claim_action(existing["id"], "onboarding", 3600):
        raise HTTPException(429, "Повторно отправить заявку можно через час")
    user = create_pending_user(telegram_id, full_name, "", role, str(telegram_user.get("username") or ""))
    notification_sent = False
    if settings.bot_token and settings.admin_ids:
        bot = telegram_bot()
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
        rows = db.execute(f"SELECT * FROM recipes WHERE 1=1{active_filter} ORDER BY sort_order, title").fetchall()
    result = []
    for row in rows:
        item = row_dict(row)
        item["ingredients"] = json.loads(item["ingredients"])
        haystack = f"{item['title']} {' '.join(item['ingredients'])}".casefold()
        if q.casefold() in haystack:
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
    user = approved_user(x_telegram_init_data)
    month = month or date.today().strftime("%Y-%m")
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(400, "Месяц должен быть в формате YYYY-MM") from exc
    with connect() as db:
        rows = db.execute("SELECT s.work_date, s.user_id, u.full_name, CASE WHEN s.user_id = ? THEN 1 ELSE 0 END AS is_mine, CASE WHEN ? = 'admin' OR s.user_id = ? THEN h.hours ELSE NULL END AS confirmed_hours FROM schedules s JOIN users u ON u.id = s.user_id LEFT JOIN shift_hour_confirmations h ON h.user_id=s.user_id AND h.work_date=s.work_date AND h.status='approved' WHERE substr(s.work_date, 1, 7) = ? AND u.is_active = 1 AND u.status = 'approved' ORDER BY s.work_date, u.full_name", (user["id"], user["role"], user["id"], month)).fetchall()
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
        requests = db.execute("SELECT r.*, u.full_name FROM day_off_requests r JOIN users u ON u.id = r.user_id ORDER BY r.created_at DESC").fetchall()
        swaps = db.execute("SELECT r.*, u.full_name AS requester_name, t.full_name AS target_name FROM shift_swap_requests r JOIN users u ON u.id = r.requester_id LEFT JOIN users t ON t.id = r.target_user_id ORDER BY r.created_at DESC").fetchall()
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
        rows = db.execute("SELECT s.id, s.user_id, s.work_date, u.full_name, h.hours AS confirmed_hours FROM schedules s JOIN users u ON u.id = s.user_id LEFT JOIN shift_hour_confirmations h ON h.user_id=s.user_id AND h.work_date=s.work_date AND h.status='approved' WHERE substr(s.work_date, 1, 7) = ? ORDER BY s.work_date, u.full_name", (month,)).fetchall()
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
        exists = db.execute("SELECT id FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "Техкарта не найдена")
        db.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
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
        if status == "approved":
            end_date = request["request_end_date"] or request["request_date"]
            db.execute("DELETE FROM schedules WHERE user_id = ? AND work_date BETWEEN ? AND ?", (request["user_id"], request["request_date"], end_date))
    if settings.bot_token:
        bot = telegram_bot()
        try:
            end_date = request["request_end_date"] or request["request_date"]
            date_label = request["request_date"] if end_date == request["request_date"] else f"{request['request_date']} — {end_date}"
            await bot.send_message(request["telegram_id"], f"Заявка на выходной {date_label} {'одобрена' if status == 'approved' else 'отклонена'}.")
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
async def day_off(request_date: str = Form(...), request_end_date: str = Form(""), x_telegram_init_data: str | None = Header(default=None)):
    user = approved_user(x_telegram_init_data)
    try:
        start = date.fromisoformat(request_date)
        end = date.fromisoformat(request_end_date) if request_end_date else start
    except ValueError as exc:
        raise HTTPException(400, "Укажите корректный диапазон дат") from exc
    if end < start:
        raise HTTPException(400, "Дата окончания не может быть раньше даты начала")
    if not claim_action(user["id"], "day_off", 5):
        raise HTTPException(429, "Подождите несколько секунд перед повторной отправкой")
    with connect() as db:
        cursor = db.execute("INSERT INTO day_off_requests(user_id, request_date, request_end_date, created_at) VALUES (?, ?, ?, ?)", (user["id"], start.isoformat(), end.isoformat(), datetime.utcnow().isoformat()))
        request_id = cursor.lastrowid
    notification_sent = False
    notification_errors: list[str] = []
    if settings.bot_token and settings.admin_ids:
        bot = telegram_bot()
        try:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Одобрить", callback_data=f"dayoff:approved:{request_id}"), InlineKeyboardButton(text="Отклонить", callback_data=f"dayoff:rejected:{request_id}")]])
            message = f"Заявка на выходной\nСотрудник: {user['full_name']}\nДаты: {start.strftime('%d.%m.%Y')}" + (f" — {end.strftime('%d.%m.%Y')}" if end != start else "")
            for admin_id in settings.admin_ids:
                try:
                    await bot.send_message(admin_id, message, reply_markup=keyboard)
                    notification_sent = True
                except Exception:
                    notification_errors.append(f"admin {admin_id}")
                    logging.exception("Не удалось доставить заявку на выходной администратору %s (заявка %s)", admin_id, request_id)
        finally:
            await bot.session.close()
    else:
        logging.error("Заявка на выходной %s сохранена, но BOT_TOKEN или ADMIN_TELEGRAM_IDS не настроены", request_id)
    return {"ok": True, "notification_sent": notification_sent, "id": request_id, "notification_errors": notification_errors}


@app.post("/api/shift-swap")
async def shift_swap(request_date: str = Form(...), target_user_id: int | None = Form(default=None), comment: str = Form(""), x_telegram_init_data: str | None = Header(default=None)):
    user = approved_user(x_telegram_init_data)
    if not claim_action(user["id"], "shift_swap", 5):
        raise HTTPException(429, "Подождите несколько секунд перед повторной отправкой")
    try:
        date.fromisoformat(request_date)
    except ValueError as exc:
        raise HTTPException(400, "Некорректная дата") from exc
    with connect() as db:
        target = db.execute("SELECT * FROM users WHERE id = ? AND is_active=1 AND status='approved'", (target_user_id,)).fetchone() if target_user_id else None
        if not target:
            raise HTTPException(400, "Выберите сотрудника для обмена")
        if target["id"] == user["id"]:
            raise HTTPException(400, "Нельзя отправить заявку самому себе")
        if not db.execute("SELECT 1 FROM schedules WHERE user_id=? AND work_date=?", (user["id"], request_date)).fetchone():
            raise HTTPException(400, "На выбранную дату у вас нет смены в графике")
        if db.execute("SELECT 1 FROM schedules WHERE user_id=? AND work_date=?", (target["id"], request_date)).fetchone():
            raise HTTPException(400, "Выбранный сотрудник уже стоит в графике на эту дату")
        cursor = db.execute("INSERT INTO shift_swap_requests(requester_id, target_user_id, request_date, comment, created_at) VALUES (?, ?, ?, ?, ?)", (user["id"], target_user_id, request_date, comment, datetime.utcnow().isoformat()))
        request_id = cursor.lastrowid
    if settings.bot_token:
        bot = telegram_bot()
        try:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Принять смену", callback_data=f"swap:approved:{request_id}"), InlineKeyboardButton(text="Отклонить", callback_data=f"swap:rejected:{request_id}")]])
            try: await bot.send_message(target["telegram_id"], f"Сотрудник {user['full_name']} предлагает передать вам смену на {request_date}. Комментарий: {comment or '—'}", reply_markup=keyboard)
            except Exception: logging.exception("Не удалось уведомить сотрудника %s об обмене %s", target["telegram_id"], request_id)
            try: await bot.send_message(user["telegram_id"], f"Заявка на обмен сменами от {request_date} отправлена сотруднику {target['full_name']}.")
            except Exception: logging.exception("Не удалось уведомить заявителя %s об обмене %s", user["telegram_id"], request_id)
        except Exception:
            logging.exception("Не удалось отправить уведомления по заявке на обмен %s", request_id)
        finally:
            await bot.session.close()
    return {"ok": True, "id": request_id}


@app.post("/api/shift-hours")
async def submit_shift_hours(payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    user = approved_user(x_telegram_init_data)
    if not claim_action(user["id"], "shift_hours", 5):
        raise HTTPException(429, "Подождите несколько секунд перед повторной отправкой")
    try:
        work_date = date.fromisoformat(str(payload.get("work_date", "")))
        hours = float(payload.get("hours", 0))
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "Укажите дату и количество часов") from exc
    if not 0 < hours <= 24:
        raise HTTPException(400, "Часы должны быть от 0 до 24")
    with connect() as db:
        if not db.execute("SELECT 1 FROM schedules WHERE user_id=? AND work_date=?", (user["id"], work_date.isoformat())).fetchone():
            raise HTTPException(400, "В графике нет назначенной смены на эту дату")
        db.execute("INSERT INTO shift_hour_confirmations(user_id, work_date, hours, status, created_at, processed_at) VALUES (?, ?, ?, 'approved', ?, ?) ON CONFLICT(user_id, work_date) DO UPDATE SET hours=excluded.hours, status='approved', created_at=excluded.created_at, processed_at=excluded.processed_at", (user["id"], work_date.isoformat(), hours, datetime.utcnow().isoformat(), datetime.utcnow().isoformat()))
    return {"ok": True}


@app.post("/api/shifts")
async def create_shift(cash_amount: float = Form(0), card_amount: float = Form(0), comment: str = Form(""), photo_types: str = Form("[]"), photos: list[UploadFile] = File(default=[]), x_telegram_init_data: str | None = Header(default=None)):
    user = approved_user(x_telegram_init_data)
    if not claim_action(user["id"], "shift_report", 5):
        raise HTTPException(429, "Подождите несколько секунд перед повторной отправкой")
    required_photo_types = {"bar_station", "sink", "tea_surface", "coffee_station", "mini_fridge"}
    submitted_photo_types = set(json.loads(photo_types))
    if len(photos) != len(required_photo_types) or submitted_photo_types != required_photo_types:
        raise HTTPException(400, "Нужно сделать все пять фотографий закрытия смены")
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
        bot = telegram_bot()
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
    order_type = "bar" if user["role"] == "bartender" else "hall"
    with connect() as db:
        rows = db.execute("SELECT s.id AS supplier_id, s.name AS supplier_name, i.id, i.name FROM suppliers s JOIN supplier_items i ON i.supplier_id=s.id WHERE s.is_active=1 AND i.is_active=1 AND s.order_type=? ORDER BY s.name, i.name", (order_type,)).fetchall()
    return [row_dict(row) for row in rows]

@app.post("/api/orders")
async def create_order(payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    user = approved_user(x_telegram_init_data); order_type = "bar" if user["role"] == "bartender" else "hall"; comment = str(payload.get("comment", "")).strip(); items = payload.get("items", [])
    if not claim_action(user["id"], "order", 5):
        raise HTTPException(429, "Подождите 5 секунд перед отправкой следующего заказа")
    if items:
        items = [{"name": str(i.get("name", "")), "quantity": int(i.get("quantity", 0)), "supplier": str(i.get("supplier", ""))} for i in items if int(i.get("quantity", 0)) > 0]
        if not items: raise HTTPException(400, "Добавьте хотя бы одну позицию")
        comment = comment or ""
    elif order_type == "hall" and not comment: raise HTTPException(400, "Заполните заказ или выберите позиции")
    elif order_type == "bar": raise HTTPException(400, "Добавьте хотя бы одну позицию")
    with connect() as db: cursor = db.execute("INSERT INTO orders(user_id, order_type, comment, items, created_at) VALUES (?, ?, ?, ?, ?)", (user["id"], order_type, comment, json.dumps(items, ensure_ascii=False), datetime.utcnow().isoformat()))
    if settings.bot_token and settings.admin_ids:
        bot = telegram_bot()
        try:
            if order_type == "hall" and items:
                grouped = {}
                for item in items: grouped.setdefault(item["supplier"], []).append(item)
                messages = [f"Заказ ЗАЛ — {supplier}\nОт: {user['full_name']}\n\n" + "\n".join(f"• {item['name']}: {item['quantity']}" for item in supplier_items) + (f"\n\nДополнительно:\n{comment}" if comment else "") for supplier, supplier_items in grouped.items()]
            elif order_type == "hall":
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
def admin_suppliers(order_type: str = "bar", x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    if order_type not in {"hall", "bar"}:
        raise HTTPException(400, "Тип заказа должен быть hall или bar")
    with connect() as db:
        suppliers = [row_dict(r) for r in db.execute("SELECT * FROM suppliers WHERE order_type=? ORDER BY name", (order_type,)).fetchall()]; items = [row_dict(r) for r in db.execute("SELECT i.* FROM supplier_items i JOIN suppliers s ON s.id=i.supplier_id WHERE s.order_type=? ORDER BY i.name", (order_type,)).fetchall()]
    return {"suppliers": suppliers, "items": items}

@app.post("/api/admin/suppliers")
def add_supplier(payload: dict = Body(...), x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data); name = str(payload.get("name", "")).strip()
    if not name: raise HTTPException(400, "Укажите поставщика")
    order_type = payload.get("order_type", "bar")
    if order_type not in {"hall", "bar"}: raise HTTPException(400, "Укажите зал или бар")
    with connect() as db: cursor = db.execute("INSERT INTO suppliers(name, created_at, order_type) VALUES (?, ?, ?)", (name, datetime.utcnow().isoformat(), order_type))
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
