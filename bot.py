import asyncio
import random
import json
import urllib.parse
import io
import html
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from cachetools import TTLCache
from deep_translator import GoogleTranslator
from typing import Callable, Dict, Any, Awaitable

from config import BOT_TOKEN, WEB_APP_URL
import ai_manager
import database as db
import i18n

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Мови, які ВИВЧАЮТЬСЯ (не мова інтерфейсу)
SUPPORTED_LANGUAGES = ["English", "German", "French", "Polish", "Spanish", "Italian"]


# ─────────────────────────────────────────
# MIDDLEWARE: ANTI-SPAM + ЗАВАНТАЖЕННЯ МОВИ
# ─────────────────────────────────────────
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
    """При кожному повідомленні підвантажує мову інтерфейсу з БД (якщо не кешована)."""
    async def __call__(self, handler, event: types.Message, data: Dict[str, Any]) -> Any:
        user_id = event.from_user.id
        if i18n.get_user_lang(user_id) == i18n.DEFAULT_LANG:
            lang_db = await db.get_user_lang(user_id)
            if lang_db:
                i18n.set_user_lang(user_id, lang_db)
        return await handler(event, data)


dp.message.middleware(ThrottlingMiddleware(1))
dp.message.middleware(LangMiddleware())


# ─────────────────────────────────────────
# СКОРОЧЕННЯ: отримати переклад для юзера
# ─────────────────────────────────────────
def ul(user_id: int, key: str, **kwargs) -> str:
    """Отримати переклад у мові конкретного користувача."""
    return i18n.t(i18n.get_user_lang(user_id), key, **kwargs)


def ulang(user_id: int) -> str:
    return i18n.get_user_lang(user_id)


# ─────────────────────────────────────────
# FSM СТАНИ
# ─────────────────────────────────────────
class Registration(StatesGroup):
    q1             = State()
    q2             = State()
    q3             = State()
    q4             = State()
    hobby_category = State()
    hobby_custom   = State()

class AddWord(StatesGroup):
    waiting_for_word        = State()
    waiting_for_language    = State()
    waiting_for_translation = State()

class DeleteWord(StatesGroup):
    waiting_for_word = State()

class PracticeWord(StatesGroup):
    waiting_for_language = State()
    waiting_for_answer   = State()

class ViewWords(StatesGroup):
    waiting_for_language = State()

class AIHelper(StatesGroup):
    waiting_for_prompt   = State()
    waiting_for_language = State()

class WordOfDayState(StatesGroup):
    waiting_for_language = State()
    waiting_for_action   = State()

class FeedbackState(StatesGroup):
    waiting_for_message = State()


# ─────────────────────────────────────────
# ДОПОМІЖНІ ФУНКЦІЇ
# ─────────────────────────────────────────
def _style_keyboard(options: list[str]) -> types.ReplyKeyboardMarkup:
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=opt)] for opt in options],
        resize_keyboard=True, one_time_keyboard=True
    )


def _hobby_keyboard(lang: str, selected_ids: list) -> types.ReplyKeyboardMarkup:
    """Клавіатура хобі: категорії з галочками + кнопка 'Готово'."""
    cats = i18n.get_list(lang, "hobby_categories")
    done_btn = i18n.t(lang, "hobby.done_btn")
    keyboard = []
    for cat in cats:
        mark = "✅ " if cat["id"] in selected_ids else ""
        keyboard.append([types.KeyboardButton(text=f"{mark}{cat['label']}")])
    if selected_ids:
        keyboard.append([types.KeyboardButton(text=done_btn)])
    return types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def _find_cat_by_label(lang: str, text: str) -> dict | None:
    """Знайти категорію хобі за відображуваним label (з або без ✅)."""
    clean = text.replace("✅ ", "").strip()
    cats = i18n.get_list(lang, "hobby_categories")
    return next((c for c in cats if c["label"] == clean), None)


async def get_main_kb(user_id: int) -> types.ReplyKeyboardMarkup:
    lang = ulang(user_id)
    words_raw  = await db.get_user_words(user_id)
    game_words = [{"w": w['word'], "t": w['translation']}
                  for w in sorted(words_raw, key=lambda x: x['usage_count'])[:50]] if words_raw else []
    game_url   = f"{WEB_APP_URL}?data={urllib.parse.quote(json.dumps(game_words))}" if game_words else WEB_APP_URL

    return types.ReplyKeyboardMarkup(keyboard=[
        [types.KeyboardButton(text=i18n.t(lang, "btn.play_webapp"), web_app=types.WebAppInfo(url=game_url))],
        [types.KeyboardButton(text="/add_word"),    types.KeyboardButton(text="/practice")],
        [types.KeyboardButton(text="/all_words"),   types.KeyboardButton(text="/stats")],
        [types.KeyboardButton(text="/word_of_day"), types.KeyboardButton(text=i18n.t(lang, "btn.top"))],
        [types.KeyboardButton(text="/import_words"),types.KeyboardButton(text="🤖 /AI")],
        [types.KeyboardButton(text=i18n.t(lang, "btn.feedback")), types.KeyboardButton(text=i18n.t(lang, "btn.help"))],
        [types.KeyboardButton(text="/exit")],
    ], resize_keyboard=True)


