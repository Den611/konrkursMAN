import asyncio
import random
import json
import urllib.parse
import io
import html
import sys
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

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

SUPPORTED_LANGUAGES = ["English", "German", "French", "Polish", "Spanish", "Italian"]
COMMANDS_TEXT = (
    "Доступні команди:\n"
    "➕ /add_word – додати слово\n"
    "🎯 /practice – тренування пам'яті\n"
    "📊 /stats – твоя статистика\n"
    "🏆 /top – таблиця лідерів\n"
    "🌟 /word_of_day – слово дня\n"
    "🤖 /AI – пояснення від ШІ\n"
    "📝 /all_words – твій словник\n"
    "📥 /import_words – масове завантаження\n"
    "❌ /delete_word – видалити слово\n"
    "💬 /feedback – надіслати відгук\n"
    "❓ /help – інформація\n"
    "🚪 /exit – вихід"
)


# --- MIDDLEWARE ANTI-SPAM ---
class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, throttle_time: int = 1):
        self.cache = TTLCache(maxsize=10000, ttl=throttle_time)

    async def __call__(self, handler: Callable[[types.Message, Dict[str, Any]], Awaitable[Any]], event: types.Message,
                       data: Dict[str, Any]) -> Any:
        user_id = event.from_user.id
        if user_id in self.cache: return
        self.cache[user_id] = True
        return await handler(event, data)


dp.message.middleware(ThrottlingMiddleware(1))


# --- СТАНИ FSM ---
class Registration(StatesGroup):
    waiting_for_learning_style = State()
    waiting_for_hobby = State()


class AddWord(StatesGroup):
    waiting_for_word = State()
    waiting_for_language = State()
    waiting_for_translation = State()


class DeleteWord(StatesGroup): waiting_for_word = State()


class PracticeWord(StatesGroup): waiting_for_language = State(); waiting_for_answer = State()


class ViewWords(StatesGroup): waiting_for_language = State()


class AIHelper(StatesGroup): waiting_for_prompt = State(); waiting_for_language = State()


class WordOfDayState(StatesGroup): waiting_for_language = State(); waiting_for_action = State()


class FeedbackState(StatesGroup): waiting_for_message = State()


async def get_main_kb(user_id):
    words_raw = await db.get_user_words(user_id)
    game_words = [{"w": w['word'], "t": w['translation']} for w in sorted(words_raw, key=lambda x: x['usage_count'])[:50]] if words_raw else []
    game_url = f"{WEB_APP_URL}?data={urllib.parse.quote(json.dumps(game_words))}" if game_words else WEB_APP_URL

    return types.ReplyKeyboardMarkup(keyboard=[
        [types.KeyboardButton(text="🎮 Грати в слова (Web App)", web_app=types.WebAppInfo(url=game_url))],
        [types.KeyboardButton(text="/add_word"), types.KeyboardButton(text="/practice")],
        [types.KeyboardButton(text="/all_words"), types.KeyboardButton(text="/stats")],
        [types.KeyboardButton(text="/word_of_day"), types.KeyboardButton(text="🏆 ТОП Лідери")],
        [types.KeyboardButton(text="/import_words"), types.KeyboardButton(text="🤖 /AI")],
        [types.KeyboardButton(text="Відгук 💬"), types.KeyboardButton(text="Допомога ❓")], # Не забуваємо кому тут
        [types.KeyboardButton(text="/exit")] # Новий рядок з кнопкою виходу
    ], resize_keyboard=True)


async def get_user_level_info(user_id):
    words = await db.get_user_words(user_id)
    total_xp = sum([w['usage_count'] for w in words])
    level, xp_needed = 1, 10
    while total_xp >= xp_needed:
        total_xp -= xp_needed
        level += 1
        xp_needed += 10
    return level, total_xp, xp_needed


