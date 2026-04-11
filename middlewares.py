from cachetools import TTLCache
from aiogram import BaseMiddleware, types
from typing import Callable, Dict, Any, Awaitable
from aiogram.fsm.context import FSMContext
import database as db
import i18n

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, throttle_time: int = 1):
        self.cache = TTLCache(maxsize=10000, ttl=throttle_time)
 
    async def __call__(self, handler: Callable[[types.Message, Dict[str, Any]], Awaitable[Any]],
                       event: types.Message, data: Dict[str, Any]) -> Any:
        user_id = event.from_user.id
        if user_id in self.cache:
            return
        self.cache[user_id] = True
        return await handler(event, data)
 
class LangMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: types.Message, data: Dict[str, Any]) -> Any:
        user_id = event.from_user.id
        if i18n.get_user_lang(user_id) == i18n.DEFAULT_LANG:
            lang_db = await db.get_user_lang(user_id)
            if lang_db:
                i18n.set_user_lang(user_id, lang_db)
        return await handler(event, data)

class FSMCommandInterceptorMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: types.Message, data: Dict[str, Any]) -> Any:
        if event.text and event.text.startswith('/'):
            state: FSMContext = data.get("state")
            if state:
                current_state = await state.get_state()
                if current_state and event.text != "/exit":
                    await event.answer("⚠️ Ви знаходитесь в середині процесу. Натисніть /exit щоб скасувати, або завершіть поточну дію.")
                    return
        return await handler(event, data)
