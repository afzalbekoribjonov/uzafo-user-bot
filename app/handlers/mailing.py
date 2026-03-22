from __future__ import annotations

import asyncio

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from app.db import check_campaign_access, get_connected_user, get_selected_groups, is_admin_user
from app.keyboards import active_process_menu, campaign_confirm_menu, cancel_menu, interval_choice_menu, main_menu
from app.services.jobs import (
    get_active_job_snapshot,
    has_active_job,
    register_job,
    run_copy_forward,
    run_text_broadcast,
    stop_active_job,
    toggle_pause_job,
)
from app.services.relay import make_outbound_content
from app.states import BroadcastState, ForwardState

router = Router()

FIXED_INTERVAL_LABELS = {
    60: '1 minut',
    180: '3 minut',
    300: '5 minut',
    600: '10 minut',
}


def _human_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f'{days} kun')
    if hours:
        parts.append(f'{hours} soat')
    if minutes:
        parts.append(f'{minutes} daqiqa')
    if secs or not parts:
        parts.append(f'{secs} soniya')
    return ', '.join(parts)


def _interval_preview(data: dict) -> str:
    if data.get('interval_mode') == 'random':
        return '🎲 Random: 1, 3, 5 yoki 10 minut'
    seconds = int(data.get('repeat_interval_seconds', 60) or 60)
    return f'🕒 Har {FIXED_INTERVAL_LABELS.get(seconds, _human_seconds(seconds))}'


async def _prepare_user_and_groups(user_id: int):
    user = await get_connected_user(user_id)
    groups = await get_selected_groups(user_id)
    return user, groups


def _active_process_text(snapshot: dict) -> str:
    summary = snapshot['summary']
    wait_block = f"\n\n⏸ Holat: kutilyapti\n{snapshot['waiting_text']}" if snapshot['waiting_text'] else ''
    current_chat = snapshot['current_title'] or 'hali boshlanmagan'
    kind = 'Matn yuborish' if snapshot['kind'] == 'text' else 'Forward yuborish'
    cycle_text = f"{max(1, snapshot['current_round'] or 1)}/∞"
    interval_text = '🎲 Random: 1/3/5/10 minut' if snapshot.get('interval_mode') == 'random' else f"🕒 Har {FIXED_INTERVAL_LABELS.get(int(snapshot.get('repeat_interval_seconds') or 60), _human_seconds(int(snapshot.get('repeat_interval_seconds') or 60)))}"
    return (
        '🧭 Faol jarayon\n\n'
        f'📌 Turi: {kind}\n'
        f'⏳ Ishlagan vaqt: {_human_seconds(snapshot["elapsed_seconds"])}\n'
        f'🔁 Aylana: {cycle_text}\n'
        f'👥 Chatlar: {snapshot["current_index"]}/{snapshot["groups_total"]}\n'
        f'💬 Oxirgi chat: {current_chat}\n'
        f'{interval_text}\n'
        f'✅ Muvaffaqiyatli: {summary.success}\n'
        f'⚠️ Xatolik: {summary.failed}\n'
        f'⏭ O‘tkazib yuborildi: {summary.skipped}'
        f'{wait_block}'
    )


@router.message(F.text == '🧭 Faol jarayonlar')
async def active_processes(message: types.Message) -> None:
    snapshot = get_active_job_snapshot(message.from_user.id)
    if not snapshot:
        await message.answer(
            '🌿 Hozircha faol yuborish jarayoni yo‘q.\n\n'
            'Yangi jarayon boshlaganingizda shu bo‘limdan uni pauza qilish, davom ettirish yoki to‘xtatish mumkin bo‘ladi.',
            reply_markup=main_menu(await is_admin_user(message.from_user.id)),
        )
        return
    await message.answer(_active_process_text(snapshot), reply_markup=active_process_menu(snapshot.get('is_paused', False)))


