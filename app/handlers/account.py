from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router, types
from aiogram.filters import Filter
from aiogram.fsm.context import FSMContext
from pyrogram.errors import (
    BadRequest,
    FloodWait,
    PasswordHashInvalid,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberBanned,
    PhoneNumberFlood,
    PhoneNumberInvalid,
    SessionPasswordNeeded,
)

from app.config import settings
from app.db import (
    check_access,
    clear_login_cooldown,
    clear_login_session,
    disconnect_user_account,
    get_connected_user,
    get_login_cooldown,
    get_login_session,
    save_login_session,
    set_login_code_part1,
    set_login_cooldown,
    set_login_step,
    update_user_account,
    is_admin_user,
)
from app.keyboards import account_menu, cancel_menu, login_code_menu, login_start_menu, login_wait_menu, main_menu, qr_login_menu
from app.services.user_clients import (
    PendingLoginMissingError,
    QrLoginPending,
    check_qr_login,
    clear_pending_login,
    complete_2fa,
    complete_login,
    diagnose_api_pair,
    login_session_exists,
    refresh_qr_login,
    restart_login,
    start_login,
    start_qr_login,
    update_profile_bio,
    update_profile_name,
)
from app.runtime import is_user_session_busy
from app.states import LoginState

router = Router()
PHONE_RE = re.compile(r'^\+[1-9]\d{7,14}$')
logger = logging.getLogger(__name__)
TASHKENT_TZ = ZoneInfo('Asia/Tashkent')


def _format_wait(seconds: int) -> str:
    seconds = max(1, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f'{hours} soat')
    if minutes:
        parts.append(f'{minutes} daqiqa')
    if secs and not hours:
        parts.append(f'{secs} soniya')
    return ' '.join(parts) or 'bir necha soniya'


def _format_until_local(seconds: int) -> str:
    until_dt = datetime.now(TASHKENT_TZ) + timedelta(seconds=max(1, int(seconds)))
    return until_dt.strftime('%d-%m-%Y %H:%M')


def _cooldown_message(seconds: int, *, reason: str | None = None) -> str:
    human = _format_wait(seconds)
    until_text = _format_until_local(seconds)
    reason_text = reason or (
        'Telegram xavfsizlik sababli shu raqam uchun yangi kirish kodini vaqtincha toʻxtatib turibdi. '
        'Odatda bunga qisqa vaqt ichida juda ko‘p urinish qilish sabab bo‘ladi.'
    )
    return (
        '⏳ Hozircha yangi tasdiqlash kodini so‘rab bo‘lmaydi.\n\n'
        f'{reason_text}\n\n'
        f'Taxminiy kutish vaqti: {human}.\n'
        f'Qayta urinib ko‘rish mumkin bo‘ladigan vaqt: {until_text} (Toshkent vaqti).\n\n'
        'Shu vaqt ichida Kodni qayta yuborish tugmasini bosmang. '
        'Agar shu akkaunt boshqa rasmiy Telegram ilovasida allaqachon ochiq bo‘lsa, QR orqali ulashni sinab ko‘rishingiz mumkin.'
    )

async def _active_login_cooldown_text(user_id: int) -> str | None:
    until_at, reason = await get_login_cooldown(user_id)
    if until_at is None:
        return None
    remaining = max(1, int((until_at - datetime.now()).total_seconds()))
    return _cooldown_message(remaining, reason=reason)


class HasPendingLogin(Filter):
    async def __call__(self, message: types.Message) -> bool:
        row = await get_login_session(message.from_user.id)
        return row is not None


async def _main_menu_markup(user_id: int):
    return main_menu(await is_admin_user(user_id))


async def _clear_pending_login(user_id: int, remove_session_files: bool = True) -> None:
    await clear_pending_login(user_id, remove_session_files=remove_session_files)
    await clear_login_session(user_id)


