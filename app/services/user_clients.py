from __future__ import annotations

import base64
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import qrcode
from pyrogram import Client, enums, raw
from pyrogram.errors import BadRequest, PeerIdInvalid

from app.config import settings
from app.runtime import get_pending_login_client, get_user_session_lock, is_user_session_busy, pop_pending_login_client, set_pending_login_client

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LoginSession:
    phone: str
    session_name: str
    phone_code_hash: str
    sent_code_type: str
    next_type: str
    timeout: int | None


@dataclass(slots=True)
class QrLoginSession:
    session_name: str
    image_path: str
    login_url: str
    expires_at: int | None


@dataclass(slots=True)
class ProfileSnapshot:
    full_name: str
    bio: str | None
    username: str | None
    dc_id: int | None
    is_premium: bool
    profile_photo_path: str | None


class PendingLoginMissingError(RuntimeError):
    pass


class QrLoginPending(RuntimeError):
    pass


class SessionBusyError(RuntimeError):
    pass


def _user_session_base(user_id: int) -> Path:
    return settings.login_session_dir / f'user_{user_id}'


def session_file_exists(session_name: str | None = None, user_id: int | None = None) -> bool:
    if session_name:
        base = settings.login_session_dir / session_name
    elif user_id is not None:
        base = _user_session_base(user_id)
    else:
        return False
    return Path(f'{base}.session').exists()


def build_login_client(user_id: int, session_name: str | None = None) -> Client:
    return Client(
        name=session_name or f'user_{user_id}',
        workdir=str(settings.login_session_dir),
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        device_model='uzafo app',
        app_version='uzafo app',
        no_updates=True,
    )


def build_user_client(user_id: int, session_string: str) -> Client:
    return Client(
        name=f'user_{user_id}_memory',
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        session_string=session_string,
        in_memory=True,
        device_model='uzafo app',
        app_version='uzafo app',
        no_updates=True,
    )


def build_authenticated_client(user_id: int, session_name: str | None, session_string: str | None) -> Client:
    if session_string:
        return build_user_client(user_id=user_id, session_string=session_string)
    if session_name and session_file_exists(session_name=session_name):
        return build_login_client(user_id=user_id, session_name=session_name)
    raise RuntimeError('Saqlangan foydalanuvchi sessiyasi topilmadi.')


async def warmup_client_peers(client: Client, groups: list | None = None) -> dict[str, int]:
    dialogs_loaded = 0
    resolved = 0
    failed = 0
    async for _dialog in client.get_dialogs():
        dialogs_loaded += 1
    if groups:
        for group in groups:
            chat_id = int(group['chat_id']) if isinstance(group, dict) or hasattr(group, '__getitem__') else int(group[0])
            try:
                await client.get_chat(chat_id)
                resolved += 1
            except Exception:
                failed += 1
    return {'dialogs_loaded': dialogs_loaded, 'resolved': resolved, 'failed': failed}


async def resolve_chat_id(client: Client, chat_id: int) -> int:
    try:
        chat = await client.get_chat(chat_id)
        return int(chat.id)
    except PeerIdInvalid:
        async for dialog in client.get_dialogs():
            if int(dialog.chat.id) == int(chat_id):
                return int(dialog.chat.id)
        raise


async def scan_admin_groups(user_id: int, session_name: str | None, session_string: str | None) -> list[tuple[int, str]]:
    lock = get_user_session_lock(user_id)
    if lock.locked():
        raise SessionBusyError('Hozir akkauntingiz bilan boshqa jarayon ishlayapti. Avval faol jarayonni pauza qiling yoki to‘xtating.')
    await lock.acquire()
    client = build_authenticated_client(user_id, session_name, session_string)
    result: list[tuple[int, str]] = []
    seen_chat_ids: set[int] = set()
    try:
        await client.start()
        await warmup_client_peers(client)
        async for dialog in client.get_dialogs():
            if dialog.chat.type not in {enums.ChatType.GROUP, enums.ChatType.SUPERGROUP}:
                continue
            chat_id = int(dialog.chat.id)
            if chat_id in seen_chat_ids:
                continue
            seen_chat_ids.add(chat_id)
            result.append((chat_id, dialog.chat.title or str(chat_id)))
    finally:
        await safe_stop(client)
        if lock.locked():
            lock.release()
    result.sort(key=lambda item: item[1].lower())
    return result


async def start_login(phone: str, user_id: int) -> LoginSession:
    await clear_pending_login(user_id)
    session_name = _user_session_base(user_id).name
    client = build_login_client(user_id, session_name=session_name)
    try:
        await client.connect()
        sent_code = await client.send_code(phone)
        logger.info(
            'Login code requested user_id=%s phone=%s sent_type_raw=%s next_type_raw=%s timeout=%s',
            user_id,
            phone,
            _raw_type_name(getattr(sent_code, 'type', None)),
            _raw_type_name(getattr(sent_code, 'next_type', None)),
            getattr(sent_code, 'timeout', None),
        )
    except Exception:
        await safe_stop(client)
        raise
    set_pending_login_client(user_id, client)
    return LoginSession(
        phone=phone,
        session_name=session_name,
        phone_code_hash=sent_code.phone_code_hash,
        sent_code_type=describe_sent_code_type(sent_code),
        next_type=describe_next_type(sent_code),
        timeout=getattr(sent_code, 'timeout', None),
    )