async def get_user_level_info(user_id: int):
    words    = await db.get_user_words(user_id)
    total_xp = sum(w['usage_count'] for w in words)
    level, xp_needed = 1, 10
    while total_xp >= xp_needed:
        total_xp  -= xp_needed
        level     += 1
        xp_needed += 10
    return level, total_xp, xp_needed


async def _update_level(user_id: int):
    words    = await db.get_user_words(user_id)
    total_xp = sum(w['usage_count'] for w in words)
    await db.update_user_level(user_id, total_xp)


def _lang_select_keyboard() -> types.InlineKeyboardMarkup:
    """Inline-клавіатура вибору мови інтерфейсу."""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(
            text=f"{flag} {name}",
            callback_data=f"setlang:{code}"
        )]
        for code, (name, flag) in i18n.SUPPORTED_UI_LANGS.items()
    ])


# ─────────────────────────────────────────
# КОМАНДА /language — зміна мови інтерфейсу
# ─────────────────────────────────────────
@dp.message(Command("language"))
async def cmd_language(message: types.Message, state: FSMContext):
    await state.update_data(lang_select_purpose="change")
    await message.answer(
        i18n.t("uk", "lang_select"),   # Повідомлення у всіх 3 мовах — нейтральне
        reply_markup=_lang_select_keyboard()
    )


@dp.callback_query(F.data.startswith("setlang:"))
async def callback_set_lang(callback: types.CallbackQuery, state: FSMContext):
    lang_code = callback.data.split(":")[1]
    user_id   = callback.from_user.id

    if lang_code not in i18n.SUPPORTED_UI_LANGS:
        await callback.answer("Unknown language", show_alert=True)
        return

    # Зберегти в БД та кеш
    await db.set_user_lang(user_id, lang_code)
    i18n.set_user_lang(user_id, lang_code)

    data    = await state.get_data()
    purpose = data.get("lang_select_purpose", "change")

    await callback.message.edit_text(i18n.t(lang_code, "lang_changed"))

    if purpose == "register":
        # Новий користувач — починаємо тест
        await state.update_data(
            scores={"visual": 0, "audial": 0, "logic": 0, "practice": 0}
        )
        await state.set_state(Registration.q1)
        await callback.message.answer(i18n.t(lang_code, "start.welcome_new"), parse_mode="HTML")
        q = i18n.get_list(lang_code, "style_test")[0]
        await callback.message.answer(q["question"], parse_mode="HTML",
                                      reply_markup=_style_keyboard(q["options"]))
    else:
        # Просто підтверджуємо зміну
        await callback.message.answer(
            i18n.t(lang_code, "lang_command_hint"),
            reply_markup=await get_main_kb(user_id)
        )
    await callback.answer()


# ─────────────────────────────────────────
# /start
# ─────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await db.add_user(user_id, message.from_user.username or "Учень")
    profile = await db.get_user_profile_data(user_id)

    if not profile or not profile['hobbies'] or profile['hobbies'] == "повсякденне життя":
        # Новий користувач — спочатку вибір мови
        await state.update_data(lang_select_purpose="register")
        await message.answer(i18n.t("uk", "lang_select"), reply_markup=_lang_select_keyboard())
    else:
        # Повертається — завантажуємо мову і вітаємо
        lang_db = await db.get_user_lang(user_id)
        if lang_db:
            i18n.set_user_lang(user_id, lang_db)
        await db.update_last_active(user_id)
        await state.clear()
        await message.answer(
            ul(user_id, "start.welcome_back"),
            reply_markup=await get_main_kb(user_id)
        )


# ─────────────────────────────────────────
# РЕЄСТРАЦІЯ — 4 питання тесту стилю
# ─────────────────────────────────────────
async def _ask_style_q(message: types.Message, lang: str, q_idx: int):
    q = i18n.get_list(lang, "style_test")[q_idx]
    await message.answer(q["question"], parse_mode="HTML",
                         reply_markup=_style_keyboard(q["options"]))


async def _handle_style_q(message: types.Message, state: FSMContext, q_idx: int, next_state):
    if not message.text: 
        return 
    lang   = ulang(message.from_user.id)
    data   = await state.get_data()
    scores = data["scores"]
    q      = i18n.get_list(lang, "style_test")[q_idx]
    style  = i18n.score_answer(message.text, q["options"])
    if style:
        scores[style] += 1
    await state.update_data(scores=scores)
    await state.set_state(next_state)
    await _ask_style_q(message, lang, q_idx + 1)


@dp.message(Registration.q1)
async def reg_q1(message: types.Message, state: FSMContext):
    await _handle_style_q(message, state, 0, Registration.q2)

