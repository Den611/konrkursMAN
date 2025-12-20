import asyncio
import sqlite3
import json
import urllib.parse
from datetime import datetime
from aiogram import Bot, Dispatcher, types, BaseMiddleware, F
from aiogram.filters import Command, CommandObject
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
from dotenv import load_dotenv
from typing import Dict, Any

def load_config_from_env(env_file: str = ".env") -> Dict[str, Any]:
    #Завантажує конфігураційні змінні (токени, ключі, URL) з .env файлу та повертає їх у вигляді словника.
    load_dotenv(dotenv_path=env_file)

    config = {}

    config["TELEGRAM_BOT_TOKEN"] = os.getenv("TELEGRAM_BOT_TOKEN", "")
    config["PIXABAY_API_KEY"] = os.getenv("PIXABAY_API_KEY", "")
    config["WEB_APP_URL"] = os.getenv("WEB_APP_URL", "")

    gemini_keys_str = os.getenv("GEMINI_API_KEYS")
    
    if gemini_keys_str:
        config["GEMINI_API_KEYS"] = [key.strip() 
                                     for key in gemini_keys_str.split(',') 
                                     if key.strip()]
    else:
        config["GEMINI_API_KEYS"] = []

    return config

config = load_config_from_env()

TELEGRAM_BOT_TOKEN = config["TELEGRAM_BOT_TOKEN"]
PIXABAY_API_KEY = config["PIXABAY_API_KEY"]
WEB_APP_URL = config["WEB_APP_URL"]
GEMINI_API_KEYS = config["GEMINI_API_KEYS"]

# Перевірка завантажених даних
print("✅ Конфігурація успішно завантажена:")
print(f"TELEGRAM_BOT_TOKEN: {TELEGRAM_BOT_TOKEN[:8]}...") 
print(f"WEB_APP_URL: {WEB_APP_URL}")
print(f"Кількість завантажених Gemini ключів: {len(GEMINI_API_KEYS)}")
print(f"Перший ключ Gemini: {GEMINI_API_KEYS[0][:8]}..." if GEMINI_API_KEYS else "Ключі Gemini відсутні.")

# Ініціалізація бота та диспетчера
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Підключення до бази даних та створення курсору
conn = sqlite3.connect("words.db")
cursor = conn.cursor()

# Створення таблиці користувачів, якщо вона не існує
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    start_date TEXT,
    last_active TEXT,
    best_score INTEGER DEFAULT 0
)
""")

# Створення таблиці слів користувачів, якщо вона не існує
# Оновлено: додані поля для картинки, асоціації та транскрипції
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
    PRIMARY KEY(user_id, word, language)
)
""")
conn.commit()


# Функція для автоматичного додавання нових колонок у старі бази даних
def migrate_db():
    columns = [
        ("image_url", "TEXT"),
        ("association", "TEXT"),
        ("transcription", "TEXT")
    ]
    for col_name, col_type in columns:
        try:
            cursor.execute(f"ALTER TABLE user_words ADD COLUMN {col_name} {col_type}")
            print(f"✅ База даних оновлена: додано колонку {col_name}")
        except sqlite3.OperationalError:
            pass  # Колонка вже існує

    try:
        cursor.execute("ALTER TABLE users ADD COLUMN best_score INTEGER DEFAULT 0")
        print("✅ База даних оновлена: додано колонку best_score")
    except sqlite3.OperationalError:
        pass

    conn.commit()


migrate_db()


# МЕНЕДЖЕР API КЛЮЧІВ GEMINI
class KeyManager:
    def __init__(self, keys):
        self.keys = keys
        self.current_index = 0
        self.client = self._init_client()

    def _init_client(self):
        if not self.keys or not self.keys[0]:
            print("❌ Помилка: Список GEMINI_API_KEYS порожній або містить пусті рядки!")
            return None
        print(f"🔄 Gemini: Використовую ключ №{self.current_index + 1}")
        return genai.Client(api_key=self.keys[self.current_index])

    def get_client(self):
        return self.client

    def rotate_key(self):
        self.current_index = (self.current_index + 1) % len(self.keys)
        print(f"⚠️ Gemini: Перемикаю на ключ №{self.current_index + 1}")
        self.client = self._init_client()


key_manager = KeyManager(GEMINI_API_KEYS)


