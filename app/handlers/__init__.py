from aiogram import Dispatcher

from app.handlers import account, admin, common, groups, mailing, support



def setup_handlers(dp: Dispatcher) -> None:
    dp.include_router(common.router)
    dp.include_router(account.router)
    dp.include_router(groups.router)
    dp.include_router(mailing.router)
    dp.include_router(support.router)
    dp.include_router(admin.router)
