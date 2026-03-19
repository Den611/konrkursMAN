import asyncpg
import aiosqlite
import asyncio
from datetime import datetime, timedelta
from config import SERVER_IP, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT

# Глобальні змінні для баз даних
db_pool = None
sqlite_conn = None
FORCE_SQLITE = False


def set_db_mode(force_sqlite: bool):
    global FORCE_SQLITE
    FORCE_SQLITE = force_sqlite


# --- УНІВЕРСАЛЬНИЙ АДАПТЕР (PostgreSQL <-> SQLite) ---
async def db_execute(query, *args):
    if FORCE_SQLITE:
        q = query.replace("SERIAL", "INTEGER PRIMARY KEY AUTOINCREMENT").replace("BIGINT", "INTEGER")
        for i in range(10, 0, -1): q = q.replace(f"${i}", "?")
        await sqlite_conn.execute(q, args)
        await sqlite_conn.commit()
    else:
        async with db_pool.acquire() as conn:
            await conn.execute(query, *args)


async def db_fetchval(query, *args):
    if FORCE_SQLITE:
        q = query
        for i in range(10, 0, -1): q = q.replace(f"${i}", "?")
        async with sqlite_conn.execute(q, args) as cursor:
            res = await cursor.fetchone()
            return res[0] if res else None
    else:
        async with db_pool.acquire() as conn:
            return await conn.fetchval(query, *args)


async def db_fetchrow(query, *args):
    if FORCE_SQLITE:
        q = query
        for i in range(10, 0, -1): q = q.replace(f"${i}", "?")
        async with sqlite_conn.execute(q, args) as cursor:
            res = await cursor.fetchone()
            return dict(res) if res else None
    else:
        async with db_pool.acquire() as conn:
            return await conn.fetchrow(query, *args)


async def db_fetch(query, *args):
    if FORCE_SQLITE:
        q = query
        for i in range(10, 0, -1): q = q.replace(f"${i}", "?")
        async with sqlite_conn.execute(q, args) as cursor:
            res = await cursor.fetchall()
            return [dict(row) for row in res]
    else:
        async with db_pool.acquire() as conn:
            return await conn.fetch(query, *args)


# --- ІНІЦІАЛІЗАЦІЯ ---
async def init_connection():
    global db_pool, sqlite_conn, FORCE_SQLITE
    from config import NEON_URL

    if FORCE_SQLITE:
        print("🎒 [DB] Примусовий АВТОНОМНИЙ режим (SQLite).")
        sqlite_conn = await aiosqlite.connect('backup_words.db')
        sqlite_conn.row_factory = aiosqlite.Row
        return

    print("🔍 [DB] Стукаємо на ДОМАШНІЙ СЕРВЕР...")
    try:
        # Даємо домашньому серверу 3 секунди на відповідь
        db_pool = await asyncio.wait_for(
            asyncpg.create_pool(user=DB_USER, password=DB_PASSWORD, database=DB_NAME, host=SERVER_IP, port=DB_PORT),
            timeout=3.0
        )
        print("✅ [DB] Підключено до ДОМАШНЬОГО СЕРВЕРА!")
        return
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        print("⚠️ [DB] Домашній сервер мовчить.")

    if NEON_URL and "тут_буде_твій_лінк" not in NEON_URL:
        print("☁️ [DB] Перемикання на хмару NEON...")
        try:
            db_pool = await asyncio.wait_for(
                asyncpg.create_pool(dsn=NEON_URL),
                timeout=5.0
            )
            print("✅ [DB] Підключено до хмари NEON!")
            return
        except Exception as e:
            print(f"⚠️ [DB] Neon недоступний: {e}")

    print("🎒 [DB] Перехід в режим виживання (Локальна SQLite)...")
    FORCE_SQLITE = True

    sqlite_conn = await aiosqlite.connect('backup_words.db')
    sqlite_conn.row_factory = aiosqlite.Row
    print("✅ [DB] Локальна база створена/підключена.")

