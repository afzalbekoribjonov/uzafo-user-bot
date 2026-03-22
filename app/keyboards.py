from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text='📱 Akkaunt')
    builder.button(text='👥 Guruhlar')
    builder.button(text='🚀 Xabar')
    builder.button(text='📤 Forward')
    builder.button(text='🧭 Faol jarayonlar')
    builder.button(text='📊 Holat')
    builder.button(text='🆘 Yordam')
    builder.button(text='🔑 Kalit')
    if is_admin:
        builder.button(text='⚙️ Admin')
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def cancel_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.button(text='❌ Bekor')
    return builder.as_markup(resize_keyboard=True)


def account_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='✏️ Ismni o‘zgartirish', callback_data='account_edit_name')
    builder.button(text='📝 BIO ni o‘zgartirish', callback_data='account_edit_bio')
    builder.button(text='🔄 Akkauntni almashtirish', callback_data='account_change')
    builder.button(text='🗑 Akkauntni uzish', callback_data='account_disconnect')
    builder.adjust(1)
    return builder.as_markup()


def login_start_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='🔳 QR orqali ulash', callback_data='login_use_qr')
    builder.button(text='❌ Bekor qilish', callback_data='login_cancel')
    builder.adjust(1)
    return builder.as_markup()


def login_code_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='🔄 Kodni qayta yuborish', callback_data='login_resend_code')
    builder.button(text='🔳 QR orqali ulash', callback_data='login_use_qr')
    builder.button(text='❌ Bekor qilish', callback_data='login_cancel')
    builder.adjust(1)
    return builder.as_markup()


def login_wait_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='🔳 QR orqali ulash', callback_data='login_use_qr')
    builder.button(text='❌ Bekor qilish', callback_data='login_cancel')
    builder.adjust(1)
    return builder.as_markup()


def qr_login_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='✅ Tasdiqladim', callback_data='login_qr_check')
    builder.button(text='🔄 QR ni yangilash', callback_data='login_qr_refresh')
    builder.button(text='❌ Bekor qilish', callback_data='login_cancel')
    builder.adjust(1)
    return builder.as_markup()


def groups_menu(groups: list[tuple[int, str, int]] | list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in groups:
        chat_id = int(row[0])
        title = str(row[1])
        auto_send = int(row[2])
        icon = '✅' if auto_send else '⬜️'
        short_title = title if len(title) <= 32 else f'{title[:29]}...'
        builder.button(text=f'{icon} {short_title}', callback_data=f'group_toggle:{chat_id}')
    builder.button(text='✅ Barchasini tanlash', callback_data='group_all')
    builder.button(text='⬜️ Tanlovni tozalash', callback_data='group_none')
    builder.button(text='💾 Saqlash', callback_data='group_save')
    builder.adjust(1)
    return builder.as_markup()


def admin_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='👥 Foydalanuvchilar', callback_data='admin:user_list')
    builder.button(text='🛡 Adminlar', callback_data='admin:admins')
    builder.button(text='🔑 Kalitlar', callback_data='admin:key_menu')
    builder.button(text='📊 Statistika', callback_data='admin:stats')
    builder.button(text='⏱ Interval', callback_data='admin:interval_menu')
    builder.button(text='🚨 Ban holati', callback_data='admin:ban_list')
    builder.button(text='🔓 Muzlatishni yechish', callback_data='admin:unfreeze_menu')
    builder.button(text='🔄 Limitlarni yangilash', callback_data='admin:reset_limits')
    builder.adjust(1)
    return builder.as_markup()


def admin_manage_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='➕ ID bo‘yicha admin qo‘shish', callback_data='admin:add_admin_prompt')
    builder.button(text='📋 Adminlar ro‘yxati', callback_data='admin:list_admins')
    builder.button(text='➖ Adminni olib tashlash', callback_data='admin:remove_admin_menu')
    builder.button(text='⬅️ Orqaga', callback_data='admin:back_main')
    builder.adjust(1)
    return builder.as_markup()


def key_days_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='1 kun', callback_data='admin:key_generate:1')
    builder.button(text='7 kun', callback_data='admin:key_generate:7')
    builder.button(text='30 kun', callback_data='admin:key_generate:30')
    builder.button(text='90 kun', callback_data='admin:key_generate:90')
    builder.button(text="📋 Kalitlar ro'yxati", callback_data='admin:key_list')
    builder.button(text='⬅️ Orqaga', callback_data='admin:back_main')
    builder.adjust(2)
    return builder.as_markup()