# Функція для безпечного виконання запитів з ротацією ключів
def generate_content_safe(contents, config=None, model="gemini-2.5-flash"):
    attempts = 0
    max_attempts = len(GEMINI_API_KEYS) + 1  # +1 спроба

    while attempts < max_attempts:
        try:
            client = key_manager.get_client()
            if not client: raise Exception("API ключі не налаштовані")

            response = client.models.generate_content(
                model=model,
                config=config,
                contents=contents,
            )
            return response
        except Exception as e:
            error_msg = str(e).lower()
            if "429" in error_msg or "quota" in error_msg or "exhausted" in error_msg:
                print(f"⚠️ Gemini Error ({e}). Пробую наступний ключ...")
                key_manager.rotate_key()
                attempts += 1
            else:
                raise e
    raise Exception("❌ Всі API ключі вичерпано.")

# Веб-сервер, щоб хостинг бачив відкритий порт
async def health_check(request):
    return web.Response(text="I am alive! Bot is running.")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080)) 
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌍 Веб-сервер запущено на порту {port}")

# Функція, яка робить щось кожні 40 секунд
async def keep_alive_task():
    while True:
        await asyncio.sleep(40)
        try:
            print("40 секунд пройшло, бот активний...")
        except Exception as e:
            print(f"Error in keep_alive: {e}")


# Функція пошуку картинки на Pixabay
async def get_image_url(query, use_random=False):
    if not query or not PIXABAY_API_KEY:
        return None
    try:
        # Шукаємо більше картинок (20), якщо потрібна випадкова
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


# Функція отримання транскрипції та асоціації від ШІ
async def get_full_word_info(word, translation, lang):
    prompt = (
        f"Analyze the word '{word}' (language: {lang}, translation: '{translation}'). "
        f"Return ONLY a string in this format: "
        f"TRANSCRIPTION|ASSOCIATION|VISUAL_SEARCH_PROMPT\n"
        f"1. Transcription: Ukrainian letters inside brackets (e.g. [хелоу]).\n"
        f"2. Association: A short funny mnemonic sentence in Ukrainian to remember the word.\n"
        f"3. Visual Search Prompt: A short 3-5 word English phrase describing a photograph depicting the association, strictly without any text, signs, or words in the image. Focus on objects, nature, or actions.\n"
        f"Example output for 'freedom': [фрідом]|Уяви птаха, який вилетів з клітки на волю.|bird flying out of cage in sky"
    )
    try:
        response = await asyncio.to_thread(generate_content_safe, contents=prompt)
        text = response.text.strip().replace("*", "")
        parts = text.split("|")

        if len(parts) >= 3:
            transc = parts[0].strip()
            assoc = parts[1].strip()
            visual_prompt = parts[2].strip()
            return transc, assoc, visual_prompt
        elif len(parts) == 2:
            # Fallback для старого формату
            return parts[0].strip(), parts[1].strip(), word

        return "[?]", text, word
    except Exception:
        return "[?]", None, word


# Функція для отримання пояснення слова від ШІ (оновлена)
async def get_ai_explanation_text(content, language_of_word):
    print(f"GenAI: Обробка запиту '{content}'...")

    system_prompt = (
        f"Ти — вчитель іноземних мов. "
        f"Поясни слово '{content}' (мова: {language_of_word}). "
        "Структура відповіді:\n"
        "1. Слово - [Транскрипція українськими літерами] - Переклад\n"
        "2. Коротке значення.\n"
        "3. Один приклад речення з перекладом.\n"
        "Без Markdown."
    )

    config = genai_types.GenerateContentConfig(
        system_instruction=system_prompt
    )

    response = await asyncio.to_thread(generate_content_safe, contents=content, config=config)
    return response.text.replace("*", "")


# ФУНКЦІЇ БАЗИ ДАНИХ

def add_word_to_db(user_id, word, translation, language, image_url=None, association=None, transcription=None):
    try:
        # Якщо слово вже є, оновлюємо його дані (наприклад, нову картинку після регенерації)
        cursor.execute("SELECT 1 FROM user_words WHERE user_id=? AND word=? AND language=?", (user_id, word, language))
        if cursor.fetchone():
            if image_url:
                cursor.execute(
                    "UPDATE user_words SET image_url=?, association=?, transcription=? WHERE user_id=? AND word=? AND language=?",
                    (image_url, association, transcription, user_id, word, language))
                conn.commit()
            return False

        cursor.execute(
            "INSERT INTO user_words (user_id, word, translation, language, usage_count, image_url, association, transcription) VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
            (user_id, word, translation, language, image_url, association, transcription)
        )
        conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"Database error in add_word_to_db: {e}")
        return False


