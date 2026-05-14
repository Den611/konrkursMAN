import json
import os
from functools import lru_cache

LOCALES_DIR = os.path.join(os.path.dirname(__file__), "locales")

# Доступні мови інтерфейсу
SUPPORTED_UI_LANGS: dict[str, tuple[str, str]] = {
    "uk": ("Українська", "🇺🇦"),
    "pl": ("Polski",     "🇵🇱"),
    "en": ("English",    "🇬🇧"),
}

DEFAULT_LANG = "uk"

# Кеш user_id
_user_lang_cache: dict[int, str] = {}


#  Завантаження JSON 

@lru_cache(maxsize=len(SUPPORTED_UI_LANGS) + 1)
def _load_locale(lang: str) -> dict:
    """Завантажити і кешувати JSON-файл локалі."""
    path = os.path.join(LOCALES_DIR, f"{lang}.json")
    if not os.path.exists(path):
        # Fallback на дефолтну мову
        path = os.path.join(LOCALES_DIR, f"{DEFAULT_LANG}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# Основна функція перекладу

def t(locale: str, key: str, **kwargs) -> str:
    data = _load_locale(locale)
    parts = key.split(".")
    val: object = data
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part)
        else:
            val = None
        if val is None:
            break

    if not isinstance(val, str):
        if locale != DEFAULT_LANG:
            return t(DEFAULT_LANG, key, **kwargs)
        return key 
    return val.format(**kwargs) if kwargs else val


def get_list(lang: str, key: str) -> list:
    """Повернути список за ключем"""
    data = _load_locale(lang)
    parts = key.split(".")
    val: object = data
    for part in parts:
        val = val.get(part) if isinstance(val, dict) else None
    return val if isinstance(val, list) else []


def get_dict(lang: str, key: str) -> dict:
    """Повернути словник за ключем"""
    data = _load_locale(lang)
    parts = key.split(".")
    val: object = data
    for part in parts:
        val = val.get(part) if isinstance(val, dict) else None
    return val if isinstance(val, dict) else {}


# Кеш мов 
def set_user_lang(user_id: int, lang: str) -> None:
    """Зберегти мову користувача в пам'яті."""
    if lang in SUPPORTED_UI_LANGS:
        _user_lang_cache[user_id] = lang


def get_user_lang(user_id: int) -> str:
    """Отримати мову користувача (дефолт: 'uk')."""
    return _user_lang_cache.get(user_id, DEFAULT_LANG)


def invalidate_user(user_id: int) -> None:
    """Скинути кеш для конкретного користувача (після зміни мови)."""
    _user_lang_cache.pop(user_id, None)



def get_style_display(lang: str, style_raw: str) -> str:
    """
    Повернути локалізовану назву стилю навчання.
    """
    legacy_map = {
        "Візуал":  "visual",
        "Аудіал":  "audial",
        "Логік":   "logic",
        "Практик": "practice",
        "Wzrokowiec": "visual",
        "Słuchowiec": "audial",
        "Logik":      "logic",
        "Praktyk":    "practice",
        "Visual":     "visual",
        "Auditory":   "audial",
        "Logical":    "logic",
        "Practical":  "practice",
    }
    internal_key = legacy_map.get(style_raw, style_raw)
    result = t(lang, f"style.{internal_key}.name")
    return result if result != f"style.{internal_key}.name" else style_raw


STYLE_ORDER = ["visual", "audial", "logic", "practice"]


def score_answer(text: str, options: list[str]) -> str | None:
    """
    Визначити внутрішній ключ стилю за текстом відповіді.
    """
    for i, opt in enumerate(options):
        if text.strip() == opt.strip():
            return STYLE_ORDER[i]
    return None
