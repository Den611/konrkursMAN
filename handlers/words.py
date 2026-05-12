import io
import html
import logging
import fuzzy_ext
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from deep_translator import GoogleTranslator
import database as db
import i18n
import ai_manager
from states import AddWord, DeleteWord
from keyboards import get_main_kb, _study_lang_keyboard
from utils import ulang, ul, study_langs, trans_lang, ai_lang_name
from handlers.general import cmd_exit
from handlers.ai_helper import text_to_speech

router = Router()


@router.message(Command("add_word"))
async def cmd_add_word(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    await db.update_last_active(uid)
    await state.set_state(AddWord.waiting_for_word)
    await message.answer(
        ul(uid, "add_word.enter_word"), reply_markup=await get_main_kb(uid)
    )


@router.message(AddWord.waiting_for_word)
async def process_word(message: types.Message, state: FSMContext):
    if not message.text:
        return
    if message.text == "/exit":
        return await cmd_exit(message, state)

    uid = message.from_user.id
    input_word = message.text.strip()

    import re

    if ulang(uid) == "uk" and re.search(r"[А-Яа-яЄєІіЇїҐґ]", input_word):
        await message.answer(ul(uid, "add_word.only_foreign"), parse_mode="HTML")
        return

    user_words_raw = await db.get_user_words(uid)

    if user_words_raw:
        existing_words = [w["word"] for w in user_words_raw]
        input_lower = input_word.lower()
        choices_lower = [w.lower() for w in existing_words]

        best_match_lower = fuzzy_ext.find_best_match(input_lower, choices_lower, 0.8)

        if best_match_lower:
            best_match = next(
                w for w in existing_words if w.lower() == best_match_lower
            )

            if best_match.lower() == input_word.lower():
                await message.answer(
                    f"⚠️ Слово <b>{best_match}</b> вже є у вашому словнику!",
                    parse_mode="HTML",
                )
                return
            else:
                data = await state.get_data()
                if data.get("ignore_fuzzy") != input_word:
                    await state.update_data(ignore_fuzzy=input_word)
                    await message.answer(
                        f"🤔 У вас вже є дуже схоже слово: <b>{best_match}</b>.\n"
                        f"Ви впевнені, що хочете додати <b>{input_word}</b>?\n"
                        f"<i>Якщо так — просто надішліть його ще раз.</i>",
                        parse_mode="HTML",
                    )
                    return

    await state.update_data(word=input_word, ignore_fuzzy=None)
    await state.set_state(AddWord.waiting_for_language)
    await message.answer(
        ul(uid, "add_word.choose_lang"), reply_markup=_study_lang_keyboard(uid)
    )


@router.message(AddWord.waiting_for_language)
async def process_language(message: types.Message, state: FSMContext):
    if not message.text:
        return
    if message.text == "/exit":
        return await cmd_exit(message, state)
    uid = message.from_user.id
    if message.text.strip() not in study_langs(uid):
        await message.answer(
            ul(uid, "add_word.choose_lang"), reply_markup=_study_lang_keyboard(uid)
        )
        return
    await state.update_data(language=message.text.strip())
    word = (await state.get_data())["word"]
    try:
        auto_trans = GoogleTranslator(source="auto", target=trans_lang(uid)).translate(
            word
        )
    except:
        auto_trans = "Помилка"
    await state.update_data(auto_translation=auto_trans)
    save_label = ul(uid, "add_word.save_btn", translation=auto_trans)
    trans_kb = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text=save_label)],
            [types.KeyboardButton(text="/exit")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await state.set_state(AddWord.waiting_for_translation)
    await message.answer(
        ul(uid, "add_word.autotrans", translation=html.escape(auto_trans)),
        reply_markup=trans_kb,
        parse_mode="HTML",
    )


@router.message(AddWord.waiting_for_translation)
async def process_custom_translation(message: types.Message, state: FSMContext):
    if not message.text:
        return
    if message.text == "/exit":
        return await cmd_exit(message, state)
    uid = message.from_user.id
    data = await state.get_data()
    save_prefix = ul(uid, "add_word.save_btn", translation="").split(":")[0]
    final_translation = (
        data["auto_translation"]
        if message.text.startswith(save_prefix)
        else message.text.strip()
    )

    msg_wait = await message.answer(ul(uid, "add_word.saving"))

    info = await ai_manager.get_full_word_info(
        data["word"],
        final_translation,
        data["language"],
        response_lang=ai_lang_name(uid),
    )
    transc = info["transcription"]
    assoc = info["association"]
    examples = info["examples"]

    try:
        word_en = GoogleTranslator(source="auto", target="en").translate(data["word"])
    except:
        word_en = data["word"]

    img = (
        await ai_manager.get_image_url(info["image_query"])
        or await ai_manager.get_image_url(word_en)
        or await ai_manager.get_image_url(data["word"])
    )

    added = await db.add_word_to_db(
        uid, data["word"], final_translation, data["language"], img, assoc, transc
    )
    if added:
        ex_text = ""
        if examples:
            ex_text = (
                "\n\n📝 <b>"
                + i18n.t(ulang(uid), "add_word.examples")
                + ":</b>\n"
                + "\n".join(f"• {e}" for e in examples)
            )
        text = (
            f"✅ <b>{html.escape(data['word'])}</b> {html.escape(transc)} — "
            f"{html.escape(final_translation)}\n\n"
            f"🧠 {html.escape(assoc)}"
            f"{ex_text}"
        )
    else:
        text = ul(uid, "add_word.already_exists")

    inline_kb = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=ul(uid, "btn.regen_photo"),
                    callback_data=f"regen:{data['word'][:20]}",
                )
            ]
        ]
    )
    # Генерація озвучки
    await msg_wait.edit_text(ul(uid, "audio.generating"))
    audio_bytes = await text_to_speech(text)
    await msg_wait.delete()
    try:
        if img and added:
            await message.answer_photo(
                img, caption=text, reply_markup=inline_kb, parse_mode="HTML"
            )
        else:
            await message.answer(
                text, reply_markup=inline_kb if added else None, parse_mode="HTML"
            )
    except Exception as e:
        logging.getLogger(__name__).error(f"⚠️ Помилка форматування: {e}")
        if img and added:
            await message.answer_photo(
                img,
                caption=f"✅ {data['word']} — {final_translation}",
                reply_markup=inline_kb,
            )
        else:
            await message.answer(
                f"✅ {data['word']} — {final_translation}",
                reply_markup=inline_kb if added else None,
            )
    # --- НОВЕ: Відправка озвучки ---
    if audio_bytes and added:
        await message.answer_voice(
            types.BufferedInputFile(audio_bytes, filename="word_audio.ogg"),
            caption=ul(uid, "audio.voiceover"),
        )
    await message.answer(
        ul(uid, "add_word.continue"), reply_markup=await get_main_kb(uid)
    )
    await state.set_state(AddWord.waiting_for_word)