def get_user_words(user_id, language=None):
    try:
        # Повертає 8 колонок. Індекси:
        # 0-word, 1-translation, 2-language, 3-usage_count, 4-image_url, 5-association, 6-transcription
        query = "SELECT word, translation, language, usage_count, image_url, association, transcription FROM user_words WHERE user_id=?"
        params = (user_id,)
        if language is not None:
            query += " AND language=?"
            params = (user_id, language)

        cursor.execute(query, params)
        return cursor.fetchall()
    except sqlite3.Error as e:
        print(f"Database error in get_user_words: {e}")
        return []


def increment_usage_count(user_id, word, language=None):
    try:
        cursor.execute(
            "UPDATE user_words SET usage_count = usage_count + 1 WHERE user_id=? AND word=?",
            (user_id, word)
        )
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error in increment_usage_count: {e}")


def get_user_level_info(user_id):
    words = get_user_words(user_id)
    # w[3] - це usage_count (4-й елемент у вибірці)
    total_xp = sum([w[3] for w in words])
    level = 1
    xp_needed = 10

    while total_xp >= xp_needed:
        total_xp -= xp_needed
        level += 1
        xp_needed += 10

    return level, total_xp, xp_needed


# Функція реєстрації нового користувача в базі даних
def add_user(user_id, username):
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, start_date, last_active) VALUES (?, ?, ?, ?)",
            (user_id, username, datetime.now().isoformat(), datetime.now().isoformat())
        )
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error in add_user: {e}")


# Оновлення часу останньої активності користувача
def update_last_active(user_id):
    try:
        cursor.execute(
            "UPDATE users SET last_active=? WHERE user_id=?",
            (datetime.now().isoformat(), user_id)
        )
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error: {e}")


# Видалення слова з бази даних
def delete_word_from_db(user_id, word):
    try:
        cursor.execute("DELETE FROM user_words WHERE user_id=? AND word=?", (user_id, word))
        conn.commit()
    except sqlite3.Error as e:
        print(f"Database error in delete_word_from_db: {e}")


# ДИНАМІЧНА КЛАВІАТУРА
# Генерує посилання на гру з 50 найменш вивченими словами
def get_main_kb(user_id):
    words_raw = get_user_words(user_id)
    game_words = []

    if words_raw:
        # Сортуємо слова за кількістю використань (найменші спочатку)
        # index 3 - це usage_count
        words_raw.sort(key=lambda x: x[3])

        # Беремо перші 50 слів
        sample = words_raw[:50]

        for w in sample:
            # w[0] - слово, w[1] - переклад
            game_words.append({"w": w[0], "t": w[1]})

    # Кодуємо в JSON для URL
    if game_words:
        json_data = json.dumps(game_words)
        encoded_data = urllib.parse.quote(json_data)
        game_url = f"{WEB_APP_URL}?data={encoded_data}"
    else:
        game_url = WEB_APP_URL

    kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🎮 Грати в слова (Web App)", web_app=types.WebAppInfo(url=game_url))],
            [types.KeyboardButton(text="/add_word"), types.KeyboardButton(text="/all_words")],
            [types.KeyboardButton(text="/practice"), types.KeyboardButton(text="/delete_word")],
            [types.KeyboardButton(text="/stats"), types.KeyboardButton(text="/word_of_day")],
            [types.KeyboardButton(text="/AI"), types.KeyboardButton(text="/exit")]
        ], resize_keyboard=True
    )
    return kb


# Визначення станів (FSM) для процесу додавання слова
class AddWord(StatesGroup):
    waiting_for_word = State()
    waiting_for_language = State()
    waiting_for_translation = State()


# Визначення станів для видалення слова
class DeleteWord(StatesGroup):
    waiting_for_word = State()


# Визначення станів для режиму тренування
class PracticeWord(StatesGroup):
    waiting_for_language = State()
    waiting_for_answer = State()


# Визначення станів для перегляду слів
class ViewWords(StatesGroup):
    waiting_for_language = State()


# Визначення станів для взаємодії зі штучним інтелектом
class AIHelper(StatesGroup):
    waiting_for_prompt = State()
    waiting_for_language = State()


# Новий стан для Слова Дня
class WordOfDayState(StatesGroup):
    waiting_for_language = State()
    waiting_for_action = State()  # Стан очікування дії (додати/далі)


# Middleware для обмеження частоти запитів (Anti-spam)
class ThrottlingMiddleware(BaseMiddleware):

    def __init__(self, throttle_time: int = 1):
        self.cache = TTLCache(maxsize=10000, ttl=throttle_time)

    async def __call__(
            self,
            handler: Callable[[types.Message, Dict[str, Any]], Awaitable[Any]],
            event: types.Message,
            data: Dict[str, Any]
    ) -> Any:

        if not isinstance(event, types.Message) or not event.from_user:
            return await handler(event, data)

        user_id = event.from_user.id

        if user_id in self.cache:
            return
        else:
            self.cache[user_id] = True
            return await handler(event, data)


