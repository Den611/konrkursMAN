from aiogram import Router, types, F
from aiogram.filters import Command
import database as db
import i18n
from keyboards import get_main_kb
from utils import ulang, get_user_level_info
 
router = Router()

@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    uid  = message.from_user.id
    lang = ulang(uid)
    profile   = await db.get_user_full_profile(uid)
    word_stat = await db.get_word_stats(uid)
    lvl, cur_xp, next_xp = await get_user_level_info(uid)
    raw_style = profile['learning_style'] if profile and profile['learning_style'] else None
    style     = i18n.get_style_display(lang, raw_style) if raw_style else i18n.t(lang, "stats.no_style")
    hobby     = profile['hobbies']     if profile and profile['hobbies']     else i18n.t(lang, "stats.no_hobby")
    level_db  = profile['level']       if profile and profile['level']       else "A1"
    streak    = profile['streak_days'] if profile and profile['streak_days'] else 0
    best      = profile['best_score']  if profile and profile['best_score']  else 0
    target    = await db.get_target_lang(uid)
    pct       = cur_xp / max(1, next_xp)
    bar       = "🟩" * int(pct * 10) + "⬜" * (10 - int(pct * 10))
    streak_line = f"\n{'🔥' * min(streak // 3 + 1, 5)} {i18n.t(lang, 'stats.streak')} <b>{streak}</b>" if streak >= 1 else ""
    pts = i18n.t(lang, "stats.points")
    await message.answer(
        f"{i18n.t(lang, 'stats.title')}\n"
        f"{i18n.t(lang, 'stats.style_label')} <b>{style}</b>\n"
        f"{i18n.t(lang, 'stats.hobby_label')} <b>{hobby}</b>\n"
        f"{i18n.t(lang, 'stats.studying')} <b>{target}</b>\n\n"
        f"{i18n.t(lang, 'stats.lang_level')} <b>{level_db}</b>\n"
        f"{i18n.t(lang, 'stats.level_label')} <b>{lvl}</b>\n"
        f"{i18n.t(lang, 'stats.xp_label')} {cur_xp}/{next_xp}\n"
        f"[{bar}]{streak_line}\n\n"
        f"{i18n.t(lang, 'stats.words_label')} <b>{word_stat['total']}</b>\n"
        f"{i18n.t(lang, 'stats.words_due')} <b>{word_stat['due']}</b>\n"
        f"{i18n.t(lang, 'stats.words_mastered')} <b>{word_stat['mastered']}</b>\n\n"
        f"{i18n.t(lang, 'stats.record_label')} <b>{best}</b>{pts}",
        parse_mode="HTML", reply_markup=await get_main_kb(uid)
    )
 
@router.message(Command("top"))
@router.message(F.text.in_(["🏆 ТОП Лідери", "🏆 TOP Gracze", "🏆 TOP Players"]))
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
    await message.answer(text, parse_mode="HTML", reply_markup=await get_main_kb(uid))
 
@router.message(Command("weak"))
async def cmd_weak(message: types.Message):
    uid   = message.from_user.id
    lang  = ulang(uid)
    words = await db.get_weak_words(uid, limit=10)
    if not words:
        return await message.answer(i18n.t(lang, "weak.empty"),
                                    reply_markup=await get_main_kb(uid))
    text = f"🔴 <b>{i18n.t(lang, 'weak.title')}</b>\n\n"
    for w in words:
        filled = min(int((w['ease_factor'] - 1.3) / 0.3), 5)
        bar    = "🟥" * max(1, filled) + "⬜" * (5 - max(1, filled))
        text  += (f"<b>{w['word']}</b> — {w['translation']} [{w['language']}]\n"
                  f"   EF: {w['ease_factor']:.2f} {bar} | "
                  f"{i18n.t(lang, 'weak.repetitions')}: {w['usage_count']}\n\n")
    text += f"<i>{i18n.t(lang, 'weak.footer')}</i>"
    await message.answer(text, parse_mode="HTML", reply_markup=await get_main_kb(uid))