@dp.message(Registration.q2)
async def reg_q2(message: types.Message, state: FSMContext):
    await _handle_style_q(message, state, 1, Registration.q3)

@dp.message(Registration.q3)
async def reg_q3(message: types.Message, state: FSMContext):
    await _handle_style_q(message, state, 2, Registration.q4)

@dp.message(Registration.q4)
async def reg_q4(message: types.Message, state: FSMContext):
    lang   = ulang(message.from_user.id)
    data   = await state.get_data()
    scores = data["scores"]
    q      = i18n.get_list(lang, "style_test")[3]
    style  = i18n.score_answer(message.text, q["options"])
    if style:
        scores[style] += 1
    final_style = max(scores, key=scores.get)
    await state.update_data(final_style=final_style, selected_hobbies=[])
    await state.set_state(Registration.hobby_category)

    score_line  = " | ".join([f"{i18n.t(lang, f'style.{k}.name')}: {v}" for k, v in scores.items()])
    style_desc  = i18n.t(lang, f"style.{final_style}.desc")
    result_text = i18n.t(lang, "style_result", desc=style_desc, score_line=score_line)
    await message.answer(result_text, parse_mode="HTML", reply_markup=_hobby_keyboard(lang, []))


@dp.message(Registration.hobby_category)
async def reg_hobby_category(message: types.Message, state: FSMContext):
    if not message.text:   
        return        
    lang     = ulang(message.from_user.id)
    data     = await state.get_data()
    selected = data.get("selected_hobbies", [])
    text     = message.text.strip()
    done_btn = i18n.t(lang, "hobby.done_btn")

    if text == done_btn:
        if "other" in selected or not selected:
            if not selected:
                await message.answer(i18n.t(lang, "hobby.at_least_one"),
                                     reply_markup=_hobby_keyboard(lang, selected))
                return
            # є "other" — просимо написати вручну
            sel_no_other = [s for s in selected if s != "other"]
            await state.update_data(selected_hobbies=sel_no_other)
            await state.set_state(Registration.hobby_custom)
            hobby_so_far = _build_keyword_str(lang, sel_no_other)
            prev = i18n.t(lang, "hobby.custom_prev", hobby_so_far=hobby_so_far) if hobby_so_far else ""
            await message.answer(
                i18n.t(lang, "hobby.custom_prompt", prev=prev),
                parse_mode="HTML", reply_markup=types.ReplyKeyboardRemove()
            )
        else:
            await _finish_registration(message, state, selected)
        return

    cat = _find_cat_by_label(lang, text)
    if not cat:
        await message.answer(i18n.t(lang, "hobby.choose_from_btns"),
                             reply_markup=_hobby_keyboard(lang, selected))
        return

    if cat["id"] in selected:
        selected.remove(cat["id"])
    else:
        selected.append(cat["id"])
    await state.update_data(selected_hobbies=selected)

    count = len(selected)
    if count == 0:
        hint = i18n.t(lang, "hobby.no_selection")
    else:
        cats_data = i18n.get_list(lang, "hobby_categories")
        names = [c["label"].split(" ", 1)[1] for c in cats_data if c["id"] in selected]
        hint = i18n.t(lang, "hobby.selected_hint", count=count, names=", ".join(names))

    await message.answer(hint, parse_mode="HTML", reply_markup=_hobby_keyboard(lang, selected))


@dp.message(Registration.hobby_custom)
async def reg_hobby_custom(message: types.Message, state: FSMContext):
    lang = ulang(message.from_user.id)
    if len(message.text.strip()) < 2:
        await message.answer(i18n.t(lang, "hobby.custom_too_short"))
        return
    data     = await state.get_data()
    selected = data.get("selected_hobbies", [])
    await _finish_registration(message, state, selected, custom=message.text.strip())


def _build_keyword_str(lang: str, selected_ids: list, custom: str = None) -> str:
    cats_data = i18n.get_list(lang, "hobby_categories")
    parts = [c["keywords"] for c in cats_data if c["id"] in selected_ids and c["keywords"]]
    if custom:
        parts.append(custom)
    return ", ".join(parts) if parts else "повсякденне життя"


async def _finish_registration(message: types.Message, state: FSMContext,
                                selected: list, custom: str = None):
    lang        = ulang(message.from_user.id)
    data        = await state.get_data()
    final_style = data.get("final_style", "visual")

    hobby_str = _build_keyword_str(lang, selected, custom)
    await db.update_user_profile(message.from_user.id, final_style, hobby_str)
    await state.clear()

    cats_data = i18n.get_list(lang, "hobby_categories")
    display   = [c["label"].split(" ", 1)[1] for c in cats_data if c["id"] in selected]
    if custom:
        display.append(custom)

    style_name = i18n.t(lang, f"style.{final_style}.name")
    finish_text = i18n.t(lang, "reg_done",
                         style=style_name,
                         interests=", ".join(display) if display else hobby_str)
    await message.answer(finish_text, parse_mode="HTML",
                         reply_markup=await get_main_kb(message.from_user.id))