async def _ask_phone(message: types.Message) -> None:
    await message.answer(
        '📱 Telefon raqamingizni yuboring.\n\n'
        'Namuna: +998901234567\n\n'
        'Agar kod umuman kelmasa, QR orqali ulash tugmasidan foydalaning.',
        reply_markup=cancel_menu(),
    )
    await message.answer('Yoki pastdagi tugma orqali QR bilan ulashing.', reply_markup=login_start_menu())


async def _ask_code_part1(
    target_message: types.Message | None,
    sent_code_type: str = 'Telegram yoki SMS',
    next_type: str = 'yo‘q',
    timeout: int | None = None,
    allow_resend: bool = True,
) -> None:
    wait_text = ''
    if timeout:
        wait_text = f'\n⏳ Keyingi usulga o‘tishdan oldin taxminan {timeout} soniya kuting.'
    text = (
        '📩 Tasdiqlash kodi so‘raldi.\n\n'
        f'📬 Hozirgi yetkazish turi: {sent_code_type}\n'
        f'➡️ Keyingi ehtimoliy tur: {next_type}{wait_text}\n\n'
        'Kodni ikki qismga bo‘lib yuboring.\n\n'
        '1-bosqich: kodning birinchi 2 raqamini yuboring.\n'
        'Masalan, kod 45221 bo‘lsa, avval 45 ni yuborasiz.\n\n'
        'Kod odatda rasmiy Telegram ilovasidagi Telegram servis chatiga keladi.\n'
        'Agar kod kelmasa, Kodni qayta yuborish yoki QR orqali ulash tugmasini bosing.'
    )
    if target_message:
        await target_message.answer(text, reply_markup=login_code_menu())


async def _ask_code_part2(target_message: types.Message) -> None:
    await target_message.answer(
        '✅ Birinchi qism qabul qilindi.\n\n'
        '2-bosqich: endi kodning oxirgi 3 raqamini yuboring.\n'
        'Masalan, 45221 kodi uchun 221 ni yuborasiz.\n\n'
        'Agar xohlasangiz, shu bosqichda 5 xonali kodni to‘liq ham yuborishingiz mumkin.',
        reply_markup=login_code_menu(),
    )


async def _send_qr_prompt(message: types.Message, image_path: str, login_url: str, expires_at: int | None) -> None:
    expire_text = '30 soniya atrofida'
    if expires_at:
        left = max(1, int(expires_at - datetime.now().timestamp()))
        expire_text = f'{left} soniya atrofida'
    photo = types.FSInputFile(image_path)
    caption = (
        '🔳 QR orqali ulash tayyor.\n\n'
        '1) QR kodni allaqachon login qilingan rasmiy Telegram ilovasi bilan skaner qiling.\n'
        '2) Ilova ichida tasdiqlang.\n'
        '3) So‘ng botdagi ✅ Tasdiqladim tugmasini bosing.\n\n'
        f'QR amal qilish vaqti: {expire_text}.\n\n'
        f'Agar rasm ochilmasa, mana havola: <code>{html.escape(login_url)}</code>'
    )
    await message.answer_photo(photo=photo, caption=caption, reply_markup=qr_login_menu())


async def _require_live_login(message: types.Message, state: FSMContext) -> tuple[bool, object]:
    row = await get_login_session(message.from_user.id)
    if not row:
        await state.clear()
        await message.answer(
            '❌ Login jarayoni topilmadi. Jarayonni boshidan boshlang.',
            reply_markup=await _main_menu_markup(message.from_user.id),
        )
        return False, None
    if row['step'] != 'qr' and not login_session_exists(row['session_name']):
        await set_login_step(message.from_user.id, 'code_part1')
        await state.set_state(LoginState.waiting_code_part1)
        await message.answer(
            '⚠️ Tasdiqlash jarayoni uzilib qoldi. Kodni qayta yuborish yoki QR orqali ulash tugmasini bosing.',
            reply_markup=login_code_menu(),
        )
        return False, row
    return True, row


