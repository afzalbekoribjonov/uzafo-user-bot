from __future__ import annotations

import html
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Iterable

import aiosqlite

from app.config import settings

DATETIME_FORMAT = '%Y-%m-%d %H:%M:%S'


@asynccontextmanager
async def db_connect():
    db = await aiosqlite.connect(settings.db_path, timeout=30)
    await db.execute('PRAGMA journal_mode=WAL')
    await db.execute('PRAGMA busy_timeout=30000')
    try:
        yield db
    finally:
        await db.close()


async def init_db() -> None:
    async with db_connect() as db:
        await db.execute(
            f'''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                expiry_date TEXT,
                is_active INTEGER DEFAULT 1,
                is_admin INTEGER DEFAULT 0,
                daily_limit INTEGER DEFAULT {settings.default_limit},
                used_today INTEGER DEFAULT 0,
                phone TEXT,
                full_name TEXT,
                session_string TEXT,
                session_name TEXT,
                ban_strikes INTEGER DEFAULT 0,
                is_frozen INTEGER DEFAULT 0,
                frozen_until TEXT,
                forward_interval INTEGER DEFAULT {settings.default_interval},
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )
        await db.execute(
            '''
            CREATE TABLE IF NOT EXISTS user_groups (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                auto_send INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, chat_id)
            )
            '''
        )
        await db.execute(
            '''
            CREATE TABLE IF NOT EXISTS ban_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                strike_num INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
        await db.execute(
            '''
            CREATE TABLE IF NOT EXISTS keys (
                key_code TEXT PRIMARY KEY,
                days INTEGER NOT NULL,
                is_used INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                used_by INTEGER,
                used_at TEXT
            )
            '''
        )
        await db.execute(
            '''
            CREATE TABLE IF NOT EXISTS login_sessions (
                user_id INTEGER PRIMARY KEY,
                phone TEXT NOT NULL,
                session_name TEXT NOT NULL,
                phone_code_hash TEXT NOT NULL,
                code_part1 TEXT,
                step TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        await _run_migrations(db)
        await db.commit()


async def _run_migrations(db: aiosqlite.Connection) -> None:
    await _ensure_columns(
        db,
        'users',
        {
            'expiry_date': 'TEXT',
            'is_active': 'INTEGER DEFAULT 1',
            'is_admin': 'INTEGER DEFAULT 0',
            'daily_limit': f'INTEGER DEFAULT {settings.default_limit}',
            'used_today': 'INTEGER DEFAULT 0',
            'phone': 'TEXT',
            'full_name': 'TEXT',
            'session_string': 'TEXT',
            'session_name': 'TEXT',
            'ban_strikes': 'INTEGER DEFAULT 0',
            'is_frozen': 'INTEGER DEFAULT 0',
            'frozen_until': 'TEXT',
            'forward_interval': f'INTEGER DEFAULT {settings.default_interval}',
            'created_at': 'TEXT DEFAULT CURRENT_TIMESTAMP',
            'updated_at': 'TEXT DEFAULT CURRENT_TIMESTAMP',
            'login_cooldown_until': 'TEXT',
            'login_cooldown_reason': 'TEXT',
        },
    )
    await _ensure_columns(
        db,
        'user_groups',
        {
            'title': 'TEXT',
            'is_admin': 'INTEGER DEFAULT 0',
            'auto_send': 'INTEGER DEFAULT 0',
            'updated_at': 'TEXT DEFAULT CURRENT_TIMESTAMP',
        },
    )
    await _ensure_columns(
        db,
        'keys',
        {
            'used_at': 'TEXT',
        },
    )
    await _ensure_columns(
        db,
        'login_sessions',
        {
            'phone': 'TEXT',
            'session_name': 'TEXT',
            'phone_code_hash': 'TEXT',
            'code_part1': 'TEXT',
            'step': 'TEXT',
            'created_at': 'TEXT',
            'updated_at': 'TEXT',
        },
    )


async def _ensure_columns(db: aiosqlite.Connection, table: str, columns: dict[str, str]) -> None:
    current = set()
    async with db.execute(f'PRAGMA table_info({table})') as cur:
        async for row in cur:
            current.add(row[1])
    for name, definition in columns.items():
        if name not in current:
            await db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')


async def fetchone(query: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
    async with db_connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, tuple(params))
        return await cur.fetchone()


async def fetchall(query: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
    async with db_connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, tuple(params))
        return await cur.fetchall()


async def execute(query: str, params: Iterable[Any] = ()) -> None:
    async with db_connect() as db:
        await db.execute(query, tuple(params))
        await db.commit()


async def ensure_user(user_id: int) -> aiosqlite.Row:
    row = await get_user(user_id)
    if row is not None:
        await _refresh_freeze_if_needed(user_id, row)
        if user_id == settings.admin_id and not row['is_admin']:
            await execute('UPDATE users SET is_admin=1 WHERE user_id=?', (user_id,))
        return await get_user(user_id)
    await execute(
        'INSERT INTO users (user_id, is_active, is_admin, daily_limit, forward_interval) VALUES (?, ?, ?, ?, ?)',
        (user_id, 1 if user_id == settings.admin_id else 0, 1 if user_id == settings.admin_id else 0, 0, settings.default_interval),
    )
    return await get_user(user_id)


async def get_user(user_id: int) -> aiosqlite.Row | None:
    return await fetchone('SELECT * FROM users WHERE user_id=?', (user_id,))


async def _refresh_freeze_if_needed(user_id: int, row: aiosqlite.Row | None) -> None:
    if row is None:
        return
    frozen_until = row['frozen_until']
    if row['is_frozen'] and frozen_until:
        try:
            until_dt = datetime.strptime(frozen_until, DATETIME_FORMAT)
        except ValueError:
            await execute('UPDATE users SET is_frozen=0, frozen_until=NULL WHERE user_id=?', (user_id,))
            return
        if datetime.now() >= until_dt:
            await execute('UPDATE users SET is_frozen=0, frozen_until=NULL, ban_strikes=0 WHERE user_id=?', (user_id,))


async def check_access(user_id: int) -> tuple[bool, str | None]:
    if user_id == settings.admin_id:
        return True, None
    row = await ensure_user(user_id)
    await _refresh_freeze_if_needed(user_id, row)
    row = await get_user(user_id)
    if row is None:
        return False, 'Foydalanuvchi topilmadi.'
    if row['is_frozen'] and row['frozen_until']:
        frozen_dt = datetime.strptime(row['frozen_until'], DATETIME_FORMAT)
        if datetime.now() < frozen_dt:
            left = frozen_dt - datetime.now()
            hours = max(1, int(left.total_seconds() // 3600))
            return False, f"Hisobingiz vaqtincha muzlatilgan. Taxminan {hours} soatdan keyin urinib ko'ring."
    if row['expiry_date']:
        try:
            expiry_dt = datetime.strptime(row['expiry_date'], DATETIME_FORMAT)
        except ValueError:
            expiry_dt = None
        if expiry_dt and datetime.now() > expiry_dt:
            await execute('UPDATE users SET is_active=0 WHERE user_id=?', (user_id,))
            return False, 'Kalit muddati tugagan. Yangi kalit oling.'
    return True, None


async def check_campaign_access(user_id: int) -> tuple[bool, str | None]:
    ok, reason = await check_access(user_id)
    if not ok:
        return ok, reason
    if user_id == settings.admin_id:
        return True, None
    row = await ensure_user(user_id)
    if row is None:
        return False, 'Foydalanuvchi topilmadi.'
    if not row['is_active'] or not row['expiry_date']:
        return False, 'Xabar yuborish uchun faol kalit kerak. Avval admin bergan kalitni faollashtiring.'
    try:
        expiry_dt = datetime.strptime(row['expiry_date'], DATETIME_FORMAT)
    except ValueError:
        return False, 'Kalit holatini tekshirib bo‘lmadi. Admin bilan bog‘laning.'
    if datetime.now() > expiry_dt:
        await execute('UPDATE users SET is_active=0 WHERE user_id=?', (user_id,))
        return False, 'Kalit muddati tugagan. Jarayon to‘xtatildi.'
    return True, None


async def update_user_account(user_id: int, phone: str, full_name: str, session_string: str | None, session_name: str | None = None) -> None:
    now = datetime.now().strftime(DATETIME_FORMAT)
    async with db_connect() as db:
        await db.execute(
            '''
            UPDATE users
            SET phone=?, full_name=?, session_string=?, session_name=?, is_active=1, updated_at=?
            WHERE user_id=?
            ''',
            (phone, full_name, session_string, session_name, now, user_id),
        )
        await db.execute('DELETE FROM user_groups WHERE user_id=?', (user_id,))
        await db.commit()


async def disconnect_user_account(user_id: int) -> None:
    async with db_connect() as db:
        await db.execute(
            'UPDATE users SET phone=NULL, full_name=NULL, session_string=NULL, session_name=NULL, updated_at=? WHERE user_id=?',
            (datetime.now().strftime(DATETIME_FORMAT), user_id),
        )
        await db.execute('DELETE FROM user_groups WHERE user_id=?', (user_id,))
        await db.commit()


async def save_login_session(user_id: int, phone: str, session_name: str, phone_code_hash: str, step: str = 'code_part1') -> None:
    now = datetime.now().strftime(DATETIME_FORMAT)
    async with db_connect() as db:
        await db.execute(
            '''
            INSERT INTO login_sessions (user_id, phone, session_name, phone_code_hash, code_part1, step, created_at, updated_at)
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                phone=excluded.phone,
                session_name=excluded.session_name,
                phone_code_hash=excluded.phone_code_hash,
                code_part1=NULL,
                step=excluded.step,
                updated_at=excluded.updated_at
            ''',
            (user_id, phone, session_name, phone_code_hash, step, now, now),
        )
        await db.commit()


async def get_login_session(user_id: int) -> aiosqlite.Row | None:
    return await fetchone('SELECT * FROM login_sessions WHERE user_id=?', (user_id,))


async def set_login_code_hash(user_id: int, phone_code_hash: str, step: str = 'code_part1') -> None:
    await execute(
        'UPDATE login_sessions SET phone_code_hash=?, code_part1=NULL, step=?, updated_at=? WHERE user_id=?',
        (phone_code_hash, step, datetime.now().strftime(DATETIME_FORMAT), user_id),
    )


async def set_login_code_part1(user_id: int, code_part1: str) -> None:
    await execute(
        'UPDATE login_sessions SET code_part1=?, step=?, updated_at=? WHERE user_id=?',
        (code_part1, 'code_part2', datetime.now().strftime(DATETIME_FORMAT), user_id),
    )


async def set_login_step(user_id: int, step: str) -> None:
    await execute(
        'UPDATE login_sessions SET step=?, updated_at=? WHERE user_id=?',
        (step, datetime.now().strftime(DATETIME_FORMAT), user_id),
    )


async def clear_login_session(user_id: int) -> None:
    await execute('DELETE FROM login_sessions WHERE user_id=?', (user_id,))


async def set_login_cooldown(user_id: int, until_at: str, reason: str | None = None) -> None:
    await execute(
        'UPDATE users SET login_cooldown_until=?, login_cooldown_reason=?, updated_at=? WHERE user_id=?',
        (until_at, reason, datetime.now().strftime(DATETIME_FORMAT), user_id),
    )


async def clear_login_cooldown(user_id: int) -> None:
    await execute(
        'UPDATE users SET login_cooldown_until=NULL, login_cooldown_reason=NULL, updated_at=? WHERE user_id=?',
        (datetime.now().strftime(DATETIME_FORMAT), user_id),
    )


async def get_login_cooldown(user_id: int) -> tuple[datetime | None, str | None]:
    row = await get_user(user_id)
    if row is None:
        return None, None
    until_text = row['login_cooldown_until']
    reason = row['login_cooldown_reason']
    if not until_text:
        return None, reason
    try:
        until_at = datetime.strptime(until_text, DATETIME_FORMAT)
    except ValueError:
        await clear_login_cooldown(user_id)
        return None, None
    if until_at <= datetime.now():
        await clear_login_cooldown(user_id)
        return None, None
    return until_at, reason


async def upsert_groups(user_id: int, groups: list[tuple[int, str]]) -> None:
    now = datetime.now().strftime(DATETIME_FORMAT)
    existing = await fetchall(
        'SELECT chat_id, auto_send FROM user_groups WHERE user_id=?',
        (user_id,),
    )
    auto_map = {row['chat_id']: row['auto_send'] for row in existing}
    found_ids = {chat_id for chat_id, _ in groups}
    async with db_connect() as db:
        for chat_id, title in groups:
            await db.execute(
                '''
                INSERT INTO user_groups (user_id, chat_id, title, is_admin, auto_send, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(user_id, chat_id) DO UPDATE SET
                    title=excluded.title,
                    is_admin=1,
                    auto_send=user_groups.auto_send,
                    updated_at=excluded.updated_at
                ''',
                (user_id, chat_id, title, auto_map.get(chat_id, 0), now),
            )
        if found_ids:
            placeholders = ','.join('?' for _ in found_ids)
            params = [user_id, *found_ids]
            await db.execute(
                f'UPDATE user_groups SET is_admin=0, updated_at=? WHERE user_id=? AND chat_id NOT IN ({placeholders})',
                (now, *params),
            )
        else:
            await db.execute(
                'UPDATE user_groups SET is_admin=0, updated_at=? WHERE user_id=?',
                (now, user_id),
            )
        await db.commit()


async def get_admin_groups(user_id: int) -> list[aiosqlite.Row]:
    return await fetchall(
        'SELECT chat_id, title, auto_send FROM user_groups WHERE user_id=? AND is_admin=1 ORDER BY title COLLATE NOCASE',
        (user_id,),
    )


async def get_selected_groups(user_id: int) -> list[aiosqlite.Row]:
    return await fetchall(
        'SELECT chat_id, title, auto_send FROM user_groups WHERE user_id=? AND is_admin=1 AND auto_send=1 ORDER BY title COLLATE NOCASE',
        (user_id,),
    )


async def toggle_group(user_id: int, chat_id: int) -> None:
    await execute(
        'UPDATE user_groups SET auto_send = CASE WHEN auto_send=1 THEN 0 ELSE 1 END WHERE user_id=? AND chat_id=?',
        (user_id, chat_id),
    )




async def set_all_groups_selection(user_id: int, enabled: bool) -> None:
    await execute(
        'UPDATE user_groups SET auto_send=?, updated_at=? WHERE user_id=? AND is_admin=1',
        (1 if enabled else 0, datetime.now().strftime(DATETIME_FORMAT), user_id),
    )


async def increment_used_today(user_id: int, amount: int) -> None:
    if amount <= 0:
        return
    await execute(
        'UPDATE users SET used_today = used_today + ?, updated_at=? WHERE user_id=?',
        (amount, datetime.now().strftime(DATETIME_FORMAT), user_id),
    )


async def set_user_interval(user_id: int, seconds: int) -> None:
    await execute(
        'UPDATE users SET forward_interval=?, updated_at=? WHERE user_id=?',
        (seconds, datetime.now().strftime(DATETIME_FORMAT), user_id),
    )


async def add_ban_strike(user_id: int, reason: str) -> int:
    row = await ensure_user(user_id)
    strike_num = int(row['ban_strikes'] or 0) + 1
    now = datetime.now().strftime(DATETIME_FORMAT)
    async with db_connect() as db:
        await db.execute(
            'UPDATE users SET ban_strikes=?, updated_at=? WHERE user_id=?',
            (strike_num, now, user_id),
        )
        await db.execute(
            'INSERT INTO ban_log (user_id, strike_num, reason, created_at) VALUES (?, ?, ?, ?)',
            (user_id, strike_num, reason, now),
        )
        await db.commit()
    return strike_num


async def freeze_user(user_id: int, hours: int) -> str:
    frozen_until = (datetime.now() + timedelta(hours=hours)).strftime(DATETIME_FORMAT)
    await execute(
        'UPDATE users SET is_frozen=1, frozen_until=?, updated_at=? WHERE user_id=?',
        (frozen_until, datetime.now().strftime(DATETIME_FORMAT), user_id),
    )
    return frozen_until


async def unfreeze_user(user_id: int) -> None:
    await execute(
        'UPDATE users SET is_frozen=0, frozen_until=NULL, ban_strikes=0, updated_at=? WHERE user_id=?',
        (datetime.now().strftime(DATETIME_FORMAT), user_id),
    )


async def reset_limits() -> None:
    await execute(
        'UPDATE users SET used_today=0, updated_at=?',
        (datetime.now().strftime(DATETIME_FORMAT),),
    )


async def generate_key(days: int, key_code: str) -> None:
    await execute(
        'INSERT INTO keys (key_code, days, is_used, created_at) VALUES (?, ?, 0, ?)',
        (key_code, days, datetime.now().strftime(DATETIME_FORMAT)),
    )


async def get_key(key_code: str) -> aiosqlite.Row | None:
    return await fetchone('SELECT * FROM keys WHERE key_code=?', (key_code,))


async def list_keys(limit: int = 30) -> list[aiosqlite.Row]:
    return await fetchall(
        '''
        SELECT keys.key_code, keys.days, keys.is_used, keys.used_by, keys.created_at, keys.used_at,
               users.full_name AS owner_name, users.phone AS owner_phone
        FROM keys
        LEFT JOIN users ON users.user_id = keys.used_by
        ORDER BY keys.created_at DESC
        LIMIT ?
        ''',
        (limit,),
    )


async def redeem_key(user_id: int, key_code: str) -> tuple[bool, str]:
    key_row = await get_key(key_code)
    if key_row is None:
        return False, 'Kalit topilmadi.'
    if key_row['is_used']:
        return False, 'Bu kalit avval ishlatilgan.'
    user = await ensure_user(user_id)
    now = datetime.now()
    expiry_base = now
    if user['expiry_date']:
        try:
            current_expiry = datetime.strptime(user['expiry_date'], DATETIME_FORMAT)
            if current_expiry > now:
                expiry_base = current_expiry
        except ValueError:
            expiry_base = now
    new_expiry = expiry_base + timedelta(days=int(key_row['days']))
    now_text = now.strftime(DATETIME_FORMAT)
    expiry_text = new_expiry.strftime(DATETIME_FORMAT)
    async with db_connect() as db:
        await db.execute(
            'UPDATE keys SET is_used=1, used_by=?, used_at=? WHERE key_code=?',
            (user_id, now_text, key_code),
        )
        await db.execute(
            'UPDATE users SET expiry_date=?, is_active=1, updated_at=? WHERE user_id=?',
            (expiry_text, now_text, user_id),
        )
        await db.commit()
    return True, expiry_text


async def list_users(limit: int = 30) -> list[aiosqlite.Row]:
    return await fetchall(
        '''
        SELECT user_id, full_name, phone, is_admin, daily_limit, used_today, forward_interval,
               ban_strikes, is_frozen, expiry_date
        FROM users
        WHERE phone IS NOT NULL
        ORDER BY updated_at DESC, created_at DESC
        LIMIT ?
        ''',
        (limit,),
    )


async def stats_rows() -> list[aiosqlite.Row]:
    return await fetchall(
        '''
        SELECT user_id, full_name, used_today, daily_limit, phone, forward_interval, expiry_date
        FROM users
        WHERE phone IS NOT NULL
        ORDER BY used_today DESC, full_name COLLATE NOCASE
        '''
    )


async def frozen_users() -> list[aiosqlite.Row]:
    rows = await fetchall(
        'SELECT user_id, full_name, frozen_until FROM users WHERE is_frozen=1 ORDER BY full_name COLLATE NOCASE'
    )
    active_rows = []
    for row in rows:
        await _refresh_freeze_if_needed(row['user_id'], row)
        current = await get_user(row['user_id'])
        if current and current['is_frozen']:
            active_rows.append(current)
    return active_rows


async def users_with_ban_strikes() -> list[aiosqlite.Row]:
    return await fetchall(
        'SELECT user_id, full_name, ban_strikes, is_frozen, frozen_until FROM users WHERE ban_strikes > 0 ORDER BY ban_strikes DESC, full_name COLLATE NOCASE'
    )


async def mark_admin(user_id: int, is_admin: bool) -> None:
    await ensure_user(user_id)
    await execute(
        'UPDATE users SET is_admin=?, updated_at=? WHERE user_id=?',
        (1 if is_admin else 0, datetime.now().strftime(DATETIME_FORMAT), user_id),
    )


async def is_admin_user(user_id: int) -> bool:
    row = await ensure_user(user_id)
    return bool(row and row['is_admin'])


async def list_admin_users() -> list[aiosqlite.Row]:
    return await fetchall(
        '''
        SELECT user_id, full_name, phone, is_admin, daily_limit, used_today, forward_interval,
               ban_strikes, is_frozen, expiry_date
        FROM users
        WHERE is_admin=1
        ORDER BY updated_at DESC, created_at DESC
        '''
    )


async def get_status_snapshot(user_id: int) -> dict[str, Any]:
    user = await ensure_user(user_id)
    await _refresh_freeze_if_needed(user_id, user)
    user = await get_user(user_id)
    groups = await get_admin_groups(user_id)
    selected = [row for row in groups if row['auto_send']]
    remaining = None
    expiry = user['expiry_date'] or 'Belgilanmagan'
    if user['expiry_date']:
        try:
            expiry = datetime.strptime(user['expiry_date'], DATETIME_FORMAT).strftime('%d.%m.%Y %H:%M')
        except ValueError:
            expiry = user['expiry_date']
    return {
        'user': user,
        'groups_count': len(groups),
        'selected_count': len(selected),
        'remaining': remaining,
        'expiry': expiry,
    }


async def get_connected_user(user_id: int) -> aiosqlite.Row | None:
    user = await get_user(user_id)
    if user and user['phone'] and (user['session_string'] or user['session_name']):
        return user
    return None


async def user_has_running_access(user_id: int) -> bool:
    ok, _ = await check_campaign_access(user_id)
    return ok


async def escape_name(value: str | None) -> str:
    return html.escape(value or '—')


async def block_user(user_id: int) -> None:
    await ensure_user(user_id)
    await execute(
        'UPDATE users SET is_active=0, updated_at=? WHERE user_id=?',
        (datetime.now().strftime(DATETIME_FORMAT), user_id),
    )


async def unblock_user(user_id: int) -> None:
    await ensure_user(user_id)
    await execute(
        'UPDATE users SET is_active=1, updated_at=? WHERE user_id=?',
        (datetime.now().strftime(DATETIME_FORMAT), user_id),
    )


async def delete_key(key_code: str) -> dict[str, Any] | None:
    row = await fetchone(
        '''
        SELECT keys.key_code, keys.days, keys.is_used, keys.used_by, keys.created_at, keys.used_at,
               users.full_name AS owner_name, users.phone AS owner_phone
        FROM keys
        LEFT JOIN users ON users.user_id = keys.used_by
        WHERE keys.key_code=?
        ''',
        (key_code,),
    )
    if row is None:
        return None
    if row['used_by']:
        await execute(
            'UPDATE users SET is_active=0, expiry_date=NULL, updated_at=? WHERE user_id=?',
            (datetime.now().strftime(DATETIME_FORMAT), row['used_by']),
        )
    await execute('DELETE FROM keys WHERE key_code=?', (key_code,))
    return {
        'key_code': row['key_code'],
        'days': row['days'],
        'is_used': row['is_used'],
        'used_by': row['used_by'],
        'owner_name': row['owner_name'],
        'owner_phone': row['owner_phone'],
    }


async def get_user_card(user_id: int) -> dict[str, Any] | None:
    row = await get_user(user_id)
    if row is None:
        return None
    groups_count_row = await fetchone(
        'SELECT COUNT(*) AS cnt FROM user_groups WHERE user_id=? AND is_admin=1',
        (user_id,),
    )
    selected_count_row = await fetchone(
        'SELECT COUNT(*) AS cnt FROM user_groups WHERE user_id=? AND is_admin=1 AND auto_send=1',
        (user_id,),
    )
    key_row = await fetchone(
        'SELECT key_code, days, created_at FROM keys WHERE used_by=? ORDER BY used_at DESC, created_at DESC LIMIT 1',
        (user_id,),
    )
    await _refresh_freeze_if_needed(user_id, row)
    row = await get_user(user_id)
    return {
        'user': row,
        'groups_count': int(groups_count_row['cnt'] if groups_count_row else 0),
        'selected_count': int(selected_count_row['cnt'] if selected_count_row else 0),
        'last_key': key_row,
    }