# ─────────────────────────────────────────
# /help
# ─────────────────────────────────────────
@dp.message(Command("help"))
@dp.message(F.text.in_(["Допомога ❓", "Pomoc ❓", "Help ❓"]))
async def cmd_help(message: types.Message):
    await message.answer(ul(message.from_user.id, "help.title"), parse_mode="HTML",
                         reply_markup=await get_main_kb(message.from_user.id))


# ─────────────────────────────────────────
# /stats
# ─────────────────────────────────────────
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    uid  = message.from_user.id
    lang = ulang(uid)
    words   = await db.get_user_words(uid)
    lvl, cur_xp, next_xp = await get_user_level_info(uid)
    bar     = "🟩" * int((cur_xp / max(1, next_xp)) * 10) + "⬜" * (10 - int((cur_xp / max(1, next_xp)) * 10))
    best    = await db.get_best_score(uid)
    profile = await db.get_user_profile_data(uid)

    raw_style = profile['learning_style'] if profile and profile['learning_style'] else None
    style = i18n.get_style_display(lang, raw_style) if raw_style else i18n.t(lang, "stats.no_style")
    hobby = profile['hobbies'] if profile and profile['hobbies'] else i18n.t(lang, "stats.no_hobby")

    pts = i18n.t(lang, "stats.points")
    stats = (
        f"{i18n.t(lang, 'stats.title')}\n"
        f"{i18n.t(lang, 'stats.style_label')} <b>{style}</b>\n"
        f"{i18n.t(lang, 'stats.hobby_label')} <b>{hobby}</b>\n\n"
        f"{i18n.t(lang, 'stats.level_label')} <b>{lvl}</b>\n"
        f"{i18n.t(lang, 'stats.xp_label')} {cur_xp}/{next_xp}\n"
        f"[{bar}]\n\n"
        f"{i18n.t(lang, 'stats.words_label')} {len(words)}\n"
        f"{i18n.t(lang, 'stats.record_label')} <b>{best}</b>{pts}"
    )
    await message.answer(stats, parse_mode="HTML",
                         reply_markup=await get_main_kb(uid))


# ─────────────────────────────────────────
# /top
# ─────────────────────────────────────────
@dp.message(Command("top"))
@dp.message(F.text.in_(["🏆 ТОП Лідери", "🏆 TOP Gracze", "🏆 TOP Players"]))
async def cmd_top(message: types.Message):
    uid  = message.from_user.id
    lang = ulang(uid)
    top_users = await db.get_top_users(10)
    if not top_users:
        return await message.answer(i18n.t(lang, "top.empty"),
                                    reply_markup=await get_main_kb(uid))
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    pts    = i18n.t(lang, "top.points")
    text   = i18n.t(lang, "top.title")
    for i, user in enumerate(top_users):
        name  = user['username'] or i18n.t(lang, "top.anon")
        text += f"{medals[i]} <b>{name}</b> — {user['best_score']}{pts}\n"
    text += i18n.t(lang, "top.footer")
    await message.answer(text, parse_mode="HTML",
                         reply_markup=await get_main_kb(uid))


# ─────────────────────────────────────────
# /exit
# ─────────────────────────────────────────
@dp.message(Command("exit"))
async def cmd_exit(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    await db.update_last_active(uid)
    await state.clear()
    await message.answer(
        ul(uid, "exit.done", commands=ul(uid, "help.commands_list")),
        reply_markup=await get_main_kb(uid)
    )


# ─────────────────────────────────────────
# ДОДАВАННЯ СЛОВА
# ─────────────────────────────────────────
@dp.message(Command("add_word"))
async def cmd_add_word(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    await db.update_last_active(uid)
    await state.set_state(AddWord.waiting_for_word)
    await message.answer(ul(uid, "add_word.enter_word"),
                         reply_markup=await get_main_kb(uid))


@dp.message(AddWord.waiting_for_word)
async def process_word(message: types.Message, state: FSMContext):
    if message.text == '/exit': return await cmd_exit(message, state)
    uid = message.from_user.id
    await state.update_data(word=message.text.strip())
    lang_kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=l)] for l in SUPPORTED_LANGUAGES] +
                 [[types.KeyboardButton(text="/exit")]],
        resize_keyboard=True, one_time_keyboard=True)
    await state.set_state(AddWord.waiting_for_language)
    await message.answer(ul(uid, "add_word.choose_lang"), reply_markup=lang_kb)


