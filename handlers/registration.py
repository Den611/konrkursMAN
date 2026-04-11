from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
import database as db
import i18n
from keyboards import _lang_select_keyboard, _style_keyboard, _hobby_keyboard, get_main_kb
from states import Registration
from utils import ulang, ul, _build_keyword_str, _find_cat_by_label
from handlers.general import cmd_exit
 
router = Router()

@router.message(Command("language"))
async def cmd_language(message: types.Message):
    await message.answer(i18n.t("uk", "lang_select"),
                         reply_markup=_lang_select_keyboard())
 
@router.callback_query(F.data.startswith("setlang:"))
async def callback_set_lang(callback: types.CallbackQuery, state: FSMContext):
    lang_code = callback.data.split(":")[1]
    user_id   = callback.from_user.id
    if lang_code not in i18n.SUPPORTED_UI_LANGS:
        await callback.answer("Unknown language", show_alert=True)
        return
    await db.set_user_lang(user_id, lang_code)
    i18n.set_user_lang(user_id, lang_code)
    data    = await state.get_data()
    purpose = data.get("lang_select_purpose", "change")
    await callback.message.edit_text(i18n.t(lang_code, "lang_changed"))
    if purpose == "register":
        await state.update_data(scores={"visual": 0, "audial": 0, "logic": 0, "practice": 0})
        await state.set_state(Registration.q1)
        await callback.message.answer(i18n.t(lang_code, "start.welcome_new"), parse_mode="HTML")
        q = i18n.get_list(lang_code, "style_test")[0]
        await callback.message.answer(q["question"], parse_mode="HTML",
                                      reply_markup=_style_keyboard(q["options"]))
    else:
        await callback.message.answer(i18n.t(lang_code, "lang_command_hint"),
                                      reply_markup=await get_main_kb(user_id))
    await callback.answer()
 
@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await db.add_user(user_id, message.from_user.username or "Учень")
    profile = await db.get_user_profile_data(user_id)
    if not profile or not profile['hobbies'] or profile['hobbies'] == "повсякденне життя":
        await state.update_data(lang_select_purpose="register")
        await message.answer(i18n.t("uk", "lang_select"), reply_markup=_lang_select_keyboard())
    else:
        lang_db = await db.get_user_lang(user_id)
        if lang_db:
            i18n.set_user_lang(user_id, lang_db)
        await db.update_last_active(user_id)
        await state.clear()
        await message.answer(ul(user_id, "start.welcome_back"),
                             reply_markup=await get_main_kb(user_id))
 
async def _ask_style_q(message, lang: str, q_idx: int):
    q = i18n.get_list(lang, "style_test")[q_idx]
    await message.answer(q["question"], parse_mode="HTML",
                         reply_markup=_style_keyboard(q["options"]))
 
async def _handle_style_q(message, state, q_idx: int, next_state):
    if not message.text:
        return
    lang   = ulang(message.from_user.id)
    data   = await state.get_data()
    scores = data["scores"]
    q      = i18n.get_list(lang, "style_test")[q_idx]
    style  = i18n.score_answer(message.text, q["options"])
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
    lang   = ulang(message.from_user.id)
    data   = await state.get_data()
    scores = data["scores"]
    q      = i18n.get_list(lang, "style_test")[3]
    style  = i18n.score_answer(message.text, q["options"])
    if style:
        scores[style] += 1
    final_style = max(scores, key=scores.get)
    await state.update_data(final_style=final_style, selected_hobbies=[])
    await state.set_state(Registration.hobby_category)
    score_line = " | ".join([f"{i18n.t(lang, f'style.{k}.name')}: {v}" for k, v in scores.items()])
    style_desc = i18n.t(lang, f"style.{final_style}.desc")
    await message.answer(i18n.t(lang, "style_result", desc=style_desc, score_line=score_line),
                         parse_mode="HTML", reply_markup=_hobby_keyboard(lang, []))
 
@router.message(Registration.hobby_category)
async def reg_hobby_category(message: types.Message, state: FSMContext):
    if not message.text:
        return
    lang     = ulang(message.from_user.id)
    data     = await state.get_data()
    selected = data.get("selected_hobbies", [])
    text     = message.text.strip()
    done_btn = i18n.t(lang, "hobby.done_btn")
 
    if text == done_btn:
        if not selected:
            await message.answer(i18n.t(lang, "hobby.at_least_one"),
                                 reply_markup=_hobby_keyboard(lang, selected))
            return
        if "other" in selected:
            sel_no_other = [s for s in selected if s != "other"]
            await state.update_data(selected_hobbies=sel_no_other)
            await state.set_state(Registration.hobby_custom)
            hobby_so_far = _build_keyword_str(lang, sel_no_other)
            prev = i18n.t(lang, "hobby.custom_prev", hobby_so_far=hobby_so_far) if hobby_so_far else ""
            await message.answer(i18n.t(lang, "hobby.custom_prompt", prev=prev),
                                 parse_mode="HTML", reply_markup=types.ReplyKeyboardRemove())
        else:
            await _finish_registration(message, state, selected)
        return
 
    cat = _find_cat_by_label(lang, text)
    if not cat:
        await message.answer(i18n.t(lang, "hobby.choose_from_btns"),
                             reply_markup=_hobby_keyboard(lang, selected))
        return
    if cat["id"] in selected:
        selected.remove(cat["id"])
    else:
        selected.append(cat["id"])
    await state.update_data(selected_hobbies=selected)
    count = len(selected)
    if count == 0:
        hint = i18n.t(lang, "hobby.no_selection")
    else:
        cats_data = i18n.get_list(lang, "hobby_categories")
        names = [c["label"].split(" ", 1)[1] for c in cats_data if c["id"] in selected]
        hint  = i18n.t(lang, "hobby.selected_hint", count=count, names=", ".join(names))
    await message.answer(hint, parse_mode="HTML", reply_markup=_hobby_keyboard(lang, selected))
 
@router.message(Registration.hobby_custom)
async def reg_hobby_custom(message: types.Message, state: FSMContext):
    if not message.text:
        return
    lang = ulang(message.from_user.id)
    if len(message.text.strip()) < 2:
        await message.answer(i18n.t(lang, "hobby.custom_too_short"))
        return
    data     = await state.get_data()
    selected = data.get("selected_hobbies", [])
    await _finish_registration(message, state, selected, custom=message.text.strip())
 
async def _finish_registration(message, state, selected: list, custom: str = None):
    uid         = message.from_user.id
    lang        = ulang(uid)
    data        = await state.get_data()
    final_style = data.get("final_style", "visual")
    hobby_str   = _build_keyword_str(lang, selected, custom)
    await db.update_user_profile(uid, final_style, hobby_str)
    await state.clear()
    cats_data = i18n.get_list(lang, "hobby_categories")
    display   = [c["label"].split(" ", 1)[1] for c in cats_data if c["id"] in selected]
    if custom:
        display.append(custom)
    style_name = i18n.t(lang, f"style.{final_style}.name")
    await message.answer(
        i18n.t(lang, "reg_done", style=style_name,
               interests=", ".join(display) if display else hobby_str),
        parse_mode="HTML", reply_markup=await get_main_kb(uid)
    )
