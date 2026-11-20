import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramNetworkError
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from .config import settings
from .db import connect, create_pending_user, init_db, upsert_user

router = Router()


@router.message(F.contact)
async def receive_contact(message: Message) -> None:
    create_pending_user(message.from_user.id, message.from_user.full_name, message.contact.phone_number, "bartender")
    await message.answer("Контакт получен. Откройте приложение, заполните имя и роль, затем отправьте заявку.", reply_markup=mini_app_keyboard())


def mini_app_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть приложение", web_app=WebAppInfo(url=settings.mini_app_url))]])


@router.message(CommandStart())
async def start(message: Message) -> None:
    if message.from_user.id in settings.admin_ids:
        upsert_user(message.from_user.id, message.from_user.full_name, "admin")
        await message.answer("Барная смена на связи. Откройте приложение:", reply_markup=mini_app_keyboard())
        await send_pending_access_requests(message.bot, message.from_user.id)
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Поделиться номером", request_contact=True)], [InlineKeyboardButton(text="Открыть анкету", web_app=WebAppInfo(url=settings.mini_app_url))]])
        await message.answer("Для первого входа подтвердите номер телефона, затем заполните анкету и выберите роль. Доступ появится после одобрения администратора.", reply_markup=keyboard)


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer("/start — открыть приложение\n/recipes — список техкарт\n/admin — меню администратора")


@router.message(Command("recipes"))
async def recipes(message: Message) -> None:
    with connect() as db:
        rows = db.execute("SELECT title, category FROM recipes WHERE is_active = 1 ORDER BY sort_order, title").fetchall()
    text = "\n".join(f"• {row['title']} — {row['category']}" for row in rows) or "Техкарт пока нет"
    await message.answer(text)


@router.message(Command("admin"))
async def admin(message: Message) -> None:
    if message.from_user.id not in settings.admin_ids:
        await message.answer("Команда доступна только администратору.")
        return
    with connect() as db:
        access_requests = db.execute("SELECT id, telegram_id, full_name, phone, role FROM users WHERE status = 'pending' AND phone != '' ORDER BY created_at").fetchall()
        requests = db.execute("SELECT r.id, r.request_date, u.full_name FROM day_off_requests r JOIN users u ON u.id = r.user_id WHERE r.status = 'pending' ORDER BY r.created_at").fetchall()
        users = db.execute("SELECT id, full_name FROM users WHERE is_active = 1 ORDER BY full_name").fetchall()
    if not requests:
        await message.answer("Ожидающих заявок на выходной нет.")
    if not access_requests:
        await message.answer("Ожидающих заявок на доступ нет.")
    for request in access_requests:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Одобрить", callback_data=f"access:approved:{request['id']}"), InlineKeyboardButton(text="Отклонить", callback_data=f"access:rejected:{request['id']}")]])
        await message.answer(f"Заявка на доступ\nФИО: {request['full_name']}\nТелефон: {request['phone']}\nРоль: {request['role']}", reply_markup=keyboard)
    for request in requests:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Одобрить", callback_data=f"dayoff:approved:{request['id']}"), InlineKeyboardButton(text="Отклонить", callback_data=f"dayoff:rejected:{request['id']}")]])
        await message.answer(f"Заявка на выходной\nБармен: {request['full_name']}\nДата: {request['request_date']}", reply_markup=keyboard)
    if users:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"График: {user['full_name']}", callback_data=f"schedule:{user['id']}")] for user in users])
        await message.answer("Выберите сотрудника, чтобы назначить смену на дату командой:\n/setshift USER_ID YYYY-MM-DD day|night|off", reply_markup=keyboard)


async def send_pending_access_requests(bot: Bot, admin_id: int) -> None:
    with connect() as db:
        requests = db.execute("SELECT id, full_name, phone, role FROM users WHERE status = 'pending' AND phone != '' ORDER BY created_at").fetchall()
    for request in requests:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Одобрить", callback_data=f"access:approved:{request['id']}"), InlineKeyboardButton(text="Отклонить", callback_data=f"access:rejected:{request['id']}")]])
        await bot.send_message(admin_id, f"Заявка на доступ\nФИО: {request['full_name']}\nТелефон: {request['phone']}\nРоль: {request['role']}", reply_markup=keyboard)