@dp.message(AddWord.waiting_for_language)
async def process_language(message: types.Message, state: FSMContext):
    if message.text == '/exit': return await cmd_exit(message, state)
    uid = message.from_user.id
    await state.update_data(language=message.text.strip())
    word = (await state.get_data())['word']
    try:
        auto_trans = GoogleTranslator(source='auto', target='uk').translate(word)
    except:
        auto_trans = "Помилка"
    await state.update_data(auto_translation=auto_trans)
    save_label = ul(uid, "add_word.save_btn", translation=auto_trans)
    trans_kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=save_label)],
                  [types.KeyboardButton(text="/exit")]],
        resize_keyboard=True, one_time_keyboard=True)
    await state.set_state(AddWord.waiting_for_translation)
    await message.answer(
        ul(uid, "add_word.autotrans", translation=html.escape(auto_trans)),
        reply_markup=trans_kb, parse_mode="HTML"
    )


@dp.message(AddWord.waiting_for_translation)
async def process_custom_translation(message: types.Message, state: FSMContext):
    if message.text == '/exit': return await cmd_exit(message, state)
    uid  = message.from_user.id
    data = await state.get_data()
    final_translation = (data['auto_translation']
                         if message.text.startswith("Зберегти:") or message.text.startswith("Zapisz:") or message.text.startswith("Save:")
                         else message.text.strip())
    await message.answer(ul(uid, "add_word.saving"))
    transc, assoc, visual = await ai_manager.get_full_word_info(data['word'], final_translation, data['language'])
    img = await ai_manager.get_image_url(visual)
    if not img: img = await ai_manager.get_image_url(data['word'])
    if not img: img = await ai_manager.get_image_url(final_translation)
    added = await db.add_word_to_db(message.from_user.id, data['word'], final_translation,
                                    data['language'], img, assoc, transc)
    if added:
        text = ul(uid, "add_word.added",
                  word=html.escape(data['word']),
                  transcription=html.escape(transc),
                  translation=html.escape(final_translation),
                  association=html.escape(assoc))
    else:
        text = ul(uid, "add_word.already_exists")

    inline_kb = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(
            text=ul(uid, "btn.regen_photo"),
            callback_data=f"regen:{data['word'][:20]}")]])
    try:
        if img and added:
            await message.answer_photo(img, caption=text, reply_markup=inline_kb, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=inline_kb if added else None, parse_mode="HTML")
    except Exception as e:
        print(f"⚠️ Помилка форматування: {e}")
        clean = f"✅ {data['word']} {transc} — {final_translation}\n🧠 {assoc}"
        if img and added:
            await message.answer_photo(img, caption=clean, reply_markup=inline_kb)
        else:
            await message.answer(clean, reply_markup=inline_kb if added else None)

    await message.answer(ul(uid, "add_word.continue"),
                         reply_markup=await get_main_kb(uid))
    await state.set_state(AddWord.waiting_for_word)


# ─────────────────────────────────────────
# СЛОВО ДНЯ
# ─────────────────────────────────────────
@dp.message(Command("word_of_day"))
async def cmd_word_of_day(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    lang_kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=l)] for l in SUPPORTED_LANGUAGES] +
                 [[types.KeyboardButton(text="/exit")]],
        resize_keyboard=True, one_time_keyboard=True)
    await state.set_state(WordOfDayState.waiting_for_language)
    await message.answer(ul(uid, "word_of_day.choose_lang"), reply_markup=lang_kb)


@dp.message(WordOfDayState.waiting_for_language)
async def process_wod_lang(message: types.Message, state: FSMContext):
    uid  = message.from_user.id
    lang_learn = message.text.strip()
    if lang_learn == '/exit': return await cmd_exit(message, state)
    await message.answer(ul(uid, "word_of_day.generating", lang=lang_learn))
    lvl, _, _ = await get_user_level_info(uid)
    diff      = "A1" if lvl <= 3 else "B1" if lvl <= 8 else "C1"
    prompt    = (f"Generate exactly ONE word in {lang_learn} for level {diff} with Ukrainian translation. "
                 f"Format strictly: Apple - Яблуко")
    result    = await ai_manager.generate_content_safe(prompt)
    w, t_word = None, None
    for line in result.split('\n'):
        line = line.strip().replace("*", "")
        if " - " in line and "Слово" not in line and "Word" not in line:
            parts = line.split(" - ", 1)
            w, t_word = parts[0].strip(), parts[1].strip()
            break
    if w and t_word:
        transc, assoc, visual = await ai_manager.get_full_word_info(w, t_word, lang_learn)
        img = await ai_manager.get_image_url(visual)
        if not img: img = await ai_manager.get_image_url(w)
        if not img: img = await ai_manager.get_image_url(t_word)
        await state.update_data(new_word=w, translation=t_word, lang=lang_learn,
                                image_url=img, association=assoc, transcription=transc)
        msg_text = ul(uid, "word_of_day.result",
                      word=html.escape(w), transcription=html.escape(transc), translation=html.escape(t_word))
        inline = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(
                text=ul(uid, "btn.regen_photo"), callback_data=f"regen:{w[:20]}")]])
        wod_kb = types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text=ul(uid, "word_of_day.add_btn"))],
                      [types.KeyboardButton(text=ul(uid, "btn.exit_menu"))]],
            resize_keyboard=True)
        try:
            if img:
                await message.answer_photo(img, caption=msg_text, reply_markup=inline, parse_mode="HTML")
            else:
                await message.answer(msg_text, reply_markup=inline, parse_mode="HTML")
        except Exception as e:
            print(f"⚠️ Помилка Telegram: {e}")
            clean = f"🌟 {w} {transc}\n🇺🇦 {t_word}"
            if img:
                await message.answer_photo(img, caption=clean, reply_markup=inline)
            else:
                await message.answer(clean, reply_markup=inline)
        await message.answer(ul(uid, "word_of_day.actions"), reply_markup=wod_kb)
        await state.set_state(WordOfDayState.waiting_for_action)
    else:
        await message.answer(ul(uid, "word_of_day.ai_confused", result=result),
                             reply_markup=await get_main_kb(uid))
        await state.clear()


