from __future__ import annotations

import html
import os

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.db import (
    check_access,
    clear_login_session,
    ensure_user,
    escape_name,
    get_connected_user,
    get_status_snapshot,
    is_admin_user,
    list_admin_users,
    redeem_key,
)
from app.keyboards import cancel_menu, main_menu
from app.services.user_clients import clear_pending_login, fetch_profile_snapshot
from app.runtime import is_user_session_busy
from app.states import AdminState
from app.texts import CANCEL_TEXT, WELCOME_TEXT

router = Router()


async def _clear_pending_login(user_id: int) -> None:
    await clear_pending_login(user_id, remove_session_files=False)
    await clear_login_session(user_id)


@router.message(Command('start'))
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await _clear_pending_login(message.from_user.id)
    await state.clear()
    await ensure_user(message.from_user.id)
    ok, reason = await check_access(message.from_user.id)
    if not ok:
        text = (
            f"{WELCOME_TEXT}\n\n"
            f"⚠️ {reason}\n\n"
            "🔑 Kalit bo‘limidan admin bergan kalitni kiritishingiz mumkin."
        )
    else:
        text = WELCOME_TEXT
    await message.answer(text, reply_markup=main_menu(await is_admin_user(message.from_user.id)))


@router.message(F.text == '❌ Bekor')
async def cancel_handler(message: types.Message, state: FSMContext) -> None:
    await _clear_pending_login(message.from_user.id)
    await state.clear()
    await message.answer(CANCEL_TEXT, reply_markup=main_menu(await is_admin_user(message.from_user.id)))


@router.message(F.text == '📊 Holat')
async def show_status(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    snapshot = await get_status_snapshot(message.from_user.id)
    user = snapshot['user']
    has_key = bool(user['expiry_date']) and bool(user['is_active'])
    status_text = 'Faol' if not user['is_frozen'] else 'Muzlatilgan'
    name = await escape_name(user['full_name'])
    phone = html.escape(user['phone'] or 'Ulanmagan')
    risk = 'Past'
    if int(user['ban_strikes'] or 0) >= 2:
        risk = 'Yuqori'
    elif int(user['ban_strikes'] or 0) == 1:
        risk = 'O‘rta'
    text = (
        '📊 Hisob holati\n\n'
        f'👤 Ism: {name}\n'
        f'📱 Raqam: {phone}\n'
        f'👥 Topilgan guruhlar: {snapshot["groups_count"]}\n'
        f'✅ Tanlangan guruhlar: {snapshot["selected_count"]}\n'
        f'⏱ Guruhlar oralig‘i: {user["forward_interval"]} soniya\n'
        f'🔐 Holat: {status_text}\n'
        f'🛡 Spam xavfi: {risk}\n'
        f'🚨 Strike: {user["ban_strikes"]}\n'
        f'📅 Amal qilish muddati: {snapshot["expiry"]}\n'
        f'🔑 Kalit holati: {"Faol" if has_key else "Faol emas"}\n\n'
        '♾ Yuborish limiti: cheksiz\n'
        '🧭 Jarayon: siz to‘xtatmaguningizcha yoki kalit tugamaguncha davom etadi'
    )
    if not has_key:
        text += '\n\n🔑 Sizda faol kalit yo‘q. Kalit olish uchun adminlarga yozing.\n🆘 Yordam bo‘limi orqali admin bilan bog‘lanishingiz mumkin.'
    connected = await get_connected_user(message.from_user.id)
    if connected and not is_user_session_busy(message.from_user.id):
        try:
            profile = await fetch_profile_snapshot(message.from_user.id, connected['session_name'], connected['session_string'])
            extra = (
                f"\n\n🪪 Telegram profili\n"
                f"• Username: @{html.escape(profile.username)}\n" if profile.username else "\n\n🪪 Telegram profili\n• Username: yo‘q\n"
            )
            extra += f"• Premium: {'Ha' if profile.is_premium else 'Yo‘q'}\n"
            extra += f"• DC: {profile.dc_id or '—'}\n"
            extra += f"• BIO: {html.escape(profile.bio or 'yo‘q')}"
            caption = text + extra
            if profile.profile_photo_path and os.path.exists(profile.profile_photo_path):
                await message.answer_photo(types.FSInputFile(profile.profile_photo_path), caption=caption, reply_markup=main_menu(await is_admin_user(message.from_user.id)))
                try:
                    os.remove(profile.profile_photo_path)
                except OSError:
                    pass
                return
            text = caption
        except Exception:
            pass
    await message.answer(text, reply_markup=main_menu(await is_admin_user(message.from_user.id)))


@router.message(F.text == '🔑 Kalit')
async def ask_key(message: types.Message, state: FSMContext) -> None:
    await state.set_state(AdminState.waiting_key)
    await message.answer(
        "🔑 Faollashtirish kalitini yuboring.\n\nNamuna: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx\n\nKalit olish uchun kerak bo‘lsa, 🆘 Yordam bo‘limidan adminlarga yozing.",
        reply_markup=cancel_menu(),
    )


@router.message(AdminState.waiting_key, F.text)
async def process_key(message: types.Message, state: FSMContext) -> None:
    key_code = message.text.strip()
    success, payload = await redeem_key(message.from_user.id, key_code)
    if not success:
        await message.answer(f'❌ {payload}')
        return
    await state.clear()
    await message.answer(
        '✅ Kalit muvaffaqiyatli faollashtirildi.\n\n'
        f'Amal qilish muddati: {payload}\n'
        '♾ Endi xabar yuborish jarayonlari cheksiz ishlaydi.',
        reply_markup=main_menu(await is_admin_user(message.from_user.id)),
    )
