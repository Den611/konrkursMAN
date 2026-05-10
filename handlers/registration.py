from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
import database as db
import i18n
from keyboards import (
    _lang_select_keyboard,
    _style_keyboard,
    _hobby_keyboard,
    get_main_kb,
)
from states import Registration
from utils import ulang, ul, _build_keyword_str, _find_cat_by_label
from handlers.general import cmd_exit

router = Router()


@router.message(Command("language"))
async def cmd_language(message: types.Message):
    await message.answer(
        i18n.t("uk", "lang_select"), reply_markup=_lang_select_keyboard()
    )


@router.callback_query(F.data.startswith("setlang:"))
async def callback_set_lang(callback: types.CallbackQuery, state: FSMContext):
    lang_code = callback.data.split(":")[1]
    user_id = callback.from_user.id
    if lang_code not in i18n.SUPPORTED_UI_LANGS:
        await callback.answer("Unknown language", show_alert=True)
        return
    await db.set_user_lang(user_id, lang_code)
    i18n.set_user_lang(user_id, lang_code)
    data = await state.get_data()
    purpose = data.get("lang_select_purpose", "change")
    await callback.message.edit_text(i18n.t(lang_code, "lang_changed"))
    if purpose == "register":
        await state.update_data(
            scores={"visual": 0, "audial": 0, "logic": 0, "practice": 0}
        )
        await state.set_state(Registration.q1)
        await callback.message.answer(
            i18n.t(lang_code, "start.welcome_new"), parse_mode="HTML"
        )
        q = i18n.get_list(lang_code, "style_test")[0]
        await callback.message.answer(
            q["question"], parse_mode="HTML", reply_markup=_style_keyboard(q["options"])
        )
    else:
        await callback.message.answer(
            i18n.t(lang_code, "lang_command_hint"),
            reply_markup=await get_main_kb(user_id),
        )
    await callback.answer()


@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await db.add_user(user_id, message.from_user.username or "Учень")
    profile = await db.get_user_profile_data(user_id)
    if (
        not profile
        or not profile["hobbies"]
        or profile["hobbies"] == "повсякденне життя"
    ):
        await state.update_data(lang_select_purpose="register")
        await message.answer(
            i18n.t("uk", "lang_select"), reply_markup=_lang_select_keyboard()
        )
    else:
        lang_db = await db.get_user_lang(user_id)
        if lang_db:
            i18n.set_user_lang(user_id, lang_db)
        await db.update_last_active(user_id)
        await state.clear()
        await message.answer(
            ul(user_id, "start.welcome_back"), reply_markup=await get_main_kb(user_id)
        )


async def _ask_style_q(message, lang: str, q_idx: int):
    q = i18n.get_list(lang, "style_test")[q_idx]
    await message.answer(
        q["question"], parse_mode="HTML", reply_markup=_style_keyboard(q["options"])
    )


async def _handle_style_q(message, state, q_idx: int, next_state):
    if not message.text:
        return
    lang = ulang(message.from_user.id)
    data = await state.get_data()
    scores = data["scores"]
    q = i18n.get_list(lang, "style_test")[q_idx]
    style = i18n.score_answer(message.text, q["options"])
    if style:
        scores[style] += 1
    await state.update_data(scores=scores)
    await state.set_state(next_state)
    await _ask_style_q(message, lang, q_idx + 1)


@router.message(Registration.q1)
async def reg_q1(message: types.Message, state: FSMContext):
    await _handle_style_q(message, state, 0, Registration.q2)


@router.message(Registration.q2)
async def reg_q2(message: types.Message, state: FSMContext):
    await _handle_style_q(message, state, 1, Registration.q3)


@router.message(Registration.q3)
async def reg_q3(message: types.Message, state: FSMContext):
    await _handle_style_q(message, state, 2, Registration.q4)


@router.message(Registration.q4)
async def reg_q4(message: types.Message, state: FSMContext):
    if not message.text:
        return
    lang = ulang(message.from_user.id)
    data = await state.get_data()
    scores = data["scores"]
    q = i18n.get_list(lang, "style_test")[3]
    style = i18n.score_answer(message.text, q["options"])
    if style:
        scores[style] += 1
    final_style = max(scores, key=scores.get)
    await state.update_data(final_style=final_style, selected_hobbies=[])
    await state.set_state(Registration.hobby_category)
    score_line = " | ".join(
        [f"{i18n.t(lang, f'style.{k}.name')}: {v}" for k, v in scores.items()]
    )
    style_desc = i18n.t(lang, f"style.{final_style}.desc")
    await message.answer(
        i18n.t(lang, "style_result", desc=style_desc, score_line=score_line),
        parse_mode="HTML",
        reply_markup=_hobby_keyboard(lang, []),
    )