async def restart_login(phone: str, user_id: int, session_name: str | None = None) -> LoginSession:
    return await start_login(phone=phone, user_id=user_id)


async def ensure_pending_login_client(user_id: int) -> Client:
    client = get_pending_login_client(user_id)
    if client is None:
        raise PendingLoginMissingError('pending_login_missing')
    if not client.is_connected:
        await client.connect()
    return client


async def complete_login(user_id: int, session_name: str, phone: str, phone_code_hash: str, code: str) -> tuple[str, str]:
    client = await ensure_pending_login_client(user_id)
    await client.sign_in(phone, phone_code_hash, code)
    me = await client.get_me()
    session_string = await client.export_session_string()
    full_name = ' '.join(part for part in [me.first_name, me.last_name] if part).strip() or '—'
    return session_string, full_name


async def complete_2fa(user_id: int, session_name: str, password: str) -> tuple[str, str]:
    client = await ensure_pending_login_client(user_id)
    await client.check_password(password)
    me = await client.get_me()
    session_string = await client.export_session_string()
    full_name = ' '.join(part for part in [me.first_name, me.last_name] if part).strip() or '—'
    return session_string, full_name


async def start_qr_login(user_id: int) -> QrLoginSession:
    await clear_pending_login(user_id)
    session_name = _user_session_base(user_id).name
    client = build_login_client(user_id, session_name=session_name)
    try:
        await client.connect()
        result = await _export_login_token(client)
    except Exception:
        await safe_stop(client)
        raise
    set_pending_login_client(user_id, client)
    login_url, expires_at = _extract_qr_payload(result)
    image_path = str(settings.temp_dir / f'qr_login_{user_id}.png')
    _save_qr_image(login_url, image_path)
    logger.info('QR login token created user_id=%s expires_at=%s', user_id, expires_at)
    return QrLoginSession(session_name=session_name, image_path=image_path, login_url=login_url, expires_at=expires_at)


async def refresh_qr_login(user_id: int) -> QrLoginSession:
    await clear_pending_login(user_id)
    return await start_qr_login(user_id)


async def check_qr_login(user_id: int) -> tuple[str, str]:
    client = await ensure_pending_login_client(user_id)
    result = await _export_login_token(client)
    if isinstance(result, raw.types.auth.LoginTokenSuccess):
        me = await client.get_me()
        session_string = await client.export_session_string()
        full_name = ' '.join(part for part in [me.first_name, me.last_name] if part).strip() or '—'
        return session_string, full_name
    raise QrLoginPending('qr_login_pending')


async def _export_login_token(client: Client):
    result = await client.invoke(raw.functions.auth.ExportLoginToken(api_id=settings.api_id, api_hash=settings.api_hash, except_ids=[]))
    if isinstance(result, raw.types.auth.LoginTokenMigrateTo):
        result = await client.invoke(raw.functions.auth.ImportLoginToken(token=result.token))
    return result


def _extract_qr_payload(result) -> tuple[str, int | None]:
    if isinstance(result, raw.types.auth.LoginTokenSuccess):
        raise QrLoginPending('qr_login_already_confirmed')
    if not isinstance(result, raw.types.auth.LoginToken):
        raise RuntimeError(f'Kutilmagan QR javobi: {result.__class__.__name__}')
    token = base64.urlsafe_b64encode(result.token).decode().rstrip('=')
    login_url = f'tg://login?token={token}'
    return login_url, getattr(result, 'expires', None)




async def _start_locked_client(user_id: int, session_name: str | None, session_string: str | None) -> tuple[Client, object]:
    lock = get_user_session_lock(user_id)
    if lock.locked():
        raise SessionBusyError('Hozir akkauntingiz bilan boshqa jarayon ishlayapti. Avval faol jarayonni pauza qiling yoki to‘xtating.')
    await lock.acquire()
    client = build_authenticated_client(user_id, session_name, session_string)
    try:
        await client.start()
        return client, lock
    except Exception:
        lock.release()
        await safe_stop(client)
        raise


async def _stop_locked_client(client: Client | None, lock) -> None:
    await safe_stop(client)
    try:
        if lock and lock.locked():
            lock.release()
    except Exception:
        pass

def _save_qr_image(login_url: str, image_path: str) -> None:
    Path(image_path).parent.mkdir(parents=True, exist_ok=True)
    img = qrcode.make(login_url)
    img.save(image_path)