async def init_db():
    await db_execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY, username TEXT, start_date TEXT, last_active TEXT,
            best_score INTEGER DEFAULT 0, level TEXT DEFAULT 'A1', hobbies TEXT, 
            learning_style TEXT DEFAULT 'Універсал', streak_days INTEGER DEFAULT 0,
            interface_lang TEXT DEFAULT 'uk'
        )
    ''')
    await db_execute('''
        CREATE TABLE IF NOT EXISTS user_words (
            id SERIAL, user_id BIGINT, word TEXT, translation TEXT, language TEXT, usage_count INTEGER DEFAULT 0,
            image_url TEXT, association TEXT, transcription TEXT, next_review_date TEXT, interval INTEGER DEFAULT 1, ease_factor FLOAT DEFAULT 2.5
        )
    ''')
    await db_execute('''
        CREATE TABLE IF NOT EXISTS feedback (
            id SERIAL, user_id BIGINT, username TEXT, message TEXT, date TEXT
        )
    ''')

    # Міграції
    columns_users = [
        ("start_date", "TEXT"), 
        ("last_active", "TEXT"), 
        ("best_score", "INTEGER DEFAULT 0"),
        ("level", "TEXT DEFAULT 'A1'"), 
        ("hobbies", "TEXT"), 
        ("learning_style", "TEXT DEFAULT 'Універсал'"),
        ("streak_days", "INTEGER DEFAULT 0"),
        ("interface_lang", "TEXT DEFAULT 'uk'"), 
    ]
    for col, dtype in columns_users:
        try:
            await db_execute(f"ALTER TABLE users ADD COLUMN {col} {dtype}")
        except:
            pass

    columns_words = [
        ("language", "TEXT"), ("usage_count", "INTEGER DEFAULT 0"), ("image_url", "TEXT"),
        ("association", "TEXT"), ("transcription", "TEXT"), ("next_review_date", "TEXT"),
        ("interval", "INTEGER DEFAULT 1"), ("ease_factor", "FLOAT DEFAULT 2.5")
    ]
    for col, dtype in columns_words:
        try:
            await db_execute(f"ALTER TABLE user_words ADD COLUMN {col} {dtype}")
        except:
            pass


# --- ФУНКЦІЇ ДОСТУПУ ---
async def add_user(user_id, username):
    try:
        await db_execute("INSERT INTO users (user_id, username, start_date, last_active, interface_lang) VALUES ($1, $2, $3, $4, 'uk')",
                         user_id, username, datetime.now().isoformat(), datetime.now().isoformat())
    except:
        pass  # Ігноруємо якщо вже є


async def update_user_profile(user_id, style, hobby):
    await db_execute("UPDATE users SET learning_style=$1, hobbies=$2 WHERE user_id=$3", style, hobby, user_id)


async def get_user_profile_data(user_id):
    return await db_fetchrow("SELECT learning_style, hobbies FROM users WHERE user_id=$1", user_id)


async def get_top_users(limit=10):
    return await db_fetch(
        "SELECT username, best_score FROM users WHERE best_score > 0 ORDER BY best_score DESC LIMIT $1", limit)


async def update_last_active(user_id):
    await db_execute("UPDATE users SET last_active=$1 WHERE user_id=$2", datetime.now().isoformat(), user_id)


async def get_user_hobby(user_id):
    return await db_fetchval("SELECT hobbies FROM users WHERE user_id=$1", user_id)

async def get_user_lang(user_id):
    res = await db_fetchval("SELECT interface_lang FROM users WHERE user_id=$1", user_id)
    return res if res else "uk"

async def update_user_lang(user_id, lang):
    await db_execute("UPDATE users SET interface_lang=$1 WHERE user_id=$2", lang, user_id)

async def update_user_level(user_id, xp):
    # Допоміжна функція для оновлення рівня, якщо потрібна буде
    pass

async def add_word_to_db(user_id, word, translation, language, image_url=None, association=None, transcription=None):
    exists = await db_fetchval("SELECT 1 FROM user_words WHERE user_id=$1 AND word=$2 AND language=$3", user_id, word,
                               language)
    if exists:
        if image_url:
            await db_execute(
                "UPDATE user_words SET image_url=$1, association=$2, transcription=$3 WHERE user_id=$4 AND word=$5 AND language=$6",
                image_url, association, transcription, user_id, word, language)
        return False
    next_date = (datetime.now() + timedelta(days=1)).isoformat()
    await db_execute("""
        INSERT INTO user_words (user_id, word, translation, language, usage_count, image_url, association, transcription, next_review_date, interval, ease_factor) 
        VALUES ($1, $2, $3, $4, 0, $5, $6, $7, $8, 1, 2.5)""",
                     user_id, word, translation, language, image_url, association, transcription, next_date)
    return True


async def update_word_progress(user_id, word, is_correct):
    res = await db_fetchrow("SELECT interval, ease_factor FROM user_words WHERE user_id=$1 AND word=$2", user_id, word)
    if not res: return
    interval, ease_factor = res['interval'] or 1, res['ease_factor'] or 2.5

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
    usage_add = 1 if is_correct else 0
    await db_execute(
        "UPDATE user_words SET usage_count=usage_count+$1, interval=$2, ease_factor=$3, next_review_date=$4 WHERE user_id=$5 AND word=$6",
        usage_add, interval, ease_factor, next_date, user_id, word)


async def get_user_words(user_id, language=None, for_review=False):
    query = "SELECT * FROM user_words WHERE user_id=$1"
    params = [user_id]
    if language and language != "Усі мови":
        query += " AND language=$2"
        params.append(language)
    if for_review:
        query += f" AND (next_review_date IS NULL OR next_review_date <= ${len(params) + 1})"
        params.append(datetime.now().isoformat())
    return await db_fetch(query, *params)


async def delete_word_from_db(user_id, word):
    await db_execute("DELETE FROM user_words WHERE user_id=$1 AND word=$2", user_id, word)


async def save_feedback(user_id, username, text):
    await db_execute("INSERT INTO feedback (user_id, username, message, date) VALUES ($1, $2, $3, $4)", user_id,
                     username, text, datetime.now().isoformat())


async def get_best_score(user_id):
    return await db_fetchval("SELECT best_score FROM users WHERE user_id=$1", user_id) or 0


async def update_best_score(user_id, score):
    await db_execute("UPDATE users SET best_score=$1 WHERE user_id=$2", score, user_id)


async def get_user_lang(user_id):
    return await db_fetchval(
        "SELECT interface_lang FROM users WHERE user_id=$1", user_id
    )
 
 
async def set_user_lang(user_id, lang: str):
    await db_execute(
        "UPDATE users SET interface_lang=$1 WHERE user_id=$2", lang, user_id
    )
 