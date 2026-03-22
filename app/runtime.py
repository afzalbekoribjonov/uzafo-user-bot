from __future__ import annotations

import asyncio

from pyrogram import Client

_pending_login_clients: dict[int, Client] = {}
_user_session_locks: dict[int, asyncio.Lock] = {}


def set_pending_login_client(user_id: int, client: Client) -> None:
    _pending_login_clients[user_id] = client


def get_pending_login_client(user_id: int) -> Client | None:
    return _pending_login_clients.get(user_id)


def pop_pending_login_client(user_id: int) -> Client | None:
    return _pending_login_clients.pop(user_id, None)


def has_pending_login_client(user_id: int) -> bool:
    return user_id in _pending_login_clients


def get_user_session_lock(user_id: int) -> asyncio.Lock:
    lock = _user_session_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_session_locks[user_id] = lock
    return lock


def is_user_session_busy(user_id: int) -> bool:
    return get_user_session_lock(user_id).locked()
