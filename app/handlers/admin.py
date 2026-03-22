from __future__ import annotations

import html
import uuid
from datetime import datetime

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from app.db import (
    block_user,
    frozen_users,
    generate_key,
    get_user_card,
    is_admin_user,
    list_admin_users,
    list_keys,
    list_users,
    mark_admin,
    reset_limits,
    set_user_interval,
    stats_rows,
    unfreeze_user,
    unblock_user,
    users_with_ban_strikes,
    delete_key,
    ensure_user,
)
from app.services.jobs import stop_active_job
from app.keyboards import (
    admin_manage_menu,
    admin_menu,
    admin_user_list_menu,
    admin_user_picker,
    key_days_menu,
    key_list_menu,
    main_menu,
    unfreeze_menu,
    user_card_menu,
    user_interval_menu,
)
from app.states import AdminState

router = Router()


async def _guard_admin(target) -> bool:
    return await is_admin_user(target.from_user.id)


def _render_user_card(card: dict) -> str:
    user = card['user']
    status = 'Muzlatilgan' if user['is_frozen'] else ('Bloklangan' if not user['is_active'] else 'Faol')
    expiry = user['expiry_date'] or 'Belgilanmagan'
    last_key = card.get('last_key')
    last_key_text = last_key['key_code'] if last_key else 'yo‘q'
    key_state = 'Faol kalit bor' if user['expiry_date'] and user['is_active'] else 'Kalit yo‘q'
    return (
        '👤 Foydalanuvchi kartasi\n\n'
        f"Ism: {html.escape(user['full_name'] or '—')}\n"
        f"ID: <code>{user['user_id']}</code>\n"
        f"Raqam: {html.escape(user['phone'] or '—')}\n"
        f"Admin: {'Ha' if user['is_admin'] else 'Yo‘q'}\n"
        f"Holat: {status}\n"
        f"Kalit: {key_state}\n"
        f"Guruhlar: {card['groups_count']}\n"
        f"Tanlangan: {card['selected_count']}\n"
        'Yuborish limiti: cheksiz\n'
        f"Interval: {user['forward_interval']} soniya\n"
        f"Strike: {user['ban_strikes']}\n"
        f"Amal qilish muddati: {expiry}\n"
        f"Oxirgi kalit: <code>{html.escape(last_key_text)}</code>"
    )


@router.message(Command('panel'))
@router.message(F.text == '⚙️ Admin')
async def open_panel(message: types.Message, state: FSMContext) -> None:
    if not await is_admin_user(message.from_user.id):
        await message.answer("❌ Ushbu bo'lim faqat admin uchun.")
        return
    await state.clear()
    await message.answer('⚙️ Admin panel', reply_markup=admin_menu())


