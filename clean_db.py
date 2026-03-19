import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()
NEON_URL = os.getenv("NEON_URL")


async def get_conn():
    if not NEON_URL or "тут_буде_твій_лінк" in NEON_URL:
        print("❌ NEON_URL не знайдено в .env")
        exit(1)
    return await asyncpg.connect(dsn=NEON_URL)


async def show_stats(conn):
    tables = ["users", "user_words", "feedback"]
    print("\n📊 Поточний стан БД (Neon):")
    for table in tables:
        try:
            count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
            print(f"   {table}: {count} записів")
        except Exception:
            print(f"   {table}: таблиця не існує")
    print()


async def clean(choice: str, conn):
    if choice == "1":
        confirm = input("   ⚠️ Видалити ВСІ слова? (y/n): ").strip().lower()
        if confirm != "y": return print("Скасовано.")
        await conn.execute("DELETE FROM user_words")
        print("✅ Всі слова видалено.")

    elif choice == "2":
        confirm = input("   ⚠️ Видалити всі відгуки? (y/n): ").strip().lower()
        if confirm != "y": return print("Скасовано.")
        await conn.execute("DELETE FROM feedback")
        print("✅ Відгуки видалено.")

    elif choice == "3":
        confirm = input("   ⚠️ Видалити ВСІХ користувачів + слова? (y/n): ").strip().lower()
        if confirm != "y": return print("Скасовано.")
        await conn.execute("DELETE FROM user_words")
        await conn.execute("DELETE FROM feedback")
        await conn.execute("DELETE FROM users")
        print("✅ Всі дані видалено.")

    elif choice == "4":
        uid = input("   Введи user_id: ").strip()
        if not uid.isdigit(): return print("❌ Невірний user_id")
        await conn.execute("DELETE FROM user_words WHERE user_id=$1", int(uid))
        await conn.execute("DELETE FROM users WHERE user_id=$1", int(uid))
        print(f"✅ Користувача {uid} видалено.")

    elif choice == "5":
        confirm = input("   ⚠️ Скинути прогрес тренувань? (y/n): ").strip().lower()
        if confirm != "y": return print("Скасовано.")
        await conn.execute(
            "UPDATE user_words SET usage_count=0, interval=1, ease_factor=2.5, next_review_date=NULL"
        )
        print("✅ Прогрес скинуто. Слова збережено.")

    elif choice == "6":
        uid = input("   Введи user_id щоб переглянути його слова: ").strip()
        if not uid.isdigit(): return print("❌ Невірний user_id")
        rows = await conn.fetch(
            "SELECT word, translation, language, usage_count FROM user_words WHERE user_id=$1 ORDER BY language",
            int(uid)
        )
        if not rows:
            print("   Слів не знайдено.")
        else:
            print(f"\n   📝 Слова користувача {uid}:")
            for r in rows:
                print(f"   [{r['language']}] {r['word']} — {r['translation']} (використань: {r['usage_count']})")
        print()

    else:
        print("❌ Невірний вибір")


async def main():
    print("🔌 Підключення до Neon...")
    try:
        conn = await asyncio.wait_for(get_conn(), timeout=5.0)
        print("✅ Підключено!\n")
    except Exception as e:
        print(f"❌ Не вдалося підключитися: {e}")
        return

    try:
        while True:
            await show_stats(conn)
            print("Що зробити?")
            print("  1 — Видалити всі слова")
            print("  2 — Видалити всі відгуки")
            print("  3 — Видалити всіх користувачів повністю")
            print("  4 — Видалити одного конкретного користувача")
            print("  5 — Скинути прогрес тренувань (слова залишаться)")
            print("  6 — Переглянути слова конкретного користувача")
            print("  0 — Вийти")
            print()

            choice = input("Вибір: ").strip()
            if choice == "0":
                print("👋 Вихід.")
                break
            await clean(choice, conn)
            print()
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
