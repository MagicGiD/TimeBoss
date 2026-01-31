import asyncio
import logging
import sqlite3
import random
import string
import re
import os
import uuid
import threading
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message
)
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from cryptography.fernet import Fernet


import os

def load_token():
    try:
        with open("token.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        raise SystemExit("❌ Файл token.txt не найден. Создай его рядом с main.py")

TOKEN = load_token()

DATABASE_NAME = "timeboss_global.db"
KEY_FILE = "secret.key"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



def load_or_create_key():
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    with open(KEY_FILE, "wb") as f:
        f.write(key)
    return key

SECRET_KEY = load_or_create_key()
fernet = Fernet(SECRET_KEY)

_dec_cache = {}

def enc(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    text = str(text)
    if not text:
        return text
    return fernet.encrypt(text.encode("utf-8")).decode("utf-8")

def dec(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    cached = _dec_cache.get(text)
    if cached is not None:
        return cached
    try:
        value = fernet.decrypt(text.encode("utf-8")).decode("utf-8")
        _dec_cache[text] = value
        return value
    except Exception:
        return text



def load_admins():
    if not os.path.exists("admins.txt"):
        return []
    with open("admins.txt", "r", encoding="utf-8") as f:
        return [int(x.strip()) for x in f.readlines() if x.strip().isdigit()]

ADMINS = load_admins()



class Database:
    def __init__(self, db_name):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._tune()
        self._init_db()

    def _tune(self):
        with self.lock:
            self.cursor.execute("PRAGMA journal_mode=WAL;")
            self.cursor.execute("PRAGMA synchronous=NORMAL;")
            self.cursor.execute("PRAGMA temp_store=MEMORY;")
            self.cursor.execute("PRAGMA cache_size=-64000;")
            self.conn.commit()

    def _init_db(self):
        with self.lock:
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT,
                    birth_date TEXT,
                    current_role TEXT DEFAULT 'member',
                    xp INTEGER DEFAULT 0,
                    completed_tasks_count INTEGER DEFAULT 0,
                    reg_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    profession TEXT
                )
            ''')

            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS teams (
                    team_id TEXT PRIMARY KEY,
                    team_name TEXT,
                    leader_id INTEGER,
                    invite_code TEXT UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS team_members (
                    team_id TEXT,
                    user_id INTEGER,
                    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (team_id, user_id)
                )
            ''')

            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_id TEXT,
                    title TEXT,
                    description TEXT,
                    deadline TEXT,
                    executor_id INTEGER,
                    priority TEXT DEFAULT 'Medium',
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notified_5d INTEGER DEFAULT 0,
                    notified_2d INTEGER DEFAULT 0,
                    notified_1d INTEGER DEFAULT 0,
                    notified_1h INTEGER DEFAULT 0,
                    notified_overdue INTEGER DEFAULT 0
                )
            ''')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_deadline ON tasks(deadline)')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_executor ON tasks(executor_id)')
            self.cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)')

            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS submissions (
                    submission_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    team_id TEXT,
                    executor_id INTEGER,
                    leader_id INTEGER,
                    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'pending',
                    comment_from_executor TEXT,
                    leader_comment TEXT,
                    attachment_type TEXT,
                    attachment_id TEXT,
                    work_text TEXT,
                    work_attachment_type TEXT,
                    work_attachment_id TEXT
                )
            ''')

            for col_def in [
                ("profession", "ALTER TABLE users ADD COLUMN profession TEXT"),
                ("work_text", "ALTER TABLE submissions ADD COLUMN work_text TEXT"),
                ("work_attachment_type", "ALTER TABLE submissions ADD COLUMN work_attachment_type TEXT"),
                ("work_attachment_id", "ALTER TABLE submissions ADD COLUMN work_attachment_id TEXT"),
            ]:
                try:
                    self.cursor.execute(col_def[1])
                except sqlite3.OperationalError:
                    pass

            self.conn.commit()

    def execute(self, sql, params=()):
        with self.lock:
            self.cursor.execute(sql, params)
            self.conn.commit()

    def fetch_one(self, sql, params=()):
        with self.lock:
            self.cursor.execute(sql, params)
            return self.cursor.fetchone()

    def fetch_all(self, sql, params=()):
        with self.lock:
            self.cursor.execute(sql, params)
            return self.cursor.fetchall()

    def close(self):
        with self.lock:
            self.conn.close()

db = Database(DATABASE_NAME)



class Form(StatesGroup):
    reg_name = State()
    reg_birth = State()
    reg_profession = State()
    team_name = State()
    team_join_code = State()
    task_title = State()
    task_desc = State()
    task_deadline = State()
    task_executor = State()
    broadcast_text = State()
    change_profession = State()
    edit_full_name = State()
    edit_birth_date = State()
    submit_work_content = State()
    submit_work_comment = State()
    leader_revision_comment = State()



GUIDE_TEXT = (
    "📘 <b>Полный гайд по TimeBOSS</b>\n\n"
    "🔹 <b>Роли</b>\n"
    "• 👨‍💼 Руководитель -> создаёт команды и задачи, назначает исполнителей\n"
    "• 👷 Участник -> вступает в команды, выполняет задачи, получает опыт\n\n"
    "🔹 <b>Команды</b>\n"
    "• Создать команду: меню <i>Команды</i> -> кнопка <i>Создать команду</i>\n"
    "• Вступить в команду: получить код приглашения у руководителя и ввести его\n"
    "• Выйти из команды: меню <i>Команды</i> -> <i>Выйти из команды</i>\n\n"
    "🔹 <b>Задачи</b>\n"
    "• Руководитель: <i>Создать задачу</i> -> выбрать команду -> ввести название, описание, дедлайн -> выбрать исполнителя\n"
    "• Участник: видит свои задачи в <i>Мои задачи</i>, сдаёт работу через <i>Сдать работу</i>\n\n"
    "🔹 <b>Дедлайны</b>\n"
    "• Бот напоминает за 5 дней, 2 дня, 1 день и за 1 час до дедлайна\n"
    "• Уведомления приходят в личные сообщения\n\n"
    "🔹 <b>Опыт</b>\n"
    "• За завершение задачи начисляется опыт XP\n\n"
    "🔹 <b>Настройки</b>\n"
    "• В <i>Настройки</i> можно открыть этот гайд и увидеть контакт поддержки\n\n"
    "Если что-то непонятно -> открой <i>Настройки</i> -> <i>Гайд по боту</i>"
)



