# Bar Mini App

MVP Telegram Mini App для небольшого бара: техкарты коктейлей, закрытие смены с фото из камеры и график барменов.

## Быстрый запуск

1. Создайте бота через BotFather и скопируйте токен.
2. Скопируйте `.env.example` в `.env` и заполните значения.
3. Установите зависимости: `python -m pip install -r requirements.txt`.
4. Запустите приложение: `python -m app`.
5. Для Telegram Mini App нужен HTTPS URL. На локальной машине удобно использовать tunnel (например, Cloudflare Tunnel), затем указать URL в `MINI_APP_URL`.

При первом запуске создаются SQLite-база и демонстрационные техкарты. Администратора можно назначить через `ADMIN_TELEGRAM_IDS`.

## Команды бота

- `/start` — открыть Mini App
- `/help` — список команд
- `/recipes` — краткий список техкарт
- `/admin` — админское меню (только для ID из `ADMIN_TELEGRAM_IDS`)

Админские callback-кнопки позволяют одобрить или отклонить заявки на выходной.

## Структура

```text
app/
  __main__.py       # запуск FastAPI и polling бота
  config.py         # настройки окружения
  db.py             # SQLite и миграции
  security.py       # проверка Telegram WebApp initData
  api.py            # FastAPI endpoints и раздача Mini App
  bot.py            # aiogram handlers
  static/           # Mini App (HTML/CSS/JS без сборки)
```

Для первого рабочего среза фронтенд сделан без отдельной сборки React: это уменьшает число сервисов и позволяет сразу подключить Mini App к Telegram. Позже его можно заменить на React без изменения API.