async def fetch_profile_snapshot(user_id: int, session_name: str | None, session_string: str | None) -> ProfileSnapshot:
    client = None
    lock = None
    photo_path: str | None = None
    try:
        client, lock = await _start_locked_client(user_id, session_name, session_string)
        me = await client.get_me()
        bio = getattr(me, 'bio', None)
        async for photo in client.get_chat_photos('me', limit=1):
            dest = settings.temp_dir / f'profile_{user_id}.jpg'
            photo_path = await client.download_media(photo.file_id, file_name=str(dest))
            break
        full_name = ' '.join(part for part in [me.first_name, me.last_name] if part).strip() or '—'
        return ProfileSnapshot(
            full_name=full_name,
            bio=bio,
            username=getattr(me, 'username', None),
            dc_id=getattr(me, 'dc_id', None),
            is_premium=bool(getattr(me, 'is_premium', False)),
            profile_photo_path=str(photo_path) if photo_path else None,
        )
    finally:
        await _stop_locked_client(client, lock)


async def update_profile_name(user_id: int, session_name: str | None, session_string: str | None, new_name: str) -> str:
    client = None
    lock = None
    try:
        client, lock = await _start_locked_client(user_id, session_name, session_string)
        parts = new_name.strip().split(maxsplit=1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ''
        await client.update_profile(first_name=first_name, last_name=last_name)
        me = await client.get_me()
        return ' '.join(part for part in [me.first_name, me.last_name] if part).strip() or first_name
    finally:
        await _stop_locked_client(client, lock)


async def update_profile_bio(user_id: int, session_name: str | None, session_string: str | None, new_bio: str) -> str:
    client = None
    lock = None
    try:
        client, lock = await _start_locked_client(user_id, session_name, session_string)
        await client.update_profile(bio=new_bio)
        me = await client.get_me()
        return getattr(me, 'bio', None) or new_bio
    finally:
        await _stop_locked_client(client, lock)


async def safe_stop(client: Client | None) -> None:
    if client is None:
        return
    try:
        if getattr(client, 'is_initialized', False):
            await client.stop()
            return
    except Exception:
        pass
    try:
        if client.is_connected:
            await client.disconnect()
    except Exception:
        pass


async def clear_pending_login(user_id: int, remove_session_files: bool = True) -> None:
    client = pop_pending_login_client(user_id)
    await safe_stop(client)
    if remove_session_files:
        cleanup_login_session_files(user_id)
    qr_path = settings.temp_dir / f'qr_login_{user_id}.png'
    try:
        if qr_path.exists():
            os.remove(qr_path)
    except OSError:
        pass


def cleanup_login_session_files(user_id: int) -> None:
    base = _user_session_base(user_id)
    for suffix in ['.session', '.session-journal', '.session-wal', '.session-shm']:
        path = Path(f'{base}{suffix}')
        try:
            if path.exists():
                os.remove(path)
        except OSError:
            pass


def login_session_exists(session_name: str) -> bool:
    suffix = session_name.replace('user_', '', 1)
    return suffix.isdigit() and get_pending_login_client(int(suffix)) is not None


async def diagnose_api_pair(user_id: int) -> str | None:
    client = build_login_client(user_id)
    try:
        await client.connect()
        return None
    except BadRequest as error:
        return str(error)
    except Exception as error:
        return f'{error.__class__.__name__}: {str(error)}'
    finally:
        await safe_stop(client)
        cleanup_login_session_files(user_id)


def _class_name(obj: object | None) -> str:
    return obj.__class__.__name__.lower() if obj is not None else ''


def _raw_type_name(obj: object | None) -> str:
    if obj is None:
        return 'none'
    return f'{obj.__class__.__module__}.{obj.__class__.__name__}'


def describe_sent_code_type(sent_code: object) -> str:
    type_name = _class_name(getattr(sent_code, 'type', None))
    if 'app' in type_name:
        return 'Telegram ilovasi'
    if 'firebase' in type_name:
        return 'Firebase SMS'
    if 'fragmentsms' in type_name or 'fragment' in type_name:
        return 'Fragment SMS'
    if 'sms' in type_name:
        return 'SMS'
    if 'flash' in type_name or 'missed' in type_name or 'call' in type_name:
        return 'telefon qo‘ng‘irog‘i'
    if 'email' in type_name:
        return 'email'
    return 'Telegram yoki SMS'


def describe_next_type(sent_code: object) -> str:
    type_name = _class_name(getattr(sent_code, 'next_type', None))
    if not type_name:
        return 'yo‘q'
    if 'app' in type_name:
        return 'Telegram ilovasi'
    if 'firebase' in type_name:
        return 'Firebase SMS'
    if 'fragmentsms' in type_name or 'fragment' in type_name:
        return 'Fragment SMS'
    if 'sms' in type_name:
        return 'SMS'
    if 'flash' in type_name or 'missed' in type_name or 'call' in type_name:
        return 'telefon qo‘ng‘irog‘i'
    if 'email' in type_name:
        return 'email'
    return type_name