@dp.message(WordOfDayState.waiting_for_action)
async def process_wod_action(message: types.Message, state: FSMContext):
    uid  = message.from_user.id
    exit_txt = ul(uid, "btn.exit_menu")
    add_txt  = ul(uid, "word_of_day.add_btn")
    if message.text == exit_txt: return await cmd_exit(message, state)
    if message.text == add_txt:
        data  = await state.get_data()
        added = await db.add_word_to_db(uid, data['new_word'], data['translation'],
                                        data['lang'], data['image_url'], data['association'], data['transcription'])
        text = (ul(uid, "word_of_day.added", association=data['association'])
                if added else ul(uid, "word_of_day.already"))
        await message.answer(text, reply_markup=await get_main_kb(uid))
        await state.clear()


# ─────────────────────────────────────────
# ПРАКТИКА (Spaced Repetition)
# ─────────────────────────────────────────
@dp.message(Command("practice"))
async def cmd_practice(message: types.Message, state: FSMContext):
    uid   = message.from_user.id
    words = await db.get_user_words(uid, for_review=True)
    if not words:
        return await message.answer(ul(uid, "practice.all_done"),
                                    reply_markup=await get_main_kb(uid))
    await state.update_data(all_practice_words=[dict(w) for w in words])
    all_langs_btn = ul(uid, "practice.all_langs_btn")
    lang_kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=l)] for l in set(w['language'] for w in words)] +
                 [[types.KeyboardButton(text=all_langs_btn)],
                  [types.KeyboardButton(text="/exit")]],
        resize_keyboard=True, one_time_keyboard=True)
    await state.set_state(PracticeWord.waiting_for_language)
    await message.answer(
        ul(uid, "practice.words_today", count=len(words)),
        reply_markup=lang_kb
    )


@dp.message(PracticeWord.waiting_for_language)
async def practice_choose_lang(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if message.text == '/exit': return await cmd_exit(message, state)
    target     = (await state.get_data())['all_practice_words']
    all_langs  = ul(uid, "practice.all_langs_btn")
    if message.text != all_langs:
        target = [w for w in target if w['language'] == message.text]
    if not target:
        return await message.answer(ul(uid, "practice.empty_lang"))
    random.shuffle(target)
    await state.update_data(plist=target[:10], pidx=0)
    await state.set_state(PracticeWord.waiting_for_answer)
    w = target[0]
    q = ul(uid, "practice.question",
           translation=html.escape(w['translation']), lang=w['language'])
    if w['image_url']:
        await message.answer_photo(w['image_url'], caption=q, parse_mode="HTML")
    else:
        await message.answer(q, parse_mode="HTML")


@dp.message(PracticeWord.waiting_for_answer)
async def process_practice_ans(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if message.text == "/exit": return await cmd_exit(message, state)
    data       = await state.get_data()
    w          = data['plist'][data['pidx']]
    is_correct = message.text.strip().lower() == w['word'].lower()
    await db.update_word_progress(uid, w['word'], is_correct)
    await _update_level(uid)

    if is_correct:
        await message.answer(ul(uid, "practice.correct", word=w['word']))
    else:
        await message.answer(ul(uid, "practice.wrong",
                                word=w['word'],
                                transcription=w['transcription'] or '',
                                association=w['association'] or ''))

    data['pidx'] += 1
    if data['pidx'] >= len(data['plist']):
        await message.answer(ul(uid, "practice.finished"),
                             reply_markup=await get_main_kb(uid))
        await state.clear()
    else:
        await state.update_data(pidx=data['pidx'])
        nw = data['plist'][data['pidx']]
        q  = ul(uid, "practice.question",
                translation=html.escape(nw['translation']), lang=nw['language'])
        if nw['image_url']:
            await message.answer_photo(nw['image_url'], caption=q, parse_mode="HTML")
        else:
            await message.answer(q, parse_mode="HTML")


# ─────────────────────────────────────────
# AI ПОЯСНЕННЯ
# ─────────────────────────────────────────
@dp.message(Command("AI"))
@dp.message(F.text == "🤖 /AI")
async def cmd_ai(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    await state.set_state(AIHelper.waiting_for_prompt)
    await message.answer(ul(uid, "ai.enter_word"),
                         reply_markup=await get_main_kb(uid))


@dp.message(AIHelper.waiting_for_prompt)
async def process_ai_prompt(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if message.text == '/exit': return await cmd_exit(message, state)
    await state.update_data(prompt=message.text.strip())
    lang_kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=l)] for l in SUPPORTED_LANGUAGES + ["Українська"]] +
                 [[types.KeyboardButton(text="/exit")]],
        resize_keyboard=True, one_time_keyboard=True)
    await state.set_state(AIHelper.waiting_for_language)
    await message.answer(ul(uid, "ai.choose_lang"), reply_markup=lang_kb)


