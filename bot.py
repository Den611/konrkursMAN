import asyncio
import sqlite3
import json
import urllib.parse
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, Router, types, BaseMiddleware, F
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from deep_translator import GoogleTranslator
import random
import google.genai as genai
from google.genai import types as genai_types
from cachetools import TTLCache
from typing import Any, Awaitable, Callable, Dict
import aiohttp
from aiohttp import web
import os
import io
from dotenv import load_dotenv


# --- КОНФІГУРАЦІЯ ---
def load_config_from_env(env_file=".env"):
    load_dotenv(dotenv_path=env_file)
    config = {}
    config["TELEGRAM_BOT_TOKEN"] = os.getenv("TELEGRAM_BOT_TOKEN", "")
    config["PIXABAY_API_KEY"] = os.getenv("PIXABAY_API_KEY", "")
    config["WEB_APP_URL"] = os.getenv("WEB_APP_URL", "")

    gemini_keys_str = os.getenv("GEMINI_API_KEYS")
    if gemini_keys_str:
        config["GEMINI_API_KEYS"] = [key.strip() for key in gemini_keys_str.split(',') if key.strip()]
    else:
        config["GEMINI_API_KEYS"] = []
    return config


config = load_config_from_env()
TELEGRAM_BOT_TOKEN = config["TELEGRAM_BOT_TOKEN"]
PIXABAY_API_KEY = config["PIXABAY_API_KEY"]
WEB_APP_URL = config["WEB_APP_URL"]
GEMINI_API_KEYS = config["GEMINI_API_KEYS"]

print("✅ Конфігурація успішно завантажена")

router = Router()

