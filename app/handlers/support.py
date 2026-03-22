from __future__ import annotations

import html

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from app.db import get_connected_user, is_admin_user, list_admin_users
from app.keyboards import cancel_menu, main_menu, support_admin_reply_menu
from app.states import SupportState

router = Router()


@router.message(F.text == '🆘 Yordam')
async def support_start(message: types.Message, state: FSMContext) -> None:
    await state.set_state(SupportState.waiting_message)
    await message.answer(
        '🆘 Qo‘llab-quvvatlash bo‘limi.\n\nMuammo yoki savolingizni bitta xabarda yozib yuboring. '
        'Xabaringiz barcha adminlarga yetkaziladi.',
        reply_markup=cancel_menu(),
    )


@router.message(SupportState.waiting_message, F.text)
async def support_send(message: types.Message, state: FSMContext) -> None:
    rows = await list_admin_users()
    text = (
        '🆘 Yangi support xabari\n\n'
        f'👤 Foydalanuvchi: {html.escape(message.from_user.full_name)}\n'
        f'🆔 ID: <code>{message.from_user.id}</code>\n'
        f'📨 Xabar: {html.escape(message.text)}'
    )
    sent = 0
    for row in rows:
        try:
            await message.bot.send_message(int(row['user_id']), text, reply_markup=support_admin_reply_menu(message.from_user.id))
            sent += 1
        except Exception:
            pass
    await state.clear()
    await message.answer(
        '✅ Xabaringiz adminlarga yuborildi. Javob shu bot ichida keladi.',
        reply_markup=main_menu(await is_admin_user(message.from_user.id)),
    )
    if sent == 0:
        await message.answer('⚠️ Hozircha birorta admin online topilmadi, ammo xabaringiz qayta yuborishga tayyor.')


@router.callback_query(F.data.startswith('support:reply:'))
async def support_reply_start(callback: types.CallbackQuery, state: FSMContext) -> None:
    if not await is_admin_user(callback.from_user.id):
        await callback.answer('Bu tugma faqat adminlar uchun.', show_alert=True)
        return
    user_id = int(callback.data.rsplit(':', 1)[-1])
    await state.set_state(SupportState.waiting_admin_reply)
    await state.update_data(support_target_user_id=user_id)
    await callback.message.answer(f'✉️ Endi foydalanuvchi <code>{user_id}</code> uchun javob matnini yuboring.', reply_markup=cancel_menu())
    await callback.answer()


@router.message(SupportState.waiting_admin_reply, F.text)
async def support_reply_send(message: types.Message, state: FSMContext) -> None:
    if not await is_admin_user(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    target_user_id = int(data['support_target_user_id'])
    reply_text = (
        '💬 Admin javobi\n\n'
        f'{html.escape(message.text)}\n\n'
        'Savol bo‘lsa, yana shu bo‘lim orqali yozishingiz mumkin.'
    )
    try:
        await message.bot.send_message(target_user_id, reply_text)
    except Exception:
        await message.answer('⚠️ Foydalanuvchiga javobni yetkazib bo‘lmadi.')
        await state.clear()
        return
    rows = await list_admin_users()
    for row in rows:
        admin_id = int(row['user_id'])
        if admin_id == message.from_user.id:
            continue
        try:
            await message.bot.send_message(
                admin_id,
                'ℹ️ Support javobi yuborildi.\n\n'
                f'Admin: {html.escape(message.from_user.full_name)}\n'
                f'Foydalanuvchi ID: <code>{target_user_id}</code>\n'
                f'Javob: {html.escape(message.text)}',
            )
        except Exception:
            pass
    await state.clear()
    await message.answer('✅ Javob foydalanuvchiga yuborildi.', reply_markup=main_menu(True))