@router.callback_query(F.data.startswith("dayoff:"))
async def process_day_off(callback: CallbackQuery) -> None:
    if callback.from_user.id not in settings.admin_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, status, request_id = callback.data.split(":")
    with connect() as db:
        request = db.execute("SELECT r.*, u.telegram_id FROM day_off_requests r JOIN users u ON u.id = r.user_id WHERE r.id = ?", (int(request_id),)).fetchone()
        if not request or request["status"] != "pending":
            await callback.answer("Заявка уже обработана", show_alert=True)
            return
        db.execute("UPDATE day_off_requests SET status = ?, processed_at = ? WHERE id = ?", (status, __import__("datetime").datetime.utcnow().isoformat(), int(request_id)))
    label = "одобрена" if status == "approved" else "отклонена"
    await callback.message.edit_text(f"Заявка на {request['request_date']} {label}.")
    if settings.bot_token:
        await callback.bot.send_message(request["telegram_id"], f"Заявка на выходной {request['request_date']} {label} администратором.")
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("access:"))
async def process_access(callback: CallbackQuery) -> None:
    if callback.from_user.id not in settings.admin_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return
    _, status, user_id = callback.data.split(":")
    with connect() as db:
        user = db.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if not user:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
        approved = status == "approved"
        db.execute("UPDATE users SET status = ?, is_active = ? WHERE id = ?", ("approved" if approved else "rejected", 1 if approved else 0, int(user_id)))
    await callback.message.edit_text(f"Доступ для {user['full_name']} {'одобрен' if status == 'approved' else 'отклонён'}.")
    await callback.bot.send_message(user["telegram_id"], "Доступ одобрен. Теперь откройте бота и Mini App." if status == "approved" else "Заявка на доступ отклонена.")
    await callback.answer("Готово")


@router.message(Command("setshift"))
async def set_shift(message: Message) -> None:
    if message.from_user.id not in settings.admin_ids:
        await message.answer("Команда доступна только администратору.")
        return
    parts = (message.text or "").split()
    if len(parts) != 4 or parts[3] not in {"day", "night", "off"}:
        await message.answer("Формат: /setshift USER_ID YYYY-MM-DD day|night|off")
        return
    try:
        user_id, work_date = int(parts[1]), parts[2]
        from datetime import date, datetime
        date.fromisoformat(work_date)
    except ValueError:
        await message.answer("Проверьте ID сотрудника и дату в формате YYYY-MM-DD.")
        return
    with connect() as db:
        db.execute("INSERT INTO schedules(user_id, work_date, shift_type, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(user_id, work_date) DO UPDATE SET shift_type=excluded.shift_type", (user_id, work_date, parts[3], datetime.utcnow().isoformat()))
        recipient = db.execute("SELECT telegram_id FROM users WHERE id = ?", (user_id,)).fetchone()
    if recipient:
        await message.bot.send_message(recipient["telegram_id"], f"Расписание обновлено: {work_date} — {parts[3]}.")
    await message.answer("Смена сохранена.")


async def run_bot() -> None:
    if not settings.bot_token:
        logging.warning("BOT_TOKEN is empty; Telegram bot is disabled")
        return
    init_db()
    while True:
        session = AiohttpSession(proxy=settings.bot_proxy) if settings.bot_proxy else AiohttpSession()
        bot = Bot(settings.bot_token, session=session)
        dp = Dispatcher()
        dp.include_router(router)
        try:
            await dp.start_polling(bot)
            return
        except TelegramNetworkError as exc:
            logging.error("Telegram недоступен: %s. Повтор через 10 секунд.", exc)
            await asyncio.sleep(10)
        except Exception:
            logging.exception("Ошибка Telegram-бота. Повтор через 10 секунд.")
            await asyncio.sleep(10)
        finally:
            await bot.session.close()