@router.message(F.text == '📱 Akkaunt')
async def account_entry(message: types.Message, state: FSMContext) -> None:
    ok, reason = await check_access(message.from_user.id)
    if not ok:
        await message.answer(f'❌ {reason}', reply_markup=await _main_menu_markup(message.from_user.id))
        return
    user = await get_connected_user(message.from_user.id)
    if user:
        name = html.escape(user['full_name'] or '—')
        phone = html.escape(user['phone'] or '—')
        await message.answer(
            '✅ Akkaunt ulangan.\n\n'
            f'👤 Ism: {name}\n'
            f'📱 Raqam: {phone}\n\n'
            "Pastdagi tugmalar orqali profil ma'lumotlarini ham boshqarishingiz mumkin.",
            reply_markup=account_menu(),
        )
        return
    await _clear_pending_login(message.from_user.id)
    await state.set_state(LoginState.waiting_phone)
    await _ask_phone(message)


@router.callback_query(F.data == 'account_change')
async def account_change(callback: types.CallbackQuery, state: FSMContext) -> None:
    await _clear_pending_login(callback.from_user.id, remove_session_files=False)
    await state.set_state(LoginState.waiting_phone)
    await callback.message.edit_text('📱 Yangi telefon raqamingizni yuboring.\n\nNamuna: +998901234567')
    await callback.message.answer('Yoki QR orqali ulashdan foydalaning.', reply_markup=login_start_menu())
    await callback.answer()


