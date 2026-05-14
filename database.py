import asyncpg
import aiosqlite
import asyncio
import logging
from datetime import datetime, timedelta
from config import SERVER_IP, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT

logger = logging.getLogger(__name__)

db_pool = None
sqlite_conn = None
FORCE_SQLITE = False


def set_db_mode(force_sqlite: bool):
    global FORCE_SQLITE
    FORCE_SQLITE = force_sqlite


# АДАПТЕР
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


# ІНІЦІАЛІЗАЦІЯ
async def init_connection():
    global db_pool, sqlite_conn, FORCE_SQLITE
    from config import NEON_URL

    if FORCE_SQLITE:
        logger.info("🎒 [DB] Примусовий АВТОНОМНИЙ режим (SQLite).")
        sqlite_conn = await aiosqlite.connect('backup_words.db')
        sqlite_conn.row_factory = aiosqlite.Row
        return

    logger.info("🔍 [DB] Стукаємо на ДОМАШНІЙ СЕРВЕР...")
    try:
        # Таймед для домашнього сервака
        db_pool = await asyncio.wait_for(
            asyncpg.create_pool(user=DB_USER, password=DB_PASSWORD, database=DB_NAME, host=SERVER_IP, port=DB_PORT),
            timeout=3.0
        )
        logger.info("✅ [DB] Підключено до ДОМАШНЬОГО СЕРВЕРА!")
        return
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        logger.warning("⚠️ [DB] Домашній сервер мовчить.")

    if NEON_URL and "лінк" not in NEON_URL:
        logger.info("☁️ [DB] Перемикання на хмару NEON...")
        try:
            db_pool = await asyncio.wait_for(
                asyncpg.create_pool(dsn=NEON_URL),
                timeout=60.0
            )
            logger.info("✅ [DB] Підключено до хмари NEON!")
            return
        except Exception as e:
            logger.error(f"⚠️ [DB] Neon недоступний: {e}")

    logger.warning("🎒 [DB] Перехід в режим виживання (Локальна SQLite)...")
    FORCE_SQLITE = True

    sqlite_conn = await aiosqlite.connect('backup_words.db')
    sqlite_conn.row_factory = aiosqlite.Row
    logger.info("✅ [DB] Локальна база створена/підключена.")

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

    await db_execute('''
        CREATE INDEX IF NOT EXISTS idx_user_words_user_id 
        ON user_words (user_id)
    ''')

    async def add_column_if_not_exists(table, col, dtype):
        try:
            await db_execute(f"ALTER TABLE {table} ADD COLUMN {col} {dtype}")
        except Exception as e:
            err_msg = str(e).lower()
            if "duplicate column name" not in err_msg and "already exists" not in err_msg:
                logger.error(f"Migration error on {table}.{col}: {e}")

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
        ("target_lang",    "TEXT DEFAULT 'English'"),
        ("last_premium_ai_use", "TEXT"), 
    ]
    for col, dtype in columns_users:
        await add_column_if_not_exists("users", col, dtype)

    columns_words = [
        ("language", "TEXT"), ("usage_count", "INTEGER DEFAULT 0"), ("image_url", "TEXT"),
        ("association", "TEXT"), ("transcription", "TEXT"), ("next_review_date", "TEXT"),
        ("interval", "INTEGER DEFAULT 1"), ("ease_factor", "FLOAT DEFAULT 2.5")
    ]
    for col, dtype in columns_words:
        await add_column_if_not_exists("user_words", col, dtype)

# ФУНКЦІЇ ДОСТУПУ 
async def add_user(user_id, username):
    try:
        await db_execute("INSERT INTO users (user_id, username, start_date, last_active, interface_lang) VALUES ($1, $2, $3, $4, 'uk')",
                         user_id, username, datetime.now().isoformat(), datetime.now().isoformat())
    except:
        pass


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
 

