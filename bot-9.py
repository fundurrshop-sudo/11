import asyncio
import logging
import os
import random

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart, Command

# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = "8411412862:AAE6xYsUVyaoLNrxujauUr6adKtSkdd01FI"

ADMIN_CHAT_ID = 5245002499
ADMIN_IDS = [5245002499]

DB_PATH = os.getenv("DB_PATH", "/data/shop.db")

DEFAULT_SETTINGS = {
    "welcome": "👋 Добро пожаловать в магазин!\n\nВыберите категорию:",
    "catalog_prompt": "Выберите категорию:",
    "product_prompt": "Выберите товар:",
    "empty_catalog": "Каталог пока пуст. Загляните позже 🙂",
    "empty_category": "В этой категории пока нет товаров",
    "email_prompt": "✉️ Введите почту от игрового аккаунта, на которую нужно зачислить покупку:",
    "confirm_header": "📋 Проверьте данные заказа:",
    "success": (
        "✅ Заказ оформлен!\n\n"
        "💳 Переведите {price} на карту {card} и обязательно укажите в комментарии "
        "к переводу код:\n\n"
        "「 {code} 」\n\n"
        "⏳ После оплаты дождитесь подтверждения от продавца."
    ),
    "btn_order_back": "⬅️ Назад",
    "btn_order_continue": "✅ Продолжить",
    "btn_back_categories": "⬅️ Назад к категориям",
    "label_product": "Товар",
    "label_price": "Цена",
    "label_email": "Почта",
    "label_payment": "Способ оплаты",
    "card_number": "не указан — задайте через /set_card_number",
}

EDITABLE_TEXTS = {
    "welcome": "приветствие (/start)",
    "catalog_prompt": 'текст "Выберите категорию"',
    "product_prompt": 'текст "Выберите товар"',
    "empty_catalog": "текст при пустом каталоге",
    "empty_category": "текст при пустой категории (всплывающее окно, без фото)",
    "email_prompt": "запрос почты игрового аккаунта",
    "confirm_header": "заголовок экрана проверки заказа",
    "success": "финальное сообщение с реквизитами (доступны {price}, {card}, {code})",
}

EDITABLE_LABELS = {
    "btn_order_back": 'кнопка "Назад" на экране проверки',
    "btn_order_continue": 'кнопка "Продолжить"',
    "btn_back_categories": 'кнопка "Назад к категориям"',
    "label_product": 'подпись поля "Товар"',
    "label_price": 'подпись поля "Цена"',
    "label_email": 'подпись поля "Почта"',
    "label_payment": 'подпись поля "Способ оплаты"',
    "card_number": "номер карты для перевода",
}

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    price TEXT NOT NULL,
    requires_email INTEGER NOT NULL DEFAULT 0,
    photo_file_id TEXT,
    FOREIGN KEY (category_id) REFERENCES categories (id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT,
    product_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    price TEXT NOT NULL,
    email TEXT,
    payment_method TEXT NOT NULL,
    payment_code TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    photo_file_id TEXT
);
"""

# ============================================================
# БАЗА ДАННЫХ
# ============================================================

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES_SQL)
        try:
            await db.execute("ALTER TABLE orders ADD COLUMN payment_code TEXT")
            await db.commit()
        except Exception:
            pass
        for key, value in DEFAULT_SETTINGS.items():
            await db.execute(
                "INSERT OR IGNORE INTO settings (key, value, photo_file_id) VALUES (?, ?, NULL)",
                (key, value),
            )
        await db.commit()


async def get_setting(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value, photo_file_id FROM settings WHERE key = ?", (key,))
        row = await cur.fetchone()
        if row:
            return row[0] if row[0] is not None else DEFAULT_SETTINGS.get(key, ""), row[1]
        return DEFAULT_SETTINGS.get(key, ""), None


async def get_setting_text(key: str) -> str:
    text, _ = await get_setting(key)
    return text


async def set_setting_text(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO settings (key, value, photo_file_id) VALUES (?, ?, NULL) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def set_setting_photo(key: str, photo_file_id: str, value: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if value is not None:
            await db.execute(
                "INSERT INTO settings (key, value, photo_file_id) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, photo_file_id = excluded.photo_file_id",
                (key, value, photo_file_id),
            )
        else:
            await db.execute(
                "INSERT INTO settings (key, value, photo_file_id) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET photo_file_id = excluded.photo_file_id",
                (key, DEFAULT_SETTINGS.get(key, ""), photo_file_id),
            )
        await db.commit()


async def clear_setting_photo(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE settings SET photo_file_id = NULL WHERE key = ?", (key,))
        await db.commit()


async def get_or_create_category(name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id FROM categories WHERE name = ?", (name,))
        row = await cur.fetchone()
        if row:
            return row[0]
        cur = await db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
        await db.commit()
        return cur.lastrowid


async def list_categories():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT id, name FROM categories ORDER BY name")
        return await cur.fetchall()


async def delete_category(category_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM products WHERE category_id = ?", (category_id,))
        await db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        await db.commit()


async def add_product(category_id: int, name: str, price: str, requires_email: bool,
                       photo_file_id: str = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO products (category_id, name, price, requires_email, photo_file_id) VALUES (?, ?, ?, ?, ?)",
            (category_id, name, price, int(requires_email), photo_file_id),
        )
        await db.commit()
        return cur.lastrowid


async def set_product_photo(product_id: int, photo_file_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE products SET photo_file_id = ? WHERE id = ?", (photo_file_id, product_id))
        await db.commit()


async def list_products(category_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, name, price, requires_email FROM products WHERE category_id = ? ORDER BY name",
            (category_id,),
        )
        return await cur.fetchall()


async def get_product(product_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, category_id, name, price, requires_email, photo_file_id FROM products WHERE id = ?",
            (product_id,),
        )
        return await cur.fetchone()


async def delete_product(product_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM products WHERE id = ?", (product_id,))
        await db.commit()


async def list_all_products():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT products.id, categories.name, products.name, products.price
               FROM products JOIN categories ON products.category_id = categories.id
               ORDER BY categories.name, products.name"""
        )
        return await cur.fetchall()