def user_interval_menu(rows: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        user_id = int(row['user_id'])
        full_name = row['full_name'] or str(user_id)
        label = full_name if len(full_name) <= 22 else f'{full_name[:19]}...'
        builder.button(text=f"⏱ {label} ({row['forward_interval']}s)", callback_data=f'admin:set_interval:{user_id}')
    builder.button(text='⬅️ Orqaga', callback_data='admin:back_main')
    builder.adjust(1)
    return builder.as_markup()


def unfreeze_menu(rows: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        user_id = int(row['user_id'])
        full_name = row['full_name'] or str(user_id)
        label = full_name if len(full_name) <= 24 else f'{full_name[:21]}...'
        builder.button(text=f'🔓 {label}', callback_data=f'admin:unfreeze:{user_id}')
    builder.button(text='⬅️ Orqaga', callback_data='admin:back_main')
    builder.adjust(1)
    return builder.as_markup()


def admin_user_picker(rows: list, action: str, include_remove_self: bool = False, owner_id: int | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        user_id = int(row['user_id'])
        if not include_remove_self and owner_id and user_id == owner_id:
            continue
        full_name = row['full_name'] or row['phone'] or str(user_id)
        label = full_name if len(full_name) <= 26 else f'{full_name[:23]}...'
        builder.button(text=label, callback_data=f'admin:{action}:{user_id}')
    builder.button(text='⬅️ Orqaga', callback_data='admin:admins')
    builder.adjust(1)
    return builder.as_markup()


def admin_user_list_menu(rows: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        user_id = int(row['user_id'])
        full_name = row['full_name'] or row['phone'] or str(user_id)
        label = full_name if len(full_name) <= 26 else f'{full_name[:23]}...'
        builder.button(text=f'👤 {label}', callback_data=f'admin:user_card:{user_id}')
    builder.button(text='⬅️ Orqaga', callback_data='admin:back_main')
    builder.adjust(1)
    return builder.as_markup()


def user_card_menu(user_id: int, is_blocked: bool, is_admin: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='🔓 Faollashtirish' if is_blocked else '⛔ Bloklash', callback_data=f'admin:user_toggle_active:{user_id}')
    builder.button(text='➖ Adminni olish' if is_admin else '➕ Admin qilish', callback_data=f'admin:user_toggle_admin:{user_id}')
    builder.button(text='♻️ Kartani yangilash', callback_data=f'admin:user_card:{user_id}')
    builder.button(text='⬅️ Foydalanuvchilar', callback_data='admin:user_list')
    builder.adjust(1)
    return builder.as_markup()


def key_list_menu(rows: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows[:20]:
        owner_name = row['owner_name'] if 'owner_name' in row.keys() else None
        owner_phone = row['owner_phone'] if 'owner_phone' in row.keys() else None
        used_by = row['used_by'] if 'used_by' in row.keys() else None
        owner = owner_name or owner_phone or ('Faollashmagan kalit' if not used_by else f'User {used_by}')
        short_owner = owner if len(owner) <= 24 else f'{owner[:21]}...'
        builder.button(text=f'🗑 {short_owner}', callback_data=f"admin:key_delete:{row['key_code']}")
    builder.button(text='⬅️ Orqaga', callback_data='admin:key_menu')
    builder.adjust(1)
    return builder.as_markup()


def campaign_confirm_menu(kind: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='🚀 Boshlash', callback_data=f'campaign:start:{kind}')
    builder.button(text='❌ Bekor qilish', callback_data='campaign:cancel')
    builder.adjust(1)
    return builder.as_markup()


def interval_choice_menu(kind: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='🟢 1 minut', callback_data=f'campaign:interval:{kind}:60')
    builder.button(text='🟡 3 minut', callback_data=f'campaign:interval:{kind}:180')
    builder.button(text='🟠 5 minut', callback_data=f'campaign:interval:{kind}:300')
    builder.button(text='🔵 10 minut', callback_data=f'campaign:interval:{kind}:600')
    builder.button(text='🎲 Random (1/3/5/10)', callback_data=f'campaign:interval:{kind}:random')
    builder.button(text='❌ Bekor qilish', callback_data='campaign:cancel')
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


def active_process_menu(is_paused: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='🔄 Yangilash', callback_data='process:refresh')
    builder.button(text='▶️ Davom ettirish' if is_paused else '⏸ Pauza', callback_data='process:toggle_pause')
    builder.button(text='🛑 To‘xtatish', callback_data='process:stop')
    builder.button(text='❌ Yopish', callback_data='process:close')
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def support_admin_reply_menu(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text='✉️ Javob yozish', callback_data=f'support:reply:{user_id}')
    builder.adjust(1)
    return builder.as_markup()