@dp.message(AIHelper.waiting_for_language)
async def process_ai_language(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if message.text == '/exit': return await cmd_exit(message, state)
    data  = await state.get_data()
    hobby = await db.get_user_hobby(uid)
    if not hobby: hobby = "повсякденне життя"
    await message.answer(ul(uid, "ai.thinking"))
    txt = ul(uid, "errors.gen_error")
    try:
        txt, img = await asyncio.gather(
            ai_manager.get_ai_explanation_text(data['prompt'], message.text.strip(), hobby),
            ai_manager.get_image_url(data['prompt']))
        inline = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(
                text=ul(uid, "btn.regen_photo"),
                callback_data=f"regen:{data['prompt'][:20]}")]])
        prefix = ul(uid, "ai.result_prefix")
        if img:
            await message.answer_photo(img, caption=f"{prefix}{txt}"[:1024],
                                       reply_markup=inline, parse_mode="HTML")
        else:
            await message.answer(f"{prefix}{txt}", reply_markup=inline, parse_mode="HTML")
    except Exception as e:
        print(f"⚠️ Помилка виводу AI: {e}")
        await message.answer(f"🤖\n\n{txt}", parse_mode=None)
    await state.set_state(AIHelper.waiting_for_prompt)
    await message.answer(ul(uid, "ai.ask_next"))


# ─────────────────────────────────────────
# ВИДАЛЕННЯ, СПИСОК, ІМПОРТ, ВІДГУК
# ─────────────────────────────────────────
@dp.message(Command("delete_word"))
async def cmd_delete_word(message: types.Message, state: FSMContext):
    await state.set_state(DeleteWord.waiting_for_word)
    await message.answer(ul(message.from_user.id, "delete.enter_word"))


@dp.message(DeleteWord.waiting_for_word)
async def process_delete_word(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if message.text == '/exit': return await cmd_exit(message, state)
    await db.delete_word_from_db(uid, message.text.strip())
    await message.answer(ul(uid, "delete.done"),
                         reply_markup=await get_main_kb(uid))
    await state.clear()


@dp.message(Command("all_words"))
async def cmd_all_words(message: types.Message):
    uid   = message.from_user.id
    words = await db.get_user_words(uid)
    if not words:
        return await message.answer(ul(uid, "all_words.empty"))
    title = ul(uid, "all_words.title")
    rows  = "\n".join([ul(uid, "all_words.row",
                          word=w['word'], translation=w['translation']) for w in words])
    await message.answer((title + rows)[:4000])


@dp.message(Command("import_words"))
async def cmd_import_words(message: types.Message):
    await message.answer(ul(message.from_user.id, "import.instructions"))


@dp.message(F.document)
async def process_document(message: types.Message):
    uid = message.from_user.id
    if not message.document.file_name.endswith(('.csv', '.txt')): return
    await message.answer(ul(uid, "import.processing"))
    try:
        file_in_io = io.BytesIO()
        await message.bot.download(message.document, destination=file_in_io)
        lines = file_in_io.getvalue().decode('utf-8').splitlines()
        added = 0
        for line in lines:
            parts = [p.strip() for p in line.split('-' if '-' in line else ',')]
            if len(parts) >= 3:
                if await db.add_word_to_db(uid, parts[0], parts[1], parts[2]):
                    added += 1
        await message.answer(ul(uid, "import.done", count=added),
                             reply_markup=await get_main_kb(uid))
    except Exception as e:
        await message.answer(ul(uid, "import.error", error=str(e)))


@dp.message(Command("feedback"))
@dp.message(F.text.in_(["Відгук 💬", "Opinia 💬", "Feedback 💬"]))
async def cmd_feedback(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    await state.set_state(FeedbackState.waiting_for_message)
    await message.answer(ul(uid, "feedback.prompt"),
                         reply_markup=types.ReplyKeyboardRemove())


@dp.message(FeedbackState.waiting_for_message)
async def process_feedback(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    if message.text == '/exit': return await cmd_exit(message, state)
    await db.save_feedback(uid, message.from_user.username or "Unknown", message.text or "Медіа")
    await state.clear()
    await message.answer(ul(uid, "feedback.thanks"),
                         reply_markup=await get_main_kb(uid))


# ─────────────────────────────────────────
# СЛУЖБОВІ КОМАНДИ (режими роботи)
# ─────────────────────────────────────────
@dp.message(Command("set_mode"))
async def cmd_set_mode(message: types.Message):
    uid     = message.from_user.id
    current = ul(uid, "set_mode.gemini") if ai_manager.FORCE_GEMINI else ul(uid, "set_mode.ollama")
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=ul(uid, "set_mode.ollama"), callback_data="mode:ollama")],
        [types.InlineKeyboardButton(text=ul(uid, "set_mode.gemini"), callback_data="mode:gemini")],
    ])
    await message.answer(ul(uid, "set_mode.current", mode=current),
                         parse_mode="HTML", reply_markup=kb)


