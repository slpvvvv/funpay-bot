import uuid
import sqlite3
import logging
import threading
import os
import asyncio
import json
import requests
import time
from datetime import datetime
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    ContextTypes, MessageHandler, filters, PreCheckoutQueryHandler
)

# ========== КОНФИГУРАЦИЯ ==========
TELEGRAM_BOT_TOKEN = "8668091678:AAHYsrKBDYfekWfP1x-gLPD6pwAAvBLzrGA"
ADMIN_ID = 6480073415
SUPPORT_CONTACT = "@gortonn"
REVIEWS_CHANNEL = "https://t.me/+GtFmXCZuPE00NDIx"
FUNPAY_LOT_URL = "https://funpay.com/lots/offer?id=66845478"
SETTINGS_FILE = "settings.json"

# Настройки по умолчанию
DEFAULT_SETTINGS = {
    "price_per_review_rub": 33,
    "stars_per_review": 35,
    "ton_per_review": 0.3,
    "min_reviews": 1,
    "max_reviews": 500,
    "min_offers": 5,
    "min_offer_price": 1,
    "crypto_wallet_ton": "UQCRGaqAqG72vK-B869dvLrA0znKYUfcW-MK9K5765oeVlD-"
}

# Загрузка настроек
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

SETTINGS = load_settings()

# Переменные из настроек
PRICE_PER_REVIEW_RUB = SETTINGS["price_per_review_rub"]
STARS_PER_REVIEW = SETTINGS["stars_per_review"]
TON_PER_REVIEW = SETTINGS["ton_per_review"]
MIN_REVIEWS = SETTINGS["min_reviews"]
MAX_REVIEWS = SETTINGS["max_reviews"]
MIN_OFFERS = SETTINGS["min_offers"]
MIN_OFFER_PRICE = SETTINGS["min_offer_price"]
CRYPTO_WALLET_TON = SETTINGS["crypto_wallet_ton"]

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS orders
                 (order_id TEXT PRIMARY KEY, user_id INTEGER, username TEXT, reviews_count INTEGER,
                  funpay_link TEXT, amount_rub INTEGER, amount_stars INTEGER, amount_ton REAL,
                  payment_method TEXT, telegram_payment_charge_id TEXT, status TEXT,
                  created_at TEXT, paid_at TEXT, completed_at TEXT, cancelled_at TEXT, cancel_reason TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, first_seen TEXT, last_active TEXT)''')
    
    try:
        c.execute("ALTER TABLE orders ADD COLUMN cancelled_at TEXT")
    except sqlite3.OperationalError:
        pass
    
    try:
        c.execute("ALTER TABLE orders ADD COLUMN cancel_reason TEXT")
    except sqlite3.OperationalError:
        pass
    
    conn.commit()
    conn.close()
    logger.info("База данных готова")

def register_user(user_id, username):
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    c.execute('''INSERT OR IGNORE INTO users (user_id, username, first_seen, last_active) 
                 VALUES (?, ?, ?, ?)''', (user_id, username, datetime.now().isoformat(), datetime.now().isoformat()))
    c.execute('''UPDATE users SET last_active=? WHERE user_id=?''', (datetime.now().isoformat(), user_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    c.execute('SELECT user_id, username FROM users')
    users = c.fetchall()
    conn.close()
    return users

def save_order(order_id, user_id, username, reviews_count, funpay_link, amount_rub, amount_stars, amount_ton, payment_method):
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    c.execute('''INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (order_id, user_id, username, reviews_count, funpay_link, amount_rub, amount_stars, amount_ton, 
               payment_method, None, 'pending', datetime.now().isoformat(), None, None, None, None))
    conn.commit()
    conn.close()

def update_order_status(order_id, status, telegram_payment_charge_id=None, paid_at=None):
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    if telegram_payment_charge_id and paid_at:
        c.execute('UPDATE orders SET status=?, telegram_payment_charge_id=?, paid_at=? WHERE order_id=?',
                  (status, telegram_payment_charge_id, paid_at, order_id))
    elif paid_at:
        c.execute('UPDATE orders SET status=?, paid_at=? WHERE order_id=?', (status, paid_at, order_id))
    else:
        c.execute('UPDATE orders SET status=? WHERE order_id=?', (status, order_id))
    conn.commit()
    conn.close()

def cancel_order(order_id, reason):
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    c.execute('UPDATE orders SET status=?, cancelled_at=?, cancel_reason=? WHERE order_id=?',
              ('cancelled', datetime.now().isoformat(), reason, order_id))
    conn.commit()
    conn.close()

def update_order_completed(order_id):
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    c.execute('UPDATE orders SET status=?, completed_at=? WHERE order_id=?',
              ('completed', datetime.now().isoformat(), order_id))
    conn.commit()
    conn.close()

def get_order(order_id):
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    try:
        c.execute('SELECT * FROM orders WHERE order_id=?', (order_id,))
        row = c.fetchone()
        conn.close()
        if row:
            if len(row) >= 16:
                return {'order_id': row[0], 'user_id': row[1], 'username': row[2], 'reviews_count': row[3],
                        'funpay_link': row[4], 'amount_rub': row[5], 'amount_stars': row[6], 'amount_ton': row[7],
                        'payment_method': row[8], 'telegram_payment_charge_id': row[9], 'status': row[10],
                        'created_at': row[11], 'paid_at': row[12], 'completed_at': row[13], 
                        'cancelled_at': row[14], 'cancel_reason': row[15]}
            else:
                return {'order_id': row[0], 'user_id': row[1], 'username': row[2], 'reviews_count': row[3],
                        'funpay_link': row[4], 'amount_rub': row[5], 'amount_stars': row[6], 'amount_ton': row[7],
                        'payment_method': row[8], 'telegram_payment_charge_id': row[9], 'status': row[10],
                        'created_at': row[11], 'paid_at': row[12], 'completed_at': row[13],
                        'cancelled_at': None, 'cancel_reason': None}
    except Exception as e:
        logger.error(f"get_order error: {e}")
        conn.close()
        return None
    return None