# --- СТАРТ ТА ПСИХОЛОГІЧНИЙ ТЕСТ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await db.add_user(message.from_user.id, message.from_user.username or "Учень")
    profile = await db.get_user_profile_data(message.from_user.id)

    if not profile or not profile['hobbies'] or profile['hobbies'] == "повсякденне життя":
        welcome_text = (
            "🚀 <b>Вітаю у WordBot — твоєму персональному ШІ-репетиторі!</b>\n\n"
            "Я не просто зберігаю слова. Я аналізую твої інтереси, створюю веселі асоціації "
            "і використовую науковий алгоритм інтервального повторення, щоб знання залишалися назавжди.\n\n"
            "🧠 <b>Але спочатку давай налаштуємо програму під тебе. Пройдемо міні-тест!</b>\n\n"
            "<i>Питання 1: Як ти найкраще сприймаєш нову інформацію?</i>"
        )
        test_kb = types.ReplyKeyboardMarkup(keyboard=[
            [types.KeyboardButton(text="👁 Візуально (Схеми, картинки)")],
            [types.KeyboardButton(text="👂 На слух (Подкасти, лекції)")],
            [types.KeyboardButton(text="⚙️ Логічно (Правила, таблиці)")],
            [types.KeyboardButton(text="🖐 На практиці (Вправи, ігри)")]
        ], resize_keyboard=True, one_time_keyboard=True)

        await state.set_state(Registration.waiting_for_learning_style)
        await message.answer(welcome_text, parse_mode="HTML", reply_markup=test_kb)
    else:
        await db.update_last_active(message.from_user.id)
        await state.clear()
        await message.answer("👋 З поверненням! Продовжимо навчання?",
                             reply_markup=await get_main_kb(message.from_user.id))


@dp.message(Registration.waiting_for_learning_style)
async def process_learning_style(message: types.Message, state: FSMContext):
    style = message.text.split()[1] if len(message.text.split()) > 1 else "Універсал"
    await state.update_data(style=style)
    await state.set_state(Registration.waiting_for_hobby)
    await message.answer(
        "🎯 Чудово! <i>Питання 2: Чим ти захоплюєшся у вільний час?</i>\nНапиши 1-2 своїх головних хобі (наприклад: відеоігри, спорт, програмування, музика):",
        reply_markup=types.ReplyKeyboardRemove())


@dp.message(Registration.waiting_for_hobby)
async def process_hobby(message: types.Message, state: FSMContext):
    data = await state.get_data()
    style = data.get("style", "Універсал")
    hobby = message.text.strip()

    await db.update_user_profile(message.from_user.id, style, hobby)
    await state.clear()

    finish_text = (
        "✅ <b>Твій профіль створено!</b>\n\n"
        f"Тип сприйняття: <b>{style}</b>\nТвої інтереси: <b>{hobby}</b>\n\n"
        "Тепер я буду генерувати асоціації спеціально під твої захоплення. "
        "Натисни /help, щоб дізнатися, що я вмію!"
    )
    await message.answer(finish_text, parse_mode="HTML", reply_markup=await get_main_kb(message.from_user.id))


# --- ДОПОМОГА, СТАТИСТИКА ТА ТОП ---
@dp.message(Command("help"))
@dp.message(F.text == "Допомога ❓")
async def cmd_help(message: types.Message):
    help_text = (
        "📚 <b>Як працює твій розумний словник?</b>\n\n"
        "➕ <b>/add_word</b> — Додай слово. ШІ сам знайде картинку та створить асоціацію.\n"
        "🎯 <b>/practice</b> — Тренування. Алгоритм SuperMemo-2 вираховує, коли ти починаєш забувати слово.\n"
        "🌟 <b>/word_of_day</b> — ШІ генерує для тебе випадкове корисне слово твого рівня.\n"
        "🤖 <b>/AI</b> — Введи незрозуміле слово, і я поясню його значення на прикладах із твоїх хобі!\n"
        "🎮 <b>Грати в слова</b> — Відкриє міні-гру прямо в Telegram. Заробляй бали!\n"
        "🏆 <b>ТОП Лідери</b> — Перевір, на якому ти місці серед усіх учнів."
    )
    await message.answer(help_text, parse_mode="HTML", reply_markup=await get_main_kb(message.from_user.id))


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    words = await db.get_user_words(message.from_user.id)
    lvl, cur_xp, next_xp = await get_user_level_info(message.from_user.id)
    bar = "🟩" * int((cur_xp / max(1, next_xp)) * 10) + "⬜" * (10 - int((cur_xp / max(1, next_xp)) * 10))
    best = await db.get_best_score(message.from_user.id)
    profile = await db.get_user_profile_data(message.from_user.id)

    style = profile['learning_style'] if profile and profile['learning_style'] else "Не визначено"
    hobby = profile['hobbies'] if profile and profile['hobbies'] else "Не вказано"

    stats = (
        f"📊 <b>Твій Профіль</b>\n"
        f"👤 Тип учня: <b>{style}</b>\n"
        f"🎯 Хобі: <b>{hobby}</b>\n\n"
        f"🏆 Рівень: <b>{lvl}</b>\n"
        f"⭐ Досвід: {cur_xp}/{next_xp}\n"
        f"[{bar}]\n\n"
        f"📚 Збережено слів: {len(words)}\n"
        f"🎮 Рекорд у міні-грі: <b>{best}</b> балів"
    )
    await message.answer(stats, parse_mode="HTML", reply_markup=await get_main_kb(message.from_user.id))