@dp.callback_query(F.data.startswith("mode:"))
async def callback_set_mode(callback: types.CallbackQuery):
    uid  = callback.from_user.id
    mode = callback.data.split(":")[1]
    ai_manager.set_ai_mode(force_gemini=(mode == "gemini"))
    label = ul(uid, "set_mode.gemini") if mode == "gemini" else ul(uid, "set_mode.ollama")
    await callback.answer(f"{label}", show_alert=True)
    await callback.message.edit_text(ul(uid, "set_mode.done", mode=label), parse_mode="HTML")


@dp.message(Command("set_db"))
async def cmd_set_db(message: types.Message):
    uid     = message.from_user.id
    current = ul(uid, "set_db.sqlite_label") if db.FORCE_SQLITE else ul(uid, "set_db.auto_label")
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=ul(uid, "set_db.auto"),   callback_data="db:auto")],
        [types.InlineKeyboardButton(text=ul(uid, "set_db.sqlite"), callback_data="db:sqlite")],
    ])
    await message.answer(ul(uid, "set_db.current", mode=current),
                         parse_mode="HTML", reply_markup=kb)


@dp.callback_query(F.data.startswith("db:"))
async def callback_set_db(callback: types.CallbackQuery):
    uid  = callback.from_user.id
    mode = callback.data.split(":")[1]
    db.set_db_mode(force_sqlite=(mode == "sqlite"))
    label = ul(uid, "set_db.sqlite_label") if mode == "sqlite" else ul(uid, "set_db.auto_label")
    await callback.answer(f"{label}", show_alert=True)
    await callback.message.edit_text(ul(uid, "set_db.done", mode=label), parse_mode="HTML")


# ─────────────────────────────────────────
# CALLBACK — РЕГЕНЕРАЦІЯ ФОТО
# ─────────────────────────────────────────
@dp.callback_query(F.data.startswith("regen:"))
async def callback_regenerate(callback: types.CallbackQuery):
    uid = callback.from_user.id
    try:
        word_prefix = callback.data.split(":")[1]
        new_url     = await ai_manager.get_image_url(word_prefix, use_random=True)
        if new_url:
            await db.db_execute(
                "UPDATE user_words SET image_url=$1 WHERE user_id=$2 AND word LIKE $3",
                new_url, uid, f"{word_prefix}%")
            await callback.message.edit_media(
                media=types.InputMediaPhoto(media=new_url, caption=callback.message.caption,
                                            parse_mode="HTML"),
                reply_markup=callback.message.reply_markup)
            await callback.answer(ul(uid, "btn.photo_updated"))
        else:
            await callback.answer(ul(uid, "btn.no_photo"), show_alert=True)
    except Exception as e:
        print(f"⚠️ Помилка регенерації: {e}")
        await callback.answer(ul(uid, "errors.regen_err"), show_alert=True)


# ─────────────────────────────────────────
# WEB APP — РЕЗУЛЬТАТИ ГРИ
# ─────────────────────────────────────────
@dp.message(F.content_type == types.ContentType.WEB_APP_DATA)
async def process_web_app_data(message: types.Message):
    uid  = message.from_user.id
    data = json.loads(message.web_app_data.data)
    if data.get('type') == 'game_result':
        score = data.get('score', 0)
        for w in data.get('learned_words', []):
            await db.update_word_progress(uid, w, True)
        await _update_level(uid)
        current_best = await db.get_best_score(uid)
        msg = ul(uid, "webapp.result", score=score, count=len(data.get('learned_words', [])))
        if score > current_best:
            await db.update_best_score(uid, score)
            msg += ul(uid, "webapp.new_record", old=current_best)
        await message.answer(msg, reply_markup=await get_main_kb(uid))


# ─────────────────────────────────────────
# ЗАПУСК
# ─────────────────────────────────────────
async def main():
    await db.init_connection()
    await db.init_db()
    print("Бот успішно запущено! 🌍 Multilang: uk / pl / en")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
