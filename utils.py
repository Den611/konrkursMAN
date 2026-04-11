from aiogram import types
import urllib.parse
import json
import i18n
import database as db
from config import WEB_APP_URL

SUPPORTED_LANGUAGES = ["English", "German", "French", "Polish", "Spanish", "Italian", "Ukrainian"]
LANG_TO_TRANSLATOR  = {"uk": "uk", "pl": "pl", "en": "en"}
LANG_TO_NAME        = {"uk": "Ukrainian", "pl": "Polish", "en": "English"}
LANG_TO_EXCLUDE     = {"uk": None, "pl": "Polish", "en": "English"}
 
def ul(user_id: int, key: str, **kwargs) -> str:
    return i18n.t(i18n.get_user_lang(user_id), key, **kwargs)
 
def ulang(user_id: int) -> str:
    return i18n.get_user_lang(user_id)
 
def trans_lang(user_id: int) -> str:
    return LANG_TO_TRANSLATOR.get(ulang(user_id), "uk")
 
def ai_lang_name(user_id: int) -> str:
    return LANG_TO_NAME.get(ulang(user_id), "Ukrainian")
 
def study_langs(user_id: int) -> list:
    exclude = LANG_TO_EXCLUDE.get(ulang(user_id))
    return [l for l in SUPPORTED_LANGUAGES if l != exclude]

async def get_user_level_info(user_id: int):
    words    = await db.get_user_words(user_id)
    total_xp = sum(w['usage_count'] for w in words)
    level, xp_needed = 1, 10
    while total_xp >= xp_needed:
        total_xp  -= xp_needed
        level     += 1
        xp_needed += 10
    return level, total_xp, xp_needed
 
async def _update_progress(user_id: int):
    words    = await db.get_user_words(user_id)
    total_xp = sum(w['usage_count'] for w in words)
    level    = await db.update_user_level(user_id, total_xp)
    streak, is_new_day = await db.update_streak(user_id)
    return level, streak, is_new_day
 
def _build_keyword_str(lang: str, selected_ids: list, custom: str = None) -> str:
    cats_data = i18n.get_list(lang, "hobby_categories")
    parts = [c["keywords"] for c in cats_data if c["id"] in selected_ids and c["keywords"]]
    if custom:
        parts.append(custom)
    return ", ".join(parts) if parts else "повсякденне життя"

def _find_cat_by_label(lang: str, text: str) -> dict | None:
    clean = text.replace("✅ ", "").strip()
    cats  = i18n.get_list(lang, "hobby_categories")
    return next((c for c in cats if c["label"] == clean), None)