@dp.message(Command("top"))
@dp.message(F.text == "🏆 ТОП Лідери")
async def cmd_top(message: types.Message):
    top_users = await db.get_top_users(10)
    if not top_users:
        return await message.answer("Поки що ніхто не грав у міні-гру. Будь першим!",
                                    reply_markup=await get_main_kb(message.from_user.id))

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    top_text = "🏆 <b>ТАБЛИЦЯ ЛІДЕРІВ (Міні-гра)</b> 🏆\n\n"

    for i, user in enumerate(top_users):
        name = user['username'] if user['username'] else "Таємний учень"
        top_text += f"{medals[i]} <b>{name}</b> — {user['best_score']} балів\n"

    top_text += "\n<i>Грай у Web App, щоб піднятися в рейтингу!</i>"
    await message.answer(top_text, parse_mode="HTML", reply_markup=await get_main_kb(message.from_user.id))


@dp.message(Command("exit"))
async def cmd_exit(message: types.Message, state: FSMContext):
    await db.update_last_active(message.from_user.id)
    await state.clear()
    await message.answer(f"🚪 Ви вийшли з поточного режиму.\n\n{COMMANDS_TEXT}",
                         reply_markup=await get_main_kb(message.from_user.id))


# --- ДОДАВАННЯ СЛОВА ---
@dp.message(Command("add_word"))
async def cmd_add_word(message: types.Message, state: FSMContext):
    await db.update_last_active(message.from_user.id)
    await state.set_state(AddWord.waiting_for_word)
    await message.answer("✏️ Введіть слово для додавання:", reply_markup=await get_main_kb(message.from_user.id))


@dp.message(AddWord.waiting_for_word)
async def process_word(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == '/exit': return await cmd_exit(message, state)
    await state.update_data(word=text)
    lang_kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=l)] for l in SUPPORTED_LANGUAGES] + [[types.KeyboardButton(text="/exit")]],
        resize_keyboard=True, one_time_keyboard=True)
    await state.set_state(AddWord.waiting_for_language)
    await message.answer("🌍 Оберіть мову:", reply_markup=lang_kb)


@dp.message(AddWord.waiting_for_language)
async def process_language(message: types.Message, state: FSMContext):
    if message.text == '/exit': return await cmd_exit(message, state)
    await state.update_data(language=message.text.strip())
    word = (await state.get_data())['word']
    try:
        auto_trans = GoogleTranslator(source='auto', target='uk').translate(word)
    except:
        auto_trans = "Помилка"

    await state.update_data(auto_translation=auto_trans)
    trans_kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=f"Зберегти: {auto_trans}")], [types.KeyboardButton(text="/exit")]],
        resize_keyboard=True, one_time_keyboard=True)
    await state.set_state(AddWord.waiting_for_translation)

    safe_trans = html.escape(auto_trans)
    await message.answer(f"🔍 Автопереклад: <b>{safe_trans}</b>\nНатисніть кнопку або напишіть свій:",
                         reply_markup=trans_kb, parse_mode="HTML")


