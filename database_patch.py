async def get_user_lang(user_id: int) -> str | None:
    """Отримати збережену мову інтерфейсу."""
    return await db_fetchval(
        "SELECT interface_lang FROM users WHERE user_id=$1", user_id
    )


async def set_user_lang(user_id: int, lang: str) -> None:
    """Зберегти мову інтерфейсу користувача."""
    await db_execute(
        "UPDATE users SET interface_lang=$1 WHERE user_id=$2", lang, user_id
    )


# ───────────────────────────────────────────────────
# КРОК 3 — В функції update_user_level() (якщо є), нічого не міняти.
#
# Якщо її немає — додай:

async def update_user_level(user_id: int, total_xp: int) -> None:
    """Оновити рівень користувача A1–C1."""
    if total_xp < 50:
        level = "A1"
    elif total_xp < 120:
        level = "A2"
    elif total_xp < 250:
        level = "B1"
    elif total_xp < 450:
        level = "B2"
    else:
        level = "C1"
    await db_execute("UPDATE users SET level=$1 WHERE user_id=$2", level, user_id)