async def update_streak(user_id):
    row = await db_fetchrow(
        "SELECT last_active, streak_days FROM users WHERE user_id=$1", user_id
    )
    if not row:
        return 1, False

    today     = datetime.now().date()
    streak    = row['streak_days'] or 0
    last_raw  = row['last_active']

    if last_raw:
        try:
            last_date = datetime.fromisoformat(last_raw).date()
        except Exception:
            last_date = None
    else:
        last_date = None

    if last_date == today:
        return streak, False

    if last_date == today - timedelta(days=1):
        streak += 1
    else:
        streak = 1

    await db_execute(
        "UPDATE users SET streak_days=$1, last_active=$2 WHERE user_id=$3",
        streak, datetime.now().isoformat(), user_id
    )
    return streak, True


async def update_word_progress_sm2(user_id, word, quality: int):
    """
      5 — ідеальна відповідь
      4 — правильна з невеликим зусиллям
      3 — правильна але важко
      2 — неправильна, але відповідь була близькою
      1 — неправильна, пам'ятав але не згадав
      0 — повний провал
    """
    res = await db_fetchrow(
        "SELECT interval, ease_factor FROM user_words WHERE user_id=$1 AND word=$2",
        user_id, word
    )
    if not res:
        return

    interval    = res['interval']    or 1
    ease_factor = res['ease_factor'] or 2.5

    if quality >= 3:
        # Правильна відповідь — SuperMemo-2 формула
        if interval == 0:
            interval = 1
        elif interval == 1:
            interval = 6
        else:
            interval = round(interval * ease_factor)

        ease_factor = max(1.3, round(
            ease_factor + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02), 2
        ))
        usage_add = 1
    else:
        interval    = 1
        ease_factor = max(1.3, round(ease_factor - 0.2, 2))
        usage_add   = 0

    next_date = (datetime.now() + timedelta(days=interval)).isoformat()
    await db_execute(
        """UPDATE user_words
           SET usage_count=usage_count+$1, interval=$2, ease_factor=$3, next_review_date=$4
           WHERE user_id=$5 AND word=$6""",
        usage_add, interval, ease_factor, next_date, user_id, word
    )


async def get_weak_words(user_id, limit=10):
    return await db_fetch(
        """SELECT word, translation, language, ease_factor, usage_count
           FROM user_words
           WHERE user_id=$1
           ORDER BY ease_factor ASC, usage_count ASC
           LIMIT $2""",
        user_id, limit
    )


async def get_word_stats(user_id):
    total  = await db_fetchval("SELECT COUNT(*) FROM user_words WHERE user_id=$1", user_id)
    due    = await db_fetchval(
        "SELECT COUNT(*) FROM user_words WHERE user_id=$1 AND (next_review_date IS NULL OR next_review_date <= $2)",
        user_id, datetime.now().isoformat()
    )
    mastered = await db_fetchval(
        "SELECT COUNT(*) FROM user_words WHERE user_id=$1 AND ease_factor >= 2.8 AND interval >= 21",
        user_id
    )
    return {
        "total":    total    or 0,
        "due":      due      or 0,
        "mastered": mastered or 0,
    }


async def get_user_full_profile(user_id):
    return await db_fetchrow(
        "SELECT learning_style, hobbies, best_score, level, streak_days, last_active FROM users WHERE user_id=$1",
        user_id
    )


async def update_user_level(user_id, total_xp):
    if total_xp < 50:
        level = "A1"
    elif total_xp < 150:
        level = "A2"
    elif total_xp < 350:
        level = "B1"
    elif total_xp < 700:
        level = "B2"
    else:
        level = "C1"
    await db_execute("UPDATE users SET level=$1 WHERE user_id=$2", level, user_id)
    return level

async def get_target_lang(user_id):
    val = await db_fetchval(
        "SELECT target_lang FROM users WHERE user_id=$1", user_id
    )
    return val or "English"


async def set_target_lang(user_id, lang: str):
    await db_execute(
        "UPDATE users SET target_lang=$1 WHERE user_id=$2", lang, user_id
    )


async def get_user_lang(user_id):
    return await db_fetchval(
        "SELECT interface_lang FROM users WHERE user_id=$1", user_id
    )


async def set_user_lang(user_id, lang: str):
    await db_execute(
        "UPDATE users SET interface_lang=$1 WHERE user_id=$2", lang, user_id
    )