# Текст з описом команд для користувача
COMMANDS_TEXT = (
    "Доступні команди:\n"
    "/add_word – додати нове слово 📚\n"
    "/delete_word – видалити слово ❌\n"
    "/all_words – список усіх слів 📝\n"
    "/practice – тренування 🎯\n"
    "/stats – ваша статистика 📊\n"
    "/word_of_day – слово дня 🌟\n"
    "/AI – допомога ШІ 🤖\n"
    "/exit – вихід з режиму 🚪"
)

# Список підтримуваних мов
SUPPORTED_LANGUAGES = ["English", "German", "French", "Polish", "Spanish", "Italian"]


# ОБРОБНИКИ

# Обробник команди /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    add_user(message.from_user.id, message.from_user.username)
    update_last_active(message.from_user.id)
    await state.clear()
    kb = get_main_kb(message.from_user.id)
    await message.answer(f"👋 Привіт!\nСпробуй нову гру 👇\n\n{COMMANDS_TEXT}", reply_markup=kb)


# Обробник команди /exit
@dp.message(Command("exit"))
async def cmd_exit(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    current_state = await state.get_state()
    if current_state is None:
        kb = get_main_kb(message.from_user.id)
        await message.answer("🚪 Зараз жоден з режимів не активний.", reply_markup=kb)
        return

    await state.clear()
    kb = get_main_kb(message.from_user.id)
    await message.answer(f"🚪 Ви вийшли з режиму.\n\n{COMMANDS_TEXT}", reply_markup=kb)


# ОБРОБКА КНОПКИ РЕГЕНЕРАЦІЇ ФОТО
@dp.callback_query(F.data.startswith("regen:"))
async def callback_regenerate(callback: types.CallbackQuery, state: FSMContext):
    # regen:mode (add/wod/ai)
    mode = callback.data.split(":")[1]
    data = await state.get_data()

    # Отримуємо збережений візуальний промпт
    query = data.get('img_query')
    if not query:
        await callback.answer("Дані застаріли", show_alert=True)
        return

    try:
        # Шукаємо нове ВИПАДКОВЕ фото
        new_url = await get_image_url(query, use_random=True)
        if not new_url:
            await callback.answer("Не знайдено іншого фото", show_alert=True)
            return

        # Якщо це режим додавання слова, оновлюємо і в БД
        if mode == 'add' and data.get('word'):
            cursor.execute("UPDATE user_words SET image_url=? WHERE user_id=? AND word=?",
                           (new_url, callback.from_user.id, data['word']))
            conn.commit()

        # Для Word of Day оновлюємо стан
        if mode == 'wod':
            await state.update_data(image_url=new_url)

        # Оновлюємо повідомлення
        caption = callback.message.caption
        await callback.message.edit_media(
            media=types.InputMediaPhoto(media=new_url, caption=caption, parse_mode="HTML"),
            reply_markup=callback.message.reply_markup
        )
        await callback.answer("Фото оновлено!")

    except Exception as e:
        print(f"Regen error: {e}")
        await callback.answer("Помилка оновлення", show_alert=True)


# ОБРОБКА ДАНИХ З ГРИ (WEB APP)
@dp.message(F.content_type == types.ContentType.WEB_APP_DATA)
async def process_web_app_data(message: types.Message):
    data = json.loads(message.web_app_data.data)

    if data.get('type') == 'game_result':
        score = data.get('score', 0)
        learned = data.get('learned_words', [])
        user_id = message.from_user.id

        # Оновлюємо статистику кожного вгаданого слова
        count_learned = 0
        for word_text in learned:
            cursor.execute("UPDATE user_words SET usage_count = usage_count + 1 WHERE user_id=? AND word=?",
                           (user_id, word_text))
            if cursor.rowcount > 0: count_learned += 1
        conn.commit()

        # Оновлюємо рекорд користувача
        cursor.execute("SELECT best_score FROM users WHERE user_id=?", (user_id,))
        res = cursor.fetchone()
        current_best = res[0] if res and res[0] else 0

        msg = f"🎮 <b>Результат гри:</b> {score} балів!"
        msg += f"\n📚 Слів повторено: {count_learned}"

        if score > current_best:
            cursor.execute("UPDATE users SET best_score=? WHERE user_id=?", (score, user_id))
            conn.commit()
            msg += f"\n🏆 <b>Новий рекорд!</b> (Було: {current_best})"

        kb = get_main_kb(user_id)
        await message.answer(msg, parse_mode="HTML", reply_markup=kb)


# Початок процесу додавання слова
@dp.message(Command("add_word"))
async def cmd_add_word(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    kb = get_main_kb(message.from_user.id)
    await state.set_state(AddWord.waiting_for_word)
    await message.answer("✏️ Введіть слово для додавання:", reply_markup=kb)


# Обробка введеного слова для додавання
@dp.message(AddWord.waiting_for_word)
async def process_word(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    text = message.text.strip()

    if text.lower() == '/exit':
        await cmd_exit(message, state)
        return
    if text.startswith("/"):
        await message.answer("❌ Будь ласка, спочатку завершіть додавання слова або натисніть /exit.")
        return

    word = text
    await state.update_data(word=word)

    keyboard = [[types.KeyboardButton(text=l)] for l in SUPPORTED_LANGUAGES]
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(AddWord.waiting_for_language)
    await message.answer("🌍 Оберіть мову слова:", reply_markup=lang_kb)


# Обробка вибору мови та збереження слова
@dp.message(AddWord.waiting_for_language)
async def process_language(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    language = message.text.strip()

    if language.lower() == '/exit':
        await cmd_exit(message, state)
        return

    if language not in SUPPORTED_LANGUAGES:
        await message.answer("❌ Невідома мова. Виберіть зі списку або /exit.")
        return

    await state.update_data(language=language)
    data = await state.get_data()
    word = data.get("word")

    try:
        translator = GoogleTranslator(source='auto', target="uk")
        auto_translation = translator.translate(data['word'])
    except Exception:
        auto_translation = "Error"

    await state.update_data(auto_translation=auto_translation)

    keyboard = [
        [types.KeyboardButton(text=f"Зберегти: {auto_translation}")],
        [types.KeyboardButton(text="/exit")]
    ]
    trans_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(AddWord.waiting_for_translation)
    await message.answer(
        f"🔍 Автопереклад: **{auto_translation}**\n\n"
        "Натисніть кнопку, щоб зберегти його, АБО **напишіть свій переклад** вручну:",
        reply_markup=trans_kb, parse_mode="Markdown"
    )


# 2. Зберігаємо фінальний варіант переклада
@dp.message(AddWord.waiting_for_translation)
async def process_custom_translation(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    user_input = message.text.strip()
    user_id = message.from_user.id

    if user_input.lower() == '/exit':
        await cmd_exit(message, state)
        return

    data = await state.get_data()
    word = data.get("word")
    language = data.get("language")
    auto_translation = data.get("auto_translation")
    final_translation = auto_translation if message.text.startswith("Зберегти:") else message.text

    await message.answer("⏳ Зберігаю, шукаю картинку та генерую асоціацію...")

    # Паралельний запуск: Картинка + Інфо
    # Спочатку отримуємо промпт для картинки від ШІ
    transcription, association, visual_prompt = await get_full_word_info(word, final_translation, language)

    # Використовуємо цей промпт для пошуку картинки без тексту
    search_query = visual_prompt if visual_prompt else word
    image_url = await get_image_url(search_query)

    # Зберігаємо для регенерації
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

        # Кнопка регенерації
        inline_kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Інше фото", callback_data="regen:add")]
        ])

        if image_url:
            await message.answer_photo(photo=image_url, caption=text, reply_markup=inline_kb)
        else:
            await message.answer(text, reply_markup=inline_kb)

    await message.answer("👇 Продовжити:", reply_markup=kb)

    await state.set_state(AddWord.waiting_for_word)


# Слово дня з ШІ
@dp.message(Command("word_of_day"))
async def cmd_word_of_day(message: types.Message, state: FSMContext):
    keyboard = [[types.KeyboardButton(text=l)] for l in SUPPORTED_LANGUAGES]
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(WordOfDayState.waiting_for_language)
    await message.answer("🌟 Оберіть мову для нового слова:", reply_markup=lang_kb)


@dp.message(WordOfDayState.waiting_for_language)
async def process_word_of_day_lang(message: types.Message, state: FSMContext):
    lang = message.text.strip()
    user_id = message.from_user.id

    if lang.lower() == '/exit':
        await cmd_exit(message, state)
        return

    if lang not in SUPPORTED_LANGUAGES:
        await message.answer("❌ Невідома мова. Виберіть зі списку.")
        return

    await message.answer(f"⏳ Генерую слово ({lang})...")

    lvl, _, _ = get_user_level_info(message.from_user.id)
    diff = "A1" if lvl <= 3 else "B1" if lvl <= 8 else "C1"

    # --- ПЕРЕВІРКА НА УНІКАЛЬНІСТЬ ---
    user_words_list = get_user_words(message.from_user.id, lang)
    existing_words = {w[0].lower() for w in user_words_list}

    new_word = None
    translation = None

    # Робимо до 3 спроб знайти нове слово
    for i in range(3):
        prompt = (
            f"Згенеруй 1 (одне) цікаве слово мовою {lang} для рівня {diff}. "
            f"Важливо: не повторюй ці слова: [{', '.join(list(existing_words)[-30:])}]. "
            f"Формат відповіді суворо: 'Слово - Переклад'. Переклад українською. "
            f"Без зайвого тексту."
        )

        response = await asyncio.to_thread(generate_content_safe, contents=prompt)
        result = response.text.strip().replace("*", "")

        if " - " in result:
            w, t = result.split(" - ", 1)
            w = w.strip()
            if w.lower() not in existing_words:
                new_word = w
                translation = t.strip()
                break
        else:
            continue

    if not new_word:
        await message.answer("⚠️ Не вдалося знайти нове унікальне слово.",
                             reply_markup=get_main_kb(message.from_user.id))
        await state.clear()
        return

    try:
        transc, assoc, visual_prompt = await get_full_word_info(new_word, translation, lang)

        search_query = visual_prompt if visual_prompt else new_word
        image_url = await get_image_url(search_query)

        await state.update_data(
            new_word=new_word, translation=translation, lang=lang,
            image_url=image_url, association=assoc, transcription=transc,
            img_query=search_query  # Зберігаємо для регенерації
        )

        msg_text = f"🌟 Слово дня: <b>{new_word}</b> {transc}\n🇺🇦 Переклад: {translation}"

        # Кнопка регенерації
        inline_regen = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Інше фото", callback_data="regen:wod")]
        ])

        # Кнопки дій
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
        kb = get_main_kb(message.from_user.id)
        await message.answer(f"⚠️ Помилка: {e}", reply_markup=kb)
        await state.clear()


@dp.message(WordOfDayState.waiting_for_action)
async def process_wod_action(message: types.Message, state: FSMContext):
    text = message.text
    data = await state.get_data()

    if text == "🚪 Вихід":
        await cmd_exit(message, state)
    elif text == "➡️ Наступне слово":
        # FIX: Прив'язуємо (mount) повідомлення до бота, щоб працював .answer()
        msg = types.Message(
            message_id=0,
            date=datetime.now(),
            chat=message.chat,
            text=data.get('lang', 'English'),
            from_user=message.from_user
        ).as_(bot)

        await process_word_of_day_lang(msg, state)
    elif text == "➕ Додати це слово":
        word = data.get("new_word")
        if not word:
            await message.answer("Дані застаріли.", reply_markup=get_main_kb(message.from_user.id))
            return

        added = add_word_to_db(message.from_user.id, word, data['translation'], data['lang'], data['image_url'],
                               data['association'], data['transcription'])
        if added:
            confirm = f"✅ Додано!\n🧠 {data['association']}" if data['association'] else "✅ Додано!"
            await message.answer(confirm)
        else:
            await message.answer("⚠️ Вже є.")
    else:
        await cmd_exit(message, state)


# Перегляд статистики
@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id
    words = get_user_words(user_id)
    total_words = len(words)
    # Індекс 3 - usage_count
    total_correct = sum([w[3] for w in words])
    lvl, current_xp, next_xp = get_user_level_info(user_id)

    percent = int((current_xp / next_xp) * 10)
    bar = "🟩" * percent + "⬜" * (10 - percent)

    # Статистика по мовах
    lang_stats = {}
    for w in words:
        l = w[2]  # language
        if l not in lang_stats:
            lang_stats[l] = 0
        lang_stats[l] += 1

    # Рекорд гри
    cursor.execute("SELECT best_score FROM users WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    best_game_score = res[0] if res else 0

    stats_text = f"📊 <b>Статистика</b>\n" \
                 f"🏆 Рівень: {lvl}\n" \
                 f"⭐ XP: {current_xp}/{next_xp}\n" \
                 f"[{bar}]\n\n" \
                 f"📚 Всього слів: {total_words}\n" \
                 f"✅ Правильних відповідей: {total_correct}\n" \
                 f"🎮 Рекорд у грі: {best_game_score}\n\n" \
                 "Слова по мовах:\n"

    for lang, count in lang_stats.items():
        stats_text += f"- {lang}: {count} сл.\n"

    await message.answer(stats_text, reply_markup=get_main_kb(user_id), parse_mode="HTML")


# Режим практики
@dp.message(Command("practice"))
async def cmd_practice(message: types.Message, state: FSMContext):
    words = get_user_words(message.from_user.id)
    if not words:
        await message.answer("📭 Ваш словник порожній. Додайте слова через /add_word.",
                             reply_markup=get_main_kb(message.from_user.id))
        return

    # Оновлено: індекс 2 - мова
    languages = sorted(list(set([w[2] for w in words if w[2] is not None])))
    keyboard = [[types.KeyboardButton(text=l)] for l in languages]
    keyboard.append([types.KeyboardButton(text="Усі мови")])
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.update_data(all_practice_words=words)
    await state.set_state(PracticeWord.waiting_for_language)
    await message.answer("🎯 Оберіть мову для практики (або 'Усі мови'):", reply_markup=lang_kb)


# Вибір мови для практики та генерація списку слів
@dp.message(PracticeWord.waiting_for_language)
async def practice_choose_lang(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    text = message.text.strip()

    if text.lower() == '/exit':
        await cmd_exit(message, state)
        return

    data = await state.get_data()
    all_words = data.get("all_practice_words", [])

    if text == "Усі мови":
        target = all_words
    else:
        target = [w for w in all_words if w[2] == text]

    if not target: await message.answer("Пусто."); return

    random.shuffle(target)
    await state.update_data(plist=target[:10], pidx=0)
    await state.set_state(PracticeWord.waiting_for_answer)
    await send_practice_q(message, target[0])


async def send_practice_q(message, w):
    # w: 0-word, 1-trans, 2-lang, 3-usage, 4-img
    q = f"✏️ Перекладіть: <b>{w[1]}</b> ({w[2]})"
    if w[4]:
        await message.answer_photo(w[4], caption=q, parse_mode="HTML")
    else:
        await message.answer(q, parse_mode="HTML")


# Перевірка відповіді користувача в режимі практики
@dp.message(PracticeWord.waiting_for_answer)
async def process_practice_ans(message: types.Message, state: FSMContext):
    if message.text == "/exit": await cmd_exit(message, state); return
    data = await state.get_data()
    p_list = data['plist']
    idx = data['pidx']

    correct_word = p_list[idx][0]

    if message.text.lower() == correct_word.lower():
        increment_usage_count(message.from_user.id, correct_word)
        await message.answer(f"✅ Правильно! {correct_word}")
    else:
        # 5-assoc, 6-transc
        hint = f"\n💡 {p_list[idx][5]}" if p_list[idx][5] else ""
        tr = f" {p_list[idx][6]}" if p_list[idx][6] else ""
        await message.answer(f"❌ Ні. {correct_word}{tr}{hint}")

    idx += 1
    if idx >= len(p_list):
        await message.answer("🏁 Кінець тренування.", reply_markup=get_main_kb(message.from_user.id))
        await state.clear()
    else:
        await state.update_data(pidx=idx)
        await send_practice_q(message, p_list[idx])


# Початок процесу видалення слова
@dp.message(Command("delete_word"))
async def cmd_delete_word(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    kb = get_main_kb(message.from_user.id)
    await state.set_state(DeleteWord.waiting_for_word)
    await message.answer("🗑️ Введіть слово для видалення (або /exit):", reply_markup=kb)


# Обробка видалення слова
@dp.message(DeleteWord.waiting_for_word)
async def process_delete_word(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    text = message.text.strip()
    user_id = message.from_user.id

    if text.lower() == '/exit':
        await cmd_exit(message, state)
        return

    words_in_db = [w[0] for w in get_user_words(user_id)]

    if text in words_in_db:
        delete_word_from_db(user_id, text)
        await message.answer(f"🗑️ Слово '{text}' видалено.", reply_markup=get_main_kb(user_id))
    else:
        await message.answer(f"❌ Слова '{text}' немає в словнику.", reply_markup=get_main_kb(user_id))


# Початок перегляду всіх слів
@dp.message(Command("all_words"))
async def cmd_all_words(message: types.Message, state: FSMContext):
    update_last_active(message.from_user.id)
    user_id = message.from_user.id
    words = get_user_words(user_id)
    if not words:
        await message.answer("📭 Ваш словник порожній.", reply_markup=get_main_kb(user_id))
        return

    # Оновлено: використовуємо індекс 2 для мови (language)
    languages = sorted(list(set([w[2] for w in words if w[2] is not None])))

    if not languages:
        words_list = "\n".join([f"{w[0]} — {w[1]}" for w in words])
        await message.answer(f"📝 Ваші слова:\n{words_list}", reply_markup=get_main_kb(user_id))
        return

    keyboard = [[types.KeyboardButton(text=l)] for l in languages]
    keyboard.append([types.KeyboardButton(text="Усі мови")])
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(ViewWords.waiting_for_language)
    await message.answer("🌐 Оберіть мову:", reply_markup=lang_kb)


# Відображення слів для вибраної мови
@dp.message(ViewWords.waiting_for_language)
async def process_view_language(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    lang_choice = message.text.strip()

    if lang_choice.lower() == '/exit':
        await cmd_exit(message, state)
        return

    if lang_choice == "Усі мови":
        words = get_user_words(user_id)
    else:
        words = get_user_words(user_id, language=lang_choice)

    if not words:
        await message.answer("📭 Словник порожній.", reply_markup=get_main_kb(user_id))
    else:
        text = f"📝 Слова ({lang_choice}):\n"
        for w in words:
            # w: 0-word, 1-trans, 6-transc
            transc_str = f" {w[6]}" if w[6] else ""
            text += f"{w[0]}{transc_str} — {w[1]}\n"

        if len(text) > 4096:
            await message.answer(f"📝 Слова ({lang_choice}):\n... (занадто багато)", reply_markup=get_main_kb(user_id))
        else:
            await message.answer(text, reply_markup=get_main_kb(user_id))

    await state.clear()


# Початок взаємодії з ШІ
@dp.message(Command("AI"))
async def cmd_ai(message: types.Message, state: FSMContext):
    await state.set_state(AIHelper.waiting_for_prompt)
    await message.answer("🤖 Введіть слово для пояснення:", reply_markup=get_main_kb(message.from_user.id))


# Отримання запиту для ШІ
@dp.message(AIHelper.waiting_for_prompt)
async def process_ai_prompt(message: types.Message, state: FSMContext):
    text = message.text.strip()

    if text.lower() == '/exit':
        await cmd_exit(message, state)
        return

    if text.startswith("/"):
        await message.answer("❌ Будь ласка, спочатку введіть запит для ШІ або натисніть /exit.")
        return

    await state.update_data(prompt=text)

    languages_list = SUPPORTED_LANGUAGES + ["Українська"]
    keyboard = [[types.KeyboardButton(text=l)] for l in languages_list]
    keyboard.append([types.KeyboardButton(text="/exit")])
    lang_kb = types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, one_time_keyboard=True)

    await state.set_state(AIHelper.waiting_for_language)
    await message.answer("🌍 Мова слова?", reply_markup=lang_kb)


# Обробка мови запиту та отримання відповіді від ШІ
@dp.message(AIHelper.waiting_for_language)
async def process_ai_language(message: types.Message, state: FSMContext):
    language_of_word = message.text.strip()

    if language_of_word.lower() == '/exit':
        await cmd_exit(message, state)
        return

    data = await state.get_data()
    prompt = data.get("prompt")

    await message.answer("🤖 Оброблюю...", reply_markup=get_main_kb(message.from_user.id))

    try:
        txt, img = await asyncio.gather(
            get_ai_explanation_text(prompt, language_of_word),
            get_image_url(prompt)
        )

        # Зберігаємо запит для регенерації
        await state.update_data(img_query=prompt)

        # Кнопка регенерації
        inline_regen = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Інше фото", callback_data="regen:ai")]
        ])

        if img:
            await message.answer_photo(photo=img, caption=f"🤖 Ось пояснення:\n\n{txt}"[:1024],
                                       reply_markup=inline_regen)
        else:
            await message.answer(f"🤖 Ось пояснення:\n\n{txt}", reply_markup=inline_regen)

    except Exception as e:
        await message.answer(f"{str(e)}", reply_markup=get_main_kb(message.from_user.id))

    await state.set_state(AIHelper.waiting_for_prompt)
    await message.answer("🤖 Ще слово? (або /exit)", reply_markup=get_main_kb(message.from_user.id))


# Обробник невідомих команд або тексту
@dp.message()
async def unknown_command(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await message.answer(
            "❌ Незрозуміла відповідь. Будь ласка, дотримуйтесь інструкцій або натисніть /exit, щоб вийти з поточного режиму.")
        return

    await message.answer("❌ Невідома команда.\n" + COMMANDS_TEXT, reply_markup=get_main_kb(message.from_user.id))


# Запуск бота
async def main():
    print("Бота запущено")
    dp.message.middleware(ThrottlingMiddleware(throttle_time=1))
    asyncio.create_task(start_web_server())
    asyncio.create_task(keep_alive_task())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

if __name__ == "__main__":
    asyncio.run(main())