@router.callback_query(F.data == 'account_disconnect')
async def account_disconnect(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _clear_pending_login(callback.from_user.id)
    await disconnect_user_account(callback.from_user.id)
    await callback.message.edit_text('✅ Akkaunt uzildi.')
    await callback.message.answer(
        'Asosiy menyudan davom etishingiz mumkin.',
        reply_markup=await _main_menu_markup(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == 'login_use_qr')
async def login_use_qr(callback: types.CallbackQuery, state: FSMContext) -> None:
    await _clear_pending_login(callback.from_user.id)
    try:
        qr_session = await start_qr_login(callback.from_user.id)
    except BadRequest as error:
        logger.exception('start_qr_login bad request for user_id=%s', callback.from_user.id)
        await callback.answer(f'QR login ochib bo‘lmadi: {str(error)[:120]}', show_alert=True)
        return
    except Exception as error:
        logger.exception('start_qr_login failed for user_id=%s', callback.from_user.id)
        await callback.answer(f'QR login ochib bo‘lmadi: {error.__class__.__name__}', show_alert=True)
        return
    await save_login_session(
        callback.from_user.id,
        phone='QR',
        session_name=qr_session.session_name,
        phone_code_hash='QR',
        step='qr',
    )
    await state.clear()
    await _send_qr_prompt(callback.message, qr_session.image_path, qr_session.login_url, qr_session.expires_at)
    await callback.answer()


@router.callback_query(F.data == 'login_qr_refresh')
async def login_qr_refresh(callback: types.CallbackQuery, state: FSMContext) -> None:
    row = await get_login_session(callback.from_user.id)
    if not row or row['step'] != 'qr':
        await callback.answer('Faol QR login topilmadi.', show_alert=True)
        return
    try:
        qr_session = await refresh_qr_login(callback.from_user.id)
    except Exception as error:
        logger.exception('refresh_qr_login failed for user_id=%s', callback.from_user.id)
        await callback.answer(f'QR yangilanmadi: {str(error)[:120]}', show_alert=True)
        return
    await save_login_session(
        callback.from_user.id,
        phone='QR',
        session_name=qr_session.session_name,
        phone_code_hash='QR',
        step='qr',
    )
    await callback.message.answer('🔄 QR yangilandi.')
    await _send_qr_prompt(callback.message, qr_session.image_path, qr_session.login_url, qr_session.expires_at)
    await callback.answer('QR yangilandi.', show_alert=False)


@router.callback_query(F.data == 'login_qr_check')
async def login_qr_check(callback: types.CallbackQuery, state: FSMContext) -> None:
    row = await get_login_session(callback.from_user.id)
    if not row or row['step'] != 'qr':
        await callback.answer('Faol QR login topilmadi.', show_alert=True)
        return
    try:
        session_string, full_name = await check_qr_login(callback.from_user.id)
    except QrLoginPending:
        await callback.answer('Hali tasdiqlanmagan. QR faqat shu akkaunt boshqa rasmiy Telegram ilovasida allaqachon ochiq bo‘lsa ishlaydi. Ilovada tasdiqlab, qayta bosing.', show_alert=True)
        return
    except Exception as error:
        logger.exception('check_qr_login failed for user_id=%s', callback.from_user.id)
        await callback.answer(f'QR login tekshiruvida xato: {str(error)[:120]}', show_alert=True)
        return
    await update_user_account(callback.from_user.id, 'QR-login', full_name, session_string, session_name=row['session_name'])
    await _clear_pending_login(callback.from_user.id, remove_session_files=False)
    await state.clear()
    await callback.message.answer(
        '✅ Akkaunt muvaffaqiyatli ulandi.\n\n'
        f'👤 Ism: {html.escape(full_name)}\n'
        '📱 Raqam: QR login orqali ulandi\n\n'
        'Endi guruhlarni skanerlab, yuborish uchun tanlashingiz mumkin.',
        reply_markup=await _main_menu_markup(callback.from_user.id),
    )
    await callback.answer('QR login muvaffaqiyatli yakunlandi.', show_alert=True)


@router.message(LoginState.waiting_phone, F.text)
async def process_phone(message: types.Message, state: FSMContext) -> None:
    phone = message.text.strip().replace(' ', '')
    if not PHONE_RE.fullmatch(phone):
        await message.answer('❌ Telefon raqamini xalqaro formatda kiriting. Namuna: +998901234567')
        return
    cooldown_text = await _active_login_cooldown_text(message.from_user.id)
    if cooldown_text:
        await message.answer(cooldown_text, reply_markup=login_wait_menu())
        return
    await _clear_pending_login(message.from_user.id)
    try:
        login_session = await start_login(phone=phone, user_id=message.from_user.id)
    except PhoneNumberInvalid:
        await message.answer('❌ Telefon raqami noto‘g‘ri.')
        return
    except PhoneNumberBanned:
        await message.answer('🚫 Ushbu raqam Telegram tomonidan cheklangan.')
        return
    except PhoneNumberFlood:
        wait_seconds = 24 * 3600
        reason = 'Telegram bu raqam uchun kirish kodini juda ko‘p marta so‘ralgan deb hisoblab, vaqtincha chekladi.'
        await set_login_cooldown(
            message.from_user.id,
            (datetime.now() + timedelta(seconds=wait_seconds)).strftime('%Y-%m-%d %H:%M:%S'),
            reason,
        )
        await message.answer(_cooldown_message(wait_seconds, reason=reason), reply_markup=login_wait_menu())
        return
    except FloodWait as error:
        wait_seconds = int(error.value)
        reason = 'Telegram xavfsizlik sababli shu raqamga yangi kirish kodini vaqtincha yubormayapti. Odatda bunga qisqa vaqt ichida juda ko‘p urinish sabab bo‘ladi.'
        await set_login_cooldown(
            message.from_user.id,
            (datetime.now() + timedelta(seconds=wait_seconds)).strftime('%Y-%m-%d %H:%M:%S'),
            reason,
        )
        await message.answer(_cooldown_message(wait_seconds, reason=reason), reply_markup=login_wait_menu())
        return
    except BadRequest as error:
        logger.exception('send_code bad request user_id=%s phone=%s', message.from_user.id, phone)
        await message.answer(
            '⚠️ Kod yuborishda Telegram xatosi yuz berdi.\n\n'
            f'{html.escape(str(error))}\n\n'
            'Bu holatda API_ID/API_HASH, Telegram cheklovi yoki ushbu raqam uchun vaqtinchalik delivery muammosi bo‘lishi mumkin. '
            'QR orqali ulashni ham sinab ko‘ring.',
            reply_markup=login_start_menu(),
        )
        return
    except Exception as error:
        logger.exception('send_code failed for user_id=%s phone=%s', message.from_user.id, phone)
        diag = await diagnose_api_pair(message.from_user.id)
        extra = f'\n\nDiagnostika: {html.escape(diag)}' if diag else ''
        await message.answer(
            f'⚠️ Kod yuborishda xatolik yuz berdi: {error.__class__.__name__}: {html.escape(str(error)[:120])}{extra}',
            reply_markup=login_start_menu(),
        )
        return
    await clear_login_cooldown(message.from_user.id)
    await save_login_session(
        message.from_user.id,
        phone=login_session.phone,
        session_name=login_session.session_name,
        phone_code_hash=login_session.phone_code_hash,
    )
    await state.set_state(LoginState.waiting_code_part1)
    await _ask_code_part1(message, login_session.sent_code_type, login_session.next_type, login_session.timeout, allow_resend=True)


@router.callback_query(F.data == 'login_resend_code')
async def login_resend(callback: types.CallbackQuery, state: FSMContext) -> None:
    row = await get_login_session(callback.from_user.id)
    if not row:
        await callback.answer('Faol login jarayoni topilmadi.', show_alert=True)
        return
    if row['step'] == 'qr':
        await callback.answer('QR login uchun QR ni yangilash tugmasidan foydalaning.', show_alert=True)
        return
    cooldown_text = await _active_login_cooldown_text(callback.from_user.id)
    if cooldown_text:
        await callback.message.answer(cooldown_text, reply_markup=login_wait_menu())
        await callback.answer('Hozircha qayta yuborib bo‘lmaydi.', show_alert=True)
        return
    try:
        login_session = await restart_login(phone=row['phone'], user_id=callback.from_user.id, session_name=row['session_name'])
    except PhoneNumberFlood:
        wait_seconds = 24 * 3600
        reason = 'Telegram bu raqam uchun kirish kodini juda ko‘p marta so‘ralgan deb hisoblab, vaqtincha chekladi.'
        await set_login_cooldown(
            callback.from_user.id,
            (datetime.now() + timedelta(seconds=wait_seconds)).strftime('%Y-%m-%d %H:%M:%S'),
            reason,
        )
        await callback.message.answer(_cooldown_message(wait_seconds, reason=reason), reply_markup=login_wait_menu())
        await callback.answer('Hozircha yangi kod so‘rab bo‘lmaydi.', show_alert=True)
        return
    except FloodWait as error:
        wait_seconds = int(error.value)
        reason = 'Telegram xavfsizlik sababli shu raqamga yangi kirish kodini vaqtincha yubormayapti. Odatda bunga qisqa vaqt ichida juda ko‘p urinish sabab bo‘ladi.'
        await set_login_cooldown(
            callback.from_user.id,
            (datetime.now() + timedelta(seconds=wait_seconds)).strftime('%Y-%m-%d %H:%M:%S'),
            reason,
        )
        await callback.message.answer(_cooldown_message(wait_seconds, reason=reason), reply_markup=login_wait_menu())
        await callback.answer('Hozircha yangi kod so‘rab bo‘lmaydi.', show_alert=True)
        return
    except BadRequest as error:
        logger.exception('restart_login bad request for user_id=%s', callback.from_user.id)
        await callback.answer(f'Yangi kod yuborib bo‘lmadi: {str(error)[:120]}', show_alert=True)
        return
    except Exception as error:
        logger.exception('restart_login failed for user_id=%s', callback.from_user.id)
        await callback.answer(f'Yangi kod yuborib bo‘lmadi: {error.__class__.__name__}', show_alert=True)
        return
    await clear_login_cooldown(callback.from_user.id)
    await save_login_session(
        callback.from_user.id,
        phone=login_session.phone,
        session_name=login_session.session_name,
        phone_code_hash=login_session.phone_code_hash,
    )
    await state.set_state(LoginState.waiting_code_part1)
    await _ask_code_part1(callback.message, login_session.sent_code_type, login_session.next_type, login_session.timeout, allow_resend=True)
    await callback.answer('Yangi kod so‘raldi.', show_alert=True)


@router.callback_query(F.data == 'login_cancel')
async def login_cancel(callback: types.CallbackQuery, state: FSMContext) -> None:
    await _clear_pending_login(callback.from_user.id)
    await state.clear()
    await callback.message.edit_text('✅ Login jarayoni bekor qilindi.')
    await callback.message.answer(
        'Asosiy menyudan davom eting.',
        reply_markup=await _main_menu_markup(callback.from_user.id),
    )
    await callback.answer()


@router.message(LoginState.waiting_code_part1, F.text)
async def process_code_part1(message: types.Message, state: FSMContext) -> None:
    ok, row = await _require_live_login(message, state)
    if not ok:
        return
    digits = ''.join(ch for ch in message.text if ch.isdigit())
    if len(digits) == 5:
        await _complete_login_code(message, state, digits)
        return
    if len(digits) != 2:
        await message.answer('❌ Avval kodning birinchi 2 raqamini yuboring. Masalan: 45')
        return
    await set_login_code_part1(message.from_user.id, digits)
    await set_login_step(message.from_user.id, 'code_part2')
    await state.set_state(LoginState.waiting_code_part2)
    await _ask_code_part2(message)


@router.message(LoginState.waiting_code_part2, F.text)
async def process_code_part2(message: types.Message, state: FSMContext) -> None:
    await _handle_code_part2(message, state)


async def _handle_code_part2(message: types.Message, state: FSMContext) -> None:
    ok, row = await _require_live_login(message, state)
    if not ok:
        return
    digits = ''.join(ch for ch in message.text if ch.isdigit())
    code_part1 = row['code_part1'] or ''
    if len(code_part1) != 2:
        await set_login_step(message.from_user.id, 'code_part1')
        await state.set_state(LoginState.waiting_code_part1)
        await message.answer('❌ Avval kodning birinchi 2 raqamini yuboring.')
        return
    if len(digits) == 5:
        code = digits
    elif len(digits) == 3:
        code = f'{code_part1}{digits}'
    else:
        await message.answer('❌ Endi kodning oxirgi 3 raqamini yuboring. Masalan: 221')
        return
    await _complete_login_code(message, state, code)


async def _complete_login_code(message: types.Message, state: FSMContext, code: str) -> None:
    row = await get_login_session(message.from_user.id)
    if not row:
        await state.clear()
        await message.answer('❌ Login jarayoni topilmadi. Jarayonni boshidan boshlang.', reply_markup=await _main_menu_markup(message.from_user.id))
        return
    try:
        session_string, full_name = await complete_login(
            user_id=message.from_user.id,
            session_name=row['session_name'],
            phone=row['phone'],
            phone_code_hash=row['phone_code_hash'],
            code=code,
        )
    except SessionPasswordNeeded:
        await set_login_step(message.from_user.id, 'two_factor')
        await state.set_state(LoginState.waiting_2fa)
        await message.answer('🔐 Ikki bosqichli parolni yuboring.')
        return
    except PhoneCodeInvalid:
        await set_login_step(message.from_user.id, 'code_part1')
        await state.set_state(LoginState.waiting_code_part1)
        await message.answer('❌ Kod noto‘g‘ri. Avval birinchi 2 raqamni qayta yuboring.')
        return
    except PhoneCodeExpired:
        await set_login_step(message.from_user.id, 'code_part1')
        await state.set_state(LoginState.waiting_code_part1)
        await message.answer(
            '⏰ Kodning amal qilish muddati tugagan. Kodni qayta yuborish yoki QR orqali ulash tugmasini bosing.',
            reply_markup=login_code_menu(),
        )
        return
    except PendingLoginMissingError:
        await set_login_step(message.from_user.id, 'code_part1')
        await state.set_state(LoginState.waiting_code_part1)
        await message.answer(
            '⚠️ Tasdiqlash jarayoni uzilib qoldi. Kodni qayta yuborish yoki QR orqali ulash tugmasini bosing.',
            reply_markup=login_code_menu(),
        )
        return
    except Exception as error:
        logger.exception('complete_login failed for user_id=%s', message.from_user.id)
        await state.clear()
        await _clear_pending_login(message.from_user.id)
        await message.answer(
            f'⚠️ Login vaqtida xatolik yuz berdi: {error.__class__.__name__}: {html.escape(str(error)[:120])}',
            reply_markup=await _main_menu_markup(message.from_user.id),
        )
        return
    await _finish_login(
        message=message,
        state=state,
        session_string=session_string,
        full_name=full_name,
        phone=row['phone'],
    )


@router.message(LoginState.waiting_2fa, F.text)
async def process_2fa(message: types.Message, state: FSMContext) -> None:
    ok, row = await _require_live_login(message, state)
    if not ok:
        return
    try:
        session_string, full_name = await complete_2fa(message.from_user.id, row['session_name'], message.text.strip())
    except PasswordHashInvalid:
        await message.answer('❌ Parol noto‘g‘ri. Qayta yuboring.')
        return
    except PendingLoginMissingError:
        await set_login_step(message.from_user.id, 'code_part1')
        await state.set_state(LoginState.waiting_code_part1)
        await message.answer(
            '⚠️ Tasdiqlash jarayoni uzilib qoldi. Kodni qayta yuborish yoki QR orqali ulash tugmasini bosing.',
            reply_markup=login_code_menu(),
        )
        return
    except Exception as error:
        logger.exception('complete_2fa failed for user_id=%s', message.from_user.id)
        await state.clear()
        await _clear_pending_login(message.from_user.id)
        await message.answer(
            f'⚠️ Parolni tekshirishda xatolik yuz berdi: {html.escape(str(error)[:120])}',
            reply_markup=await _main_menu_markup(message.from_user.id),
        )
        return
    await _finish_login(
        message=message,
        state=state,
        session_string=session_string,
        full_name=full_name,
        phone=row['phone'],
    )


async def _finish_login(
    message: types.Message,
    state: FSMContext,
    session_string: str,
    full_name: str,
    phone: str,
) -> None:
    await update_user_account(message.from_user.id, phone, full_name, session_string, session_name=f'user_{message.from_user.id}')
    await _clear_pending_login(message.from_user.id, remove_session_files=False)
    await state.clear()
    await message.answer(
        '✅ Akkaunt muvaffaqiyatli ulandi.\n\n'
        f'👤 Ism: {html.escape(full_name)}\n'
        f'📱 Raqam: {html.escape(phone)}\n\n'
        'Endi guruhlarni skanerlab, yuborish uchun tanlashingiz mumkin.',
        reply_markup=await _main_menu_markup(message.from_user.id),
    )


@router.message(HasPendingLogin(), F.text)
async def pending_login_resume(message: types.Message, state: FSMContext) -> None:
    row = await get_login_session(message.from_user.id)
    if not row:
        return
    step = row['step']
    if step == 'code_part1':
        await state.set_state(LoginState.waiting_code_part1)
        await process_code_part1(message, state)
        return
    if step == 'code_part2':
        await state.set_state(LoginState.waiting_code_part2)
        await _handle_code_part2(message, state)
        return
    if step == 'two_factor':
        await state.set_state(LoginState.waiting_2fa)
        await process_2fa(message, state)
        return
    if step == 'qr':
        await message.answer('🔳 Hozir QR login faol. QR ni tasdiqlagan bo‘lsangiz, ✅ Tasdiqladim tugmasini bosing.', reply_markup=qr_login_menu())


@router.callback_query(F.data == 'account_edit_name')
async def account_edit_name(callback: types.CallbackQuery, state: FSMContext) -> None:
    user = await get_connected_user(callback.from_user.id)
    if not user:
        await callback.answer('Avval akkauntni ulang.', show_alert=True)
        return
    await state.set_state(LoginState.waiting_profile_name)
    await callback.message.answer('✏️ Yangi ismni yuboring. Masalan: Ali Valiyev', reply_markup=cancel_menu())
    await callback.answer()


@router.callback_query(F.data == 'account_edit_bio')
async def account_edit_bio(callback: types.CallbackQuery, state: FSMContext) -> None:
    user = await get_connected_user(callback.from_user.id)
    if not user:
        await callback.answer('Avval akkauntni ulang.', show_alert=True)
        return
    await state.set_state(LoginState.waiting_profile_bio)
    await callback.message.answer('📝 Yangi BIO matnini yuboring. 70 ta belgigacha yozish tavsiya qilinadi.', reply_markup=cancel_menu())
    await callback.answer()


@router.message(LoginState.waiting_profile_name, F.text)
async def process_profile_name(message: types.Message, state: FSMContext) -> None:
    user = await get_connected_user(message.from_user.id)
    if not user:
        await state.clear()
        await message.answer('❌ Avval akkauntni ulang.', reply_markup=await _main_menu_markup(message.from_user.id))
        return
    if is_user_session_busy(message.from_user.id):
        await message.answer('⏳ Hozir akkauntingiz bilan boshqa jarayon ishlayapti. Avval faol jarayonni pauza qiling yoki to‘xtating, keyin ismni o‘zgartiring.')
        return
    new_name = message.text.strip()
    if len(new_name) < 2:
        await message.answer('❌ Kamida 2 ta belgi yuboring.')
        return
    try:
        full_name = await update_profile_name(message.from_user.id, user['session_name'], user['session_string'], new_name)
        await update_user_account(message.from_user.id, user['phone'], full_name, user['session_string'], session_name=user['session_name'])
    except Exception as error:
        logger.exception('update_profile_name failed user_id=%s', message.from_user.id)
        await message.answer(f'⚠️ Ismni yangilab bo‘lmadi: {html.escape(str(error)[:140])}')
        return
    await state.clear()
    await message.answer(f'✅ Ism yangilandi: {html.escape(full_name)}', reply_markup=await _main_menu_markup(message.from_user.id))


@router.message(LoginState.waiting_profile_bio, F.text)
async def process_profile_bio(message: types.Message, state: FSMContext) -> None:
    user = await get_connected_user(message.from_user.id)
    if not user:
        await state.clear()
        await message.answer('❌ Avval akkauntni ulang.', reply_markup=await _main_menu_markup(message.from_user.id))
        return
    if is_user_session_busy(message.from_user.id):
        await message.answer('⏳ Hozir akkauntingiz bilan boshqa jarayon ishlayapti. Avval faol jarayonni pauza qiling yoki to‘xtating, keyin BIO ni o‘zgartiring.')
        return
    new_bio = message.text.strip()
    if not new_bio:
        await message.answer('❌ Bo‘sh BIO yuborib bo‘lmaydi.')
        return
    try:
        bio = await update_profile_bio(message.from_user.id, user['session_name'], user['session_string'], new_bio)
    except Exception as error:
        logger.exception('update_profile_bio failed user_id=%s', message.from_user.id)
        await message.answer(f'⚠️ BIO ni yangilab bo‘lmadi: {html.escape(str(error)[:140])}')
        return
    await state.clear()
    await message.answer(
        f'✅ BIO yangilandi:\n{html.escape(bio)}',
        reply_markup=await _main_menu_markup(message.from_user.id),
    )
