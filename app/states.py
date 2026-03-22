from aiogram.fsm.state import State, StatesGroup


class LoginState(StatesGroup):
    waiting_phone = State()
    waiting_code_part1 = State()
    waiting_code_part2 = State()
    waiting_2fa = State()
    waiting_profile_name = State()
    waiting_profile_bio = State()


class BroadcastState(StatesGroup):
    waiting_text = State()
    waiting_interval_choice = State()
    waiting_confirm = State()


class ForwardState(StatesGroup):
    waiting_message = State()
    waiting_interval_choice = State()
    waiting_confirm = State()


class SupportState(StatesGroup):
    waiting_message = State()
    waiting_admin_reply = State()


class AdminState(StatesGroup):
    waiting_interval = State()
    waiting_key = State()
    waiting_new_admin_id = State()