async def create_order(user_id: int, username: str, product_id: int, product_name: str,
                        price: str, email: str, payment_method: str, payment_code: str = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """INSERT INTO orders (user_id, username, product_id, product_name, price, email, payment_method, payment_code)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, username, product_id, product_name, price, email, payment_method, payment_code),
        )
        await db.commit()
        return cur.lastrowid


async def code_exists(code: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT 1 FROM orders WHERE payment_code = ?", (code,))
        return (await cur.fetchone()) is not None


async def get_order_by_code(code: str):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT id, user_id, username, product_name, price, email, payment_method, status
               FROM orders WHERE payment_code = ?""",
            (code,),
        )
        return await cur.fetchone()


async def mark_order_paid(order_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order_id,))
        await db.commit()


async def list_recent_orders(limit: int = 20):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id, username, user_id, product_name, price, status, created_at, payment_code FROM orders ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return await cur.fetchall()


async def add_admin(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (user_id,))
        await db.commit()


async def remove_admin(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()


async def list_admin_ids():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM admins")
        rows = await cur.fetchall()
        return [r[0] for r in rows]


# ============================================================
# ХЕНДЛЕРЫ ПОКУПАТЕЛЯ
# ============================================================

user_router = Router()


class OrderStates(StatesGroup):
    waiting_email = State()
    waiting_confirm = State()


async def send_styled(target: Message, key: str, reply_markup=None, extra: str = ""):
    text, photo = await get_setting(key)
    final_text = (text or "") + extra
    if photo:
        await target.answer_photo(photo, caption=final_text, reply_markup=reply_markup)
    else:
        await target.answer(final_text, reply_markup=reply_markup)


@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    categories = await list_categories()
    if not categories:
        await send_styled(message, "empty_catalog")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"cat:{cid}")]
        for cid, name in categories
    ])
    await send_styled(message, "welcome", reply_markup=kb)


@user_router.callback_query(F.data.startswith("cat:"))
async def show_products(callback: CallbackQuery, state: FSMContext):
    category_id = int(callback.data.split(":")[1])
    products = await list_products(category_id)
    if not products:
        empty_text = await get_setting_text("empty_category")
        await callback.answer(empty_text, show_alert=True)
        return
    btn_back = await get_setting_text("btn_back_categories")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{name} — {price}", callback_data=f"prod:{pid}")]
        for pid, name, price, _ in products
    ] + [[InlineKeyboardButton(text=btn_back, callback_data="back_cats")]])
    await send_styled(callback.message, "product_prompt", reply_markup=kb)
    await callback.answer()


@user_router.callback_query(F.data == "back_cats")
async def back_to_categories(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    categories = await list_categories()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"cat:{cid}")]
        for cid, name in categories
    ])
    await send_styled(callback.message, "catalog_prompt", reply_markup=kb)
    await callback.answer()


