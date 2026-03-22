from __future__ import annotations

import asyncio
import html
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable

from aiogram import Bot
from pyrogram.errors import ChatWriteForbidden, FloodWait, PeerFlood, PeerIdInvalid, SlowmodeWait, UserBannedInChannel

from app.db import add_ban_strike, check_campaign_access, freeze_user
from app.services.relay import OutboundContent, cleanup_content, send_content
from app.services.user_clients import SessionBusyError, build_authenticated_client, safe_stop, warmup_client_peers
from app.runtime import get_user_session_lock

RANDOM_WAIT_CHOICES = [60, 180, 300, 600]


class CampaignAccessStopped(RuntimeError):
    pass


@dataclass(slots=True)
class JobSummary:
    success: int = 0
    failed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    peer_source: str = 'unknown'
    dialogs_loaded: int = 0
    resolved: int = 0
    warmup_failed: int = 0


@dataclass(slots=True)
class ActiveJob:
    task: asyncio.Task
    kind: str
    status_message: object | None
    started_at: datetime
    groups_total: int
    repeat_count: int
    repeat_interval_seconds: int
    interval_mode: str = 'fixed'
    pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    is_paused: bool = False
    current_round: int = 0
    current_index: int = 0
    current_title: str = ''
    waiting_text: str = ''
    summary: JobSummary = field(default_factory=JobSummary)


active_jobs: dict[int, ActiveJob] = {}


def has_active_job(user_id: int) -> bool:
    job = active_jobs.get(user_id)
    return bool(job and not job.task.done())


def register_job(
    user_id: int,
    task: asyncio.Task,
    *,
    kind: str,
    status_message,
    groups_total: int,
    repeat_count: int,
    repeat_interval_seconds: int,
    interval_mode: str = 'fixed',
) -> None:
    active_jobs[user_id] = ActiveJob(
        task=task,
        kind=kind,
        status_message=status_message,
        started_at=datetime.now(),
        groups_total=groups_total,
        repeat_count=repeat_count,
        repeat_interval_seconds=repeat_interval_seconds,
        interval_mode=interval_mode,
    )
    active_jobs[user_id].pause_event.set()

    def _cleanup(done_task: asyncio.Task) -> None:
        current = active_jobs.get(user_id)
        if current and current.task is task:
            active_jobs.pop(user_id, None)
        try:
            done_task.exception()
        except Exception:
            pass

    task.add_done_callback(_cleanup)


def get_active_job_snapshot(user_id: int) -> dict | None:
    job = active_jobs.get(user_id)
    if not job or job.task.done():
        return None
    elapsed = datetime.now() - job.started_at
    elapsed_seconds = max(0, int(elapsed.total_seconds()))
    return {
        'kind': job.kind,
        'started_at': job.started_at,
        'elapsed_seconds': elapsed_seconds,
        'groups_total': job.groups_total,
        'repeat_count': job.repeat_count,
        'repeat_interval_seconds': job.repeat_interval_seconds,
        'interval_mode': job.interval_mode,
        'current_round': job.current_round,
        'current_index': job.current_index,
        'current_title': job.current_title,
        'waiting_text': job.waiting_text,
        'is_paused': job.is_paused,
        'summary': job.summary,
    }


async def stop_active_job(user_id: int) -> bool:
    job = active_jobs.get(user_id)
    if not job or job.task.done():
        return False
    job.task.cancel()
    return True


async def toggle_pause_job(user_id: int) -> tuple[bool, bool]:
    job = active_jobs.get(user_id)
    if not job or job.task.done():
        return False, False
    if job.is_paused:
        job.is_paused = False
        job.waiting_text = ''
        job.pause_event.set()
        return True, False
    job.is_paused = True
    job.waiting_text = '⏸ Jarayon foydalanuvchi tomonidan pauzaga qo‘yildi.'
    job.pause_event.clear()
    return True, True