@router.callback_query(F.data == 'process:refresh')
async def refresh_active_process(callback: types.CallbackQuery) -> None:
    snapshot = get_active_job_snapshot(callback.from_user.id)
    if not snapshot:
        await callback.message.edit_text('🌿 Hozircha faol jarayon yo‘q.')
        await callback.answer('Faol jarayon topilmadi.')
        return
    await callback.message.edit_text(_active_process_text(snapshot), reply_markup=active_process_menu(snapshot.get('is_paused', False)))
    await callback.answer('Yangilandi.')


@router.callback_query(F.data == 'process:toggle_pause')
async def toggle_pause_process(callback: types.CallbackQuery) -> None:
    found, is_paused = await toggle_pause_job(callback.from_user.id)
    if not found:
        await callback.answer('Jarayon topilmadi.', show_alert=True)
        return
    snapshot = get_active_job_snapshot(callback.from_user.id)
    if not snapshot:
        await callback.message.edit_text('🌿 Hozircha faol jarayon yo‘q.')
        await callback.answer('Faol jarayon tugagan.')
        return
    await callback.message.edit_text(_active_process_text(snapshot), reply_markup=active_process_menu(snapshot.get('is_paused', False)))
    await callback.answer('Jarayon pauzaga qo‘yildi.' if is_paused else 'Jarayon davom ettirildi.')


@router.callback_query(F.data == 'process:stop')
async def stop_process(callback: types.CallbackQuery) -> None:
    stopped = await stop_active_job(callback.from_user.id)
    if not stopped:
        await callback.answer('To‘xtatiladigan jarayon topilmadi.', show_alert=True)
        return
    await callback.message.edit_text(
        '🛑 Jarayonni to‘xtatish buyrug‘i yuborildi.\n\n'
        'Bir necha soniya ichida holat yangilanadi.',
    )
    await callback.answer('Jarayon to‘xtatilmoqda.')


@router.callback_query(F.data == 'process:close')
async def close_process_panel(callback: types.CallbackQuery) -> None:
    await callback.message.edit_text('✅ Faol jarayon oynasi yopildi.')
    await callback.answer()


@router.message(F.text == '🚀 Xabar')
async def start_broadcast(message: types.Message, state: FSMContext) -> None:
    ok, reason = await check_campaign_access(message.from_user.id)
    if not ok:
        await message.answer(f'❌ {reason}\n\n🆘 Kalit olish uchun Yordam bo‘limidan adminlarga yozing.')
        return
    if has_active_job(message.from_user.id):
        await message.answer(
            '⏳ Sizda allaqachon faol yuborish jarayoni bor.\n\n'
            'Uni `🧭 Faol jarayonlar` bo‘limidan boshqarishingiz mumkin.'
        )
        return
    user, groups = await _prepare_user_and_groups(message.from_user.id)
    if not user:
        await message.answer('📱 Avval akkauntni ulang, keyin xabar yuborishni boshlaymiz.')
        return
    if not groups:
        await message.answer('👥 Avval Guruhlar bo‘limiga kirib, kamida bitta chatni tanlang.')
        return
    await state.clear()
    await state.set_state(BroadcastState.waiting_text)
    await message.answer(
        '🚀 Xabar yuborishni boshlaymiz.\n\n'
        '1-qadam: yuboriladigan matnni shu yerga yuboring.\n\n'
        f'✅ Tanlangan chatlar: {len(groups)}\n'
        '♾ Jarayon siz to‘xtatmaguningizcha yoki kalit muddati tugamaguncha davom etadi.',
        reply_markup=cancel_menu(),
    )


@router.message(BroadcastState.waiting_text, F.text)
async def process_broadcast_text(message: types.Message, state: FSMContext) -> None:
    await state.update_data(text=message.text)
    await state.set_state(BroadcastState.waiting_interval_choice)
    await message.answer(
        '⏰ 2-qadam: xabar nechchi vaqtda bir qayta yuborilsin?\n\n'
        'Quyidagi tayyor variantlardan birini tanlang 👇\n\n'
        '• 1 minut\n'
        '• 3 minut\n'
        '• 5 minut\n'
        '• 10 minut\n'
        '• Random: 1, 3, 5 yoki 10 minut',
        reply_markup=interval_choice_menu('text'),
    )


