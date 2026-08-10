"""FSM-состояния для пошагового ввода."""
from aiogram.fsm.state import State, StatesGroup


class AddSource(StatesGroup):
    waiting_input = State()


class AddTopic(StatesGroup):
    waiting_title = State()
    waiting_description = State()
    waiting_keywords = State()
    waiting_stopwords = State()


class AddCategory(StatesGroup):
    waiting_title = State()
    waiting_hint = State()


class BindChannel(StatesGroup):
    waiting_channel = State()