async def _safe_edit(status_message, text: str) -> None:
    if status_message is None:
        return
    try:
        await status_message.edit_text(text)
    except Exception:
        try:
            await status_message.answer(text)
        except Exception:
            pass


def _human_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f'{days} kun')
    if hours:
        parts.append(f'{hours} soat')
    if minutes:
        parts.append(f'{minutes} daqiqa')
    if secs or not parts:
        parts.append(f'{secs} soniya')
    return ', '.join(parts)


async def _ensure_campaign_access(user_id: int, status_message=None) -> None:
    ok, reason = await check_campaign_access(user_id)
    if ok:
        return
    message = (
        '🛑 Faol jarayon to‘xtatildi.\n\n'
        f'{reason}\n\n'
        'Yangi kalit faollashtirsangiz, jarayonni qayta boshlashingiz mumkin.'
    )
    if status_message is not None:
        await _safe_edit(status_message, message)
    raise CampaignAccessStopped(reason or 'campaign_access_denied')


async def _wait_if_paused(user_id: int, status_message=None) -> None:
    while True:
        await _ensure_campaign_access(user_id, status_message)
        job = active_jobs.get(user_id)
        if not job or job.task.done():
            return
        if not job.is_paused:
            return
        if status_message is not None:
            await _safe_edit(status_message, _paused_text(job))
        await job.pause_event.wait()


async def _cooperative_sleep(user_id: int, seconds: int, status_message=None) -> None:
    remaining = max(0, int(seconds))
    while remaining > 0:
        await _wait_if_paused(user_id, status_message)
        await _ensure_campaign_access(user_id, status_message)
        chunk = 1 if remaining > 1 else remaining
        await asyncio.sleep(chunk)
        remaining -= chunk


def _pick_cycle_wait(interval_mode: str, repeat_interval_seconds: int) -> tuple[int, str]:
    if interval_mode == 'random':
        seconds = random.choice(RANDOM_WAIT_CHOICES)
        return seconds, f'🎲 Random kutish tanlandi: {_human_seconds(seconds)}'
    return max(0, int(repeat_interval_seconds)), ''


async def run_text_broadcast(
    bot: Bot,
    user_id: int,
    session_name: str | None,
    session_string: str | None,
    groups: list,
    text: str,
    interval: int,
    status_message,
    repeat_count: int = 0,
    repeat_interval_seconds: int = 0,
    interval_mode: str = 'fixed',
) -> JobSummary:
    lock = get_user_session_lock(user_id)
    if lock.locked():
        await _safe_edit(status_message, '⏳ Hozir akkauntingiz bilan boshqa jarayon ishlayapti. Avval uni yakunlang yoki kuting.')
        return JobSummary()
    await lock.acquire()
    client = build_authenticated_client(user_id, session_name, session_string)
    summary = JobSummary(peer_source='session_string' if session_string else 'session_file')
    _update_job(user_id, summary=summary)
    finished_title = 'Xabar yuborish to‘xtatildi'
    try:
        try:
            await client.start()
            await client.get_me()
            warmup = await warmup_client_peers(client, groups)
            summary.dialogs_loaded = warmup['dialogs_loaded']
            summary.resolved = warmup['resolved']
            summary.warmup_failed = warmup['failed']
            _update_job(user_id, summary=summary)
        except Exception as error:
            await _safe_edit(status_message, f"⚠️ Akkauntni ochib bo‘lmadi: {str(error)[:180]}")
            return summary

        round_no = 0
        while True:
            await _ensure_campaign_access(user_id, status_message)
            round_no += 1
            if repeat_count and round_no > repeat_count:
                finished_title = 'Xabar yuborish yakunlandi'
                break
            _update_job(user_id, current_round=round_no, current_index=0, current_title='', waiting_text='')
            await _wait_if_paused(user_id, status_message)
            for index, group in enumerate(groups, start=1):
                await _wait_if_paused(user_id, status_message)
                await _ensure_campaign_access(user_id, status_message)
                chat_id = int(group['chat_id'])
                title = str(group['title'])
                action = lambda current_chat_id=chat_id: _send_text(client, current_chat_id, text)
                should_stop = await _handle_action(
                    bot=bot,
                    action=action,
                    user_id=user_id,
                    title=title,
                    summary=summary,
                    status_message=status_message,
                    index=index,
                    total=len(groups),
                    round_no=round_no,
                    repeat_count=repeat_count,
                )
                _update_job(user_id, summary=summary, current_round=round_no, current_index=index, current_title=title, waiting_text='')
                if should_stop:
                    finished_title = 'Xabar yuborish xavfsizlik sabab to‘xtatildi'
                    break
                if index < len(groups):
                    await _cooperative_sleep(user_id, interval, status_message)
            else:
                cycle_wait, note = _pick_cycle_wait(interval_mode, repeat_interval_seconds)
                if cycle_wait > 0:
                    wait_text = _wait_round_text(round_no, repeat_count, cycle_wait, summary, note=note)
                    _update_job(user_id, summary=summary, waiting_text=wait_text)
                    await _safe_edit(status_message, wait_text)
                    await _cooperative_sleep(user_id, cycle_wait, status_message)
                    _update_job(user_id, waiting_text='')
                continue
            break
    except CampaignAccessStopped:
        return summary
    except asyncio.CancelledError:
        await _safe_edit(status_message, _cancelled_text('Xabar yuborish', summary))
        return summary
    finally:
        await safe_stop(client)
        if lock.locked():
            lock.release()
    await _finish_status(status_message, finished_title, summary, len(groups), repeat_count)
    return summary