@dp.message(AddWord.waiting_for_translation)
async def process_custom_translation(message: types.Message, state: FSMContext):
    if message.text == '/exit': return await cmd_exit(message, state)
    data = await state.get_data()
    final_translation = data['auto_translation'] if message.text.startswith("Зберегти:") else message.text.strip()

    await message.answer("⏳ Зберігаю, шукаю картинку та асоціацію...")
    transc, assoc, visual = await ai_manager.get_full_word_info(data['word'], final_translation, data['language'])

    # Каскадний пошук фото
    img = await ai_manager.get_image_url(visual)
    if not img: img = await ai_manager.get_image_url(data['word'])
    if not img: img = await ai_manager.get_image_url(final_translation)

    added = await db.add_word_to_db(message.from_user.id, data['word'], final_translation, data['language'], img, assoc,
                                    transc)

    safe_word_html = html.escape(data['word'])
    safe_trans_html = html.escape(final_translation)
    safe_assoc = html.escape(assoc)
    safe_transc = html.escape(transc)

    text = f"✅ <b>Додано:</b> {safe_word_html} {safe_transc} — {safe_trans_html}\n🧠 <i>{safe_assoc}</i>" if added else f"⚠️ Слово вже є у словнику."

    safe_word_btn = data['word'][:20]
    inline_kb = types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="🔄 Інше фото", callback_data=f"regen:{safe_word_btn}")]])

    try:
        if img and added:
            await message.answer_photo(img, caption=text, reply_markup=inline_kb, parse_mode="HTML")
        else:
            await message.answer(text, reply_markup=inline_kb if added else None, parse_mode="HTML")
    except Exception as e:
        print(f"⚠️ Помилка форматування: {e}")
        clean_text = f"✅ Додано: {data['word']} {transc} — {final_translation}\n🧠 {assoc}"
        if img and added:
            await message.answer_photo(img, caption=clean_text, reply_markup=inline_kb)
        else:
            await message.answer(clean_text, reply_markup=inline_kb if added else None)

    await message.answer("👇 Продовжити:", reply_markup=await get_main_kb(message.from_user.id))
    await state.set_state(AddWord.waiting_for_word)


# --- СЛОВО ДНЯ ---
@dp.message(Command("word_of_day"))
async def cmd_word_of_day(message: types.Message, state: FSMContext):
    lang_kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=l)] for l in SUPPORTED_LANGUAGES] + [[types.KeyboardButton(text="/exit")]],
        resize_keyboard=True, one_time_keyboard=True)
    await state.set_state(WordOfDayState.waiting_for_language)
    await message.answer("🌟 Оберіть мову для нового слова:", reply_markup=lang_kb)