@user_router.callback_query(F.data.startswith("prod:"))
async def start_order(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":")[1])
    product = await get_product(product_id)
    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return
    pid, category_id, name, price, requires_email, photo = product
    await state.update_data(product_id=pid, product_name=name, price=price,
                             requires_email=bool(requires_email), email=None)

    label_product = await get_setting_text("label_product")
    label_price = await get_setting_text("label_price")
    card_text = f"{label_product}: {name}\n{label_price}: {price}"

    if photo:
        await callback.message.answer_photo(photo, caption=card_text)
    else:
        await callback.message.answer(card_text)

    if requires_email:
        await send_styled(callback.message, "email_prompt")
        await state.set_state(OrderStates.waiting_email)
    else:
        await show_confirmation(callback.message, state)
    await callback.answer()


@user_router.message(OrderStates.waiting_email)
async def receive_email(message: Message, state: FSMContext):
    await state.update_data(email=message.text.strip())
    await show_confirmation(message, state)


async def show_confirmation(message: Message, state: FSMContext):
    data = await state.get_data()
    label_product = await get_setting_text("label_product")
    label_price = await get_setting_text("label_price")
    label_email = await get_setting_text("label_email")
    label_payment = await get_setting_text("label_payment")

    details = f"\n\n{label_product}: {data['product_name']}\n{label_price}: {data['price']}\n"
    if data.get("requires_email"):
        details += f"{label_email}: {data.get('email')}\n"
    details += f"{label_payment}: Перевод на карту"

    btn_back = await get_setting_text("btn_order_back")
    btn_continue = await get_setting_text("btn_order_continue")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=btn_back, callback_data="order_back"),
            InlineKeyboardButton(text=btn_continue, callback_data="order_confirm"),
        ]
    ])
    await send_styled(message, "confirm_header", reply_markup=kb, extra=details)
    await state.set_state(OrderStates.waiting_confirm)


@user_router.callback_query(OrderStates.waiting_confirm, F.data == "order_back")
async def order_go_back(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("requires_email"):
        await send_styled(callback.message, "email_prompt")
        await state.set_state(OrderStates.waiting_email)
    else:
        await state.clear()
        await callback.message.answer("Заказ отменён. Чтобы начать заново, нажмите /start")
    await callback.answer()


async def generate_unique_code() -> str:
    while True:
        candidate = "".join(random.choices("0123456789", k=6))
        if not await code_exists(candidate):
            return candidate


@user_router.callback_query(OrderStates.waiting_confirm, F.data == "order_confirm")
async def order_confirm(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    user = callback.from_user
    payment_code = await generate_unique_code()

    order_id = await create_order(
        user_id=user.id,
        username=user.username or user.full_name,
        product_id=data["product_id"],
        product_name=data["product_name"],
        price=data["price"],
        email=data.get("email"),
        payment_method="Перевод на карту",
        payment_code=payment_code,
    )

    card = await get_setting_text("card_number")
    text, photo = await get_setting("success")
    final_text = text.replace("{price}", data["price"]).replace("{code}", payment_code).replace("{card}", card)
    if photo:
        await callback.message.answer_photo(photo, caption=final_text)
    else:
        await callback.message.answer(final_text)

    await state.clear()
    await callback.answer()

    if ADMIN_CHAT_ID:
        who = f"@{user.username}" if user.username else user.full_name
        notify_text = f"🆕 Новый заказ #{order_id}\n\nПокупатель: {who} (id: {user.id})\n"
        notify_text += f"Товар: {data['product_name']}\n"
        notify_text += f"Цена: {data['price']}\n"
        if data.get("requires_email"):
            notify_text += f"Почта: {data.get('email')}\n"
        notify_text += f"Код для перевода: {payment_code} (проверить и подтвердить: /confirm {payment_code})"
        try:
            await bot.send_message(ADMIN_CHAT_ID, notify_text)
        except Exception:
            pass


# ============================================================
# ХЕНДЛЕРЫ АДМИНА
# ============================================================

admin_router = Router()


async def is_admin(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    db_admins = await list_admin_ids()
    return user_id in db_admins


class AddProductStates(StatesGroup):
    waiting_category = State()
    waiting_name = State()
    waiting_price = State()
    waiting_email_flag = State()
    waiting_photo = State()
    waiting_confirm = State()


class EditContentStates(StatesGroup):
    waiting_value = State()
    waiting_confirm = State()


class ProductPhotoStates(StatesGroup):
    waiting_photo = State()
    waiting_confirm = State()


SAVE_DISCARD_KB = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="🗑 Удалить", callback_data="pv:discard"),
        InlineKeyboardButton(text="💾 Сохранить", callback_data="pv:save"),
    ]
])


