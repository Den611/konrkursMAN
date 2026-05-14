import asyncio
import logging
from aiogram import Bot, Dispatcher
from config import BOT_TOKEN
import ai_manager
import database as db

from middlewares import ThrottlingMiddleware, LangMiddleware, FSMCommandInterceptorMiddleware
from handlers import general, registration, stats, ai_helper, practice, words

# Логування 
class BotFormatter(logging.Formatter):
    SEP  = "═" * 52
    FMT  = "%(asctime)s  %(levelname)-8s  %(message)s"
    DATEFMT = "%Y-%m-%d %H:%M"

    HIGHLIGHT = {logging.CRITICAL, logging.ERROR}

    def format(self, record: logging.LogRecord) -> str:
        record.levelname = {
            "DEBUG":    "DEBUG   ",
            "INFO":     "INFO    ",
            "WARNING":  "WARNING ",
            "ERROR":    "ERROR   ",
            "CRITICAL": "CRITICAL",
        }.get(record.levelname, record.levelname)

        formatted = super().format(record)

        if record.levelno in self.HIGHLIGHT:
            return f"\n{self.SEP}\n{formatted}\n{self.SEP}\n"
        return formatted

_handler = logging.StreamHandler()
_handler.setFormatter(BotFormatter(
    fmt=BotFormatter.FMT,
    datefmt=BotFormatter.DATEFMT,
))
logging.root.setLevel(logging.INFO)
logging.root.handlers = [_handler]

for _noisy in ("aiogram", "asyncio", "aiohttp"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher()

dp.message.middleware(ThrottlingMiddleware(1))
dp.message.middleware(LangMiddleware())
dp.message.middleware(FSMCommandInterceptorMiddleware())

dp.include_router(general.router)
dp.include_router(registration.router)
dp.include_router(stats.router)
dp.include_router(ai_helper.router)
dp.include_router(practice.router)
dp.include_router(words.router)

async def main():
    await db.init_connection()
    await db.init_db()
    await ai_manager.init_ai_session()
    sep = "═" * 52
    print(f"\n{sep}")
    logger.info("✅  БОТ ЗАПУЩЕНО  |  uk/pl/en  |  SM-2  |  Streak")
    print(sep)
    try:
        await dp.start_polling(bot)
    finally:
        print(f"\n{sep}")
        logger.info("🛑  Бот зупинено")
        print(f"{sep}\n")
        await ai_manager.close_ai_session()

if __name__ == "__main__":
    asyncio.run(main())