@dp.message(WordOfDayState.waiting_for_language)
async def process_wod_lang(message: types.Message, state: FSMContext):
    lang = message.text.strip()
    if lang == '/exit': return await cmd_exit(message, state)
    await message.answer(f"⏳ Генерую слово ({lang})...")

    lvl, _, _ = await get_user_level_info(message.from_user.id)
    diff = "A1" if lvl <= 3 else "B1" if lvl <= 8 else "C1"

    prompt = (
        f"Generate exactly ONE word in {lang} for level {diff} with Ukrainian translation. "
        f"Do not write lists. Do not write 'Word - Translation'. "
        f"Format strictly: Apple - Яблуко"
    )
    result = await ai_manager.generate_content_safe(prompt)

    w, t = None, None
    for line in result.split('\n'):
        line = line.strip().replace("*", "")
        if " - " in line and "Слово" not in line and "Word" not in line:
            parts = line.split(" - ", 1)
            w, t = parts[0].strip(), parts[1].strip()
            break

    if w and t:
        transc, assoc, visual = await ai_manager.get_full_word_info(w, t, lang)

        img = await ai_manager.get_image_url(visual)
        if not img: img = await ai_manager.get_image_url(w)
        if not img: img = await ai_manager.get_image_url(t)

        await state.update_data(new_word=w, translation=t, lang=lang, image_url=img, association=assoc,
                                transcription=transc)

        safe_w = html.escape(w)
        safe_t = html.escape(t)
        safe_transc = html.escape(transc)

        msg_text = f"🌟 Слово дня: <b>{safe_w}</b> {safe_transc}\n🇺🇦 Переклад: {safe_t}"

        safe_word_btn = w[:20]
        inline = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="🔄 Інше фото", callback_data=f"regen:{safe_word_btn}")]])
        wod_kb = types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="➕ Додати це слово")], [types.KeyboardButton(text="🚪 Вихід")]],
            resize_keyboard=True)

        try:
            if img:
                await message.answer_photo(img, caption=msg_text, reply_markup=inline, parse_mode="HTML")
            else:
                await message.answer(msg_text, reply_markup=inline, parse_mode="HTML")
        except Exception as e:
            print(f"⚠️ Помилка Telegram: {e}")
            clean_text = f"🌟 Слово дня: {w} {transc}\n🇺🇦 Переклад: {t}"
            if img:
                await message.answer_photo(img, caption=clean_text, reply_markup=inline)
            else:
                await message.answer(clean_text, reply_markup=inline)

        await message.answer("Дії:", reply_markup=wod_kb)
        await state.set_state(WordOfDayState.waiting_for_action)
    else:
        await message.answer(f"⚠️ ШІ трохи заплутався і видав це:\n{result}\n\nСпробуй ще раз!",
                             reply_markup=await get_main_kb(message.from_user.id))
        await state.clear()


@dp.message(WordOfDayState.waiting_for_action)
async def process_wod_action(message: types.Message, state: FSMContext):
    if message.text == "🚪 Вихід": return await cmd_exit(message, state)
    if message.text == "➕ Додати це слово":
        data = await state.get_data()
        added = await db.add_word_to_db(message.from_user.id, data['new_word'], data['translation'], data['lang'],
                                        data['image_url'], data['association'], data['transcription'])
        await message.answer(f"✅ Додано!\n🧠 {data['association']}" if added else "⚠️ Вже є.",
                             reply_markup=await get_main_kb(message.from_user.id))
        await state.clear()


# --- ПРАКТИКА (Spaced Repetition) ---
@dp.message(Command("practice"))
async def cmd_practice(message: types.Message, state: FSMContext):
    words = await db.get_user_words(message.from_user.id, for_review=True)
    if not words: return await message.answer("🎉 На сьогодні всі слова повторені!",
                                              reply_markup=await get_main_kb(message.from_user.id))

    await state.update_data(all_practice_words=[dict(w) for w in words])
    lang_kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=l)] for l in set([w['language'] for w in words])] + [
            [types.KeyboardButton(text="Усі мови")], [types.KeyboardButton(text="/exit")]], resize_keyboard=True,
        one_time_keyboard=True)
    await state.set_state(PracticeWord.waiting_for_language)
    await message.answer(f"🎯 Слів на сьогодні: {len(words)}\nОберіть мову:", reply_markup=lang_kb)


@dp.message(PracticeWord.waiting_for_language)
async def practice_choose_lang(message: types.Message, state: FSMContext):
    if message.text == '/exit': return await cmd_exit(message, state)
    target = (await state.get_data())['all_practice_words']
    if message.text != "Усі мови": target = [w for w in target if w['language'] == message.text]

    if not target: return await message.answer("Пусто.")
    random.shuffle(target)
    await state.update_data(plist=target[:10], pidx=0)
    await state.set_state(PracticeWord.waiting_for_answer)
    w = target[0]
    q = f"✏️ Перекладіть: <b>{html.escape(w['translation'])}</b> ({w['language']})"
    if w['image_url']:
        await message.answer_photo(w['image_url'], caption=q, parse_mode="HTML")
    else:
        await message.answer(q, parse_mode="HTML")