def main_menu_keyboard(role="member"):
    if role == "leader":
        buttons = [
            [KeyboardButton(text="➕ Создать задачу"), KeyboardButton(text="📂 Работы команд")],
            [KeyboardButton(text="📋 Мои задачи"), KeyboardButton(text="👥 Команды")],
            [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="🔄 Сменить роль")],
            [KeyboardButton(text="⚙ Настройки")]
        ]
    else:
        buttons = [
            [KeyboardButton(text="📋 Мои задачи"), KeyboardButton(text="👥 Команды")],
            [KeyboardButton(text="📤 Сдать работу"), KeyboardButton(text="👤 Мой профиль")],
            [KeyboardButton(text="🔄 Сменить роль"), KeyboardButton(text="⚙ Настройки")]
        ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def generate_invite_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

async def check_menu_buttons(message: Message, state: FSMContext):
    menu_texts = [
        "📋 Мои задачи", "👥 Команды", "👤 Мой профиль",
        "🔄 Сменить роль", "➕ Создать задачу", "⚙ Настройки",
        "📤 Сдать работу", "📂 Работы команд"
    ]
    if message.text in menu_texts:
        await state.clear()
        await global_router(message, state)
        return True
    return False

def map_task_status(status: str) -> str:
    if status == "pending":
        return "в работе"
    if status == "in_review":
        return "рассматривается"
    if status == "revision":
        return "на доработке"
    if status == "completed":
        return "завершено"
    return status



session = AiohttpSession(timeout=30)
bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode="HTML"),
    session=session
)
dp = Dispatcher(storage=MemoryStorage())



async def global_router(message: Message, state: FSMContext):
    if message.text == "📋 Мои задачи":
        await my_tasks(message, state)
    elif message.text == "👥 Команды":
        await teams_menu(message, state)
    elif message.text == "👤 Мой профиль":
        await profile_handler(message, state)
    elif message.text == "🔄 Сменить роль":
        await change_role(message, state)
    elif message.text == "➕ Создать задачу":
        await create_task_start(message, state)
    elif message.text == "⚙ Настройки":
        await settings_handler(message, state)
    elif message.text == "📤 Сдать работу":
        await submit_work_start(message, state)
    elif message.text == "📂 Работы команд":
        await leader_submissions_menu(message, state)



@dp.message(Command("start"))
async def start_handler(message: Message, state: FSMContext):
    await state.clear()
    user = db.fetch_one("SELECT * FROM users WHERE user_id = ?", (message.from_user.id,))
    
    if not user:
        text = (
            "👋 Добро пожаловать в <b>TimeBOSS</b>\n\n"
            f"{GUIDE_TEXT}\n\n"
            "Теперь введи своё <b>ФИО</b> для регистрации"
        )
        await message.answer(text)
        await state.set_state(Form.reg_name)
    else:
        full_name = dec(user[1])
        await message.answer(
            f"С возвращением, <b>{full_name}</b>",
            reply_markup=main_menu_keyboard(user[3])
        )