@router.message(F.text == '📤 Forward')
async def start_forward(message: types.Message, state: FSMContext) -> None:
    ok, reason = await check_campaign_access(message.from_user.id)
    if not ok:
        await message.answer(f'❌ {reason}\n\n🆘 Kalit olish uchun Yordam bo‘limidan adminlarga yozing.')
        return
    if has_active_job(message.from_user.id):
        await message.answer(
            '⏳ Sizda allaqachon faol yuborish jarayoni bor.\n\n'
            'Uni `🧭 Faol jarayonlar` bo‘limidan boshqarishingiz mumkin.'
        )
        return
    user, groups = await _prepare_user_and_groups(message.from_user.id)
    if not user:
        await message.answer('📱 Avval akkauntni ulang, keyin forward yuborishni boshlaymiz.')
        return
    if not groups:
        await message.answer('👥 Avval Guruhlar bo‘limiga kirib, kamida bitta chatni tanlang.')
        return
    await state.clear()
    await state.set_state(ForwardState.waiting_message)
    await message.answer(
        '📤 Forward/copy yuborishni boshlaymiz.\n\n'
        '1-qadam: yubormoqchi bo‘lgan xabarni shu yerga tashlang yoki forward qiling.\n\n'
        'Qo‘llanadi: matn, rasm, video, hujjat, audio, voice, sticker va animation.\n\n'
        '♾ Jarayon siz to‘xtatmaguningizcha yoki kalit muddati tugamaguncha davom etadi.',
        reply_markup=cancel_menu(),
    )


@router.message(ForwardState.waiting_message)
async def process_forward(message: types.Message, state: FSMContext) -> None:
    try:
        content = await make_outbound_content(message.bot, message)
    except ValueError as error:
        await message.answer(f'❌ {error}')
        return
    except Exception as error:
        await state.clear()
        await message.answer(f'⚠️ Xabarni tayyorlashda xatolik yuz berdi: {str(error)[:120]}')
        return
    await state.update_data(content=content)
    await state.set_state(ForwardState.waiting_interval_choice)
    await message.answer(
        '⏰ 2-qadam: bu xabar nechchi vaqtda bir qayta yuborilsin?\n\n'
        'Quyidagi tayyor variantlardan birini tanlang 👇',
        reply_markup=interval_choice_menu('forward'),
    )


async def _finalize_campaign_preview(message_or_callback, state: FSMContext, kind: str) -> None:
    user_id = message_or_callback.from_user.id
    user, groups = await _prepare_user_and_groups(user_id)
    if not user or not groups:
        await state.clear()
        target = message_or_callback if isinstance(message_or_callback, types.Message) else message_or_callback.message
        await target.answer('❌ Kerakli ma’lumot topilmadi.', reply_markup=main_menu(await is_admin_user(user_id)))
        return
    data = await state.get_data()
    await state.set_state(BroadcastState.waiting_confirm if kind == 'text' else ForwardState.waiting_confirm)
    preview = (
        '🎯 Hammasi tayyor.\n\n'
        f'👥 Tanlangan chatlar: {len(groups)}\n'
        f'{_interval_preview(data)}\n'
        f'📏 Chatlar oralig‘i: {user["forward_interval"]} soniya\n\n'
        '♾ Jarayon cheksiz ishlaydi.\n'
        'U quyidagi holatlardan birida to‘xtaydi:\n'
        '• siz o‘zingiz to‘xtatsangiz\n'
        '• kalit muddati tugasa\n'
        '• Telegram xavfsizlik cheklovi ishga tushsa\n\n'
        'Hammasi to‘g‘ri bo‘lsa, pastdagi tugma orqali boshlang.'
    )
    if isinstance(message_or_callback, types.Message):
        await message_or_callback.answer(preview, reply_markup=campaign_confirm_menu(kind))
    else:
        await message_or_callback.message.edit_text(preview, reply_markup=campaign_confirm_menu(kind))


