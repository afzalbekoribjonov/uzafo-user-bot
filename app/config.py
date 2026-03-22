from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


@dataclass(slots=True)
class Settings:
    api_id: int
    api_hash: str
    bot_token: str
    admin_id: int
    db_path: Path
    temp_dir: Path
    login_session_dir: Path
    default_limit: int
    default_interval: int
    freeze_hours: int
    port: int
    webhook_base_url: str
    webhook_path: str
    webhook_secret: str
    use_webhook: bool



def _to_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    if value is None or value == '':
        return default
    return int(value)


settings = Settings(
    api_id=_to_int('API_ID'),
    api_hash=os.getenv('API_HASH', '').strip(),
    bot_token=os.getenv('BOT_TOKEN', '').strip(),
    admin_id=_to_int('ADMIN_ID'),
    db_path=BASE_DIR / os.getenv('DB_PATH', 'data/bot.db'),
    temp_dir=BASE_DIR / os.getenv('TEMP_DIR', 'tmp'),
    login_session_dir=BASE_DIR / os.getenv('LOGIN_SESSION_DIR', 'tmp/login_sessions'),
    default_limit=_to_int('DEFAULT_LIMIT', 5000),
    default_interval=_to_int('DEFAULT_INTERVAL', 15),
    freeze_hours=_to_int('FREEZE_HOURS', 24),
    port=_to_int('PORT', 10000),
    webhook_base_url=os.getenv('WEBHOOK_BASE_URL', os.getenv('RENDER_EXTERNAL_URL', '')).strip().rstrip('/'),
    webhook_path=os.getenv('WEBHOOK_PATH', '/telegram/webhook').strip() or '/telegram/webhook',
    webhook_secret=os.getenv('WEBHOOK_SECRET', os.getenv('BOT_TOKEN', '')).strip(),
    use_webhook=os.getenv('USE_WEBHOOK', '').strip().lower() in {'1','true','yes','on'} or bool(os.getenv('RENDER')),
)


def normalized_webhook_path() -> str:
    path = settings.webhook_path.strip() or '/telegram/webhook'
    return path if path.startswith('/') else f'/{path}'


def webhook_url() -> str:
    base = settings.webhook_base_url.rstrip('/')
    if not base:
        return ''
    return f"{base}{normalized_webhook_path()}"


def validate_settings() -> None:
    missing = []
    if not settings.api_id:
        missing.append('API_ID')
    if not settings.api_hash:
        missing.append('API_HASH')
    if not settings.bot_token:
        missing.append('BOT_TOKEN')
    if not settings.admin_id:
        missing.append('ADMIN_ID')
    if missing:
        joined = ', '.join(missing)
        raise RuntimeError(f".env faylida quyidagi qiymatlar to'ldirilmagan: {joined}")
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    settings.login_session_dir.mkdir(parents=True, exist_ok=True)
    settings.webhook_path = normalized_webhook_path()
    if settings.use_webhook and not settings.webhook_base_url:
        raise RuntimeError('Webhook rejimi uchun WEBHOOK_BASE_URL yoki RENDER_EXTERNAL_URL kerak')