@dp.message(Form.reg_name)
async def process_reg_name(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return
    if len(message.text.strip()) < 3:
        return await message.answer("ФИО слишком короткое. Введи полностью")
    if not re.match(r"^[А-Яа-яA-Za-zЁё\s\-]+$", message.text.strip()):
        return await message.answer("ФИО должно содержать только буквы, пробелы и дефисы. Попробуй ещё раз")
    await state.update_data(full_name=message.text.strip())
    await message.answer("Введи дату рождения в формате <b>ДД.ММ.ГГГГ</b>")
    await state.set_state(Form.reg_birth)

@dp.message(Form.reg_birth)
async def process_reg_birth(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return
    if not re.match(r"^\d{2}\.\d{2}\.\d{4}$", message.text):
        return await message.answer("Формат: <b>ДД.ММ.ГГГГ</b> (например, 01.01.2000)")
    try:
        datetime.strptime(message.text, "%d.%m.%Y")
    except ValueError:
        return await message.answer("Некорректная дата. Проверь день, месяц и год")
    
    await state.update_data(birth_date=message.text)
    await message.answer(
        "Расскажи коротко о себе\n\n"
        "Например: <i>дизайнер</i>, <i>программист</i>, <i>проджект</i>, <i>студент</i> и т.д.\n"
        "Если не хочешь указывать, просто напиши: <b>пропустить</b>"
    )
    await state.set_state(Form.reg_profession)

@dp.message(Form.reg_profession)
async def process_reg_profession(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return

    data = await state.get_data()
    full_name = data["full_name"]
    birth_date = data["birth_date"]

    text = message.text.strip()
    profession = None if text.lower() == "пропустить" else text[:300]

    db.execute(
        "INSERT INTO users (user_id, full_name, birth_date, profession) VALUES (?, ?, ?, ?)",
        (message.from_user.id, enc(full_name), enc(birth_date), enc(profession) if profession else None)
    )
    
    kb_rows = [
        [InlineKeyboardButton(text="👨‍💼 Руководитель", callback_data="set_role:leader"),
         InlineKeyboardButton(text="👷 Участник", callback_data="set_role:member")]
    ]
    if message.from_user.id in ADMINS:
        kb_rows.append([InlineKeyboardButton(text="🛠 Админ‑панель", callback_data="admin_panel")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    await state.clear()
    await message.answer("✅ Регистрация завершена\nТеперь выбери свою роль", reply_markup=kb)

@dp.callback_query(F.data.startswith("set_role:"))
async def set_role(cb: CallbackQuery):
    role = cb.data.split(":")[1]
    if role not in ("leader", "member"):
        return await cb.answer("Некорректная роль", show_alert=True)
    db.execute("UPDATE users SET current_role = ? WHERE user_id = ?", (role, cb.from_user.id))
    await cb.message.delete()
    await cb.message.answer(
        f"Роль успешно обновлена на: <b>{'Руководитель' if role == 'leader' else 'Участник'}</b>",
        reply_markup=main_menu_keyboard(role)
    )



@dp.message(F.text == "👥 Команды")
async def teams_menu(message: Message, state: FSMContext):
    await state.clear()
    user_role = db.fetch_one("SELECT current_role FROM users WHERE user_id = ?", (message.from_user.id,))
    if not user_role:
        return
    
    role = user_role[0]

    if role == "leader":
        teams = db.fetch_all("SELECT team_id, team_name FROM teams WHERE leader_id = ?", (message.from_user.id,))
        text = "📋 <b>Команды:</b>"
        kb_rows = []

        if teams:
            for t in teams:
                team_id, team_name = t
                kb_rows.append([InlineKeyboardButton(text=dec(team_name), callback_data=f"open_team:{team_id}")])

        kb_rows.append([InlineKeyboardButton(text="➕ Создать команду", callback_data="create_team")])
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        await message.answer(text, reply_markup=kb)

    else:
        teams = db.fetch_all("""
            SELECT t.team_id, t.team_name 
            FROM teams t 
            JOIN team_members tm ON t.team_id = tm.team_id 
            WHERE tm.user_id = ?
        """, (message.from_user.id,))

        text = "🤝 <b>Команды, в которых ты состоишь:</b>"
        kb_rows = []

        if teams:
            for t in teams:
                team_id, team_name = t
                kb_rows.append([InlineKeyboardButton(text=dec(team_name), callback_data=f"open_team:{team_id}")])

        kb_rows.append([InlineKeyboardButton(text="🔗 Вступить по коду", callback_data="join_team")])
        if teams:
            kb_rows.append([InlineKeyboardButton(text="🚪 Выйти из команды", callback_data="leave_team")])

        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("open_team:"))
async def open_team_handler(cb: CallbackQuery):
    team_id = cb.data.split(":")[1]

    team = db.fetch_one("""
        SELECT team_name, leader_id, created_at, invite_code
        FROM teams
        WHERE team_id = ?
    """, (team_id,))
    if not team:
        return await cb.answer("Команда не найдена", show_alert=True)

    team_name_enc, leader_id, created_at, invite_code_enc = team
    team_name = dec(team_name_enc)
    invite_code = dec(invite_code_enc)

    leader = db.fetch_one("SELECT full_name FROM users WHERE user_id = ?", (leader_id,))
    leader_name = dec(leader[0]) if leader else "Неизвестно"

    membership = db.fetch_one(
        "SELECT 1 FROM team_members WHERE team_id = ? AND user_id = ?",
        (team_id, cb.from_user.id)
    )
    if not membership and cb.from_user.id != leader_id:
        return await cb.answer("У тебя нет доступа к этой команде", show_alert=True)

    members = db.fetch_all("""
        SELECT u.user_id, u.full_name
        FROM users u
        JOIN team_members tm ON u.user_id = tm.user_id
        WHERE tm.team_id = ?
    """, (team_id,))

    text = (
        f"🧩 <b>Команда:</b> <i>{team_name}</i>\n"
        "────────────────────────────\n"
        f"👑 <b>Лидер:</b> {leader_name} (ID: {leader_id})\n"
        f"📅 <b>Создана:</b> {created_at}\n"
        f"🔗 <b>Код приглашения:</b> <tg-spoiler>{invite_code}</tg-spoiler>\n"
        "────────────────────────────\n"
        "👥 <b>Участники:</b>"
    )

    kb_rows = []
    for user_id, full_name_enc in members:
        kb_rows.append([InlineKeyboardButton(text=f"👤 {dec(full_name_enc)}", callback_data=f"view_user:{user_id}")])

    if cb.from_user.id == leader_id:
        kb_rows.append([InlineKeyboardButton(text="🗑 Удалить команду", callback_data=f"delete_team:{team_id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await cb.message.answer(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("delete_team:"))
async def delete_team_init(cb: CallbackQuery):
    team_id = cb.data.split(":")[1]
    team = db.fetch_one("SELECT team_name, leader_id FROM teams WHERE team_id = ?", (team_id,))
    if not team:
        return await cb.answer("Команда не найдена", show_alert=True)
    team_name_enc, leader_id = team
    team_name = dec(team_name_enc)
    if cb.from_user.id != leader_id:
        return await cb.answer("Удалять команду может только её лидер", show_alert=True)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить команду", callback_data=f"delete_team_confirm:{team_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="delete_team_cancel")]
    ])
    await cb.message.answer(
        f"Ты точно хочешь удалить команду <b>{team_name}</b>\n"
        "Будут удалены задачи, участники и все сданные работы",
        reply_markup=kb
    )

@dp.callback_query(F.data == "delete_team_cancel")
async def delete_team_cancel(cb: CallbackQuery):
    await cb.answer("Удаление отменено", show_alert=False)

@dp.callback_query(F.data.startswith("delete_team_confirm:"))
async def delete_team_confirm(cb: CallbackQuery):
    team_id = cb.data.split(":")[1]
    team = db.fetch_one("SELECT team_name, leader_id FROM teams WHERE team_id = ?", (team_id,))
    if not team:
        return await cb.answer("Команда не найдена", show_alert=True)
    team_name_enc, leader_id = team
    team_name = dec(team_name_enc)
    if cb.from_user.id != leader_id:
        return await cb.answer("Удалять команду может только её лидер", show_alert=True)

    db.execute("DELETE FROM submissions WHERE team_id = ?", (team_id,))
    db.execute("DELETE FROM tasks WHERE team_id = ?", (team_id,))
    db.execute("DELETE FROM team_members WHERE team_id = ?", (team_id,))
    db.execute("DELETE FROM teams WHERE team_id = ?", (team_id,))

    await cb.message.answer(f"Команда <b>{team_name}</b> и все связанные данные удалены")

@dp.callback_query(F.data.startswith("view_user:"))
async def view_user_handler(cb: CallbackQuery):
    try:
        user_id = int(cb.data.split(":")[1])
    except ValueError:
        return await cb.answer("Некорректный ID пользователя", show_alert=True)
    u = db.fetch_one("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if not u:
        return await cb.answer("Пользователь не найден", show_alert=True)

    full_name = dec(u[1])
    birth_date = dec(u[2])
    profession = dec(u[7]) if u[7] else "Не указано"

    text = (
        "👤 <b>Профиль участника</b>\n"
        "────────────────────────────\n"
        f"🆔 <b>ID:</b> {u[0]}\n"
        f"👤 <b>Имя:</b> {full_name}\n"
        f"💼 <b>Описание:</b> {profession}\n"
        f"💠 <b>Опыт:</b> {u[4]} XP\n"
        f"✅ <b>Завершено задач:</b> {u[5]}\n"
        f"🎂 <b>Дата рождения:</b> {birth_date}\n"
        f"📅 <b>Дата регистрации:</b> {u[6]}"
    )
    await cb.message.answer(text)

@dp.callback_query(F.data == "create_team")
async def create_team_init(cb: CallbackQuery, state: FSMContext):
    await cb.message.answer("Введи название для новой команды")
    await state.set_state(Form.team_name)

@dp.message(Form.team_name)
async def process_team_name(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return
    name = message.text.strip()
    if len(name) < 2:
        return await message.answer("Название команды слишком короткое. Введи другое")
    t_id = str(uuid.uuid4())[:8]
    code = generate_invite_code()
    db.execute(
        "INSERT INTO teams (team_id, team_name, leader_id, invite_code) VALUES (?, ?, ?, ?)",
        (t_id, enc(name), message.from_user.id, enc(code))
    )
    try:
        db.execute("INSERT INTO team_members (team_id, user_id) VALUES (?, ?)", (t_id, message.from_user.id))
    except sqlite3.IntegrityError:
        pass
    await message.answer(
        f"✅ Команда <b>{name}</b> успешно создана\n"
        f"Код для приглашения участников: <tg-spoiler>{code}</tg-spoiler>"
    )
    await state.clear()

@dp.callback_query(F.data == "join_team")
async def join_team_init(cb: CallbackQuery, state: FSMContext):
    await cb.message.answer("Введи 6-значный код команды")
    await state.set_state(Form.team_join_code)

@dp.message(Form.team_join_code)
async def process_join_code(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return
    code = message.text.upper().strip()
    if not re.match(r"^[A-Z0-9]{6}$", code):
        return await message.answer("Код должен состоять из 6 символов (латинские буквы и цифры)")
    teams = db.fetch_all("SELECT team_id, team_name, invite_code FROM teams", ())
    team_id = None
    team_name_enc = None
    for t in teams:
        tid, tname_enc, icode_enc = t
        if dec(icode_enc) == code:
            team_id = tid
            team_name_enc = tname_enc
            break

    if team_id:
        try:
            db.execute("INSERT INTO team_members (team_id, user_id) VALUES (?, ?)", (team_id, message.from_user.id))
            await message.answer(f"✅ Успех Ты вступил в команду <b>{dec(team_name_enc)}</b>")
        except sqlite3.IntegrityError:
            await message.answer("Ты уже являешься участником этой команды")
    else:
        await message.answer("Команда с таким кодом не найдена")
    await state.clear()

@dp.callback_query(F.data == "leave_team")
async def leave_team_init(cb: CallbackQuery):
    teams = db.fetch_all("""
        SELECT t.team_id, t.team_name 
        FROM teams t 
        JOIN team_members tm ON t.team_id = tm.team_id
        WHERE tm.user_id = ?
    """, (cb.from_user.id,))
    if not teams:
        return await cb.answer("Ты не состоишь ни в одной команде", show_alert=True)
    kb = [
        [InlineKeyboardButton(text=dec(t[1]), callback_data=f"leave_team_confirm:{t[0]}")]
        for t in teams
    ]
    await cb.message.answer("Выбери команду, из которой хочешь выйти", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("leave_team_confirm:"))
async def leave_team_confirm(cb: CallbackQuery):
    team_id = cb.data.split(":")[1]
    leader = db.fetch_one("SELECT leader_id FROM teams WHERE team_id = ?", (team_id,))
    if leader and leader[0] == cb.from_user.id:
        return await cb.answer("Лидер не может покинуть свою команду", show_alert=True)
    db.execute("DELETE FROM team_members WHERE team_id = ? AND user_id = ?", (team_id, cb.from_user.id))
    await cb.message.answer("🚪 Ты успешно вышел из команды")


@dp.message(F.text == "📋 Мои задачи")
async def my_tasks(message: Message, state: FSMContext):
    await state.clear()
    user_data = db.fetch_one("SELECT current_role FROM users WHERE user_id = ?", (message.from_user.id,))
    if not user_data:
        return
    
    role = user_data[0]
    if role == "leader":
        tasks = db.fetch_all("""
            SELECT t.task_id, t.title, t.deadline, t.status, u.full_name, teams.team_name
            FROM tasks t
            JOIN teams ON t.team_id = teams.team_id 
            JOIN users u ON t.executor_id = u.user_id
            WHERE teams.leader_id = ? 
            ORDER BY t.deadline ASC
        """, (message.from_user.id,))
        text = "📂 <b>Задачи, которые ты назначил</b>\n────────────────────────────\n\n"
        for t in tasks:
            task_id, title_enc, deadline, status, exec_name_enc, team_name_enc = t
            title = dec(title_enc)
            exec_name = dec(exec_name_enc)
            team_name = dec(team_name_enc)
            status_label = map_task_status(status)
            text += (
                f"📌 <b>{title}</b>\n"
                f" -> Команда: {team_name}\n"
                f" -> Исполнитель: {exec_name}\n"
                f" -> Срок: {deadline}\n"
                f" -> Статус: <b>{status_label}</b>\n\n"
            )
        if not tasks:
            text += "Список задач пуст"
        await message.answer(text)
    else:
        tasks = db.fetch_all("""
            SELECT t.task_id, t.title, t.deadline, t.status, t.description, teams.team_name
            FROM tasks t
            JOIN teams ON t.team_id = teams.team_id
            WHERE t.executor_id = ? AND t.status != 'completed'
            ORDER BY t.deadline ASC
        """, (message.from_user.id,))
        text = "📥 <b>Задачи, назначенные тебе</b>\n────────────────────────────\n\n"
        for t in tasks:
            task_id, title_enc, deadline, status, desc_enc, team_name_enc = t
            title = dec(title_enc)
            desc = dec(desc_enc)
            team_name = dec(team_name_enc)
            status_label = map_task_status(status)
            text += (
                f"📌 <b>{title}</b>\n"
                f" -> Команда: {team_name}\n"
                f" -> Срок: {deadline}\n"
                f" -> Описание: {desc}\n"
                f" -> Статус: <b>{status_label}</b>\n\n"
            )
        if not tasks:
            text += "Список задач пуст"
        await message.answer(text)

@dp.message(F.text == "➕ Создать задачу")
async def create_task_start(message: Message, state: FSMContext):
    await state.clear()
    teams = db.fetch_all("SELECT team_id, team_name FROM teams WHERE leader_id = ?", (message.from_user.id,))
    if not teams:
        return await message.answer("У тебя нет команд. Сначала создай команду в разделе <b>Команды</b>")
    kb = [[InlineKeyboardButton(text=dec(t[1]), callback_data=f"task_team:{t[0]}")] for t in teams]
    await message.answer("Выбери команду для постановки задачи", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("task_team:"))
async def task_team_select(cb: CallbackQuery, state: FSMContext):
    await state.update_data(team_id=cb.data.split(":")[1])
    await cb.message.answer("Введи краткое название задачи")
    await state.set_state(Form.task_title)

@dp.message(Form.task_title)
async def process_task_title(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return
    title = message.text.strip()
    if len(title) < 3:
        return await message.answer("Название задачи слишком короткое. Введи другое")
    await state.update_data(title=title)
    await message.answer("Введи подробное описание")
    await state.set_state(Form.task_desc)

@dp.message(Form.task_desc)
async def process_task_desc(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return
    desc = message.text.strip()
    if len(desc) < 5:
        return await message.answer("Описание слишком короткое. Добавь деталей")
    await state.update_data(description=desc)
    await message.answer("Укажи дедлайн в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>")
    await state.set_state(Form.task_deadline)

@dp.message(Form.task_deadline)
async def process_task_deadline(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return
    if not re.match(r"^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}$", message.text):
        return await message.answer("Неверный формат. Нужно: <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>")
    try:
        dl_dt = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
        if dl_dt <= datetime.now():
            return await message.answer("Дедлайн должен быть в будущем. Укажи другую дату и время")
    except ValueError:
        return await message.answer("Некорректная дата или время. Проверь значения")
    
    await state.update_data(deadline=message.text)
    data = await state.get_data()

    team_row = db.fetch_one("SELECT leader_id FROM teams WHERE team_id = ?", (data['team_id'],))
    leader_id = team_row[0] if team_row else None

    if leader_id is None:
        await state.clear()
        return await message.answer("Ошибка команды. Попробуй ещё раз")

    members = db.fetch_all("""
        SELECT u.user_id, u.full_name 
        FROM users u 
        JOIN team_members tm ON u.user_id = tm.user_id 
        WHERE tm.team_id = ? AND u.user_id != ?
    """, (data['team_id'], leader_id))
    
    if not members:
        await state.clear()
        return await message.answer("В этой команде нет участников, кроме тебя. Некому назначить задачу.")
        
    kb = [[InlineKeyboardButton(text=dec(m[1]), callback_data=f"exec:{m[0]}")] for m in members]
    await message.answer("Выбери исполнителя из списка участников", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("exec:"))
async def process_task_executor(cb: CallbackQuery, state: FSMContext):
    exec_id = int(cb.data.split(":")[1])
    data = await state.get_data()

    team_row = db.fetch_one("SELECT leader_id FROM teams WHERE team_id = ?", (data['team_id'],))
    leader_id = team_row[0] if team_row else None
    if leader_id is not None and exec_id == leader_id:
        return await cb.answer("Нельзя назначать задачу самому себе", show_alert=True)

    db.execute(
        "INSERT INTO tasks (team_id, title, description, deadline, executor_id) VALUES (?, ?, ?, ?, ?)", 
        (data['team_id'], enc(data['title']), enc(data['description']), data['deadline'], exec_id)
    )
    
    try:
        await bot.send_message(
            exec_id, 
            (
                "📩 <b>Новая задача</b>\n"
                "────────────────────────────\n"
                f"📌 <b>{data['title']}</b>\n"
                f"⏳ Срок: {data['deadline']}"
            )
        )
    except Exception as e:
        logger.warning(f"Не удалось отправить сообщение исполнителю {exec_id}: {e}")
    
    await cb.message.answer("✅ Задача успешно создана и назначена")
    await state.clear()

# --- СДАЧА РАБОТЫ ---

@dp.message(F.text == "📤 Сдать работу")
async def submit_work_start(message: Message, state: FSMContext):
    await state.clear()
    tasks = db.fetch_all("""
        SELECT t.task_id, t.title, t.deadline, t.status, teams.team_name
        FROM tasks t
        JOIN teams ON t.team_id = teams.team_id
        WHERE t.executor_id = ? AND t.status IN ('pending', 'revision')
        ORDER BY t.deadline ASC
    """, (message.from_user.id,))
    if not tasks:
        return await message.answer("У тебя нет задач, которые можно сдать")

    kb_rows = []
    text = "📤 <b>Выбери задачу, по которой хочешь сдать работу</b>\n────────────────────────────\n\n"
    for t in tasks:
        task_id, title_enc, deadline, status, team_name_enc = t
        title = dec(title_enc)
        team_name = dec(team_name_enc)
        status_label = map_task_status(status)
        text += (
            f"📌 <b>{title}</b>\n"
            f" -> Команда: {team_name}\n"
            f" -> Срок: {deadline}\n"
            f" -> Статус: <b>{status_label}</b>\n\n"
        )
        kb_rows.append([InlineKeyboardButton(text=title, callback_data=f"submit_task:{task_id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("submit_task:"))
async def submit_task_init(cb: CallbackQuery, state: FSMContext):
    try:
        task_id = int(cb.data.split(":")[1])
    except ValueError:
        return await cb.answer("Некорректная задача", show_alert=True)

    task = db.fetch_one("""
        SELECT t.task_id, t.title, t.description, t.deadline, t.team_id, teams.team_name
        FROM tasks t
        JOIN teams ON t.team_id = teams.team_id
        WHERE t.task_id = ? AND t.executor_id = ?
    """, (task_id, cb.from_user.id))
    if not task:
        return await cb.answer("Задача не найдена или не принадлежит тебе", show_alert=True)

    await state.update_data(submit_task_id=task_id)
    await cb.message.answer(
        "📎 <b>Отправь текст или файл для сдачи</b>\n"
        "Это и будет твоей работой по задаче.\n\n"
        "Отправь <b>одним сообщением</b> либо текст, либо файл (документ/фото/видео)."
    )
    await state.set_state(Form.submit_work_content)

@dp.message(Form.submit_work_content)
async def submit_work_content_process(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return

    data = await state.get_data()
    task_id = data.get("submit_task_id")
    if not task_id:
        await state.clear()
        return await message.answer("Что-то пошло не так, попробуй ещё раз")

    work_text = message.text if message.text else None
    work_attachment_type: Optional[str] = None
    work_attachment_id: Optional[str] = None

    if message.document:
        work_attachment_type = "document"
        work_attachment_id = message.document.file_id
    elif message.photo:
        work_attachment_type = "photo"
        work_attachment_id = message.photo[-1].file_id
    elif message.video:
        work_attachment_type = "video"
        work_attachment_id = message.video.file_id

    if not work_text and not work_attachment_id:
        return await message.answer(
            "Нужно отправить <b>текст</b> или <b>файл</b> для сдачи работы.\n"
            "Попробуй ещё раз одним сообщением."
        )

    await state.update_data(
        work_text=work_text,
        work_attachment_type=work_attachment_type,
        work_attachment_id=work_attachment_id
    )

    await message.answer(
        "Если хочешь, добавь комментарий к работе (что сделал, нюансы и т.п.).\n"
        "Если комментарий не нужен -> напиши: <b>пропустить</b>."
    )
    await state.set_state(Form.submit_work_comment)

@dp.message(Form.submit_work_comment)
async def submit_work_comment_process(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return

    data = await state.get_data()
    task_id = data.get("submit_task_id")
    if not task_id:
        await state.clear()
        return await message.answer("Что-то пошло не так, попробуй ещё раз")

    task = db.fetch_one("""
        SELECT t.task_id, t.title, t.description, t.deadline, t.team_id, teams.team_name, teams.leader_id
        FROM tasks t
        JOIN teams ON t.team_id = teams.team_id
        WHERE t.task_id = ? AND t.executor_id = ?
    """, (task_id, message.from_user.id))
    if not task:
        await state.clear()
        return await message.answer("Задача не найдена или не принадлежит тебе")

    task_id, title_enc, description_enc, deadline, team_id, team_name_enc, leader_id = task
    title = dec(title_enc)
    team_name = dec(team_name_enc)

    work_text = data.get("work_text")
    work_attachment_type = data.get("work_attachment_type")
    work_attachment_id = data.get("work_attachment_id")

    comment_text = message.text.strip()
    comment_from_executor = None if comment_text.lower() == "пропустить" else comment_text

    db.execute(
        """
        INSERT INTO submissions (
            task_id, team_id, executor_id, leader_id,
            comment_from_executor, attachment_type, attachment_id,
            work_text, work_attachment_type, work_attachment_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            team_id,
            message.from_user.id,
            leader_id,
            enc(comment_from_executor) if comment_from_executor else None,
            None,
            None,
            enc(work_text) if work_text else None,
            work_attachment_type,
            work_attachment_id
        )
    )

    db.execute("UPDATE tasks SET status = 'in_review' WHERE task_id = ?", (task_id,))

    executor = db.fetch_one("SELECT full_name FROM users WHERE user_id = ?", (message.from_user.id,))
    executor_name = dec(executor[0]) if executor else str(message.from_user.id)

    submission_row = db.fetch_one(
        "SELECT submission_id, submitted_at FROM submissions WHERE rowid = last_insert_rowid()",
    )
    submission_id, submitted_at = submission_row

    text_for_leader = (
        "📥 <b>Новая сданная работа</b>\n"
        "────────────────────────────\n"
        f"🧩 Команда: {team_name}\n"
        f"📌 Задача: {title}\n"
        f"⏳ Срок: {deadline}\n"
        f"👤 Исполнитель: {executor_name} (ID: {message.from_user.id})\n"
        f"🕒 Время сдачи: {submitted_at}\n"
        "────────────────────────────\n"
        "📎 <b>Работа:</b>\n"
        f"{work_text or 'файл без текста'}\n"
    )

    if comment_from_executor:
        text_for_leader += (
            "────────────────────────────\n"
            "💬 <b>Комментарий исполнителя:</b>\n"
            f"{comment_from_executor}"
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Принять", callback_data=f"approve_sub:{submission_id}"),
            InlineKeyboardButton(text="✏ На доработку", callback_data=f"revise_sub:{submission_id}")
        ]
    ])

    try:
        if work_attachment_type and work_attachment_id:
            if work_attachment_type == "document":
                await bot.send_document(leader_id, work_attachment_id, caption=text_for_leader, reply_markup=kb)
            elif work_attachment_type == "photo":
                await bot.send_photo(leader_id, work_attachment_id, caption=text_for_leader, reply_markup=kb)
            elif work_attachment_type == "video":
                await bot.send_video(leader_id, work_attachment_id, caption=text_for_leader, reply_markup=kb)
        else:
            await bot.send_message(leader_id, text_for_leader, reply_markup=kb)
    except Exception as e:
        logger.warning(f"Не удалось отправить работу лидеру {leader_id}: {e}")

    await state.clear()
    await message.answer("✅ Работа отправлена на проверку. Статус задачи теперь: <b>рассматривается</b>")


@dp.callback_query(F.data.startswith("approve_sub:"))
async def approve_submission(cb: CallbackQuery):
    try:
        submission_id = int(cb.data.split(":")[1])
    except ValueError:
        return await cb.answer("Некорректная работа", show_alert=True)

    sub = db.fetch_one("""
        SELECT submission_id, task_id, executor_id, status
        FROM submissions
        WHERE submission_id = ? AND leader_id = ?
    """, (submission_id, cb.from_user.id))
    if not sub:
        return await cb.answer("Работа не найдена или не принадлежит тебе", show_alert=True)

    _, task_id, executor_id, status = sub
    if status == "approved":
        return await cb.answer("Эта работа уже принята", show_alert=True)
    if status == "revision":
        return await cb.answer("Работа уже отправлена на доработку", show_alert=True)

    db.execute("UPDATE submissions SET status = 'approved' WHERE submission_id = ?", (submission_id,))
    db.execute("UPDATE tasks SET status = 'completed' WHERE task_id = ?", (task_id,))
    db.execute("""
        UPDATE users 
        SET xp = xp + 10, completed_tasks_count = completed_tasks_count + 1 
        WHERE user_id = ?
    """, (executor_id,))

    try:
        await bot.send_message(
            executor_id,
            "✅ <b>Твоя работа принята</b>\n"
            "────────────────────────────\n"
            "Задача отмечена как <b>завершённая</b>\n"
            "Тебе начислено <b>10 XP</b>"
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить исполнителя {executor_id} о принятии работы: {e}")

    await cb.answer("Работа принята", show_alert=False)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

@dp.callback_query(F.data.startswith("revise_sub:"))
async def revise_submission_init(cb: CallbackQuery, state: FSMContext):
    try:
        submission_id = int(cb.data.split(":")[1])
    except ValueError:
        return await cb.answer("Некорректная работа", show_alert=True)

    sub = db.fetch_one("""
        SELECT submission_id, task_id, executor_id, status
        FROM submissions
        WHERE submission_id = ? AND leader_id = ?
    """, (submission_id, cb.from_user.id))
    if not sub:
        return await cb.answer("Работа не найдена или не принадлежит тебе", show_alert=True)

    _, task_id, executor_id, status = sub
    if status == "approved":
        return await cb.answer("Работа уже принята, на доработку отправлять нельзя", show_alert=True)
    if status == "revision":
        return await cb.answer("Работа уже отправлена на доработку", show_alert=True)

    await state.update_data(revision_submission_id=submission_id)
    await cb.message.answer(
        "✏ Напиши, что нужно доработать по этой работе\n"
        "Твой комментарий будет отправлен исполнителю"
    )
    await state.set_state(Form.leader_revision_comment)

@dp.message(Form.leader_revision_comment)
async def revise_submission_process(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return

    data = await state.get_data()
    submission_id = data.get("revision_submission_id")
    if not submission_id:
        await state.clear()
        return await message.answer("Что-то пошло не так, попробуй ещё раз")

    sub = db.fetch_one("""
        SELECT submission_id, task_id, executor_id, status
        FROM submissions
        WHERE submission_id = ?
    """, (submission_id,))
    if not sub:
        await state.clear()
        return await message.answer("Работа не найдена")

    _, task_id, executor_id, status = sub
    if status == "approved":
        await state.clear()
        return await message.answer("Работа уже принята, на доработку отправлять нельзя")
    if status == "revision":
        await state.clear()
        return await message.answer("Работа уже отправлена на доработку")

    leader_comment = message.text.strip()

    db.execute(
        "UPDATE submissions SET status = 'revision', leader_comment = ? WHERE submission_id = ?",
        (enc(leader_comment), submission_id)
    )
    db.execute("UPDATE tasks SET status = 'revision' WHERE task_id = ?", (task_id,))

    try:
        await bot.send_message(
            executor_id,
            (
                "⚠ <b>Твоя работа отправлена на доработку</b>\n"
                "────────────────────────────\n"
                "<b>Комментарий руководителя:</b>\n"
                f"{leader_comment}"
            )
        )
    except Exception as e:
        logger.warning(f"Не удалось уведомить исполнителя {executor_id} о доработке: {e}")

    await state.clear()
    await message.answer("Комментарий отправлен исполнителю. Статус задачи: <b>на доработке</b>")



@dp.message(F.text == "📂 Работы команд")
async def leader_submissions_menu(message: Message, state: FSMContext):
    await state.clear()
    teams = db.fetch_all("SELECT team_id, team_name FROM teams WHERE leader_id = ?", (message.from_user.id,))
    if not teams:
        return await message.answer("У тебя нет команд, по которым можно смотреть работы")

    kb = [[InlineKeyboardButton(text=dec(t[1]), callback_data=f"leader_team_subs:{t[0]}")] for t in teams]
    await message.answer(
        "📂 <b>Работы по командам</b>\n────────────────────────────\n\n"
        "Выбери команду, чтобы посмотреть <b>готовые</b> работы (принятые задачи).",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
    )

@dp.callback_query(F.data.startswith("leader_team_subs:"))
async def leader_team_submissions(cb: CallbackQuery, state: FSMContext):
    team_id = cb.data.split(":")[1]
    team = db.fetch_one("SELECT team_name, leader_id FROM teams WHERE team_id = ?", (team_id,))
    if not team:
        return await cb.answer("Команда не найдена", show_alert=True)
    team_name_enc, leader_id = team
    team_name = dec(team_name_enc)
    if cb.from_user.id != leader_id:
        return await cb.answer("Нет доступа к этой команде", show_alert=True)

    subs = db.fetch_all("""
        SELECT s.submission_id, s.task_id, s.executor_id, s.submitted_at, s.status,
               t.title, u.full_name
        FROM submissions s
        JOIN tasks t ON s.task_id = t.task_id
        JOIN users u ON s.executor_id = u.user_id
        WHERE s.team_id = ? AND s.status = 'approved'
        ORDER BY s.submitted_at DESC
    """, (team_id,))
    if not subs:
        return await cb.message.answer(f"По команде <b>{team_name}</b> пока нет принятых работ")

    text = (
        f"📂 <b>Готовые работы по команде:</b> <i>{team_name}</i>\n"
        "────────────────────────────\n\n"
    )
    kb_rows = []
    for s in subs:
        submission_id, task_id, executor_id, submitted_at, status, title_enc, full_name_enc = s
        title = dec(title_enc)
        full_name = dec(full_name_enc)
        text += (
            f"🆔 ID работы: {submission_id}\n"
            f"📌 Задача: {title}\n"
            f"👤 Исполнитель: {full_name} (ID: {executor_id})\n"
            f"🕒 Время сдачи: {submitted_at}\n"
            "────────────────────────────\n"
        )
        kb_rows.append([InlineKeyboardButton(text=f"Открыть работу {submission_id}", callback_data=f"view_sub:{submission_id}")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await cb.message.answer(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("view_sub:"))
async def view_submission(cb: CallbackQuery):
    try:
        submission_id = int(cb.data.split(":")[1])
    except ValueError:
        return await cb.answer("Некорректная работа", show_alert=True)

    sub = db.fetch_one("""
        SELECT s.submission_id, s.task_id, s.executor_id, s.submitted_at, s.status,
               s.comment_from_executor, s.leader_comment,
               s.work_text, s.work_attachment_type, s.work_attachment_id,
               t.title, u.full_name, t.deadline, teams.team_name
        FROM submissions s
        JOIN tasks t ON s.task_id = t.task_id
        JOIN users u ON s.executor_id = u.user_id
        JOIN teams ON s.team_id = teams.team_id
        WHERE s.submission_id = ?
    """, (submission_id,))
    if not sub:
        return await cb.answer("Работа не найдена", show_alert=True)

    (
        submission_id, task_id, executor_id, submitted_at, status,
        comment_enc, leader_comment_enc,
        work_text_enc, work_attachment_type, work_attachment_id,
        title_enc, full_name_enc, deadline, team_name_enc
    ) = sub

    title = dec(title_enc)
    full_name = dec(full_name_enc)
    team_name = dec(team_name_enc)
    work_text = dec(work_text_enc) if work_text_enc else None
    comment_from_executor = dec(comment_enc) if comment_enc else None

    text = (
        "📄 <b>Сданная работа</b>\n"
        "────────────────────────────\n"
        f"🧩 Команда: {team_name}\n"
        f"📌 Задача: {title}\n"
        f"👤 Исполнитель: {full_name} (ID: {executor_id})\n"
        f"🕒 Время сдачи: {submitted_at}\n"
        f"⏳ Дедлайн: {deadline}\n"
        "────────────────────────────\n"
        "📎 <b>Работа:</b>\n"
        f"{work_text or 'файл без текста'}\n"
    )

    if comment_from_executor:
        text += (
            "────────────────────────────\n"
            "💬 <b>Комментарий исполнителя:</b>\n"
            f"{comment_from_executor}\n"
        )

    await cb.message.answer(text)

    if work_attachment_type and work_attachment_id:
        try:
            if work_attachment_type == "document":
                await bot.send_document(cb.from_user.id, work_attachment_id)
            elif work_attachment_type == "photo":
                await bot.send_photo(cb.from_user.id, work_attachment_id)
            elif work_attachment_type == "video":
                await bot.send_video(cb.from_user.id, work_attachment_id)
        except Exception as e:
            logger.warning(f"Не удалось отправить файл по работе {submission_id}: {e}")



@dp.message(F.text == "👤 Мой профиль")
async def profile_handler(message: Message, state: FSMContext):
    await state.clear()
    u = db.fetch_one("SELECT * FROM users WHERE user_id = ?", (message.from_user.id,))
    if not u:
        return
    full_name = dec(u[1])
    birth_date = dec(u[2])
    profession = dec(u[7]) if u[7] else "Не указано"
    text = (
        "👤 <b>Твой профиль</b>\n"
        "────────────────────────────\n"
        f"👤 <b>Имя:</b> {full_name}\n"
        f"💼 <b>Описание:</b> {profession}\n"
        f"💠 <b>Опыт:</b> {u[4]} XP\n"
        f"✅ <b>Завершено задач:</b> {u[5]}\n"
        f"🎂 <b>Дата рождения:</b> {birth_date}\n"
        f"📅 <b>Дата регистрации:</b> {u[6]}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏ Редактировать профиль", callback_data="edit_profile")]
    ])
    await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data == "edit_profile")
async def edit_profile_menu(cb: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить ФИО", callback_data="edit_full_name")],
        [InlineKeyboardButton(text="Изменить дату рождения", callback_data="edit_birth_date")],
        [InlineKeyboardButton(text="Изменить описание", callback_data="change_profession")]
    ])
    await cb.message.answer("Что хочешь изменить в профиле", reply_markup=kb)

@dp.callback_query(F.data == "edit_full_name")
async def edit_full_name_init(cb: CallbackQuery, state: FSMContext):
    await cb.message.answer("Введи новое ФИО")
    await state.set_state(Form.edit_full_name)

@dp.message(Form.edit_full_name)
async def edit_full_name_process(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return
    text = message.text.strip()
    if len(text) < 3:
        return await message.answer("ФИО слишком короткое. Введи полностью")
    if not re.match(r"^[А-Яа-яA-Za-zЁё\s\-]+$", text):
        return await message.answer("ФИО должно содержать только буквы, пробелы и дефисы. Попробуй ещё раз")
    db.execute("UPDATE users SET full_name = ? WHERE user_id = ?", (enc(text), message.from_user.id))
    await state.clear()
    await message.answer("ФИО обновлено")

@dp.callback_query(F.data == "edit_birth_date")
async def edit_birth_date_init(cb: CallbackQuery, state: FSMContext):
    await cb.message.answer("Введи новую дату рождения в формате <b>ДД.ММ.ГГГГ</b>")
    await state.set_state(Form.edit_birth_date)

@dp.message(Form.edit_birth_date)
async def edit_birth_date_process(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return
    if not re.match(r"^\d{2}\.\d{2}\.\d{4}$", message.text):
        return await message.answer("Формат: <b>ДД.ММ.ГГГГ</b> (например, 01.01.2000)")
    try:
        datetime.strptime(message.text, "%d.%m.%Y")
    except ValueError:
        return await message.answer("Некорректная дата. Проверь день, месяц и год")
    db.execute("UPDATE users SET birth_date = ? WHERE user_id = ?", (enc(message.text), message.from_user.id))
    await state.clear()
    await message.answer("Дата рождения обновлена")

@dp.callback_query(F.data == "change_profession")
async def change_profession_init(cb: CallbackQuery, state: FSMContext):
    await cb.message.answer(
        "Напиши, что хочешь указать в описании\n\n"
        "Например: <i>дизайнер</i>, <i>программист</i>, <i>тимлид</i>, твои сильные стороны и т.д.\n"
        "Чтобы очистить поле -> напиши: <b>пропустить</b>"
    )
    await state.set_state(Form.change_profession)

@dp.message(Form.change_profession)
async def change_profession_process(message: Message, state: FSMContext):
    if await check_menu_buttons(message, state):
        return

    text = message.text.strip()
    profession = None if text.lower() == "пропустить" else text[:300]

    db.execute(
        "UPDATE users SET profession = ? WHERE user_id = ?",
        (enc(profession) if profession else None, message.from_user.id)
    )

    await state.clear()
    await message.answer("Описание обновлено. Открой профиль, чтобы посмотреть изменения")

# --- СМЕНА РОЛИ ---

@dp.message(F.text == "🔄 Сменить роль")
async def change_role(message: Message, state: FSMContext):
    await state.clear()
    kb_rows = [
        [InlineKeyboardButton(text="Стать Руководителем", callback_data="set_role:leader")],
        [InlineKeyboardButton(text="Стать Участником", callback_data="set_role:member")]
    ]
    if message.from_user.id in ADMINS:
        kb_rows.append([InlineKeyboardButton(text="Админ‑панель", callback_data="admin_panel")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer("Выбери новую роль. Это изменит доступные функции меню", reply_markup=kb)



@dp.message(F.text == "⚙ Настройки")
async def settings_handler(message: Message, state: FSMContext):
    await state.clear()
    kb_rows = [
        [InlineKeyboardButton(text="📘 Гайд по боту", callback_data="guide")],
        [InlineKeyboardButton(text="🆘 Поддержка", callback_data="support")]
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer("⚙ <b>Настройки</b>\n────────────────────────────\n\nВыбери нужный пункт", reply_markup=kb)

@dp.callback_query(F.data == "guide")
async def guide_handler(cb: CallbackQuery):
    await cb.message.answer(GUIDE_TEXT)

@dp.callback_query(F.data == "support")
async def support_handler(cb: CallbackQuery):
    await cb.message.answer(
        "Если что-то сломалось или есть идеи по улучшению\n"
        "Напиши создателю бота: @mgidd"
    )


@dp.callback_query(F.data == "admin_panel")
async def admin_panel_handler(cb: CallbackQuery):
    if cb.from_user.id not in ADMINS:
        return await cb.answer("Доступ запрещён", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📣 Оповещение всех", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🧪 Тест дедлайн‑уведомлений", callback_data="admin_test_deadlines")]
    ])
    await cb.message.answer("🛠 <b>Админ‑панель</b>\nВыбери действие:", reply_markup=kb)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats_handler(cb: CallbackQuery):
    if cb.from_user.id not in ADMINS:
        return await cb.answer("Доступ запрещён", show_alert=True)

    users_count = db.fetch_one("SELECT COUNT(*) FROM users", ())[0]
    teams_count = db.fetch_one("SELECT COUNT(*) FROM teams", ())[0]
    tasks_count = db.fetch_one("SELECT COUNT(*) FROM tasks", ())[0]
    completed_tasks = db.fetch_one("SELECT COUNT(*) FROM tasks WHERE status = 'completed'", ())[0]
    submissions_count = db.fetch_one("SELECT COUNT(*) FROM submissions", ())[0]
    approved_submissions = db.fetch_one("SELECT COUNT(*) FROM submissions WHERE status = 'approved'", ())[0]

    text = (
        "📊 <b>Статистика бота</b>\n"
        "────────────────────────────\n"
        f"👥 Пользователей: <b>{users_count}</b>\n"
        f"🧩 Команд: <b>{teams_count}</b>\n"
        f"📌 Задач всего: <b>{tasks_count}</b>\n"
        f"✅ Завершённых задач: <b>{completed_tasks}</b>\n"
        f"📥 Сданных работ: <b>{submissions_count}</b>\n"
        f"📄 Принятых работ: <b>{approved_submissions}</b>\n"
    )
    await cb.message.answer(text)

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_init(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMINS:
        return await cb.answer("Доступ запрещён", show_alert=True)
    await cb.message.answer("✉ Напиши текст для оповещения всех пользователей.")
    await state.set_state(Form.broadcast_text)

@dp.message(Form.broadcast_text)
async def admin_broadcast_process(message: Message, state: FSMContext):
    if message.from_user.id not in ADMINS:
        await state.clear()
        return await message.answer("Доступ запрещён")

    text = message.text
    users = db.fetch_all("SELECT user_id FROM users", ())
    sent = 0
    for (uid,) in users:
        try:
            await bot.send_message(uid, f"📣 <b>Оповещение</b>\n\n{text}")
            sent += 1
        except Exception as e:
            logger.warning(f"Не удалось отправить рассылку пользователю {uid}: {e}")
    await state.clear()
    await message.answer(f"✅ Оповещение отправлено. Успешно: {sent} пользователей.")

@dp.callback_query(F.data == "admin_test_deadlines")
async def admin_test_deadlines(cb: CallbackQuery):
    if cb.from_user.id not in ADMINS:
        return await cb.answer("Доступ запрещён", show_alert=True)
    await cb.message.answer(
        "🧪 Тест дедлайн‑уведомлений включён автоматически фоновым процессом.\n"
        "Просто создай задачи с разными дедлайнами и подожди."
    )



async def send_deadline_notice(user_id, title, deadline, when):
    text = (
        "⏳ <b>Напоминание о дедлайне</b>\n"
        "────────────────────────────\n"
        f"📌 Задача: {title}\n"
        f"⏳ Срок: {deadline}\n"
        f"⏰ Осталось: <b>{when}</b>"
    )
    try:
        await bot.send_message(user_id, text)
    except Exception as e:
        logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

async def send_deadline_overdue(user_id, title, deadline):
    text = (
        "❗ <b>Дедлайн просрочен</b>\n"
        "────────────────────────────\n"
        f"📌 Задача: {title}\n"
        f"⏳ Срок был: {deadline}\n"
        "Постарайся завершить задачу как можно скорее"
    )
    try:
        await bot.send_message(user_id, text)
    except Exception as e:
        logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

async def deadline_notifier():
    while True:
        now = datetime.now()

        tasks = db.fetch_all("""
            SELECT task_id, title, deadline, executor_id,
                   notified_5d, notified_2d, notified_1d, notified_1h, notified_overdue
            FROM tasks
            WHERE status IN ('pending', 'in_review', 'revision')
        """)

        for t in tasks:
            task_id, title_enc, deadline_str, executor_id, n5, n2, n1, n1h, noverdue = t

            try:
                deadline = datetime.strptime(deadline_str, "%d.%m.%Y %H:%M")
            except:
                continue

            title = dec(title_enc)
            delta = deadline - now
            seconds = delta.total_seconds()

            if seconds <= 5*24*3600 and seconds > 2*24*3600 and n5 == 0:
                await send_deadline_notice(executor_id, title, deadline_str, "5 дней")
                db.execute("UPDATE tasks SET notified_5d = 1 WHERE task_id = ?", (task_id,))
                continue

            if seconds <= 2*24*3600 and seconds > 1*24*3600 and n2 == 0:
                await send_deadline_notice(executor_id, title, deadline_str, "2 дня")
                db.execute("UPDATE tasks SET notified_2d = 1 WHERE task_id = ?", (task_id,))
                continue

            if seconds <= 1*24*3600 and seconds > 3600 and n1 == 0:
                await send_deadline_notice(executor_id, title, deadline_str, "1 день")
                db.execute("UPDATE tasks SET notified_1d = 1 WHERE task_id = ?", (task_id,))
                continue

            if seconds <= 3600 and seconds > 0 and n1h == 0:
                await send_deadline_notice(executor_id, title, deadline_str, "1 час")
                db.execute("UPDATE tasks SET notified_1h = 1 WHERE task_id = ?", (task_id,))
                continue

            if seconds <= 0 and noverdue == 0:
                await send_deadline_overdue(executor_id, title, deadline_str)
                db.execute("UPDATE tasks SET notified_overdue = 1 WHERE task_id = ?", (task_id,))
                continue

        await asyncio.sleep(60)



async def main():
    asyncio.create_task(deadline_notifier())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

