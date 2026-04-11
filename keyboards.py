from aiogram import types
import urllib.parse
import json
import i18n
import database as db
from config import WEB_APP_URL
from utils import ulang, study_langs

def _style_keyboard(options: list) -> types.ReplyKeyboardMarkup:
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=opt)] for opt in options],
        resize_keyboard=True, one_time_keyboard=True
    )
 
def _hobby_keyboard(lang: str, selected_ids: list) -> types.ReplyKeyboardMarkup:
    cats     = i18n.get_list(lang, "hobby_categories")
    done_btn = i18n.t(lang, "hobby.done_btn")
    keyboard = []
    for cat in cats:
        mark = "✅ " if cat["id"] in selected_ids else ""
        keyboard.append([types.KeyboardButton(text=f"{mark}{cat['label']}")])
    if selected_ids:
        keyboard.append([types.KeyboardButton(text=done_btn)])
    return types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
 
def _study_lang_keyboard(user_id: int) -> types.ReplyKeyboardMarkup:
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=l)] for l in study_langs(user_id)] +
                 [[types.KeyboardButton(text="/exit")]],
        resize_keyboard=True, one_time_keyboard=True
    )
 
def _lang_select_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text=f"{flag} {name}", callback_data=f"setlang:{code}"
        )]
        for code, (name, flag) in i18n.SUPPORTED_UI_LANGS.items()
    ])
 
async def get_main_kb(user_id: int) -> types.ReplyKeyboardMarkup:
    lang       = ulang(user_id)
    words_raw  = await db.get_user_words(user_id)
    game_words = [{"w": w['word'], "t": w['translation']}
                  for w in sorted(words_raw, key=lambda x: x['usage_count'])[:50]] if words_raw else []
    game_url   = f"{WEB_APP_URL}?data={urllib.parse.quote(json.dumps(game_words))}" if game_words else WEB_APP_URL
    return types.ReplyKeyboardMarkup(keyboard=[
        [types.KeyboardButton(text=i18n.t(lang, "btn.play_webapp"),
                              web_app=types.WebAppInfo(url=game_url))],
        [types.KeyboardButton(text="/add_word"),     types.KeyboardButton(text="/practice")],
        [types.KeyboardButton(text="/all_words"),    types.KeyboardButton(text="/stats")],
        [types.KeyboardButton(text="/word_of_day"),  types.KeyboardButton(text=i18n.t(lang, "btn.top"))],
        [types.KeyboardButton(text="/import_words"), types.KeyboardButton(text="🤖 /AI")],
        [types.KeyboardButton(text=i18n.t(lang, "btn.feedback")),
         types.KeyboardButton(text=i18n.t(lang, "btn.help"))],
        [types.KeyboardButton(text="/weak")],
        [types.KeyboardButton(text="/exit")],
    ], resize_keyboard=True)
