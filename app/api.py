import json
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
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