@dp.message(PracticeWord.waiting_for_answer)
async def process_practice_ans(message: types.Message, state: FSMContext):
    if message.text == "/exit": return await cmd_exit(message, state)
    data = await state.get_data()
    w = data['plist'][data['pidx']]

    is_correct = message.text.strip().lower() == w['word'].lower()
    await db.update_word_progress(message.from_user.id, w['word'], is_correct)

    if is_correct:
        await message.answer(f"✅ Правильно! {w['word']}")
    else:
        await message.answer(f"❌ Ні. Правильно: {w['word']} {w['transcription'] or ''}\n💡 {w['association'] or ''}")

    data['pidx'] += 1
    if data['pidx'] >= len(data['plist']):
        await message.answer("🏁 Тренування завершено!", reply_markup=await get_main_kb(message.from_user.id))
        await state.clear()
    else:
        await state.update_data(pidx=data['pidx'])
        nw = data['plist'][data['pidx']]
        q = f"✏️ Перекладіть: <b>{html.escape(nw['translation'])}</b> ({nw['language']})"
        if nw['image_url']:
            await message.answer_photo(nw['image_url'], caption=q, parse_mode="HTML")
        else:
            await message.answer(q, parse_mode="HTML")


# --- AI ПОЯСНЕННЯ ---
@dp.message(Command("AI"))
@dp.message(F.text == "🤖 /AI")
async def cmd_ai(message: types.Message, state: FSMContext):
    await state.set_state(AIHelper.waiting_for_prompt)
    await message.answer("🤖 Введіть слово для пояснення (або /exit):",
                         reply_markup=await get_main_kb(message.from_user.id))


@dp.message(AIHelper.waiting_for_prompt)
async def process_ai_prompt(message: types.Message, state: FSMContext):
    if message.text == '/exit': return await cmd_exit(message, state)
    await state.update_data(prompt=message.text.strip())
    lang_kb = types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=l)] for l in SUPPORTED_LANGUAGES + ["Українська"]] + [
            [types.KeyboardButton(text="/exit")]], resize_keyboard=True, one_time_keyboard=True)
    await state.set_state(AIHelper.waiting_for_language)
    await message.answer("🌍 Мова слова?", reply_markup=lang_kb)


@dp.message(AIHelper.waiting_for_language)
async def process_ai_language(message: types.Message, state: FSMContext):
    if message.text == '/exit': return await cmd_exit(message, state)
    data = await state.get_data()
    hobby = await db.get_user_hobby(message.from_user.id)
    if not hobby: hobby = "повсякденне життя"
    await message.answer("⏳ 🤖 ШІ аналізує слово та підбирає приклади...")

    txt = "Помилка генерації"
    try:
        txt, img = await asyncio.gather(ai_manager.get_ai_explanation_text(data['prompt'], message.text.strip(), hobby),
                                        ai_manager.get_image_url(data['prompt']))

        safe_word = data['prompt'][:20]
        inline = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="🔄 Інше фото", callback_data=f"regen:{safe_word}")]])

        if img:
            await message.answer_photo(img, caption=f"🤖 <b>Ось пояснення:</b>\n\n{txt}"[:1024], reply_markup=inline,
                                       parse_mode="HTML")
        else:
            await message.answer(f"🤖 <b>Ось пояснення:</b>\n\n{txt}", reply_markup=inline, parse_mode="HTML")

    except Exception as e:
        print(f"⚠️ Помилка виводу AI: {e}")
        await message.answer(f"🤖 Пояснення:\n\n{txt}", parse_mode=None)

    await state.set_state(AIHelper.waiting_for_prompt)
    await message.answer("🤖 Ще одне слово? (або /exit)")


# --- ІНШЕ (Видалення, Список, Callback, WebApp) ---
@dp.message(Command("delete_word"))
async def cmd_delete_word(message: types.Message, state: FSMContext):
    await state.set_state(DeleteWord.waiting_for_word)
    await message.answer("🗑️ Введіть слово для видалення:")


@dp.message(DeleteWord.waiting_for_word)
async def process_delete_word(message: types.Message, state: FSMContext):
    if message.text == '/exit': return await cmd_exit(message, state)
    await db.delete_word_from_db(message.from_user.id, message.text.strip())
    await message.answer("🗑️ Виконано.", reply_markup=await get_main_kb(message.from_user.id))
    await state.clear()


