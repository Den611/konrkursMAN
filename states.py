from aiogram.fsm.state import State, StatesGroup

class Registration(StatesGroup):
    q1             = State()
    q2             = State()
    q3             = State()
    q4             = State()
    hobby_category = State()
    hobby_custom   = State()
 
class AddWord(StatesGroup):
    waiting_for_word        = State()
    waiting_for_language    = State()
    waiting_for_translation = State()
 
class DeleteWord(StatesGroup):
    waiting_for_word = State()
 
class PracticeWord(StatesGroup):
    waiting_for_language = State()
    waiting_for_answer   = State()
 
class AIHelper(StatesGroup):
    waiting_for_prompt = State()
 
class WordOfDayState(StatesGroup):
    waiting_for_language = State()
    waiting_for_action   = State()
 
class FeedbackState(StatesGroup):
    waiting_for_message = State()
