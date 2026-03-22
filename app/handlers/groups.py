from __future__ import annotations

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from app.db import (
    check_access,
    get_admin_groups,
    get_connected_user,
    get_selected_groups,
    is_admin_user,
    set_all_groups_selection,
    toggle_group,
    upsert_groups,
)
from app.keyboards import groups_menu, main_menu
from app.services.user_clients import scan_admin_groups

router = Router()


@router.message(F.text == '👥 Guruhlar')
async def groups_entry(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    ok, reason = await check_access(message.from_user.id)
    if not ok:
        await message.answer(f'❌ {reason}', reply_markup=main_menu(await is_admin_user(message.from_user.id)))
        return
    user = await get_connected_user(message.from_user.id)
    if not user:
        await message.answer('❌ Avval akkauntni ulang.')
        return
    status_message = await message.answer('🔄 Guruhlar skanerlanmoqda...')
    try:
        groups = await scan_admin_groups(message.from_user.id, user['session_name'], user['session_string'])
    except Exception as error:
        await status_message.edit_text(f"⚠️ Chatlarni o'qishda xatolik: {str(error)[:180]}")
        return
    await upsert_groups(message.from_user.id, groups)
    rows = await get_admin_groups(message.from_user.id)
    if not rows:
        await status_message.edit_text("❌ Admin bo‘lgan guruh topilmadi.")
        return
    await status_message.edit_text(
        "✅ Guruhlar topildi.\n\nKerakli guruhlarni tanlang.",
        reply_markup=groups_menu(rows),
    )


@router.callback_query(F.data.startswith('group_toggle:'))
async def group_toggle_handler(callback: types.CallbackQuery) -> None:
    chat_id = int(callback.data.split(':', 1)[1])
    await toggle_group(callback.from_user.id, chat_id)
    rows = await get_admin_groups(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=groups_menu(rows))
    await callback.answer()


@router.callback_query(F.data == 'group_all')
async def group_all(callback: types.CallbackQuery) -> None:
    await set_all_groups_selection(callback.from_user.id, True)
    rows = await get_admin_groups(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=groups_menu(rows))
    await callback.answer('Barchasi tanlandi.')


@router.callback_query(F.data == 'group_none')
async def group_none(callback: types.CallbackQuery) -> None:
    await set_all_groups_selection(callback.from_user.id, False)
    rows = await get_admin_groups(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=groups_menu(rows))
    await callback.answer('Tanlov tozalandi.')


@router.callback_query(F.data == 'group_save')
async def group_save(callback: types.CallbackQuery) -> None:
    rows = await get_admin_groups(callback.from_user.id)
    selected = await get_selected_groups(callback.from_user.id)
    await callback.message.edit_text(
        "✅ Tanlov saqlandi.\n\n"
        f"Jami chat: {len(rows)}\n"
        f"Tanlangan chat: {len(selected)}"
    )
    await callback.answer()
