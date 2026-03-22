from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.types import Message
from pyrogram import Client

from app.config import settings


@dataclass(slots=True)
class OutboundContent:
    kind: str
    text: str | None = None
    caption: str | None = None
    file_path: Path | None = None
    file_name: str | None = None


async def make_outbound_content(bot: Bot, message: Message) -> OutboundContent:
    if message.text:
        return OutboundContent(kind="text", text=message.text)
    if message.photo:
        path = await _download(bot, message.photo[-1].file_id, "jpg")
        return OutboundContent(kind="photo", caption=message.caption, file_path=path)
    if message.video:
        suffix = _guess_suffix(message.video.file_name, "mp4")
        path = await _download(bot, message.video.file_id, suffix)
        return OutboundContent(kind="video", caption=message.caption, file_path=path, file_name=message.video.file_name)
    if message.animation:
        suffix = _guess_suffix(message.animation.file_name, "mp4")
        path = await _download(bot, message.animation.file_id, suffix)
        return OutboundContent(kind="animation", caption=message.caption, file_path=path, file_name=message.animation.file_name)
    if message.document:
        suffix = _guess_suffix(message.document.file_name, "bin")
        path = await _download(bot, message.document.file_id, suffix)
        return OutboundContent(kind="document", caption=message.caption, file_path=path, file_name=message.document.file_name)
    if message.audio:
        suffix = _guess_suffix(message.audio.file_name, "mp3")
        path = await _download(bot, message.audio.file_id, suffix)
        return OutboundContent(kind="audio", caption=message.caption, file_path=path, file_name=message.audio.file_name)
    if message.voice:
        path = await _download(bot, message.voice.file_id, "ogg")
        return OutboundContent(kind="voice", file_path=path)
    if message.sticker:
        suffix = _guess_sticker_suffix(message.sticker)
        path = await _download(bot, message.sticker.file_id, suffix)
        return OutboundContent(kind="sticker", file_path=path)
    if message.video_note:
        path = await _download(bot, message.video_note.file_id, "mp4")
        return OutboundContent(kind="video_note", file_path=path)
    raise ValueError("Ushbu turdagi xabar hozircha qo'llab-quvvatlanmaydi.")


async def send_content(client: Client, chat_id: int, content: OutboundContent) -> None:
    if content.kind == "text":
        await client.send_message(chat_id, content.text or "")
        return
    if content.kind == "photo":
        await client.send_photo(chat_id, str(content.file_path), caption=content.caption or "")
        return
    if content.kind == "video":
        await client.send_video(chat_id, str(content.file_path), caption=content.caption or "", file_name=content.file_name)
        return
    if content.kind == "animation":
        await client.send_animation(chat_id, str(content.file_path), caption=content.caption or "", file_name=content.file_name)
        return
    if content.kind == "document":
        await client.send_document(chat_id, str(content.file_path), caption=content.caption or "", file_name=content.file_name)
        return
    if content.kind == "audio":
        await client.send_audio(chat_id, str(content.file_path), caption=content.caption or "", file_name=content.file_name)
        return
    if content.kind == "voice":
        await client.send_voice(chat_id, str(content.file_path))
        return
    if content.kind == "sticker":
        await client.send_sticker(chat_id, str(content.file_path))
        return
    if content.kind == "video_note":
        await client.send_video_note(chat_id, str(content.file_path))
        return
    raise ValueError("Noma'lum kontent turi")


async def cleanup_content(content: OutboundContent) -> None:
    if content.file_path and content.file_path.exists():
        try:
            os.remove(content.file_path)
        except OSError:
            pass


async def _download(bot: Bot, file_id: str, suffix: str) -> Path:
    filename = f"{uuid.uuid4().hex}.{suffix.lstrip('.')}"
    destination = settings.temp_dir / filename
    await bot.download(file=file_id, destination=destination)
    return destination



def _guess_suffix(file_name: str | None, fallback: str) -> str:
    if file_name and "." in file_name:
        return file_name.rsplit(".", 1)[-1]
    return fallback



def _guess_sticker_suffix(sticker) -> str:
    if sticker.is_animated:
        return "tgs"
    if sticker.is_video:
        return "webm"
    return "webp"