# --- БАЗА ДАНИХ ---
conn = sqlite3.connect("words.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    start_date TEXT,
    last_active TEXT,
    best_score INTEGER DEFAULT 0,
    level TEXT DEFAULT 'A1',
    hobbies TEXT,
    streak_days INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS user_words (
    user_id INTEGER,
    word TEXT,
    translation TEXT,
    language TEXT,
    usage_count INTEGER DEFAULT 0,
    image_url TEXT,
    association TEXT,
    transcription TEXT,
    next_review_date TEXT,
    interval INTEGER DEFAULT 1,
    ease_factor REAL DEFAULT 2.5,
    grammar_info TEXT,
    PRIMARY KEY(user_id, word, language)
)
""")

# Таблиця для відгуків
cursor.execute("""
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    message TEXT,
    date TEXT,
    status TEXT DEFAULT 'Новий'
)
""")
conn.commit()


def migrate_db():
    cols_words = [
        ("image_url", "TEXT"), ("association", "TEXT"), ("transcription", "TEXT"),
        ("next_review_date", "TEXT"), ("interval", "INTEGER DEFAULT 1"),
        ("ease_factor", "REAL DEFAULT 2.5"), ("grammar_info", "TEXT")
    ]
    for col_name, col_type in cols_words:
        try:
            cursor.execute(f"ALTER TABLE user_words ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass

    cols_users = [
        ("best_score", "INTEGER DEFAULT 0"), ("level", "TEXT DEFAULT 'A1'"),
        ("hobbies", "TEXT"), ("streak_days", "INTEGER DEFAULT 0")
    ]
    for col_name, col_type in cols_users:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            pass
    conn.commit()


migrate_db()


# --- РОБОТА З ШІ (GEMINI) ТА API ---
class KeyManager:
    def __init__(self, keys):
        self.keys = keys
        self.current_index = 0
        self.client = self._init_client()

    def _init_client(self):
        if not self.keys or not self.keys[0]:
            return None
        return genai.Client(api_key=self.keys[self.current_index])

    def get_client(self):
        return self.client

    def rotate_key(self):
        self.current_index = (self.current_index + 1) % len(self.keys)
        self.client = self._init_client()


key_manager = KeyManager(GEMINI_API_KEYS)


def generate_content_safe(contents, config=None, model="gemini-2.5-flash"):
    attempts = 0
    max_attempts = len(GEMINI_API_KEYS) + 1
    while attempts < max_attempts:
        try:
            client = key_manager.get_client()
            if not client: raise Exception("API ключі не налаштовані")
            return client.models.generate_content(model=model, config=config, contents=contents)
        except Exception as e:
            error_msg = str(e).lower()
            if "429" in error_msg or "quota" in error_msg or "exhausted" in error_msg:
                key_manager.rotate_key()
                attempts += 1
            else:
                raise e
    raise Exception("❌ Всі API ключі вичерпано.")


async def health_check(request):
    return web.Response(text="Bot is running.")


async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()


async def keep_alive_task():
    while True:
        await asyncio.sleep(40)
        pass


async def get_image_url(query, use_random=False):
    if not query or not PIXABAY_API_KEY:
        return None
    try:
        per_page = 20 if use_random else 3
        encoded_query = urllib.parse.quote(query)
        url = f"https://pixabay.com/api/?key={PIXABAY_API_KEY}&q={encoded_query}&image_type=photo&orientation=horizontal&safesearch=true&per_page={per_page}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data['hits']:
                        if use_random:
                            return random.choice(data['hits'])['webformatURL']
                        else:
                            return data['hits'][0]['webformatURL']
    except Exception as e:
        print(f"Pixabay Error: {e}")
    return None


async def get_full_word_info(word, translation, lang):
    prompt = (
        f"Analyze the word '{word}' (language: {lang}, translation: '{translation}'). "
        f"Return ONLY a string in this format: "
        f"TRANSCRIPTION|ASSOCIATION|VISUAL_SEARCH_PROMPT\n"
        f"1. Transcription: Ukrainian letters inside brackets (e.g. [хелоу]).\n"
        f"2. Association: A short funny mnemonic sentence in Ukrainian to remember the word.\n"
        f"3. Visual Search Prompt: A short 3-5 word English phrase describing a photograph depicting the association, strictly without any text, signs, or words in the image. Focus on objects, nature, or actions.\n"
    )
    try:
        response = await asyncio.to_thread(generate_content_safe, contents=prompt)
        text = response.text.strip().replace("*", "")
        parts = text.split("|")
        if len(parts) >= 3:
            return parts[0].strip(), parts[1].strip(), parts[2].strip()
        elif len(parts) == 2:
            return parts[0].strip(), parts[1].strip(), word
        return "[?]", text, word
    except Exception:
        return "[?]", None, word


async def get_ai_explanation_text(content, language_of_word, user_id):
    cursor.execute("SELECT hobbies FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    hobby = res[0] if res and res[0] else "повсякденне життя"

    system_prompt = (
        f"Ти — вчитель іноземних мов. "
        f"Поясни слово '{content}' (мова: {language_of_word}). "
        f"Дуже важливо: користувач цікавиться темою '{hobby}'. "
        "Поясни слово і наведи приклад речення ТІЛЬКИ через призму цього інтересу! "
        "Структура відповіді:\n"
        "1. Слово - [Транскрипція українськими літерами] - Переклад\n"
        "2. Коротке значення.\n"
        "3. Один приклад речення з перекладом.\n"
        "Без Markdown."
    )
    config = genai_types.GenerateContentConfig(system_instruction=system_prompt)
    response = await asyncio.to_thread(generate_content_safe, contents=content, config=config)
    return response.text.replace("*", "")


# --- ФУНКЦІЇ БАЗИ ДАНИХ ---
def add_word_to_db(user_id, word, translation, language, image_url=None, association=None, transcription=None):
    try:
        cursor.execute("SELECT 1 FROM user_words WHERE user_id=? AND word=? AND language=?", (user_id, word, language))
        if cursor.fetchone():
            if image_url:
                cursor.execute(
                    "UPDATE user_words SET image_url=?, association=?, transcription=? WHERE user_id=? AND word=? AND language=?",
                    (image_url, association, transcription, user_id, word, language))
                conn.commit()
            return False

        next_date = (datetime.now() + timedelta(days=1)).isoformat()
        cursor.execute(
            "INSERT INTO user_words (user_id, word, translation, language, usage_count, image_url, association, transcription, next_review_date, interval, ease_factor) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, 1, 2.5)",
            (user_id, word, translation, language, image_url, association, transcription, next_date)
        )
        conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"Database error in add_word_to_db: {e}")
        return False


def update_word_progress(user_id, word, is_correct):
    try:
        cursor.execute("SELECT interval, ease_factor FROM user_words WHERE user_id=? AND word=?", (user_id, word))
        res = cursor.fetchone()
        if not res: return

        interval, ease_factor = res[0] if res[0] else 1, res[1] if res[1] else 2.5

        if is_correct:
            if interval == 0:
                interval = 1
            elif interval == 1:
                interval = 6
            else:
                interval = int(interval * ease_factor)
            ease_factor = round(ease_factor + 0.1, 2)
        else:
            interval = 1
            ease_factor = max(1.3, round(ease_factor - 0.2, 2))

        next_date = (datetime.now() + timedelta(days=interval)).isoformat()
        cursor.execute("""
            UPDATE user_words 
            SET usage_count = usage_count + ?, interval = ?, ease_factor = ?, next_review_date = ?
            WHERE user_id=? AND word=?
        """, (1 if is_correct else 0, interval, ease_factor, next_date, user_id, word))
        conn.commit()
    except Exception as e:
        print(e)


def get_user_words(user_id, language=None, for_review=False):
    query = "SELECT word, translation, language, usage_count, image_url, association, transcription FROM user_words WHERE user_id=?"
    params = [user_id]
    if language is not None and language != "Усі мови":
        query += " AND language=?"
        params.append(language)
    if for_review:
        query += " AND (next_review_date IS NULL OR next_review_date <= ?)"
        params.append(datetime.now().isoformat())

    cursor.execute(query, tuple(params))
    return cursor.fetchall()


def get_user_level_info(user_id):
    words = get_user_words(user_id)
    total_xp = sum([w[3] for w in words])
    level = 1
    xp_needed = 10
    while total_xp >= xp_needed:
        total_xp -= xp_needed
        level += 1
        xp_needed += 10
    return level, total_xp, xp_needed


def add_user(user_id, username):
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, start_date, last_active) VALUES (?, ?, ?, ?)",
                   (user_id, username, datetime.now().isoformat(), datetime.now().isoformat()))
    conn.commit()


def update_last_active(user_id):
    cursor.execute("UPDATE users SET last_active=? WHERE user_id=?", (datetime.now().isoformat(), user_id))
    conn.commit()


def delete_word_from_db(user_id, word):
    cursor.execute("DELETE FROM user_words WHERE user_id=? AND word=?", (user_id, word))
    conn.commit()


# --- КЛАВІАТУРА ТА ТЕКСТИ ---
SUPPORTED_LANGUAGES = ["English", "German", "French", "Polish", "Spanish", "Italian"]

COMMANDS_TEXT = (
    "Доступні команди:\n"
    "➕ /add_word – додати нове слово\n"
    "📥 /import_words – масово завантажити словник\n"
    "❌ /delete_word – видалити слово\n"
    "📝 /all_words – список усіх слів\n"
    "🎯 /practice – тренування (Spaced Repetition)\n"
    "📊 /stats – ваша статистика\n"
    "🌟 /word_of_day – розумне слово дня\n"
    "🤖 /AI – допомога ШІ у поясненнях\n"
    "💬 /feedback – надіслати відгук чи ідею\n"
    "❓ /help – інформація про алгоритми\n"
    "🚪 /exit – вихід з поточного режиму"
)


def get_main_kb(user_id):
    words_raw = get_user_words(user_id)
    game_words = []

    if words_raw:
        words_raw.sort(key=lambda x: x[3])
        sample = words_raw[:50]
        for w in sample:
            game_words.append({"w": w[0], "t": w[1]})

    if game_words:
        json_data = json.dumps(game_words)
        encoded_data = urllib.parse.quote(json_data)
        game_url = f"{WEB_APP_URL}?data={encoded_data}"
    else:
        game_url = WEB_APP_URL

    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🎮 Грати в слова (Web App)", web_app=types.WebAppInfo(url=game_url))],
            [types.KeyboardButton(text="/add_word"), types.KeyboardButton(text="/practice")],
            [types.KeyboardButton(text="/all_words"), types.KeyboardButton(text="/stats")],
            [types.KeyboardButton(text="/word_of_day"), types.KeyboardButton(text="/AI")],
            [types.KeyboardButton(text="/import_words"), types.KeyboardButton(text="Відгук 💬")],
            [types.KeyboardButton(text="Допомога ❓")]
        ], resize_keyboard=True
    )
    return kb


# --- СТАНИ FSM ---
class Registration(StatesGroup):
    waiting_for_hobby = State()


class AddWord(StatesGroup):
    waiting_for_word = State()
    waiting_for_language = State()
    waiting_for_translation = State()


class DeleteWord(StatesGroup):
    waiting_for_word = State()


class PracticeWord(StatesGroup):
    waiting_for_language = State()
    waiting_for_answer = State()


class ViewWords(StatesGroup):
    waiting_for_language = State()


class AIHelper(StatesGroup):
    waiting_for_prompt = State()
    waiting_for_language = State()


class WordOfDayState(StatesGroup):
    waiting_for_language = State()
    waiting_for_action = State()


class FeedbackState(StatesGroup):
    waiting_for_message = State()


# --- MIDDLEWARE ANTI-SPAM ---
class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, throttle_time: int = 1):
        self.cache = TTLCache(maxsize=10000, ttl=throttle_time)

    async def __call__(self, handler: Callable[[types.Message, Dict[str, Any]], Awaitable[Any]], event: types.Message,
                       data: Dict[str, Any]) -> Any:
        if not isinstance(event, types.Message) or not event.from_user:
            return await handler(event, data)
        user_id = event.from_user.id
        if user_id in self.cache:
            return
        else:
            self.cache[user_id] = True
            return await handler(event, data)


# === ОБРОБНИКИ КОМАНД ===

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    add_user(message.from_user.id, message.from_user.username)

    cursor.execute("SELECT hobbies FROM users WHERE user_id=?", (message.from_user.id,))
    res = cursor.fetchone()

    if not res or not res[0]:
        await state.set_state(Registration.waiting_for_hobby)
        await message.answer(
            "👋 Привіт! Щоб я міг пояснювати слова цікавіше (за допомогою ШІ), напишіть ваші головні хобі (наприклад: ігри, програмування, музика, спорт):")
    else:
        update_last_active(message.from_user.id)
        await state.clear()
        kb = get_main_kb(message.from_user.id)
        await message.answer(f"👋 Привіт знову!\nОбирай дію в меню 👇\n\n{COMMANDS_TEXT}", reply_markup=kb)


@router.message(Registration.waiting_for_hobby)
async def process_hobby(message: types.Message, state: FSMContext):
    cursor.execute("UPDATE users SET hobbies=? WHERE user_id=?", (message.text, message.from_user.id))
    conn.commit()
    await state.clear()
    await message.answer(f"✅ Збережено! Тепер я буду генерувати приклади саме під ваші інтереси.\n\n{COMMANDS_TEXT}",
                         reply_markup=get_main_kb(message.from_user.id))


# --- ВІДГУКИ ---
@router.message(Command("feedback"))
@router.message(F.text == "Відгук 💬")
async def cmd_feedback(message: types.Message, state: FSMContext):
    await state.set_state(FeedbackState.waiting_for_message)
    await message.answer(
        "💬 Будь ласка, напишіть ваш відгук, побажання або повідомлення про помилку. \n\nАдміністратор отримає його найближчим часом. (Або напишіть /exit для скасування)",
        reply_markup=types.ReplyKeyboardRemove())


@router.message(FeedbackState.waiting_for_message)
async def process_feedback(message: types.Message, state: FSMContext):
    if message.text and message.text.lower() == '/exit':
        return await cmd_exit(message, state)

    feedback_text = message.text if message.text else "Без тексту (медіа)"

    cursor.execute(
        "INSERT INTO feedback (user_id, username, message, date) VALUES (?, ?, ?, ?)",
        (message.from_user.id, message.from_user.username or "Unknown", feedback_text, datetime.now().isoformat())
    )
    conn.commit()

    await state.clear()
    await message.answer("✅ Дякуємо за ваш відгук! Ми обов'язково його розглянемо.",
                         reply_markup=get_main_kb(message.from_user.id))


@router.message(Command("help"))
@router.message(F.text == "Допомога ❓")
async def cmd_help(message: types.Message):
    help_text = (
        "🧠 <b>Як працює цей бот? (Науковий підхід)</b>\n\n"
        "Цей бот створено для максимально ефективного вивчення мов. Ось його головні можливості:\n\n"
        "🔹 <b>Інтервальне повторення (Spaced Repetition)</b>\n"
        "Команда /practice не дає випадкові слова. Вона працює за алгоритмом SuperMemo-2. Бот вираховує, коли ви почнете забувати слово, і пропонує його повторити саме в цей день (через 1, 6, 14 днів і т.д.).\n\n"
        "🔹 <b>Адаптивний ШІ (Google Gemini)</b>\n"
        "Використовуючи команду /AI або /word_of_day, ви отримуєте пояснення, створені нейромережею спеціально для вас. Приклади речень базуються на ваших особистих інтересах та хобі.\n\n"
        "🔹 <b>Гейміфікація та Міні-гра</b>\n"
        "Натисніть «Грати в слова» для переходу у Web App, де ви зможете тренувати свою швидкість та заробляти бали для підвищення рівня у /stats.\n\n"
        "🔹 <b>Зворотній зв'язок</b>\n"
        "Ви можете надіслати повідомлення адміністратору за допомогою кнопки «Відгук 💬»."
    )
    await message.answer(help_text, parse_mode="HTML", reply_markup=get_main_kb(message.from_user.id))


@router.message(Command("exit"))
async def cmd_exit(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    await state.clear()
    kb = get_main_kb(message.from_user.id)
    await message.answer(f"🚪 Ви вийшли з поточного режиму.\n\n{COMMANDS_TEXT}", reply_markup=kb)


@router.callback_query(F.data.startswith("regen:"))
async def callback_regenerate(callback: types.CallbackQuery, state: FSMContext):
    mode = callback.data.split(":")[1]
    data = await state.get_data()
    query = data.get('img_query')

    if not query:
        return await callback.answer("Дані застаріли", show_alert=True)

    try:
        new_url = await get_image_url(query, use_random=True)
        if not new_url:
            return await callback.answer("Не знайдено іншого фото", show_alert=True)

        if mode == 'add' and data.get('word'):
            cursor.execute("UPDATE user_words SET image_url=? WHERE user_id=? AND word=?",
                           (new_url, callback.from_user.id, data['word']))
            conn.commit()

        if mode == 'wod':
            await state.update_data(image_url=new_url)

        caption = callback.message.caption
        await callback.message.edit_media(
            media=types.InputMediaPhoto(media=new_url, caption=caption, parse_mode="HTML"),
            reply_markup=callback.message.reply_markup
        )
        await callback.answer("Фото оновлено!")
    except Exception as e:
        await callback.answer("Помилка оновлення", show_alert=True)


@router.message(F.content_type == types.ContentType.WEB_APP_DATA)
async def process_web_app_data(message: types.Message):
    data = json.loads(message.web_app_data.data)

    if data.get('type') == 'game_result':
        score = data.get('score', 0)
        learned = data.get('learned_words', [])
        user_id = message.from_user.id

        count_learned = 0
        for word_text in learned:
            update_word_progress(user_id, word_text, is_correct=True)
            count_learned += 1

        cursor.execute("SELECT best_score FROM users WHERE user_id=?", (user_id,))
        res = cursor.fetchone()
        current_best = res[0] if res and res[0] else 0

        msg = f"🎮 <b>Результат гри:</b> {score} балів!\n📚 Слів повторено: {count_learned}"
        if score > current_best:
            cursor.execute("UPDATE users SET best_score=? WHERE user_id=?", (score, user_id))
            conn.commit()
            msg += f"\n🏆 <b>Новий рекорд!</b> (Було: {current_best})"

        kb = get_main_kb(user_id)
        await message.answer(msg, parse_mode="HTML", reply_markup=kb)


# --- ІМПОРТ ФАЙЛІВ ---
@router.message(Command("import_words"))
async def cmd_import_words(message: types.Message):
    text = (
        "📥 <b>Масове додавання слів</b>\n\n"
        "Надішліть мені файл у форматі <b>.txt</b> або <b>.csv</b>.\n\n"
        "📝 <b>Формат файлу (по одному слову на рядок):</b>\n"
        "<code>слово - переклад - мова</code>\n\n"
        "<b>Приклад:</b>\n"
        "apple - яблуко - English\n"
        "house - будинок - English"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(F.document)
async def process_document(message: types.Message):
    if not message.document.file_name.endswith(('.csv', '.txt')):
        return

    await message.answer("⏳ Обробляю ваш словник...")
    try:
        file_in_io = io.BytesIO()
        await message.bot.download(message.document, destination=file_in_io)
        file_in_io.seek(0)
        content = file_in_io.read().decode('utf-8')

        lines = content.splitlines()
        added_count = 0

        for line in lines:
            line = line.strip()
            if not line: continue

            parts = []
            if '-' in line:
                parts = [p.strip() for p in line.split('-')]
            elif ',' in line:
                parts = [p.strip() for p in line.split(',')]

            if len(parts) >= 3:
                word, trans, lang = parts[0], parts[1], parts[2]
                cursor.execute("SELECT 1 FROM user_words WHERE user_id=? AND word=? AND language=?",
                               (message.from_user.id, word, lang))
                if not cursor.fetchone():
                    next_date = (datetime.now() + timedelta(days=1)).isoformat()
                    cursor.execute(
                        "INSERT INTO user_words (user_id, word, translation, language, next_review_date, interval, ease_factor) VALUES (?, ?, ?, ?, ?, 1, 2.5)",
                        (message.from_user.id, word, trans, lang, next_date)
                    )
                    added_count += 1
        conn.commit()
        await message.answer(f"✅ Успішно імпортовано {added_count} нових слів!",
                             reply_markup=get_main_kb(message.from_user.id))
    except Exception as e:
        await message.answer(f"❌ Сталася помилка при обробці файлу: {e}")


# --- ДОДАВАННЯ СЛОВА ---
@router.message(Command("add_word"))
async def cmd_add_word(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    await state.set_state(AddWord.waiting_for_word)
    await message.answer("✏️ Введіть слово для додавання:", reply_markup=get_main_kb(message.from_user.id))


@router.message(AddWord.waiting_for_word)
async def process_word(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    text = message.text.strip()

    if text.lower() == '/exit':
        return await cmd_exit(message, state)
    if text.startswith("/"):
        return await message.answer("❌ Завершіть або натисніть /exit.")

    await state.update_data(word=text)
    keyboard = [[types.KeyboardButton(text=l)] for l in SUPPORTED_LANGUAGES]
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(AddWord.waiting_for_language)
    await message.answer("🌍 Оберіть мову слова:", reply_markup=lang_kb)


@router.message(AddWord.waiting_for_language)
async def process_language(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    language = message.text.strip()

    if language.lower() == '/exit':
        return await cmd_exit(message, state)
    if language not in SUPPORTED_LANGUAGES:
        return await message.answer("❌ Невідома мова.")

    await state.update_data(language=language)
    data = await state.get_data()
    word = data.get("word")

    try:
        auto_translation = GoogleTranslator(source='auto', target="uk").translate(word)
    except Exception:
        auto_translation = "Error"

    await state.update_data(auto_translation=auto_translation)
    keyboard = [[types.KeyboardButton(text=f"Зберегти: {auto_translation}")], [types.KeyboardButton(text="/exit")]]
    trans_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(AddWord.waiting_for_translation)
    await message.answer(
        f"🔍 Автопереклад: **{auto_translation}**\n\n"
        "Натисніть кнопку, щоб зберегти його, АБО **напишіть свій переклад** вручну:",
        reply_markup=trans_kb, parse_mode="Markdown"
    )


@router.message(AddWord.waiting_for_translation)
async def process_custom_translation(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    user_input = message.text.strip()

    if user_input.lower() == '/exit':
        return await cmd_exit(message, state)

    data = await state.get_data()
    word = data.get("word")
    language = data.get("language")
    auto_translation = data.get("auto_translation")
    final_translation = auto_translation if message.text.startswith("Зберегти:") else message.text

    await message.answer("⏳ Зберігаю, шукаю картинку та генерую асоціацію...")
    transcription, association, visual_prompt = await get_full_word_info(word, final_translation, language)
    search_query = visual_prompt if visual_prompt else word
    image_url = await get_image_url(search_query)

    await state.update_data(img_query=search_query)

    added = add_word_to_db(message.from_user.id, word, final_translation, language, image_url, association,
                           transcription)
    kb = get_main_kb(message.from_user.id)

    if not added:
        await message.answer(f"⚠️ Слово '{word}' вже є у вашому словнику.", reply_markup=kb)
    else:
        text = f"✅ Додано: {word} {transcription} — {final_translation}"
        if association:
            text += f"\n🧠 Асоціація: {association}"

        inline_kb = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="🔄 Інше фото", callback_data="regen:add")]])
        if image_url:
            await message.answer_photo(photo=image_url, caption=text, reply_markup=inline_kb)
        else:
            await message.answer(text, reply_markup=inline_kb)

    await message.answer("👇 Продовжити:", reply_markup=kb)
    await state.set_state(AddWord.waiting_for_word)


# --- СЛОВО ДНЯ ---
@router.message(Command("word_of_day"))
async def cmd_word_of_day(message: types.Message, state: FSMContext):
    keyboard = [[types.KeyboardButton(text=l)] for l in SUPPORTED_LANGUAGES]
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(WordOfDayState.waiting_for_language)
    await message.answer("🌟 Оберіть мову для нового слова:", reply_markup=lang_kb)


@router.message(WordOfDayState.waiting_for_language)
async def process_word_of_day_lang(message: types.Message, state: FSMContext):
    lang = message.text.strip()
    if lang.lower() == '/exit': return await cmd_exit(message, state)
    if lang not in SUPPORTED_LANGUAGES: return await message.answer("❌ Невідома мова.")

    await message.answer(f"⏳ Генерую слово ({lang})...")

    lvl, _, _ = get_user_level_info(message.from_user.id)
    diff = "A1" if lvl <= 3 else "B1" if lvl <= 8 else "C1"
    user_words_list = get_user_words(message.from_user.id, lang)
    existing_words = {w[0].lower() for w in user_words_list}

    new_word = None
    translation = None

    for i in range(3):
        prompt = (
            f"Згенеруй 1 (одне) цікаве слово мовою {lang} для рівня {diff}. "
            f"Важливо: не повторюй ці слова: [{', '.join(list(existing_words)[-30:])}]. "
            f"Формат відповіді суворо: 'Слово - Переклад'. Переклад українською. Без зайвого тексту."
        )
        response = await asyncio.to_thread(generate_content_safe, contents=prompt)
        result = response.text.strip().replace("*", "")

        if " - " in result:
            w, t = result.split(" - ", 1)
            if w.strip().lower() not in existing_words:
                new_word = w.strip()
                translation = t.strip()
                break

    if not new_word:
        await message.answer("⚠️ Не вдалося знайти нове унікальне слово.",
                             reply_markup=get_main_kb(message.from_user.id))
        return await state.clear()

    try:
        transc, assoc, visual_prompt = await get_full_word_info(new_word, translation, lang)
        search_query = visual_prompt if visual_prompt else new_word
        image_url = await get_image_url(search_query)

        await state.update_data(
            new_word=new_word, translation=translation, lang=lang,
            image_url=image_url, association=assoc, transcription=transc, img_query=search_query
        )

        msg_text = f"🌟 Слово дня: <b>{new_word}</b> {transc}\n🇺🇦 Переклад: {translation}"
        inline_regen = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="🔄 Інше фото", callback_data="regen:wod")]])

        wod_kb = types.ReplyKeyboardMarkup(keyboard=[
            [types.KeyboardButton(text="➕ Додати це слово")],
            [types.KeyboardButton(text="➡️ Наступне слово"), types.KeyboardButton(text="🚪 Вихід")]
        ], resize_keyboard=True)

        if image_url:
            await message.answer_photo(photo=image_url, caption=msg_text, reply_markup=inline_regen, parse_mode="HTML")
        else:
            await message.answer(msg_text, reply_markup=inline_regen, parse_mode="HTML")

        await message.answer("Дії:", reply_markup=wod_kb)
        await state.set_state(WordOfDayState.waiting_for_action)

    except Exception as e:
        await message.answer(f"⚠️ Помилка: {e}", reply_markup=get_main_kb(message.from_user.id))
        await state.clear()


@router.message(WordOfDayState.waiting_for_action)
async def process_wod_action(message: types.Message, state: FSMContext):
    text = message.text
    data = await state.get_data()

    if text == "🚪 Вихід":
        await cmd_exit(message, state)
    elif text == "➡️ Наступне слово":
        msg = types.Message(message_id=0, date=datetime.now(), chat=message.chat, text=data.get('lang', 'English'),
                            from_user=message.from_user).as_(message.bot)
        await process_word_of_day_lang(msg, state)
    elif text == "➕ Додати це слово":
        word = data.get("new_word")
        if not word:
            return await message.answer("Дані застаріли.", reply_markup=get_main_kb(message.from_user.id))

        added = add_word_to_db(message.from_user.id, word, data['translation'], data['lang'], data['image_url'],
                               data['association'], data['transcription'])

        if added:
            await message.answer(f"✅ Додано!\n🧠 {data['association']}" if data['association'] else "✅ Додано!")
        else:
            await message.answer("⚠️ Вже є.")
    else:
        await cmd_exit(message, state)


# --- СТАТИСТИКА ---
@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id
    words = get_user_words(user_id)
    total_words = len(words)
    total_correct = sum([w[3] for w in words])
    lvl, current_xp, next_xp = get_user_level_info(user_id)

    percent = int((current_xp / next_xp) * 10)
    bar = "🟩" * percent + "⬜" * (10 - percent)

    lang_stats = {}
    for w in words:
        l = w[2]
        if l not in lang_stats:
            lang_stats[l] = 0
        lang_stats[l] += 1

    cursor.execute("SELECT best_score FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    best_game_score = res[0] if res else 0

    stats_text = (f"📊 <b>Статистика</b>\n"
                  f"🏆 Рівень: {lvl}\n"
                  f"⭐ XP: {current_xp}/{next_xp}\n"
                  f"[{bar}]\n\n"
                  f"📚 Всього слів: {total_words}\n"
                  f"✅ Правильних відповідей: {total_correct}\n"
                  f"🎮 Рекорд у грі: {best_game_score}\n\n"
                  "Слова по мовах:\n")

    for lang, count in lang_stats.items():
        stats_text += f"- {lang}: {count} сл.\n"

    await message.answer(stats_text, reply_markup=get_main_kb(user_id), parse_mode="HTML")


# --- ПРАКТИКА (SPACED REPETITION) ---
@router.message(Command("practice"))
async def cmd_practice(message: types.Message, state: FSMContext):
    # Обираємо слова, які потрібно повторити саме сьогодні
    words = get_user_words(message.from_user.id, for_review=True)
    if not words:
        await message.answer("🎉 На сьогодні всі слова повторені! Відпочивайте або додайте нові.",
                             reply_markup=get_main_kb(message.from_user.id))
        return

    languages = sorted(list(set([w[2] for w in words if w[2] is not None])))
    keyboard = [[types.KeyboardButton(text=l)] for l in languages]
    keyboard.append([types.KeyboardButton(text="Усі мови")])
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.update_data(all_practice_words=words)
    await state.set_state(PracticeWord.waiting_for_language)
    await message.answer(f"🎯 Слів для повторення сьогодні: {len(words)}\nОберіть мову:", reply_markup=lang_kb)


@router.message(PracticeWord.waiting_for_language)
async def practice_choose_lang(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    text = message.text.strip()
    if text.lower() == '/exit': return await cmd_exit(message, state)

    data = await state.get_data()
    all_words = data.get("all_practice_words", [])
    target = all_words if text == "Усі мови" else [w for w in all_words if w[2] == text]

    if not target:
        return await message.answer("Пусто.")

    random.shuffle(target)
    await state.update_data(plist=target[:10], pidx=0)
    await state.set_state(PracticeWord.waiting_for_answer)
    await send_practice_q(message, target[0])


async def send_practice_q(message, w):
    q = f"✏️ Перекладіть: <b>{w[1]}</b> ({w[2]})"
    if w[4]:
        await message.answer_photo(w[4], caption=q, parse_mode="HTML")
    else:
        await message.answer(q, parse_mode="HTML")


@router.message(PracticeWord.waiting_for_answer)
async def process_practice_ans(message: types.Message, state: FSMContext):
    if message.text == "/exit": return await cmd_exit(message, state)

    data = await state.get_data()
    p_list = data['plist']
    idx = data['pidx']

    correct_word = p_list[idx][0]
    is_correct = message.text.lower() == correct_word.lower()

    # Інтервальне оновлення прогресу
    update_word_progress(message.from_user.id, correct_word, is_correct)

    if is_correct:
        await message.answer(f"✅ Правильно! {correct_word}")
    else:
        hint = f"\n💡 {p_list[idx][5]}" if p_list[idx][5] else ""
        tr = f" {p_list[idx][6]}" if p_list[idx][6] else ""
        await message.answer(f"❌ Ні. {correct_word}{tr}{hint}")

    idx += 1
    if idx >= len(p_list):
        await message.answer("🏁 Тренування завершено!", reply_markup=get_main_kb(message.from_user.id))
        await state.clear()
    else:
        await state.update_data(pidx=idx)
        await send_practice_q(message, p_list[idx])


# --- ВИДАЛЕННЯ СЛОВА ---
@router.message(Command("delete_word"))
async def cmd_delete_word(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    await state.set_state(DeleteWord.waiting_for_word)
    await message.answer("🗑️ Введіть слово для видалення (або /exit):", reply_markup=get_main_kb(message.from_user.id))


@router.message(DeleteWord.waiting_for_word)
async def process_delete_word(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    text = message.text.strip()

    if text.lower() == '/exit':
        return await cmd_exit(message, state)

    words_in_db = [w[0] for w in get_user_words(message.from_user.id)]
    if text in words_in_db:
        delete_word_from_db(message.from_user.id, text)
        await message.answer(f"🗑️ Слово '{text}' видалено.", reply_markup=get_main_kb(message.from_user.id))
    else:
        await message.answer(f"❌ Слова '{text}' немає в словнику.", reply_markup=get_main_kb(message.from_user.id))


# --- ПЕРЕГЛЯД УСІХ СЛІВ ---
@router.message(Command("all_words"))
async def cmd_all_words(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    words = get_user_words(message.from_user.id)
    if not words:
        return await message.answer("📭 Ваш словник порожній.", reply_markup=get_main_kb(message.from_user.id))

    languages = sorted(list(set([w[2] for w in words if w[2] is not None])))
    if not languages:
        words_list = "\n".join([f"{w[0]} — {w[1]}" for w in words])
        return await message.answer(f"📝 Ваші слова:\n{words_list}", reply_markup=get_main_kb(message.from_user.id))

    keyboard = [[types.KeyboardButton(text=l)] for l in languages]
    keyboard.append([types.KeyboardButton(text="Усі мови")])
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(ViewWords.waiting_for_language)
    await message.answer("🌐 Оберіть мову:", reply_markup=lang_kb)


@router.message(ViewWords.waiting_for_language)
async def process_view_language(message: types.Message, state: FSMContext):
    lang_choice = message.text.strip()
    if lang_choice.lower() == '/exit':
        return await cmd_exit(message, state)

    words = get_user_words(message.from_user.id) if lang_choice == "Усі мови" else get_user_words(message.from_user.id,
                                                                                                  language=lang_choice)

    if not words:
        await message.answer("📭 Словник порожній.", reply_markup=get_main_kb(message.from_user.id))
    else:
        text = f"📝 Слова ({lang_choice}):\n"
        for w in words:
            transc_str = f" {w[6]}" if w[6] else ""
            text += f"{w[0]}{transc_str} — {w[1]}\n"

        if len(text) > 4096:
            await message.answer(f"📝 Слова ({lang_choice}):\n... (занадто багато для одного повідомлення)",
                                 reply_markup=get_main_kb(message.from_user.id))
        else:
            await message.answer(text, reply_markup=get_main_kb(message.from_user.id))

    await state.clear()


# --- ШТУЧНИЙ ІНТЕЛЕКТ (AI) ---
@router.message(Command("AI"))
async def cmd_ai(message: types.Message, state: FSMContext):
    await state.set_state(AIHelper.waiting_for_prompt)
    await message.answer("🤖 Введіть слово для пояснення:", reply_markup=get_main_kb(message.from_user.id))


@router.message(AIHelper.waiting_for_prompt)
async def process_ai_prompt(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() == '/exit': return await cmd_exit(message, state)
    if text.startswith("/"): return await message.answer(
        "❌ Будь ласка, спочатку введіть запит для ШІ або натисніть /exit.")

    await state.update_data(prompt=text)
    languages_list = SUPPORTED_LANGUAGES + ["Українська"]
    keyboard = [[types.KeyboardButton(text=l)] for l in languages_list] + [[types.KeyboardButton(text="/exit")]]
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(AIHelper.waiting_for_language)
    await message.answer("🌍 Мова слова?", reply_markup=lang_kb)


@router.message(AIHelper.waiting_for_language)
async def process_ai_language(message: types.Message, state: FSMContext):
    language_of_word = message.text.strip()
    if language_of_word.lower() == '/exit': return await cmd_exit(message, state)

    data = await state.get_data()
    prompt = data.get("prompt")
    await message.answer("🤖 Оброблюю...", reply_markup=get_main_kb(message.from_user.id))

    try:
        txt, img = await asyncio.gather(
            get_ai_explanation_text(prompt, language_of_word, message.from_user.id),
            get_image_url(prompt)
        )
        await state.update_data(img_query=prompt)
        inline_regen = types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text="🔄 Інше фото", callback_data="regen:ai")]])

        if img:
            await message.answer_photo(photo=img, caption=f"🤖 Ось пояснення:\n\n{txt}"[:1024],
                                       reply_markup=inline_regen)
        else:
            await message.answer(f"🤖 Ось пояснення:\n\n{txt}", reply_markup=inline_regen)
    except Exception as e:
        await message.answer(f"{str(e)}", reply_markup=get_main_kb(message.from_user.id))

    await state.set_state(AIHelper.waiting_for_prompt)
    await message.answer("🤖 Ще слово? (або /exit)", reply_markup=get_main_kb(message.from_user.id))


# --- ОБРОБНИК НЕВІДОМИХ КОМАНД ---
@router.message()
async def unknown_command(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await message.answer(
            "❌ Незрозуміла відповідь. Будь ласка, дотримуйтесь інструкцій або натисніть /exit, щоб вийти з поточного режиму.")
        return
    await message.answer("❌ Невідома команда. Натисніть /help для інформації.\n\n" + COMMANDS_TEXT,
                         reply_markup=get_main_kb(message.from_user.id))


# --- ЗАПУСК БОТА ---
async def main():
    print("🚀 Бота запущено")
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    dp.message.middleware(ThrottlingMiddleware(throttle_time=1))

    # Фонові задачі
    asyncio.create_task(start_web_server())
    asyncio.create_task(keep_alive_task())

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")