async def run_copy_forward(
    bot: Bot,
    user_id: int,
    session_name: str | None,
    session_string: str | None,
    groups: list,
    content: OutboundContent,
    interval: int,
    status_message,
    repeat_count: int = 0,
    repeat_interval_seconds: int = 0,
    interval_mode: str = 'fixed',
) -> JobSummary:
    lock = get_user_session_lock(user_id)
    if lock.locked():
        await _safe_edit(status_message, '⏳ Hozir akkauntingiz bilan boshqa jarayon ishlayapti. Avval uni yakunlang yoki kuting.')
        return JobSummary()
    await lock.acquire()
    client = build_authenticated_client(user_id, session_name, session_string)
    summary = JobSummary(peer_source='session_string' if session_string else 'session_file')
    _update_job(user_id, summary=summary)
    finished_title = 'Forward jarayoni to‘xtatildi'
    try:
        try:
            await client.start()
            await client.get_me()
            warmup = await warmup_client_peers(client, groups)
            summary.dialogs_loaded = warmup['dialogs_loaded']
            summary.resolved = warmup['resolved']
            summary.warmup_failed = warmup['failed']
            _update_job(user_id, summary=summary)
        except Exception as error:
            await _safe_edit(status_message, f"⚠️ Akkauntni ochib bo‘lmadi: {str(error)[:180]}")
            return summary

        round_no = 0
        while True:
            await _ensure_campaign_access(user_id, status_message)
            round_no += 1
            if repeat_count and round_no > repeat_count:
                finished_title = 'Forward yakunlandi'
                break
            _update_job(user_id, current_round=round_no, current_index=0, current_title='', waiting_text='')
            await _wait_if_paused(user_id, status_message)
            for index, group in enumerate(groups, start=1):
                await _wait_if_paused(user_id, status_message)
                await _ensure_campaign_access(user_id, status_message)
                chat_id = int(group['chat_id'])
                title = str(group['title'])
                action = lambda current_chat_id=chat_id: _send_content(client, current_chat_id, content)
                should_stop = await _handle_action(
                    bot=bot,
                    action=action,
                    user_id=user_id,
                    title=title,
                    summary=summary,
                    status_message=status_message,
                    index=index,
                    total=len(groups),
                    round_no=round_no,
                    repeat_count=repeat_count,
                )
                _update_job(user_id, summary=summary, current_round=round_no, current_index=index, current_title=title, waiting_text='')
                if should_stop:
                    finished_title = 'Forward xavfsizlik sabab to‘xtatildi'
                    break
                if index < len(groups):
                    await _cooperative_sleep(user_id, interval, status_message)
            else:
                cycle_wait, note = _pick_cycle_wait(interval_mode, repeat_interval_seconds)
                if cycle_wait > 0:
                    wait_text = _wait_round_text(round_no, repeat_count, cycle_wait, summary, note=note)
                    _update_job(user_id, summary=summary, waiting_text=wait_text)
                    await _safe_edit(status_message, wait_text)
                    await _cooperative_sleep(user_id, cycle_wait, status_message)
                    _update_job(user_id, waiting_text='')
                continue
            break
    except CampaignAccessStopped:
        return summary
    except asyncio.CancelledError:
        await _safe_edit(status_message, _cancelled_text('Forward yuborish', summary))
        return summary
    finally:
        await safe_stop(client)
        await cleanup_content(content)
        if lock.locked():
            lock.release()
    await _finish_status(status_message, finished_title, summary, len(groups), repeat_count)
    return summary


