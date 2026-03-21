import aiohttp
import asyncio
import random
import urllib.parse
from config import AI_URL, GEMINI_KEYS, PIXABAY_KEY

FORCE_GEMINI = False


def set_ai_mode(force_gemini: bool):
    global FORCE_GEMINI
    FORCE_GEMINI = force_gemini


class KeyManager:
    def __init__(self, keys):
        self.keys          = [k for k in keys if k.strip()]
        self.current_index = 0

    def get_key(self):
        if not self.keys: return None
        return self.keys[self.current_index]

    def rotate_key(self):
        if self.keys:
            self.current_index = (self.current_index + 1) % len(self.keys)
            print(f"🔄 [AI] Перемикання на ключ Gemini №{self.current_index + 1}")


key_manager = KeyManager(GEMINI_KEYS)


async def generate_content_safe(prompt: str, model_name: str = "gemma:2b") -> str:
    if not FORCE_GEMINI:
        print(f"⏳ [AI] Запит до Ollama ({model_name})...")
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                payload = {"model": model_name, "prompt": prompt, "stream": False}
                async with session.post(AI_URL, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        print("✅ [AI] Ollama відповіла!")
                        return data.get("response", "").strip()
                    print(f"⚠️ [AI] Ollama: {response.status}")
        except asyncio.TimeoutError:
            print("⚠️ [AI] Ollama timeout. Перемикаємось на Gemini!")
        except Exception as e:
            print(f"⚠️ [AI] Ollama недоступна ({type(e).__name__}).")

    if FORCE_GEMINI:
        print("⚡ [AI] Режим Gemini API.")
    else:
        print("☁️ [AI] Gemini резерв...")

    if not key_manager.keys:
        return "😅 ШІ зараз перевантажений! Спробуй пізніше."

    attempts, max_attempts = 0, len(key_manager.keys) + 1
    while attempts < max_attempts:
        api_key    = key_manager.get_key()
        gemini_url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                      f"gemini-2.5-flash:generateContent?key={api_key}")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.post(
                    gemini_url,
                    json={"contents": [{"parts": [{"text": prompt}]}]}
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        try:
                            text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                            print("✅ [AI] Gemini відповіла!")
                            return text
                        except (KeyError, IndexError):
                            print("⚠️ [AI] Фільтр безпеки Google.")
                            key_manager.rotate_key()
                            attempts += 1
                            await asyncio.sleep(1)
                            continue
                    else:
                        if response.status in [429, 403, 404, 500, 503]:
                            key_manager.rotate_key()
                            attempts += 1
                            await asyncio.sleep(1)
                        else:
                            break
        except Exception as e:
            print(f"⚠️ [AI] Збій Gemini: {type(e).__name__}")
            attempts += 1

    return "😅 ШІ зараз обробляє багато запитів. Спробуй через хвилину!"


async def get_image_url(query, use_random=False):
    if not query or not PIXABAY_KEY: return None
    try:
        per_page      = 20 if use_random else 3
        encoded_query = urllib.parse.quote(query)
        url = (f"https://pixabay.com/api/?key={PIXABAY_KEY}&q={encoded_query}"
               f"&image_type=photo&orientation=horizontal&safesearch=true&per_page={per_page}")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('hits'):
                        return (random.choice(data['hits'])['webformatURL'] if use_random
                                else data['hits'][0]['webformatURL'])
    except Exception:
        pass
    return None


async def get_full_word_info(word: str, translation: str, lang: str,
                             response_lang: str = "Ukrainian") -> tuple:
    """
    Повертає (транскрипцію, асоціацію, visual_query).
    response_lang — мова на якій писати асоціацію (Ukrainian / Polish / English).
    """
    prompt = (
        f"Analyze the word '{word}' ({lang}, translation: '{translation}'). "
        f"Write the association ONLY in {response_lang}. "
        f"Output exactly ONE line in this format: TRANSCRIPTION|ASSOCIATION|VISUAL\n"
        f"Example: [æpl]|A red apple fell on Newton's head|red apple fruit\n"
        f"Do not write any other text. Output:"
    )
    text = await generate_content_safe(prompt)
    if "😅" in text:
        return "[?]", text, word
    text  = (text.replace("*", "").replace("\n", "")
                 .replace("<", "").replace(">", "")
                 .replace("Output:", "").strip())
    parts = text.split("|")
    if len(parts) >= 3:
        return parts[0].strip(), parts[1].strip(), parts[2].strip()
    elif len(parts) == 2:
        return parts[0].strip(), parts[1].strip(), word
    else:
        return "[?]", text, word


async def get_ai_explanation_text(content: str, language_of_word: str,
                                  hobby: str = "everyday life",
                                  response_lang: str = "Ukrainian") -> str:
    """
    Пояснює слово на мові response_lang з прикладом через хобі.
    language_of_word = 'auto' → AI сам визначить мову слова.
    """
    if language_of_word == "auto":
        lang_hint = "Detect the language of the word automatically."
    else:
        lang_hint = f"The word is in {language_of_word}."

    prompt = (
        f"Explain the word '{content}'. {lang_hint} "
        f"The student is interested in: '{hobby}'. "
        f"Write the ENTIRE explanation ONLY in {response_lang}. "
        f"Structure: 1. Word — [Transcription] — Translation. 2. Meaning. 3. Example using '{hobby}'."
    )
    result = await generate_content_safe(prompt)
    return result.replace("<", "").replace(">", "")