@admin_router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not await is_admin(message.from_user.id):
        return
    text = (
        "🛠 Админ-панель\n\n"
        "Каталог:\n"
        "/addproduct — добавить товар\n"
        "/products — список всех товаров\n"
        "/delproduct <id> — удалить товар\n"
        "/setproductphoto <id> — задать/сменить фото товара\n"
        "/categories — список категорий\n"
        "/delcategory <id> — удалить категорию (и все товары в ней)\n\n"
        "Заказы:\n"
        "/orders — последние заказы\n"
        "/confirm <код> — подтвердить оплату: показывает покупателя, товар и почту, "
        "и отмечает заказ как оплаченный\n\n"
        "Админы:\n"
        "/addadmin <user_id> — назначить админа\n"
        "/deladmin <user_id> — снять админа\n\n"
        "Тексты сообщений (пришлите текст или фото с подписью — перед сохранением бот покажет предпросмотр):\n"
    )
    text += "\n".join(f"/set_{key} — {label}" for key, label in EDITABLE_TEXTS.items())
    text += "\n\nПодписи кнопок, полей и номер карты (только текст):\n"
    text += "\n".join(f"/set_{key} — {label}" for key, label in EDITABLE_LABELS.items())
    await message.answer(text)


def register_edit_handler(key: str, supports_photo: bool):
    async def start_edit(message: Message, state: FSMContext):
        if not await is_admin(message.from_user.id):
            return
        await state.update_data(edit_key=key, edit_supports_photo=supports_photo)
        if supports_photo:
            await message.answer(
                "Пришлите новый текст, или фото с подписью (подпись станет текстом, "
                "а фото будет прикрепляться к этому сообщению бота).\n\n"
                "Чтобы убрать ранее прикреплённое фото — напишите: убрать фото"
            )
        else:
            await message.answer("Пришлите новый текст:")
        await state.set_state(EditContentStates.waiting_value)

    admin_router.message.register(start_edit, Command(f"set_{key}"))


for _key in EDITABLE_TEXTS:
    register_edit_handler(_key, supports_photo=True)
for _key in EDITABLE_LABELS:
    register_edit_handler(_key, supports_photo=False)


@admin_router.message(EditContentStates.waiting_value, F.photo)
async def edit_receive_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("edit_supports_photo"):
        await message.answer("Для этого пункта фото не поддерживается, пришлите текст.")
        return
    photo_id = message.photo[-1].file_id
    caption = message.caption.strip() if message.caption else None
    await state.update_data(pending_photo=photo_id, pending_text=caption, pending_remove_photo=False)
    await message.answer_photo(photo_id, caption=f"Предпросмотр:\n\n{caption or '(текст не изменится)'}",
                                reply_markup=SAVE_DISCARD_KB)
    await state.set_state(EditContentStates.waiting_confirm)


@admin_router.message(EditContentStates.waiting_value, F.text)
async def edit_receive_text(message: Message, state: FSMContext):
    data = await state.get_data()
    text = message.text.strip()
    if text.lower() == "убрать фото" and data.get("edit_supports_photo"):
        await state.update_data(pending_photo=None, pending_text=None, pending_remove_photo=True)
        await message.answer("Предпросмотр: фото будет убрано, текст останется прежним.",
                              reply_markup=SAVE_DISCARD_KB)
    else:
        await state.update_data(pending_photo=None, pending_text=text, pending_remove_photo=False)
        await message.answer(f"Предпросмотр:\n\n{text}", reply_markup=SAVE_DISCARD_KB)
    await state.set_state(EditContentStates.waiting_confirm)