@router.callback_query(F.data.startswith('campaign:interval:'))
async def select_interval_choice(callback: types.CallbackQuery, state: FSMContext) -> None:
    _, _, kind, value = callback.data.split(':', 3)
    if value == 'random':
        await state.update_data(interval_mode='random', repeat_interval_seconds=0)
    else:
        seconds = int(value)
        await state.update_data(interval_mode='fixed', repeat_interval_seconds=seconds)
    await _finalize_campaign_preview(callback, state, kind)
    await callback.answer('Vaqt oralig‘i saqlandi.')


@router.callback_query(F.data == 'campaign:cancel')
async def cancel_campaign(callback: types.CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text('❌ Yuborish sozlash jarayoni bekor qilindi.')
    await callback.message.answer('Asosiy menyudan kerakli bo‘limni tanlashingiz mumkin.', reply_markup=main_menu(await is_admin_user(callback.from_user.id)))
    await callback.answer()


@router.callback_query(F.data == 'campaign:start:text')
async def confirm_text_campaign(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    user, groups = await _prepare_user_and_groups(callback.from_user.id)
    if not user or not groups:
        await state.clear()
        await callback.message.answer('❌ Kerakli ma’lumot topilmadi.', reply_markup=main_menu(await is_admin_user(callback.from_user.id)))
        await callback.answer()
        return
    text = str(data.get('text') or '')
    interval_mode = str(data.get('interval_mode') or 'fixed')
    repeat_interval_seconds = int(data.get('repeat_interval_seconds') or 0)
    status_message = await callback.message.answer(
        '🚀 Xabar yuborish boshlandi.\n\n'
        'Jarayonni `🧭 Faol jarayonlar` bo‘limidan kuzatish, pauza qilish yoki to‘xtatish mumkin.',
        reply_markup=active_process_menu(),
    )
    task = asyncio.create_task(
        run_text_broadcast(
            bot=callback.bot,
            user_id=callback.from_user.id,
            session_name=user['session_name'],
            session_string=user['session_string'],
            groups=groups,
            text=text,
            interval=int(user['forward_interval'] or 1),
            status_message=status_message,
            repeat_count=0,
            repeat_interval_seconds=repeat_interval_seconds,
            interval_mode=interval_mode,
        )
    )
    register_job(
        callback.from_user.id,
        task,
        kind='text',
        status_message=status_message,
        groups_total=len(groups),
        repeat_count=0,
        repeat_interval_seconds=repeat_interval_seconds,
        interval_mode=interval_mode,
    )
    await state.clear()
    await callback.answer('Jarayon boshlandi.')


@router.callback_query(F.data == 'campaign:start:forward')
async def confirm_forward_campaign(callback: types.CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    user, groups = await _prepare_user_and_groups(callback.from_user.id)
    if not user or not groups:
        await state.clear()
        await callback.message.answer('❌ Kerakli ma’lumot topilmadi.', reply_markup=main_menu(await is_admin_user(callback.from_user.id)))
        await callback.answer()
        return
    content = data.get('content')
    interval_mode = str(data.get('interval_mode') or 'fixed')
    repeat_interval_seconds = int(data.get('repeat_interval_seconds') or 0)
    status_message = await callback.message.answer(
        '🚀 Forward yuborish boshlandi.\n\n'
        'Jarayonni `🧭 Faol jarayonlar` bo‘limidan kuzatish, pauza qilish yoki to‘xtatish mumkin.',
        reply_markup=active_process_menu(),
    )
    task = asyncio.create_task(
        run_copy_forward(
            bot=callback.bot,
            user_id=callback.from_user.id,
            session_name=user['session_name'],
            session_string=user['session_string'],
            groups=groups,
            content=content,
            interval=int(user['forward_interval'] or 1),
            status_message=status_message,
            repeat_count=0,
            repeat_interval_seconds=repeat_interval_seconds,
            interval_mode=interval_mode,
        )
    )
    register_job(
        callback.from_user.id,
        task,
        kind='forward',
        status_message=status_message,
        groups_total=len(groups),
        repeat_count=0,
        repeat_interval_seconds=repeat_interval_seconds,
        interval_mode=interval_mode,
    )
    await state.clear()
    await callback.answer('Jarayon boshlandi.')
