import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from .config import settings


def validate_init_data(init_data: str) -> dict:
    if not init_data:
        if settings.bot_token:
            raise ValueError("Откройте приложение через Telegram")
        return {"id": 0, "first_name": "Гость", "last_name": ""}
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", "")
    check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    auth_date = int(pairs.get("auth_date", "0"))
    if settings.bot_token and (not hmac.compare_digest(expected, received) or time.time() - auth_date > 86400):
        raise ValueError("Некорректные данные Telegram")
    user = json.loads(pairs.get("user", "{}"))
    return user