async def get_user_full_profile(user_id):
    return await db_fetchrow(
        "SELECT learning_style, hobbies, best_score, level, streak_days, last_active FROM users WHERE user_id=$1",
        user_id
    )


async def get_word_stats(user_id):
    total = await db_fetchval("SELECT COUNT(*) FROM user_words WHERE user_id=$1", user_id)
    due   = await db_fetchval(
        "SELECT COUNT(*) FROM user_words WHERE user_id=$1 AND (next_review_date IS NULL OR next_review_date <= $2)",
        user_id, datetime.now().isoformat()
    )
    mastered = await db_fetchval(
        "SELECT COUNT(*) FROM user_words WHERE user_id=$1 AND ease_factor >= 2.8 AND interval >= 21",
        user_id
    )
    return {"total": total or 0, "due": due or 0, "mastered": mastered or 0}


async def get_weak_words(user_id, limit=10):
    return await db_fetch(
        """SELECT word, translation, language, ease_factor, usage_count
           FROM user_words WHERE user_id=$1
           ORDER BY ease_factor ASC, usage_count ASC LIMIT $2""",
        user_id, limit
    )


async def update_streak(user_id):
    row = await db_fetchrow(
        "SELECT last_active, streak_days FROM users WHERE user_id=$1", user_id
    )
    if not row:
        return 1, False
    today    = datetime.now().date()
    streak   = row['streak_days'] or 0
    last_raw = row['last_active']
    try:
        last_date = datetime.fromisoformat(last_raw).date() if last_raw else None
    except Exception:
        last_date = None
    if last_date == today:
        return streak, False
    streak = streak + 1 if last_date == today - timedelta(days=1) else 1
    await db_execute(
        "UPDATE users SET streak_days=$1, last_active=$2 WHERE user_id=$3",
        streak, datetime.now().isoformat(), user_id
    )
    return streak, True


async def update_word_progress_sm2(user_id, word, quality: int):
    res = await db_fetchrow(
        "SELECT interval, ease_factor FROM user_words WHERE user_id=$1 AND word=$2",
        user_id, word
    )
    if not res:
        return
    interval    = res['interval']    or 1
    ease_factor = res['ease_factor'] or 2.5
    if quality >= 3:
        interval    = 1 if interval == 0 else (6 if interval == 1 else round(interval * ease_factor))
        ease_factor = max(1.3, round(ease_factor + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02), 2))
        usage_add   = 1
    else:
        interval    = 1
        ease_factor = max(1.3, round(ease_factor - 0.2, 2))
        usage_add   = 0
    next_date = (datetime.now() + timedelta(days=interval)).isoformat()
    await db_execute(
        "UPDATE user_words SET usage_count=usage_count+$1, interval=$2, ease_factor=$3, next_review_date=$4 WHERE user_id=$5 AND word=$6",
        usage_add, interval, ease_factor, next_date, user_id, word
    )


async def update_user_level(user_id, total_xp):
    level = ("A1" if total_xp < 50 else "A2" if total_xp < 150 else
             "B1" if total_xp < 350 else "B2" if total_xp < 700 else "C1")
    await db_execute("UPDATE users SET level=$1 WHERE user_id=$2", level, user_id)
    return level


async def can_use_premium_ai(user_id: int) -> bool: #Перевіряє, чи не використовував юзер преміум ШІ сьогодні.
    row = await db_fetchrow("SELECT last_premium_ai_use FROM users WHERE user_id=$1", user_id)
    if not row or not row['last_premium_ai_use']:
        return True
    try:
        last_use = datetime.fromisoformat(row['last_premium_ai_use']).date()
        return last_use < datetime.now().date()
    except Exception:
        return True

async def update_premium_ai_usage(user_id: int): #Фіксує сьогоднішню дату використання преміум ШІ.
    await db_execute(
        "UPDATE users SET last_premium_ai_use=$1 WHERE user_id=$2",
        datetime.now().isoformat(), user_id
    )

async def reset_premium_ai_usage(user_id: int): #Скидає ліміт (для адмін-команди).
    await db_execute("UPDATE users SET last_premium_ai_use=NULL WHERE user_id=$1", user_id)