async def _send_text(client, chat_id: int, text: str) -> None:
    await client.send_message(chat_id, text)


async def _send_content(client, chat_id: int, content: OutboundContent) -> None:
    await send_content(client, chat_id, content)


async def _handle_action(
    *,
    bot: Bot,
    action: Callable[[], Awaitable[None]],
    user_id: int,
    title: str,
    summary: JobSummary,
    status_message,
    index: int,
    total: int,
    round_no: int,
    repeat_count: int,
) -> bool:
    try:
        await action()
        summary.success += 1
    except (ChatWriteForbidden, UserBannedInChannel) as error:
        summary.failed += 1
        summary.errors.append(f'{title}: yozish taqiqlangan ({error.__class__.__name__})')
    except PeerIdInvalid as error:
        summary.failed += 1
        summary.errors.append(f'{title}: chat topilmadi ({error.__class__.__name__})')
    except SlowmodeWait as error:
        summary.skipped += 1
        summary.errors.append(f'{title}: slowmode {error.value}s')
    except FloodWait as error:
        strike = await add_ban_strike(user_id, f'FloodWait {error.value}s')
        summary.failed += 1
        summary.errors.append(f'{title}: FloodWait {error.value}s')
        if strike >= 2:
            frozen_until = await freeze_user(user_id, 24)
            await _safe_edit(
                status_message,
                '🧊 Jarayon to‘xtatildi.\n\n'
                'Telegram ushbu akkauntni vaqtincha chekladi.\n'
                f'Muzlatish muddati: {frozen_until}\n\n'
                '24 soatdan keyin qayta urinib ko‘ring.',
            )
            return True
        await _cooperative_sleep(user_id, int(error.value), status_message)
    except PeerFlood as error:
        strike = await add_ban_strike(user_id, 'PeerFlood')
        summary.failed += 1
        summary.errors.append(f'{title}: PeerFlood')
        if strike >= 2:
            frozen_until = await freeze_user(user_id, 24)
            await _safe_edit(
                status_message,
                '🧊 Jarayon to‘xtatildi.\n\n'
                'Telegram ushbu akkauntni spam xavfi sabab vaqtincha chekladi.\n'
                f'Muzlatish muddati: {frozen_until}',
            )
            return True
    except Exception as error:
        summary.failed += 1
        summary.errors.append(f'{title}: {html.escape(str(error)[:100])}')
    await _safe_edit(
        status_message,
        _progress_text(index=index, total=total, summary=summary, current_title=title, round_no=round_no, repeat_count=repeat_count),
    )
    return False