@router.message(Command("delete_word"))
async def cmd_delete_word(message: types.Message, state: FSMContext):
    await state.set_state(DeleteWord.waiting_for_word)
    await message.answer(ul(message.from_user.id, "delete.enter_word"))


@router.message(DeleteWord.waiting_for_word)
async def process_delete_word(message: types.Message, state: FSMContext):
    if not message.text:
        return
    uid = message.from_user.id
    if message.text == "/exit":
        return await cmd_exit(message, state)

    target_word = message.text.strip()

    user_words = await db.get_user_words(uid)
    existing_words = [w["word"] for w in user_words] if user_words else []

    exact_match = next(
        (w for w in existing_words if w.lower() == target_word.lower()), None
    )

    if exact_match:
        await db.delete_word_from_db(uid, exact_match)
        await message.answer(
            ul(uid, "delete.done"), reply_markup=await get_main_kb(uid)
        )
        await state.clear()
    else:
        match_lower = fuzzy_ext.find_best_match(
            target_word.lower(), [w.lower() for w in existing_words], 0.7
        )
        if match_lower:
            best_match = next(w for w in existing_words if w.lower() == match_lower)
            kb = types.ReplyKeyboardMarkup(
                keyboard=[
                    [types.KeyboardButton(text=best_match)],
                    [types.KeyboardButton(text="/exit")],
                ],
                resize_keyboard=True,
                one_time_keyboard=True,
            )
            await message.answer(
                f"❌ Слово не знайдено. Можливо, ви мали на увазі <b>{best_match}</b>?",
                reply_markup=kb,
                parse_mode="HTML",
            )
        else:
            await message.answer(
                "❌ Слово не знайдено у вашому словнику. Спробуйте ще раз або /exit."
            )


@router.message(Command("all_words"))
async def cmd_all_words(message: types.Message):
    uid = message.from_user.id
    words = await db.get_user_words(uid)
    if not words:
        return await message.answer(ul(uid, "all_words.empty"))
    title = ul(uid, "all_words.title")
    rows = "\n".join(
        [
            ul(uid, "all_words.row", word=w["word"], translation=w["translation"])
            for w in words
        ]
    )
    await message.answer((title + rows)[:4000])


@router.message(Command("import_words"))
async def cmd_import_words(message: types.Message):
    await message.answer(ul(message.from_user.id, "import.instructions"))


@router.message(F.document)
async def process_document(message: types.Message):
    uid = message.from_user.id
    if not message.document.file_name.endswith((".csv", ".txt")):
        return
    await message.answer(ul(uid, "import.processing"))
    try:
        file_in_io = io.BytesIO()
        await message.bot.download(message.document, destination=file_in_io)
        lines = file_in_io.getvalue().decode("utf-8").splitlines()
        added = 0
        for line in lines:
            parts = [p.strip() for p in line.split("-" if "-" in line else ",")]
            if len(parts) >= 3:
                if await db.add_word_to_db(uid, parts[0], parts[1], parts[2]):
                    added += 1
        await message.answer(
            ul(uid, "import.done", count=added), reply_markup=await get_main_kb(uid)
        )
    except Exception as e:
        await message.answer(ul(uid, "import.error", error=str(e)))