def get_user_orders(user_id):
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    c.execute('SELECT order_id, reviews_count, amount_rub, status, created_at FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 10', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_all_orders(status=None):
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    if status:
        c.execute('SELECT * FROM orders WHERE status=? ORDER BY created_at DESC', (status,))
    else:
        c.execute('SELECT * FROM orders ORDER BY created_at DESC')
    rows = c.fetchall()
    conn.close()
    return rows

def get_stats():
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM orders')
    total_orders = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM orders WHERE status="paid" OR status="completed"')
    paid_orders = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM orders WHERE status="completed"')
    completed_orders = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM orders WHERE status="cancelled"')
    cancelled_orders = c.fetchone()[0]
    c.execute('SELECT SUM(amount_rub) FROM orders WHERE status="paid" OR status="completed"')
    total_revenue = c.fetchone()[0] or 0
    c.execute('SELECT SUM(reviews_count) FROM orders WHERE status="paid" OR status="completed"')
    total_reviews = c.fetchone()[0] or 0
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    conn.close()
    return {'total_orders': total_orders, 'paid_orders': paid_orders, 'completed_orders': completed_orders,
            'cancelled_orders': cancelled_orders, 'total_revenue': total_revenue, 'total_reviews': total_reviews,
            'total_users': total_users}

# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("🌟 ЗАКАЗАТЬ ОТЗЫВЫ", callback_data="order")],
        [InlineKeyboardButton("📦 МОИ ЗАКАЗЫ", callback_data="my_orders")],
        [InlineKeyboardButton("✨ ОТЗЫВЫ О НАС", callback_data="reviews")],
        [InlineKeyboardButton("❓ ПОМОЩЬ", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_main")]])

def get_payment_keyboard(order_id, amount_stars, amount_ton):
    keyboard = [
        [InlineKeyboardButton(f"⭐ Telegram Stars ({amount_stars}⭐)", callback_data=f"stars_{order_id}")],
        [InlineKeyboardButton(f"🪙 TON ({amount_ton} TON)", callback_data=f"crypto_{order_id}")],
        [InlineKeyboardButton("🎮 Оплатить через FanPay", callback_data=f"funpay_{order_id}", url=FUNPAY_LOT_URL)],
        [InlineKeyboardButton("◀️ НАЗАД", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📋 ВСЕ ЗАКАЗЫ", callback_data="admin_all_orders")],
        [InlineKeyboardButton("⏳ ОЖИДАЮТ ОПЛАТЫ", callback_data="admin_pending")],
        [InlineKeyboardButton("✅ ОПЛАЧЕННЫЕ", callback_data="admin_paid")],
        [InlineKeyboardButton("🎉 ВЫПОЛНЕННЫЕ", callback_data="admin_completed")],
        [InlineKeyboardButton("❌ ОТМЕНЕННЫЕ", callback_data="admin_cancelled")],
        [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data="admin_stats")],
        [InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data="admin_settings")],
        [InlineKeyboardButton("📢 РАССЫЛКА", callback_data="admin_broadcast")],
        [InlineKeyboardButton("◀️ ГЛАВНОЕ МЕНЮ", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_settings_keyboard():
    keyboard = [
        [InlineKeyboardButton(f"💰 Цена: {PRICE_PER_REVIEW_RUB}₽", callback_data="edit_price")],
        [InlineKeyboardButton(f"⭐ Stars: {STARS_PER_REVIEW}⭐", callback_data="edit_stars")],
        [InlineKeyboardButton(f"🪙 TON: {TON_PER_REVIEW}", callback_data="edit_ton")],
        [InlineKeyboardButton(f"📦 Отзывы: {MIN_REVIEWS}-{MAX_REVIEWS}", callback_data="edit_reviews")],
        [InlineKeyboardButton(f"📢 Условие: {MIN_OFFERS}×{MIN_OFFER_PRICE}₽", callback_data="edit_offers")],
        [InlineKeyboardButton(f"💳 TON кошелек", callback_data="edit_wallet")],
        [InlineKeyboardButton("◀️ НАЗАД В АДМИНКУ", callback_data="admin_panel")],
        [InlineKeyboardButton("🏠 ГЛАВНОЕ МЕНЮ", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def format_settings_text():
    return (
        f"🔧 *НАСТРОЙКИ СЕРВИСА*\n\n"
        f"💰 Цена за отзыв: *{PRICE_PER_REVIEW_RUB} ₽*\n"
        f"⭐ Telegram Stars: *{STARS_PER_REVIEW} ⭐*\n"
        f"🪙 TON: *{TON_PER_REVIEW} TON*\n\n"
        f"📦 Диапазон: *{MIN_REVIEWS}* — *{MAX_REVIEWS}* отзывов\n"
        f"📢 Условие: *{MIN_OFFERS}* объявлений по *{MIN_OFFER_PRICE}₽*\n\n"
        f"💳 Кошелек TON:\n`{CRYPTO_WALLET_TON}`\n\n"
        f"👇 *Нажмите на параметр для изменения*"
    )

# ========== ОБРАБОТЧИКИ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "unknown"
    register_user(user_id, username)
    
    await update.message.reply_text(
        "✨ *ДОБРО ПОЖАЛОВАТЬ В СЕРВИС НАКРУТКИ ОТЗЫВОВ FUNPAY!* ✨\n\n"
        f"💰 *ЦЕНА:* {PRICE_PER_REVIEW_RUB}₽ за 1 отзыв\n"
        f"⭐ *STARS:* {STARS_PER_REVIEW}⭐ за отзыв\n"
        f"🪙 *TON:* {TON_PER_REVIEW} TON за отзыв\n\n"
        f"📋 *УСЛОВИЯ:*\n"
        f"• Минимум *{MIN_OFFERS}* объявлений на вашем профиле\n"
        f"• Цена объявлений от *{MIN_OFFER_PRICE}₽*\n\n"
        f"⭐ *Наши отзывы:* {REVIEWS_CHANNEL}\n\n"
        f"📞 *ПОДДЕРЖКА:* {SUPPORT_CONTACT}\n\n"
        f"👇 *ВЫБЕРИТЕ ДЕЙСТВИЕ* 👇",
        parse_mode='Markdown', reply_markup=get_main_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if query.data == "back_to_main":
        await query.edit_message_text(
            "✨ *ГЛАВНОЕ МЕНЮ* ✨\n\nВыберите действие:",
            parse_mode='Markdown', reply_markup=get_main_keyboard()
        )
        return
    
    if query.data == "reviews":
        await query.edit_message_text(
            f"⭐ *ОТЗЫВЫ О НАС* ⭐\n\n"
            f"Посмотрите отзывы наших клиентов:\n"
            f"[👉 ПЕРЕЙТИ В КАНАЛ С ОТЗЫВАМИ 👈]({REVIEWS_CHANNEL})\n\n"
            f"✨ *Присоединяйтесь!* ✨",
            parse_mode='Markdown', reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "order":
        context.user_data['state'] = 'waiting_reviews_count'
        await query.edit_message_text(
            f"📝 *ОФОРМЛЕНИЕ ЗАКАЗА*\n\n"
            f"Введите количество необходимых отзывов:\n"
            f"*Доступно:* от {MIN_REVIEWS} до {MAX_REVIEWS}\n"
            f"*Цена:* {PRICE_PER_REVIEW_RUB}₽ за отзыв\n\n"
            f"⚠️ *Важно:* на вашем профиле должно быть минимум {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n\n"
            f"Пример: `50`",
            parse_mode='Markdown', reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "my_orders":
        orders = get_user_orders(user_id)
        if not orders:
            await query.edit_message_text(
                "📭 *У ВАС ПОКА НЕТ ЗАКАЗОВ*\n\n"
                "Нажмите «ЗАКАЗАТЬ ОТЗЫВЫ» для создания первого заказа!",
                parse_mode='Markdown', reply_markup=get_back_keyboard()
            )
            return
        text = "📦 *ВАШИ ЗАКАЗЫ* 📦\n\n"
        for order in orders:
            status_emoji = {'pending': '⏳', 'paid': '✅', 'completed': '🎉', 'cancelled': '❌'}.get(order[3], '❓')
            status_text = {'pending': 'Ожидает оплаты', 'paid': 'Оплачен', 'completed': 'Выполнен', 'cancelled': 'Отменен'}.get(order[3], 'Неизвестно')
            text += f"{status_emoji} *Заказ #{order[0]}*\n   📦 {order[1]} отзывов | {order[2]}₽\n   📊 {status_text}\n   📅 {order[4][:10]}\n\n"
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=get_back_keyboard())
        return
    
    if query.data == "help":
        await query.edit_message_text(
            "🆘 *ПОМОЩЬ* 🆘\n\n"
            "*КАК ОСТАВИТЬ ЗАКАЗ:*\n"
            "1️⃣ Нажмите «ЗАКАЗАТЬ ОТЗЫВЫ»\n"
            "2️⃣ Введите количество отзывов\n"
            "3️⃣ Отправьте ссылку на профиль FunPay\n"
            "4️⃣ Выберите способ оплаты\n\n"
            "*СПОСОБЫ ОПЛАТЫ:*\n"
            f"⭐ Telegram Stars — автоматически\n"
            f"🪙 TON — перевод на кошелек\n"
            f"🎮 FunPay — оплата через [лот]({FUNPAY_LOT_URL})\n\n"
            f"⚠️ *ВАЖНО:* при оплате через FunPay обязательно ознакомьтесь с описанием лота!\n\n"
            f"📞 *ПОДДЕРЖКА:* {SUPPORT_CONTACT}",
            parse_mode='Markdown', reply_markup=get_back_keyboard()
        )
        return
    
    # ========== АДМИН ПАНЕЛЬ ==========
    if query.data == "admin_panel":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        await query.edit_message_text(
            "👑 *АДМИН ПАНЕЛЬ* 👑\n\nВыберите действие:",
            parse_mode='Markdown', reply_markup=get_admin_keyboard()
        )
        return
    
    if query.data == "admin_all_orders":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        orders = get_all_orders()
        if not orders:
            await query.edit_message_text("📭 *Нет заказов*", parse_mode='Markdown', reply_markup=get_admin_keyboard())
            return
        text = "📋 *ВСЕ ЗАКАЗЫ (последние 20)*\n\n"
        for order in orders[:20]:
            status_emoji = {'pending': '⏳', 'paid': '✅', 'completed': '🎉', 'cancelled': '❌'}.get(order[10], '❓')
            text += f"{status_emoji} `{order[0]}` | {order[3]} отз | {order[5]}₽\n"
        text += "\n🔍 Для деталей используйте `/check ID`"
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=get_admin_keyboard())
        return
    
    if query.data == "admin_pending":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        orders = get_all_orders('pending')
        if not orders:
            await query.edit_message_text("⏳ *Нет заказов в ожидании*", parse_mode='Markdown', reply_markup=get_admin_keyboard())
            return
        text = "⏳ *ЗАКАЗЫ В ОЖИДАНИИ ОПЛАТЫ*\n\n"
        for order in orders:
            text += f"🆔 `{order[0]}` | {order[3]} отз | @{order[2]}\n"
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=get_admin_keyboard())
        return
    
    if query.data == "admin_paid":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        orders = get_all_orders('paid')
        if not orders:
            await query.edit_message_text("✅ *Нет оплаченных заказов*", parse_mode='Markdown', reply_markup=get_admin_keyboard())
            return
        text = "✅ *ОПЛАЧЕННЫЕ ЗАКАЗЫ (В РАБОТЕ)*\n\n"
        for order in orders:
            text += f"🆔 `{order[0]}` | {order[3]} отз | @{order[2]}\n"
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=get_admin_keyboard())
        return
    
    if query.data == "admin_completed":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        orders = get_all_orders('completed')
        if not orders:
            await query.edit_message_text("🎉 *Нет выполненных заказов*", parse_mode='Markdown', reply_markup=get_admin_keyboard())
            return
        text = "🎉 *ВЫПОЛНЕННЫЕ ЗАКАЗЫ*\n\n"
        for order in orders[:20]:
            text += f"🆔 `{order[0]}` | {order[3]} отз | {order[5]}₽\n"
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=get_admin_keyboard())
        return
    
    if query.data == "admin_cancelled":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        orders = get_all_orders('cancelled')
        if not orders:
            await query.edit_message_text("❌ *Нет отмененных заказов*", parse_mode='Markdown', reply_markup=get_admin_keyboard())
            return
        text = "❌ *ОТМЕНЕННЫЕ ЗАКАЗЫ*\n\n"
        for order in orders[:20]:
            reason = order[15] if len(order) > 15 else "Не указана"
            text += f"🆔 `{order[0]}` | {order[3]} отз | {reason}\n"
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=get_admin_keyboard())
        return
    
    if query.data == "admin_stats":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        stats = get_stats()
        text = (
            f"📊 *СТАТИСТИКА* 📊\n\n"
            f"👥 Всего пользователей: *{stats['total_users']}*\n"
            f"📦 Всего заказов: *{stats['total_orders']}*\n"
            f"✅ Оплаченных: *{stats['paid_orders']}*\n"
            f"🎉 Выполненных: *{stats['completed_orders']}*\n"
            f"❌ Отмененных: *{stats['cancelled_orders']}*\n"
            f"💰 Выручка: *{stats['total_revenue']} ₽*\n"
            f"⭐ Отзывов: *{stats['total_reviews']}*\n\n"
            f"📈 *Средний чек:* {stats['total_revenue'] // stats['paid_orders'] if stats['paid_orders'] > 0 else 0} ₽"
        )
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=get_admin_keyboard())
        return
    
    if query.data == "admin_settings":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        await query.edit_message_text(
            format_settings_text(),
            parse_mode='Markdown',
            reply_markup=get_settings_keyboard()
        )
        return
    
    if query.data == "admin_broadcast":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        context.user_data['broadcast_mode'] = True
        await query.edit_message_text(
            "📢 *РАССЫЛКА* 📢\n\n"
            "Введите текст сообщения для рассылки всем пользователям.\n\n"
            "Сообщение может содержать Markdown разметку.\n\n"
            "Для отмены нажмите /cancel",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard()
        )
        return
    
    # ========== РЕДАКТИРОВАНИЕ НАСТРОЕК ==========
    if query.data == "edit_price":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        context.user_data['edit_mode'] = 'price'
        await query.edit_message_text(
            f"💰 *ИЗМЕНЕНИЕ ЦЕНЫ*\n\n"
            f"Текущая цена: *{PRICE_PER_REVIEW_RUB} ₽*\n\n"
            f"Введите новую цену за 1 отзыв:",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "edit_stars":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        context.user_data['edit_mode'] = 'stars'
        await query.edit_message_text(
            f"⭐ *ИЗМЕНЕНИЕ STARS*\n\n"
            f"Текущее значение: *{STARS_PER_REVIEW} ⭐*\n\n"
            f"Введите новое количество Stars за 1 отзыв:",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "edit_ton":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        context.user_data['edit_mode'] = 'ton'
        await query.edit_message_text(
            f"🪙 *ИЗМЕНЕНИЕ TON*\n\n"
            f"Текущее значение: *{TON_PER_REVIEW} TON*\n\n"
            f"Введите новое количество TON за 1 отзыв:",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "edit_reviews":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        context.user_data['edit_mode'] = 'reviews'
        await query.edit_message_text(
            f"📦 *ИЗМЕНЕНИЕ ДИАПАЗОНА*\n\n"
            f"Текущий диапазон: *{MIN_REVIEWS}* — *{MAX_REVIEWS}*\n\n"
            f"Введите новый диапазон в формате: `мин макс`\n"
            f"Пример: `1 500`",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "edit_offers":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        context.user_data['edit_mode'] = 'offers'
        await query.edit_message_text(
            f"📢 *ИЗМЕНЕНИЕ УСЛОВИЯ*\n\n"
            f"Текущее условие: *{MIN_OFFERS}* объявлений по *{MIN_OFFER_PRICE}₽*\n\n"
            f"Введите новое условие в формате: `количество цена`\n"
            f"Пример: `5 1`",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "edit_wallet":
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        context.user_data['edit_mode'] = 'wallet'
        await query.edit_message_text(
            f"💳 *ИЗМЕНЕНИЕ TON КОШЕЛЬКА*\n\n"
            f"Текущий кошелек:\n`{CRYPTO_WALLET_TON}`\n\n"
            f"Вставьте новый адрес кошелька:",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard()
        )
        return
    
    # ========== ОБРАБОТКА ОПЛАТ ==========
    if query.data.startswith("stars_"):
        order_id = query.data.replace("stars_", "")
        order = get_order(order_id)
        if not order or order['status'] != 'pending':
            await query.edit_message_text("❌ Заказ не найден", reply_markup=get_back_keyboard())
            return
        try:
            await context.bot.send_invoice(
                chat_id=user_id, title="Накрутка отзывов FunPay",
                description=f"📦 {order['reviews_count']} отзывов\n🔗 {order['funpay_link']}\n\n⚠️ Условие: {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽",
                payload=f"order_{order_id}", provider_token="", currency="XTR",
                prices=[LabeledPrice(f"{order['reviews_count']} отзывов", order['amount_stars'])],
                start_parameter=f"order_{order_id}"
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {e}", reply_markup=get_back_keyboard())
        return
    
    if query.data.startswith("crypto_"):
        order_id = query.data.replace("crypto_", "")
        order = get_order(order_id)
        if not order or order['status'] != 'pending':
            await query.edit_message_text("❌ Заказ не найден", reply_markup=get_back_keyboard())
            return
        keyboard = [[InlineKeyboardButton("✅ Я ОПЛАТИЛ", callback_data=f"confirm_crypto_{order_id}")]]
        await query.edit_message_text(
            f"🪙 *ОПЛАТА TON*\n\n"
            f"💰 Сумма: *{order['amount_ton']} TON*\n\n"
            f"📤 *Кошелек для перевода:*\n`{CRYPTO_WALLET_TON}`\n\n"
            f"⚠️ *ВНИМАНИЕ!*\n"
            f"• На вашем профиле должно быть минимум {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n"
            f"• После перевода нажмите кнопку *«Я ОПЛАТИЛ»*\n\n"
            f"🆔 *ID заказа:* `{order_id}`",
            parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    if query.data.startswith("funpay_"):
        order_id = query.data.replace("funpay_", "")
        order = get_order(order_id)
        if not order or order['status'] != 'pending':
            await query.edit_message_text("❌ Заказ не найден", reply_markup=get_back_keyboard())
            return
        
        text = (
            f"🎮 *ОПЛАТА ЧЕРЕЗ FUNPAY* 🎮\n\n"
            f"💰 Сумма: *{order['amount_rub']} ₽*\n"
            f"📦 Отзывы: {order['reviews_count']}\n\n"
            f"📌 *ИНСТРУКЦИЯ:*\n"
            f"1️⃣ Перейдите по ссылке на [ЛОТ]({FUNPAY_LOT_URL})\n"
            f"2️⃣ *ВНИМАТЕЛЬНО ОЗНАКОМЬТЕСЬ С ОПИСАНИЕМ!*\n"
            f"3️⃣ Оплатите заказ\n"
            f"4️⃣ После оплаты нажмите кнопку *«Я ОПЛАТИЛ»*\n\n"
            f"⚠️ *ВАЖНО:* Без ознакомления с описанием заказ может быть отклонен!\n\n"
            f"🆔 *ID заказа:* `{order_id}`"
        )
        keyboard = [[InlineKeyboardButton("✅ Я ОПЛАТИЛ", callback_data=f"confirm_funpay_{order_id}")]]
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True
        )
        return
    
    if query.data.startswith("confirm_crypto_"):
        order_id = query.data.replace("confirm_crypto_", "")
        order = get_order(order_id)
        if not order:
            return
        admin_text = (
            f"🟡 *НОВЫЙ ЗАКАЗ (ПРОВЕРКА)*\n\n"
            f"🆔 Заказ: `{order_id}`\n"
            f"👤 Пользователь: @{order['username']}\n"
            f"📦 Отзывы: {order['reviews_count']}\n"
            f"🔗 Ссылка: {order['funpay_link']}\n"
            f"💰 Сумма: {order['amount_ton']} TON\n\n"
            f"⚠️ *ПРОВЕРЬТЕ:*\n"
            f"• Наличие {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n"
            f"• Наличие перевода"
        )
        admin_keyboard = [
            [InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data=f"approve_{order_id}")],
            [InlineKeyboardButton("❌ НЕТ 5 ОБЪЯВЛЕНИЙ", callback_data=f"reject_offers_{order_id}")],
            [InlineKeyboardButton("❌ НЕТ ПЕРЕВОДА", callback_data=f"reject_payment_{order_id}")]
        ]
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(admin_keyboard))
        await query.edit_message_text("✅ Заявка отправлена! Администратор проверит платеж.", reply_markup=get_back_keyboard())
        return
    
    if query.data.startswith("confirm_funpay_"):
        order_id = query.data.replace("confirm_funpay_", "")
        order = get_order(order_id)
        if not order:
            return
        
        update_order_status(order_id, 'waiting_verification')
        
        admin_text = (
            f"🟡 *ЗАКАЗ ОЖИДАЕТ ПРОВЕРКИ (FUNPAY)*\n\n"
            f"🆔 Заказ: `{order_id}`\n"
            f"👤 Пользователь: @{order['username']} (ID: {order['user_id']})\n"
            f"📦 Отзывы: {order['reviews_count']}\n"
            f"🔗 Ссылка FunPay: {order['funpay_link']}\n"
            f"💰 Сумма: {order['amount_rub']} ₽\n"
            f"💳 Способ: FunPay\n\n"
            f"⚠️ *ПРОВЕРЬТЕ ПЛАТЕЖ ПО ССЫЛКЕ:*\n[ЛОТ FUNPAY]({FUNPAY_LOT_URL})"
        )
        admin_keyboard = [
            [InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data=f"approve_{order_id}")],
            [InlineKeyboardButton("❌ ОТКЛОНИТЬ", callback_data=f"reject_funpay_{order_id}")]
        ]
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(admin_keyboard),
            disable_web_page_preview=True
        )
        
        await query.edit_message_text(
            f"✅ *ЗАЯВКА ОТПРАВЛЕНА!*\n\n"
            f"Администратор проверит оплату и подтвердит заказ.\n"
            f"Обычно это занимает до 15 минут.\n\n"
            f"🆔 Заказ: `{order_id}`",
            parse_mode='Markdown',
            reply_markup=get_back_keyboard()
        )
        return
    
    if query.data.startswith("approve_"):
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("approve_", "")
        order = get_order(order_id)
        update_order_status(order_id, 'paid', paid_at=datetime.now().isoformat())
        await context.bot.send_message(
            chat_id=order['user_id'], 
            text=f"✅ *ОПЛАТА ПОДТВЕРЖДЕНА!*\n\n🆔 Заказ #{order_id}\n📦 {order['reviews_count']} отзывов\n\nАдминистратор приступит к выполнению.",
            parse_mode='Markdown'
        )
        await query.edit_message_text(f"✅ Заказ #{order_id} подтвержден!")
        return
    
    if query.data.startswith("reject_offers_"):
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("reject_offers_", "")
        order = get_order(order_id)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        reason = f"Нет {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽"
        cancel_order(order_id, reason)
        await context.bot.send_message(
            chat_id=order['user_id'],
            text=f"❌ *ЗАКАЗ ОТКЛОНЕН*\n\n🆔 Заказ #{order_id}\n\nПричина: {reason}\n\nСоздайте {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽ и попробуйте снова.\n\n📞 Поддержка: {SUPPORT_CONTACT}",
            parse_mode='Markdown'
        )
        await query.edit_message_text(f"✅ Заказ #{order_id} отклонен (нет объявлений)")
        return
    
    if query.data.startswith("reject_payment_"):
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("reject_payment_", "")
        order = get_order(order_id)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        reason = "Платеж не получен"
        cancel_order(order_id, reason)
        await context.bot.send_message(
            chat_id=order['user_id'],
            text=f"❌ *ЗАКАЗ ОТКЛОНЕН*\n\n🆔 Заказ #{order_id}\n\nПричина: {reason}\n\nПроверьте правильность перевода и попробуйте снова.\n\n📞 Поддержка: {SUPPORT_CONTACT}",
            parse_mode='Markdown'
        )
        await query.edit_message_text(f"✅ Заказ #{order_id} отклонен (нет перевода)")
        return
    
    if query.data.startswith("reject_funpay_"):
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("reject_funpay_", "")
        order = get_order(order_id)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        
        reason = "Оплата через FunPay не подтверждена"
        cancel_order(order_id, reason)
        
        await context.bot.send_message(
            chat_id=order['user_id'],
            text=f"❌ *ЗАКАЗ ОТКЛОНЕН*\n\n🆔 Заказ #{order_id}\n\nПричина: {reason}\n\nПроверьте, что вы перевели точную сумму и ознакомились с описанием лота.\n\n📞 Поддержка: {SUPPORT_CONTACT}",
            parse_mode='Markdown'
        )
        await query.edit_message_text(f"✅ Заказ #{order_id} отклонен (оплата не подтверждена)")
        return
    
    if query.data.startswith("cancel_"):
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("cancel_", "")
        order = get_order(order_id)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        reason = "Отменен администратором"
        cancel_order(order_id, reason)
        await context.bot.send_message(
            chat_id=order['user_id'],
            text=f"❌ *ЗАКАЗ ОТМЕНЕН*\n\n🆔 Заказ #{order_id}\n\nПричина: {reason}\n\n📞 Вопросы: {SUPPORT_CONTACT}",
            parse_mode='Markdown'
        )
        await query.edit_message_text(f"✅ Заказ #{order_id} отменен")
        return
    
    if query.data.startswith("refund_"):
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("refund_", "")
        order = get_order(order_id)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        
        if order['payment_method'] == 'stars' and order['telegram_payment_charge_id']:
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"⚠️ *ТРЕБУЕТСЯ РУЧНОЙ ВОЗВРАТ STARS*\n\nЗаказ #{order_id}\nСумма: {order['amount_stars']}⭐\nCharge ID: {order['telegram_payment_charge_id']}\n\nНеобходимо вернуть средства вручную через @BotFather"
                )
                await query.edit_message_text(f"⚠️ Заказ #{order_id} отправлен на возврат")
            except Exception as e:
                await query.edit_message_text(f"❌ Ошибка: {e}")
                return
        else:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⚠️ *ТРЕБУЕТСЯ РУЧНОЙ ВОЗВРАТ TON*\n\nЗаказ #{order_id}\nСумма: {order['amount_ton']} TON\nКошелек: {CRYPTO_WALLET_TON}"
            )
            await query.edit_message_text(f"⚠️ Заказ #{order_id} отправлен на возврат TON")
        
        cancel_order(order_id, "Возврат средств")
        await context.bot.send_message(
            chat_id=order['user_id'],
            text=f"💰 *ВОЗВРАТ СРЕДСТВ*\n\nЗаказ #{order_id} отменен, средства возвращаются.\nОжидайте в течение 24 часов.\n\n📞 Вопросы: {SUPPORT_CONTACT}",
            parse_mode='Markdown'
        )
        return
    
    if query.data.startswith("complete_"):
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("complete_", "")
        order = get_order(order_id)
        update_order_completed(order_id)
        await context.bot.send_message(
            chat_id=order['user_id'], 
            text=f"🎉 *ЗАКАЗ ВЫПОЛНЕН!*\n\n🆔 Заказ #{order_id}\n📦 {order['reviews_count']} отзывов накручено!\n\nСпасибо, что воспользовались нашим сервисом! Оставьте пожалуйста отзыв, отправив его @fanpay_agent",
            parse_mode='Markdown'
        )
        await query.edit_message_text(f"🎉 Заказ #{order_id} выполнен!")
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    user_id = update.effective_user.id
    username = update.effective_user.username or "unknown"
    register_user(user_id, username)
    
    # Обработка рассылки
    if context.user_data.get('broadcast_mode') and user_id == ADMIN_ID:
        message_text = update.message.text
        if message_text == "/cancel":
            context.user_data['broadcast_mode'] = False
            await update.message.reply_text("❌ Рассылка отменена", reply_markup=get_admin_keyboard())
            return
        
        users = get_all_users()
        success = 0
        fail = 0
        
        await update.message.reply_text(f"📢 Начинаю рассылку {len(users)} пользователям...")
        
        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user[0],
                    text=f"📢 *УВЕДОМЛЕНИЕ ОТ АДМИНИСТРАТОРА*\n\n{message_text}",
                    parse_mode='Markdown'
                )
                success += 1
            except:
                fail += 1
            await asyncio.sleep(0.05)
            
        await update.message.reply_text(f"✅ Рассылка завершена!\n\n✅ Успешно: {success}\n❌ Ошибок: {fail}", reply_markup=get_admin_keyboard())
        context.user_data['broadcast_mode'] = False
        return
    
    # Обработка редактирования настроек
    edit_mode = context.user_data.get('edit_mode')
    if edit_mode and user_id == ADMIN_ID:
        try:
            value = update.message.text.strip()
            settings = load_settings()
            
            if edit_mode == 'price':
                settings['price_per_review_rub'] = int(value)
                save_settings(settings)
                await update.message.reply_text(f"✅ Цена изменена на {value} ₽")
            elif edit_mode == 'stars':
                settings['stars_per_review'] = int(value)
                save_settings(settings)
                await update.message.reply_text(f"✅ Stars изменены на {value} ⭐")
            elif edit_mode == 'ton':
                settings['ton_per_review'] = float(value)
                save_settings(settings)
                await update.message.reply_text(f"✅ TON изменен на {value}")
            elif edit_mode == 'reviews':
                parts = value.split()
                if len(parts) >= 2:
                    settings['min_reviews'] = int(parts[0])
                    settings['max_reviews'] = int(parts[1])
                    save_settings(settings)
                    await update.message.reply_text(f"✅ Диапазон отзывов: {parts[0]} - {parts[1]}")
                else:
                    await update.message.reply_text("❌ Формат: `мин макс`\nПример: `1 500`", parse_mode='Markdown')
                    return
            elif edit_mode == 'offers':
                parts = value.split()
                if len(parts) >= 2:
                    settings['min_offers'] = int(parts[0])
                    settings['min_offer_price'] = int(parts[1])
                    save_settings(settings)
                    await update.message.reply_text(f"✅ Условие: {parts[0]} объявлений по {parts[1]}₽")
                else:
                    await update.message.reply_text("❌ Формат: `количество цена`\nПример: `5 1`", parse_mode='Markdown')
                    return
            elif edit_mode == 'wallet':
                settings['crypto_wallet_ton'] = value
                save_settings(settings)
                await update.message.reply_text(f"✅ Кошелек обновлен")
            
            # Обновляем глобальные переменные
            global PRICE_PER_REVIEW_RUB, STARS_PER_REVIEW, TON_PER_REVIEW, MIN_REVIEWS, MAX_REVIEWS, MIN_OFFERS, MIN_OFFER_PRICE, CRYPTO_WALLET_TON
            PRICE_PER_REVIEW_RUB = settings['price_per_review_rub']
            STARS_PER_REVIEW = settings['stars_per_review']
            TON_PER_REVIEW = settings['ton_per_review']
            MIN_REVIEWS = settings['min_reviews']
            MAX_REVIEWS = settings['max_reviews']
            MIN_OFFERS = settings['min_offers']
            MIN_OFFER_PRICE = settings['min_offer_price']
            CRYPTO_WALLET_TON = settings['crypto_wallet_ton']
            
            await update.message.reply_text(
                format_settings_text(),
                parse_mode='Markdown',
                reply_markup=get_settings_keyboard()
            )
            
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
        
        context.user_data['edit_mode'] = None
        return
    
    # ========== ОСНОВНАЯ ОБРАБОТКА ЗАКАЗОВ ==========
    if state == 'waiting_reviews_count':
        try:
            reviews_count = int(update.message.text)
            if MIN_REVIEWS <= reviews_count <= MAX_REVIEWS:
                context.user_data['reviews_count'] = reviews_count
                context.user_data['state'] = 'waiting_funpay_link'
                await update.message.reply_text(
                    f"✅ *{reviews_count} ОТЗЫВОВ*\n\n"
                    f"💰 *Сумма:* {reviews_count * PRICE_PER_REVIEW_RUB}₽\n"
                    f"⭐ *Stars:* {reviews_count * STARS_PER_REVIEW}⭐\n"
                    f"🪙 *TON:* {round(reviews_count * TON_PER_REVIEW, 2)} TON\n\n"
                    f"⚠️ *Напоминание:* на вашем профиле должно быть минимум {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n\n"
                    f"🔗 *Отправьте ссылку на профиль FunPay:*\n\n"
                    f"Пример: `https://funpay.com/users/123456/`",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(f"❌ От {MIN_REVIEWS} до {MAX_REVIEWS}")
        except ValueError:
            await update.message.reply_text("❌ Введите число")
        return
    
    if state == 'waiting_funpay_link':
        print(f"DEBUG: Получена ссылка: {update.message.text}")  # для отладки
        
        if "funpay.com" in update.message.text:
            reviews_count = context.user_data['reviews_count']
            order_id = str(uuid.uuid4())[:8]
            
            save_order(order_id, user_id, username, reviews_count, update.message.text,
                      reviews_count * PRICE_PER_REVIEW_RUB, 
                      reviews_count * STARS_PER_REVIEW, 
                      round(reviews_count * TON_PER_REVIEW, 2), None)
            
            context.user_data['state'] = None
            
            await update.message.reply_text(
                f"🆔 *ЗАКАЗ #{order_id} СОЗДАН!*\n\n"
                f"📦 {reviews_count} отзывов\n"
                f"💰 {reviews_count * PRICE_PER_REVIEW_RUB}₽\n\n"
                f"⚠️ *Условие:* {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n\n"
                f"👇 *ВЫБЕРИТЕ СПОСОБ ОПЛАТЫ* 👇",
                parse_mode='Markdown',
                reply_markup=get_payment_keyboard(order_id, 
                    reviews_count * STARS_PER_REVIEW, 
                    round(reviews_count * TON_PER_REVIEW, 2))
            )
        else:
            await update.message.reply_text(
                "❌ *НЕВЕРНАЯ ССЫЛКА*\n\n"
                "Отправьте ссылку вида:\n"
                "`https://funpay.com/users/123456/`\n"
                "или\n"
                "`https://funpay.com/offers/123456789/`",
                parse_mode='Markdown'
            )
        return

async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    order_id = query.invoice_payload.replace("order_", "")
    if get_order(order_id):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Заказ не найден")

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    order_id = payment.invoice_payload.replace("order_", "")
    order = get_order(order_id)
    update_order_status(order_id, 'paid', payment.telegram_payment_charge_id, datetime.now().isoformat())
    await update.message.reply_text(
        f"✅ *ОПЛАТА УСПЕШНО ПОЛУЧЕНА!*\n\n"
        f"🆔 Заказ #{order_id}\n"
        f"📦 {order['reviews_count']} отзывов\n\n"
        f"⚠️ Администратор проверит наличие {MIN_OFFERS} объявлений и выполнит заказ.\n\n"
        f"📞 Вопросы: {SUPPORT_CONTACT}",
        parse_mode='Markdown'
    )
    admin_text = (
        f"🆕 *НОВЫЙ ЗАКАЗ!*\n\n"
        f"🆔 Заказ: `{order_id}`\n"
        f"👤 Пользователь: @{order['username']}\n"
        f"📦 Отзывы: {order['reviews_count']}\n"
        f"🔗 Ссылка: {order['funpay_link']}\n"
        f"💎 Оплачено: {payment.total_amount}⭐\n\n"
        f"⚠️ *Проверьте условие:* {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽"
    )
    await context.bot.send_message(
        chat_id=ADMIN_ID, 
        text=admin_text, 
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data=f"approve_{order_id}")],
            [InlineKeyboardButton("❌ НЕТ 5 ОБЪЯВЛЕНИЙ", callback_data=f"reject_offers_{order_id}")],
            [InlineKeyboardButton("💰 ВЕРНУТЬ СРЕДСТВА", callback_data=f"refund_{order_id}")],
            [InlineKeyboardButton("🎉 ВЫПОЛНЕН", callback_data=f"complete_{order_id}")]
        ])
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа")
        return
    await update.message.reply_text(
        "👑 *АДМИН ПАНЕЛЬ* 👑\n\nВыберите действие:",
        parse_mode='Markdown', reply_markup=get_admin_keyboard()
    )

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            "📝 *ИСПОЛЬЗОВАНИЕ:* `/check ID_ЗАКАЗА`\n\n"
            "Пример: `/check a1b2c3d4`",
            parse_mode='Markdown'
        )
        return
    
    order_id = args[0]
    order = get_order(order_id)
    
    if not order:
        await update.message.reply_text(f"❌ Заказ `{order_id}` не найден", parse_mode='Markdown')
        return
    
    status_text = {
        'pending': '⏳ Ожидает оплаты',
        'paid': '✅ Оплачен, в работе',
        'completed': '🎉 Выполнен',
        'cancelled': '❌ Отменен'
    }.get(order['status'], '❓ Неизвестно')
    
    text = (
        f"📋 *ДЕТАЛИ ЗАКАЗА #{order_id}*\n\n"
        f"👤 Пользователь: @{order['username']} (ID: {order['user_id']})\n"
        f"📦 Отзывы: {order['reviews_count']}\n"
        f"🔗 Ссылка: {order['funpay_link']}\n"
        f"💰 Сумма: {order['amount_rub']} ₽\n"
        f"💎 Stars: {order['amount_stars']} ⭐\n"
        f"🪙 TON: {order['amount_ton']}\n"
        f"📊 Статус: {status_text}\n"
        f"📅 Создан: {order['created_at'][:19]}\n"
    )
    
    if order['paid_at']:
        text += f"✅ Оплачен: {order['paid_at'][:19]}\n"
    if order['completed_at']:
        text += f"🎉 Выполнен: {order['completed_at'][:19]}\n"
    if order['cancelled_at']:
        text += f"❌ Отменен: {order['cancelled_at'][:19]}\nПричина: {order['cancel_reason']}"
    
    # Кнопки управления статусом
    keyboard = []
    if order['status'] == 'pending':
        keyboard.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ ОПЛАТУ", callback_data=f"approve_{order_id}")])
        keyboard.append([InlineKeyboardButton("❌ НЕТ 5 ОБЪЯВЛЕНИЙ", callback_data=f"reject_offers_{order_id}")])
        keyboard.append([InlineKeyboardButton("❌ НЕТ ПЕРЕВОДА", callback_data=f"reject_payment_{order_id}")])
    if order['status'] == 'paid':
        keyboard.append([InlineKeyboardButton("🎉 ОТМЕТИТЬ ВЫПОЛНЕННЫМ", callback_data=f"complete_{order_id}")])
        keyboard.append([InlineKeyboardButton("💰 ВЕРНУТЬ СРЕДСТВА", callback_data=f"refund_{order_id}")])
    if order['status'] in ['pending', 'paid']:
        keyboard.append([InlineKeyboardButton("🗑 ОТМЕНИТЬ ЗАКАЗ", callback_data=f"cancel_{order_id}")])
    
    if keyboard:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode='Markdown')

# ========== FLASK ДЛЯ KEEP-ALIVE ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "OK", 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

# ========== АВТО-ПИНГ ==========
def start_self_ping():
    def ping_loop():
        url = f"http://localhost:10000/"
        while True:
            try:
                response = requests.get(url, timeout=5)
                logger.info(f"🔄 Self-ping: {response.status_code}")
            except Exception as e:
                logger.error(f"❌ Self-ping error: {e}")
            time.sleep(240)
    
    ping_thread = threading.Thread(target=ping_loop, daemon=True)
    ping_thread.start()
    logger.info("🚀 Self-ping запущен (каждые 4 минуты)")

# ========== ЗАПУСК ==========
async def run_bot():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    
    logger.info("🤖 БОТ ЗАПУЩЕН!")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    while True:
        await asyncio.sleep(3600)

def main():
    init_db()
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask keep-alive запущен на порту 10000")
    
    start_self_ping()
    
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")

if __name__ == "__main__":
    main()