@dp.message(Command("all_words"))
async def cmd_all_words(message: types.Message):
    words = await db.get_user_words(message.from_user.id)
    if not words: return await message.answer("📭 Ваш словник порожній.")
    text = "📝 Слова:\n" + "\n".join([f"{w['word']} — {w['translation']}" for w in words])
    await message.answer(text[:4000])


@dp.message(Command("import_words"))
async def cmd_import_words(message: types.Message):
    await message.answer("📥 Надішліть файл .txt або .csv. Формат: слово - переклад - мова")


@dp.message(F.document)
async def process_document(message: types.Message):
    if not message.document.file_name.endswith(('.csv', '.txt')): return
    await message.answer("⏳ Обробляю словник...")
    try:
        file_in_io = io.BytesIO()
        await message.bot.download(message.document, destination=file_in_io)
        lines = file_in_io.getvalue().decode('utf-8').splitlines()
        added = 0
        for line in lines:
            parts = [p.strip() for p in line.split('-' if '-' in line else ',')]
            if len(parts) >= 3:
                if await db.add_word_to_db(message.from_user.id, parts[0], parts[1], parts[2]): added += 1
        await message.answer(f"✅ Імпортовано {added} слів!", reply_markup=await get_main_kb(message.from_user.id))
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")


@dp.message(Command("feedback"))
@dp.message(F.text == "Відгук 💬")
async def cmd_feedback(message: types.Message, state: FSMContext):
    await state.set_state(FeedbackState.waiting_for_message)
    await message.answer("💬 Напишіть ваш відгук або ідею (або /exit):", reply_markup=types.ReplyKeyboardRemove())


@dp.message(FeedbackState.waiting_for_message)
async def process_feedback(message: types.Message, state: FSMContext):
    if message.text == '/exit': return await cmd_exit(message, state)
    await db.save_feedback(message.from_user.id, message.from_user.username or "Unknown", message.text or "Медіа")
    await state.clear()
    await message.answer("✅ Дякуємо за відгук!", reply_markup=await get_main_kb(message.from_user.id))


@dp.callback_query(F.data.startswith("regen:"))
async def callback_regenerate(callback: types.CallbackQuery):
    try:
        word_prefix = callback.data.split(":")[1]
        new_url = await ai_manager.get_image_url(word_prefix, use_random=True)
        if new_url:
            await db.db_execute("UPDATE user_words SET image_url=$1 WHERE user_id=$2 AND word LIKE $3", new_url,
                                callback.from_user.id, f"{word_prefix}%")
            await callback.message.edit_media(
                media=types.InputMediaPhoto(media=new_url, caption=callback.message.caption, parse_mode="HTML"),
                reply_markup=callback.message.reply_markup)
            await callback.answer("Фото оновлено!")
        else:
            await callback.answer("Не знайдено іншого фото", show_alert=True)
    except Exception as e:
        print(f"⚠️ Помилка регенерації: {e}")
        await callback.answer("Помилка", show_alert=True)


@dp.message(F.content_type == types.ContentType.WEB_APP_DATA)
async def process_web_app_data(message: types.Message):
    data = json.loads(message.web_app_data.data)
    if data.get('type') == 'game_result':
        score = data.get('score', 0)
        for w in data.get('learned_words', []): await db.update_word_progress(message.from_user.id, w, True)

        current_best = await db.get_best_score(message.from_user.id)
        msg = f"🎮 Результат: {score} балів!\n📚 Слів повторено: {len(data.get('learned_words', []))}"
        if score > current_best:
            await db.update_best_score(message.from_user.id, score)
            msg += f"\n🏆 Новий рекорд! (Було: {current_best})"
        await message.answer(msg, reply_markup=await get_main_kb(message.from_user.id))


# --- ГОЛОВНЕ МЕНЮ ЗАПУСКУ ---
async def main():

    await db.init_connection()
    await db.init_db()
    print("Бот успішно запущено !")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())