@router.message(Registration.hobby_category)
async def reg_hobby_category(message: types.Message, state: FSMContext):
    if not message.text:
        return

    lang = ulang(message.from_user.id)
    data = await state.get_data()
    selected = data.get("selected_hobbies", [])
    custom_list = data.get(
        "custom_hobbies_list", []
    )  # Створюємо окремий список для написаних вручну
    text = message.text.strip()
    done_btn = i18n.t(lang, "hobby.done_btn")

    if text == done_btn:
        if not selected and not custom_list:  # Перевіряємо обидва списки
            await message.answer(
                i18n.t(lang, "hobby.at_least_one"),
                reply_markup=_hobby_keyboard(lang, selected),
            )
            return

        if "other" in selected:
            sel_no_other = [s for s in selected if s != "other"]
            await state.update_data(
                selected_hobbies=sel_no_other, custom_hobbies_list=custom_list
            )
            await state.set_state(Registration.hobby_custom)

            # Збираємо те, що вже вибрано/написано для підказки
            hobby_so_far = _build_keyword_str(lang, sel_no_other)
            if custom_list:
                custom_joined = ", ".join(custom_list)
                hobby_so_far = (
                    f"{hobby_so_far}, {custom_joined}"
                    if hobby_so_far
                    else custom_joined
                )

            prev = (
                i18n.t(lang, "hobby.custom_prev", hobby_so_far=hobby_so_far)
                if hobby_so_far
                else ""
            )
            await message.answer(
                i18n.t(lang, "hobby.custom_prompt", prev=prev),
                parse_mode="HTML",
                reply_markup=types.ReplyKeyboardRemove(),
            )
        else:
            custom_str = ", ".join(custom_list) if custom_list else None
            await _finish_registration(message, state, selected, custom=custom_str)
        return

    cat = _find_cat_by_label(lang, text)
    if cat:
        # 1. Якщо це клік по стандартній кнопці
        if cat["id"] in selected:
            selected.remove(cat["id"])
        else:
            selected.append(cat["id"])
    else:
        # 2. Якщо ввели текст вручну з клавіатури
        raw_hobbies = text.split(",")
        for hobby in raw_hobbies:
            cleaned = hobby.strip().capitalize()
            if len(cleaned) >= 2 and cleaned not in custom_list:
                custom_list.append(cleaned)

    # Зберігаємо оновлені дані в стан (FSM)
    await state.update_data(selected_hobbies=selected, custom_hobbies_list=custom_list)

    count = len(selected) + len(custom_list)
    if count == 0:
        hint = i18n.t(lang, "hobby.no_selection")
    else:
        cats_data = i18n.get_list(lang, "hobby_categories")
        names = [c["label"].split(" ", 1)[1] for c in cats_data if c["id"] in selected]
        names.extend(
            custom_list
        )  # Додаємо власні слова у повідомлення "Вибрано (X): ..."
        hint = i18n.t(lang, "hobby.selected_hint", count=count, names=", ".join(names))

    await message.answer(
        hint, parse_mode="HTML", reply_markup=_hobby_keyboard(lang, selected)
    )


@router.message(Registration.hobby_custom)
async def reg_hobby_custom(message: types.Message, state: FSMContext):
    if not message.text:
        return

    lang = ulang(message.from_user.id)
    data = await state.get_data()
    selected = data.get("selected_hobbies", [])
    custom_list = data.get("custom_hobbies_list", [])

    raw_hobbies = message.text.split(",")

    for hobby in raw_hobbies:
        cleaned_hobby = hobby.strip().capitalize()
        if len(cleaned_hobby) >= 2 and cleaned_hobby not in custom_list:
            custom_list.append(cleaned_hobby)

    if not custom_list:
        await message.answer(i18n.t(lang, "hobby.custom_too_short"))
        return

    clean_custom_str = ", ".join(custom_list)
    await _finish_registration(message, state, selected, custom=clean_custom_str)


async def _finish_registration(message, state, selected: list, custom: str = None):
    uid = message.from_user.id
    lang = ulang(uid)
    data = await state.get_data()
    final_style = data.get("final_style", "visual")
    hobby_str = _build_keyword_str(lang, selected, custom)

    await db.update_user_profile(uid, final_style, hobby_str)
    await state.clear()

    cats_data = i18n.get_list(lang, "hobby_categories")
    display = [c["label"].split(" ", 1)[1] for c in cats_data if c["id"] in selected]
    if custom:
        display.append(custom)

    style_name = i18n.t(lang, f"style.{final_style}.name")

    await message.answer(
        i18n.t(
            lang,
            "reg_done",
            style=style_name,
            interests=", ".join(display) if display else hobby_str,
        ),
        parse_mode="HTML",
        reply_markup=await get_main_kb(uid),
    )