@router.callback_query(F.data == 'admin:back_main')
async def back_main(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    await callback.message.edit_text('⚙️ Admin panel', reply_markup=admin_menu())
    await callback.answer()


@router.callback_query(F.data == 'admin:admins')
async def admin_manage(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    await callback.message.edit_text('🛡 Adminlarni boshqarish', reply_markup=admin_manage_menu())
    await callback.answer()


@router.callback_query(F.data == 'admin:add_admin_prompt')
async def add_admin_prompt(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    await state.set_state(AdminState.waiting_new_admin_id)
    await callback.message.answer('Admin qilinadigan foydalanuvchi ID sini yuboring.')
    await callback.answer()


@router.message(AdminState.waiting_new_admin_id, F.text)
async def add_admin_by_id(message: types.Message, state: FSMContext) -> None:
    if not await is_admin_user(message.from_user.id):
        await state.clear()
        await message.answer("❌ Ushbu bo'lim faqat admin uchun.")
        return
    try:
        target_user_id = int(message.text.strip())
    except ValueError:
        await message.answer('❌ Faqat son ko‘rinishidagi user ID yuboring.')
        return
    await mark_admin(target_user_id, True)
    await state.clear()
    await message.answer(f'✅ {target_user_id} endi admin.', reply_markup=main_menu(True))
    try:
        await message.bot.send_message(target_user_id, '✅ Sizga admin huquqi berildi.')
    except Exception:
        pass


@router.callback_query(F.data == 'admin:list_admins')
async def list_admins(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    rows = await list_admin_users()
    if not rows:
        await callback.message.answer('Adminlar topilmadi.')
        await callback.answer()
        return
    lines = ["🛡 Adminlar ro'yxati\n"]
    for row in rows:
        lines.append(
            f"• {html.escape(row['full_name'] or '—')}\n"
            f"  ID: {row['user_id']}\n"
            f"  Raqam: {html.escape(row['phone'] or '—')}"
        )
    await callback.message.answer('\n\n'.join(lines))
    await callback.answer()


@router.callback_query(F.data == 'admin:remove_admin_menu')
async def remove_admin_menu(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    rows = await list_admin_users()
    await callback.message.edit_text(
        '➖ Olib tashlanadigan adminni tanlang.',
        reply_markup=admin_user_picker(rows, 'remove_admin', include_remove_self=False, owner_id=callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('admin:remove_admin:'))
async def remove_admin(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    target_user_id = int(callback.data.rsplit(':', 1)[-1])
    await mark_admin(target_user_id, False)
    await callback.message.answer('✅ Admin huquqi olib tashlandi.')
    try:
        await callback.bot.send_message(target_user_id, 'ℹ️ Sizdagi admin huquqi olib tashlandi.')
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == 'admin:key_menu')
async def key_menu(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    await callback.message.edit_text("🔑 Kalitlar bo'limi", reply_markup=key_days_menu())
    await callback.answer()


@router.callback_query(F.data.startswith('admin:key_generate:'))
async def key_generate(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    days = int(callback.data.rsplit(':', 1)[-1])
    key_code = str(uuid.uuid4())
    await generate_key(days, key_code)
    await callback.message.answer(
        '✅ Yangi kalit yaratildi.\n\n'
        f'Muddat: {days} kun\n'
        f'Kalit: <code>{html.escape(key_code)}</code>'
    )
    await callback.answer()


@router.callback_query(F.data == 'admin:key_list')
async def key_list(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    rows = await list_keys()
    if not rows:
        await callback.message.answer('Kalitlar topilmadi.')
        await callback.answer()
        return
    lines = ["🔑 So'nggi kalitlar\n"]
    for row in rows:
        status = 'Ishlatilgan' if row['is_used'] else 'Faol'
        owner = row['owner_name'] or row['owner_phone'] or ('hali biriktirilmagan' if not row['used_by'] else f"User {row['used_by']}")
        lines.append(f"• {status} | {row['days']} kun\n  Egasi: {html.escape(owner)}")
    await callback.message.answer('\n\n'.join(lines), reply_markup=key_list_menu(rows))
    await callback.answer()


@router.callback_query(F.data.startswith('admin:key_delete:'))
async def key_delete(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    key_code = callback.data.split(':', 2)[-1]
    deleted = await delete_key(key_code)
    if not deleted:
        await callback.answer('Kalit topilmadi.', show_alert=True)
        return
    owner_id = deleted.get('used_by')
    owner_name = deleted.get('owner_name') or deleted.get('owner_phone') or ('Biriktirilmagan' if not owner_id else f'User {owner_id}')
    if owner_id:
        await stop_active_job(int(owner_id))
        try:
            await callback.bot.send_message(
                int(owner_id),
                '⚠️ Sizga biriktirilgan kalit admin tomonidan o‘chirildi.\n\n'
                '🛑 Shu sabab faol jarayonlar to‘xtatildi.\n'
                '🔑 Davom ettirish uchun adminlardan yangi kalit oling.'
            )
        except Exception:
            pass
    admin_rows = await list_admin_users()
    actor = await ensure_user(callback.from_user.id)
    notify_text = (
        '🗑 Kalit o‘chirildi.\n\n'
        f"🔑 Kalit: <code>{html.escape(key_code)}</code>\n"
        f"👤 Egasi: {html.escape(str(owner_name))}\n"
        f"🛠 O‘chirgan admin: {html.escape(actor['full_name'] or 'Admin')}\n"
        f"🆔 Admin ID: <code>{callback.from_user.id}</code>"
    )
    for admin in admin_rows:
        try:
            await callback.bot.send_message(admin['user_id'], notify_text)
        except Exception:
            pass
    await callback.answer('Kalit o‘chirildi.')
    rows = await list_keys()
    await callback.message.answer('🗑 Kalit o‘chirildi.', reply_markup=key_list_menu(rows) if rows else None)


@router.callback_query(F.data == 'admin:user_list')
async def user_list(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    rows = await list_users(limit=200)
    if not rows:
        await callback.message.answer('Ulangan foydalanuvchilar topilmadi.')
        await callback.answer()
        return
    await callback.message.edit_text('👥 Foydalanuvchini tanlang.', reply_markup=admin_user_list_menu(rows))
    await callback.answer()


@router.callback_query(F.data.startswith('admin:user_card:'))
async def admin_user_card(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    user_id = int(callback.data.rsplit(':', 1)[-1])
    card = await get_user_card(user_id)
    if not card:
        await callback.answer('Foydalanuvchi topilmadi.', show_alert=True)
        return
    await callback.message.edit_text(
        _render_user_card(card),
        reply_markup=user_card_menu(user_id, is_blocked=not bool(card['user']['is_active']), is_admin=bool(card['user']['is_admin'])),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('admin:user_toggle_active:'))
async def admin_user_toggle_active(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    user_id = int(callback.data.rsplit(':', 1)[-1])
    card = await get_user_card(user_id)
    if not card:
        await callback.answer('Topilmadi.', show_alert=True)
        return
    if card['user']['is_active']:
        await block_user(user_id)
    else:
        await unblock_user(user_id)
    card = await get_user_card(user_id)
    await callback.message.edit_text(
        _render_user_card(card),
        reply_markup=user_card_menu(user_id, is_blocked=not bool(card['user']['is_active']), is_admin=bool(card['user']['is_admin'])),
    )
    await callback.answer('Holat yangilandi.')


@router.callback_query(F.data.startswith('admin:user_toggle_admin:'))
async def admin_user_toggle_admin(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    user_id = int(callback.data.rsplit(':', 1)[-1])
    card = await get_user_card(user_id)
    if not card:
        await callback.answer('Topilmadi.', show_alert=True)
        return
    await mark_admin(user_id, not bool(card['user']['is_admin']))
    card = await get_user_card(user_id)
    await callback.message.edit_text(
        _render_user_card(card),
        reply_markup=user_card_menu(user_id, is_blocked=not bool(card['user']['is_active']), is_admin=bool(card['user']['is_admin'])),
    )
    await callback.answer('Admin huquqi yangilandi.')


@router.callback_query(F.data == 'admin:stats')
async def show_stats(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    rows = await stats_rows()
    if not rows:
        await callback.message.answer("Statistika uchun ma'lumot topilmadi.")
        await callback.answer()
        return
    lines = ['📊 Foydalanuvchilar statistikasi\n']
    for row in rows:
        key_state = 'Faol kalit bor' if row['expiry_date'] else 'Kalit yo‘q'
        lines.append(
            f"{html.escape(row['full_name'] or '—')}\n"
            f"Kalit: {key_state}\n"
            f"Interval: {row['forward_interval']}s"
        )
    await callback.message.answer('\n\n'.join(lines))
    await callback.answer()


@router.callback_query(F.data == 'admin:interval_menu')
async def interval_menu(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    rows = await list_users(limit=100)
    if not rows:
        await callback.message.answer('Foydalanuvchilar topilmadi.')
        await callback.answer()
        return
    await callback.message.edit_text(
        "⏱ Intervalni o'zgartirish uchun foydalanuvchini tanlang.",
        reply_markup=user_interval_menu(rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('admin:set_interval:'))
async def ask_interval(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    user_id = int(callback.data.rsplit(':', 1)[-1])
    await state.set_state(AdminState.waiting_interval)
    await state.update_data(target_user_id=user_id)
    await callback.message.answer("Yangi guruhlar oralig'ini soniya ko'rinishida yuboring. Masalan: 20")
    await callback.answer()


@router.message(AdminState.waiting_interval, F.text)
async def set_interval_value(message: types.Message, state: FSMContext) -> None:
    if not await is_admin_user(message.from_user.id):
        await state.clear()
        await message.answer("❌ Ushbu bo'lim faqat admin uchun.")
        return
    data = await state.get_data()
    try:
        seconds = int(message.text.strip())
    except ValueError:
        await message.answer('❌ Faqat butun son yuboring.')
        return
    if seconds < 1:
        await message.answer("❌ Interval kamida 1 soniya bo'lishi kerak.")
        return
    await set_user_interval(int(data['target_user_id']), seconds)
    await state.clear()
    await message.answer(f'✅ Interval yangilandi: {seconds} soniya', reply_markup=main_menu(True))


@router.callback_query(F.data == 'admin:ban_list')
async def ban_list(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    rows = await users_with_ban_strikes()
    if not rows:
        await callback.message.answer('Ban holatlari topilmadi.')
        await callback.answer()
        return
    lines = ['🚨 Ban holati\n']
    for row in rows:
        frozen_text = 'Muzlatilgan' if row['is_frozen'] else 'Faol'
        until_text = f" | {row['frozen_until']}" if row['frozen_until'] else ''
        lines.append(
            f"• {html.escape(row['full_name'] or '—')}\n"
            f"  Strike: {row['ban_strikes']}\n"
            f"  Holat: {frozen_text}{until_text}"
        )
    await callback.message.answer('\n\n'.join(lines))
    await callback.answer()


@router.callback_query(F.data == 'admin:unfreeze_menu')
async def unfreeze_list(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    rows = await frozen_users()
    if not rows:
        await callback.message.answer('Muzlatilgan foydalanuvchilar topilmadi.')
        await callback.answer()
        return
    await callback.message.edit_text(
        '🔓 Muzlatishni yechish uchun foydalanuvchini tanlang.',
        reply_markup=unfreeze_menu(rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith('admin:unfreeze:'))
async def unfreeze_action(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    user_id = int(callback.data.rsplit(':', 1)[-1])
    await unfreeze_user(user_id)
    await callback.message.answer('✅ Foydalanuvchi qayta faollashtirildi.')
    try:
        await callback.bot.send_message(user_id, '✅ Hisobingiz qayta faollashtirildi.')
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == 'admin:reset_limits')
async def reset_limits_action(callback: types.CallbackQuery) -> None:
    if not await _guard_admin(callback):
        await callback.answer()
        return
    await reset_limits()
    await callback.message.answer(
        f"✅ Barcha foydalanuvchilarning bugungi limiti yangilandi.\nVaqt: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await callback.answer()
