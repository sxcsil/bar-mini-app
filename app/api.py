import json
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from aiogram import Bot
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from .config import settings
from .db import connect, init_db, row_dict, upsert_user
from .security import validate_init_data

app = FastAPI(title="Bar Mini App API")
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


def current_user(init_data: str | None):
    try:
        user = validate_init_data(init_data or "")
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    telegram_id = int(user.get("id", 0))
    role = "admin" if telegram_id in settings.admin_ids else "bartender"
    return upsert_user(telegram_id, " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or "Бармен", role)


def admin_user(init_data: str | None):
    user = current_user(init_data)
    if user["role"] != "admin":
        raise HTTPException(403, "Доступ только для администратора")
    return user


@app.get("/api/me")
def me(x_telegram_init_data: str | None = Header(default=None)):
    return row_dict(current_user(x_telegram_init_data))


@app.get("/api/recipes")
def recipes(q: str = "", x_telegram_init_data: str | None = Header(default=None)):
    current_user(x_telegram_init_data)
    with connect() as db:
        rows = db.execute("SELECT * FROM recipes WHERE is_active = 1 AND (title LIKE ? OR ingredients LIKE ?) ORDER BY sort_order, title", (f"%{q}%", f"%{q}%")).fetchall()
    result = []
    for row in rows:
        item = row_dict(row)
        item["ingredients"] = json.loads(item["ingredients"])
        result.append(item)
    return result


@app.get("/api/schedule")
def schedule(x_telegram_init_data: str | None = Header(default=None)):
    user = current_user(x_telegram_init_data)
    with connect() as db:
        rows = db.execute("SELECT * FROM schedules WHERE user_id = ? AND work_date >= date('now', '-7 day') ORDER BY work_date", (user["id"],)).fetchall()
    return [row_dict(row) for row in rows]


@app.get("/api/admin/overview")
def admin_overview(x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    with connect() as db:
        recipes = db.execute("SELECT * FROM recipes WHERE is_active = 1 ORDER BY sort_order, title").fetchall()
        users = db.execute("SELECT id, telegram_id, full_name, role, is_active FROM users ORDER BY full_name").fetchall()
        requests = db.execute("SELECT r.*, u.full_name FROM day_off_requests r JOIN users u ON u.id = r.user_id ORDER BY r.created_at DESC LIMIT 50").fetchall()
        shifts = db.execute("SELECT s.*, u.full_name FROM shifts s JOIN users u ON u.id = s.user_id ORDER BY s.created_at DESC LIMIT 50").fetchall()
        schedules = db.execute("SELECT s.*, u.full_name FROM schedules s JOIN users u ON u.id = s.user_id WHERE s.work_date >= date('now', '-7 day') ORDER BY s.work_date, u.full_name").fetchall()
    recipe_items = []
    for row in recipes:
        item = row_dict(row)
        item["ingredients"] = json.loads(item["ingredients"])
        recipe_items.append(item)
    return {"recipes": recipe_items, "users": [row_dict(row) for row in users], "requests": [row_dict(row) for row in requests], "shifts": [row_dict(row) for row in shifts], "schedules": [row_dict(row) for row in schedules]}


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
        cursor = db.execute("INSERT INTO recipes(title, category, ingredients, recipe_text, notes, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (title, payload.get("category", "Коктейль"), json.dumps(ingredients, ensure_ascii=False), recipe_text, payload.get("notes", ""), int(payload.get("sort_order", 0)), now, now))
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
        db.execute("UPDATE recipes SET title=?, category=?, ingredients=?, recipe_text=?, notes=?, sort_order=?, updated_at=? WHERE id=?", (payload.get("title", ""), payload.get("category", "Коктейль"), json.dumps(ingredients, ensure_ascii=False), payload.get("recipe_text", ""), payload.get("notes", ""), int(payload.get("sort_order", 0)), datetime.utcnow().isoformat(), recipe_id))
    return {"ok": True}


@app.delete("/api/admin/recipes/{recipe_id}")
def delete_recipe(recipe_id: int, x_telegram_init_data: str | None = Header(default=None)):
    admin_user(x_telegram_init_data)
    with connect() as db:
        db.execute("UPDATE recipes SET is_active = 0, updated_at = ? WHERE id = ?", (datetime.utcnow().isoformat(), recipe_id))
    return {"ok": True}


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
    user = current_user(x_telegram_init_data)
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


@app.post("/api/shifts")
async def create_shift(cash_amount: float = Form(0), card_amount: float = Form(0), comment: str = Form(""), photo_types: str = Form("[]"), photos: list[UploadFile] = File(default=[]), x_telegram_init_data: str | None = Header(default=None)):
    user = current_user(x_telegram_init_data)
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