@admin_router.callback_query(EditContentStates.waiting_confirm, F.data.startswith("pv:"))
async def edit_confirm(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[1]
    data = await state.get_data()
    key = data["edit_key"]
    if action == "save":
        if data.get("pending_remove_photo"):
            await clear_setting_photo(key)
        elif data.get("pending_photo"):
            await set_setting_photo(key, data["pending_photo"], value=data.get("pending_text"))
        else:
            await set_setting_text(key, data.get("pending_text") or "")
        await callback.message.answer("✅ Сохранено.")
    else:
        await callback.message.answer("🗑 Изменение отменено, прежнее значение осталось без изменений.")
    await state.clear()
    await callback.answer()


# ---------- Добавление товара ----------

@admin_router.message(Command("addproduct"))
async def cmd_addproduct(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    await message.answer(
        "Введите название категории (если такой категории ещё нет — она будет создана):"
    )
    await state.set_state(AddProductStates.waiting_category)


@admin_router.message(AddProductStates.waiting_category)
async def addproduct_category(message: Message, state: FSMContext):
    await state.update_data(category_name=message.text.strip())
    await message.answer("Введите название товара:")
    await state.set_state(AddProductStates.waiting_name)


@admin_router.message(AddProductStates.waiting_name)
async def addproduct_name(message: Message, state: FSMContext):
    await state.update_data(product_name=message.text.strip())
    await message.answer("Введите цену товара (например: 150₽ или 100⭐):")
    await state.set_state(AddProductStates.waiting_price)


@admin_router.message(AddProductStates.waiting_price)
async def addproduct_price(message: Message, state: FSMContext):
    await state.update_data(price=message.text.strip())
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Да", callback_data="email:yes"),
            InlineKeyboardButton(text="Нет", callback_data="email:no"),
        ]
    ])
    await message.answer("Нужна ли почта от игрового аккаунта для этого товара?", reply_markup=kb)
    await state.set_state(AddProductStates.waiting_email_flag)


@admin_router.callback_query(AddProductStates.waiting_email_flag, F.data.startswith("email:"))
async def addproduct_email_flag(callback: CallbackQuery, state: FSMContext):
    requires_email = callback.data.split(":")[1] == "yes"
    await state.update_data(requires_email=requires_email)
    await callback.message.edit_text(
        "Пришлите фото товара, или напишите «-», если фото не нужно:"
    )
    await state.set_state(AddProductStates.waiting_photo)
    await callback.answer()


async def show_product_preview(message: Message, state: FSMContext, photo_id):
    data = await state.get_data()
    await state.update_data(pending_photo=photo_id)
    text = (
        f"Предпросмотр товара:\n\n"
        f"Категория: {data['category_name']}\n"
        f"Название: {data['product_name']}\n"
        f"Цена: {data['price']}\n"
        f"Требуется почта: {'да' if data['requires_email'] else 'нет'}"
    )
    if photo_id:
        await message.answer_photo(photo_id, caption=text, reply_markup=SAVE_DISCARD_KB)
    else:
        await message.answer(text, reply_markup=SAVE_DISCARD_KB)
    await state.set_state(AddProductStates.waiting_confirm)


@admin_router.message(AddProductStates.waiting_photo, F.photo)
async def addproduct_photo(message: Message, state: FSMContext):
    await show_product_preview(message, state, message.photo[-1].file_id)


@admin_router.message(AddProductStates.waiting_photo, F.text)
async def addproduct_no_photo(message: Message, state: FSMContext):
    if message.text.strip() != "-":
        await message.answer("Пришлите фото товара, или напишите «-», если фото не нужно:")
        return
    await show_product_preview(message, state, None)