def _update_job(user_id: int, **kwargs) -> None:
    job = active_jobs.get(user_id)
    if not job:
        return
    for key, value in kwargs.items():
        setattr(job, key, value)


async def _finish_status(status_message, title: str, summary: JobSummary, total: int, repeat_count: int) -> None:
    cycle_text = 'cheksiz' if repeat_count == 0 else str(repeat_count)
    text = (
        f'✅ {title}\n\n'
        f'Chatlar: {total}\n'
        f'Aylana soni: {cycle_text}\n'
        f'Muvaffaqiyatli: {summary.success}\n'
        f'Xatolik: {summary.failed}\n'
        f'O‘tkazib yuborildi: {summary.skipped}\n'
        f'Peer manbasi: {summary.peer_source}\n'
        f'Yuklangan dialoglar: {summary.dialogs_loaded}\n'
        f'Resolve bo‘lgan chatlar: {summary.resolved}\n'
        f'Resolve xatolari: {summary.warmup_failed}'
    )
    if summary.errors:
        text += '\n\nSo‘nggi xatolar:\n' + '\n'.join(summary.errors[-5:])
    await _safe_edit(status_message, text)



def _progress_text(index: int, total: int, summary: JobSummary, current_title: str, round_no: int, repeat_count: int) -> str:
    cycle_text = f'{round_no}/∞' if repeat_count == 0 else f'{round_no}/{repeat_count}'
    return (
        '🚀 Jarayon davom etmoqda\n\n'
        f'Aylana: {cycle_text}\n'
        f'Chat: {index}/{total}\n'
        f'Hozirgi chat: {html.escape(current_title)}\n\n'
        f'✅ Muvaffaqiyatli: {summary.success}\n'
        f'⚠️ Xatolik: {summary.failed}\n'
        f'⏭ O‘tkazib yuborildi: {summary.skipped}'
    )



def _wait_round_text(round_no: int, repeat_count: int, wait_seconds: int, summary: JobSummary, note: str = '') -> str:
    cycle_text = f'{round_no}/∞' if repeat_count == 0 else f'{round_no}/{repeat_count}'
    extra = f'\n{note}' if note else ''
    return (
        '⏳ Keyingi aylana kutilyapti\n\n'
        f'Tugagan aylana: {cycle_text}\n'
        f'Keyingi aylana boshlanishi: {_human_seconds(wait_seconds)} dan keyin\n\n'
        f'✅ Muvaffaqiyatli: {summary.success}\n'
        f'⚠️ Xatolik: {summary.failed}\n'
        f'⏭ O‘tkazib yuborildi: {summary.skipped}'
        f'{extra}'
    )



def _paused_text(job: ActiveJob) -> str:
    return (
        '⏸ Jarayon pauzada\n\n'
        f'Aylana: {max(1, job.current_round or 1)}/{"∞" if job.repeat_count == 0 else job.repeat_count}\n'
        f'Chat: {job.current_index}/{job.groups_total}\n'
        f'Hozirgi chat: {html.escape(job.current_title or "hali boshlanmagan")}\n\n'
        f'✅ Muvaffaqiyatli: {job.summary.success}\n'
        f'⚠️ Xatolik: {job.summary.failed}\n'
        f'⏭ O‘tkazib yuborildi: {job.summary.skipped}\n\n'
        '▶️ Davom ettirish tugmasi bosilsa, jarayon shu joydan davom etadi.'
    )



def _cancelled_text(title: str, summary: JobSummary) -> str:
    text = (
        f'🛑 {title} foydalanuvchi tomonidan to‘xtatildi.\n\n'
        f'✅ Muvaffaqiyatli: {summary.success}\n'
        f'⚠️ Xatolik: {summary.failed}\n'
        f'⏭ O‘tkazib yuborildi: {summary.skipped}'
    )
    if summary.errors:
        text += '\n\nSo‘nggi xatolar:\n' + '\n'.join(summary.errors[-5:])
    return text