@admin_router.callback_query(AddProductStates.waiting_confirm, F.data.startswith("pv:"))
async def addproduct_confirm(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[1]
    data = await state.get_data()
    if action == "save":
        category_id = await get_or_create_category(data["category_name"])
        product_id = await add_product(
            category_id, data["product_name"], data["price"], data["requires_email"],
            data.get("pending_photo")
        )
        await callback.message.answer(f"✅ Товар добавлен (id {product_id}).")
    else:
        await callback.message.answer("🗑 Добавление товара отменено.")
    await state.clear()
    await callback.answer()


# ---------- Фото у существующего товара ----------

@admin_router.message(Command("setproductphoto"))
async def cmd_setproductphoto(message: Message, state: FSMContext):
    if not await is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /setproductphoto <id>")
        return
    product_id = int(parts[1])
    product = await get_product(product_id)
    if not product:
        await message.answer("Товар с таким id не найден.")
        return
    await state.update_data(product_id=product_id)
    await message.answer("Пришлите новое фото товара (или «-», чтобы убрать текущее фото):")
    await state.set_state(ProductPhotoStates.waiting_photo)


@admin_router.message(ProductPhotoStates.waiting_photo, F.photo)
async def setproductphoto_receive(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    await state.update_data(pending_photo=photo_id, pending_remove=False)
    await message.answer_photo(photo_id, caption="Предпросмотр нового фото товара",
                                reply_markup=SAVE_DISCARD_KB)
    await state.set_state(ProductPhotoStates.waiting_confirm)


@admin_router.message(ProductPhotoStates.waiting_photo, F.text == "-")
async def setproductphoto_clear(message: Message, state: FSMContext):
    await state.update_data(pending_photo=None, pending_remove=True)
    await message.answer("Предпросмотр: фото товара будет убрано.", reply_markup=SAVE_DISCARD_KB)
    await state.set_state(ProductPhotoStates.waiting_confirm)


@admin_router.callback_query(ProductPhotoStates.waiting_confirm, F.data.startswith("pv:"))
async def setproductphoto_confirm(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[1]
    data = await state.get_data()
    if action == "save":
        await set_product_photo(data["product_id"], data.get("pending_photo"))
        await callback.message.answer("✅ Фото товара обновлено.")
    else:
        await callback.message.answer("🗑 Изменение отменено.")
    await state.clear()
    await callback.answer()


# ---------- Просмотр / удаление ----------

@admin_router.message(Command("products"))
async def cmd_products(message: Message):
    if not await is_admin(message.from_user.id):
        return
    products = await list_all_products()
    if not products:
        await message.answer("Товаров пока нет.")
        return
    lines = [f"#{pid} [{cat}] {name} — {price}" for pid, cat, name, price in products]
    await message.answer("📦 Товары:\n\n" + "\n".join(lines))


@admin_router.message(Command("delproduct"))
async def cmd_delproduct(message: Message):
    if not await is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /delproduct <id>")
        return
    await delete_product(int(parts[1]))
    await message.answer("🗑 Товар удалён.")


@admin_router.message(Command("categories"))
async def cmd_categories(message: Message):
    if not await is_admin(message.from_user.id):
        return
    cats = await list_categories()
    if not cats:
        await message.answer("Категорий пока нет.")
        return
    lines = [f"#{cid} {name}" for cid, name in cats]
    await message.answer("📂 Категории:\n\n" + "\n".join(lines))


@admin_router.message(Command("delcategory"))
async def cmd_delcategory(message: Message):
    if not await is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /delcategory <id>")
        return
    await delete_category(int(parts[1]))
    await message.answer("🗑 Категория и все товары в ней удалены.")


@admin_router.message(Command("orders"))
async def cmd_orders(message: Message):
    if not await is_admin(message.from_user.id):
        return
    orders = await list_recent_orders()
    if not orders:
        await message.answer("Заказов пока нет.")
        return
    lines = []
    for oid, username, uid, pname, price, status, created, pcode in orders:
        who = f"@{username}" if username else str(uid)
        line = f"#{oid} | {who} | {pname} ({price}) | {status} | {created}"
        if pcode:
            line += f" | код: {pcode}"
        lines.append(line)
    await message.answer("🧾 Последние заказы:\n\n" + "\n".join(lines))


@admin_router.message(Command("confirm"))
async def cmd_confirm(message: Message, bot: Bot):
    if not await is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer("Использование: /confirm <код из комментария к переводу>")
        return
    code = parts[1].strip()
    order = await get_order_by_code(code)
    if not order:
        await message.answer("❌ Заказ с таким кодом не найден.")
        return

    order_id, user_id, username, product_name, price, email, payment_method, status = order
    who = f"@{username}" if username else str(user_id)
    text = (
        f"🔎 Заказ #{order_id} по коду {code}\n\n"
        f"Покупатель: {who} (id: {user_id})\n"
        f"Товар: {product_name}\n"
        f"Цена: {price}\n"
        f"Почта: {email or '—'}\n"
        f"Способ оплаты: {payment_method}\n"
        f"Текущий статус: {status}"
    )

    if status == "paid":
        await message.answer(text + "\n\n⚠️ Этот заказ уже был отмечен как оплаченный ранее.")
        return

    await mark_order_paid(order_id)
    await message.answer(text + "\n\n✅ Отмечен как оплаченный.")

    try:
        await bot.send_message(user_id, "✅ Оплата получена! Поставщик скоро выполнит ваш заказ.")
    except Exception:
        pass


@admin_router.message(Command("addadmin"))
async def cmd_addadmin(message: Message):
    if not await is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /addadmin <user_id>")
        return
    await add_admin(int(parts[1]))
    await message.answer(f"✅ Пользователь {parts[1]} назначен админом.")


@admin_router.message(Command("deladmin"))
async def cmd_deladmin(message: Message):
    if not await is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /deladmin <user_id>")
        return
    await remove_admin(int(parts[1]))
    await message.answer(f"✅ Пользователь {parts[1]} снят с админки.")


# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(admin_router)
    dp.include_router(user_router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
