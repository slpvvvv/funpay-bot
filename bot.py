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
FEEDBACK_CONTACT = "@fanpay_agent"
SETTINGS_FILE = "settings.json"

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

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            return json.load(f)
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

SETTINGS = load_settings()

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
    except: pass
    try:
        c.execute("ALTER TABLE orders ADD COLUMN cancel_reason TEXT")
    except: pass
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
            return {'order_id': row[0], 'user_id': row[1], 'username': row[2], 'reviews_count': row[3],
                    'funpay_link': row[4], 'amount_rub': row[5], 'amount_stars': row[6], 'amount_ton': row[7],
                    'payment_method': row[8], 'telegram_payment_charge_id': row[9], 'status': row[10],
                    'created_at': row[11], 'paid_at': row[12], 'completed_at': row[13], 
                    'cancelled_at': row[14] if len(row) > 14 else None, 
                    'cancel_reason': row[15] if len(row) > 15 else None}
    except: return None
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
        [InlineKeyboardButton("📝 Заказать отзывы", callback_data="order")],
        [InlineKeyboardButton("📦 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton("⭐ Отзывы", callback_data="reviews")],
        [InlineKeyboardButton("❓ Помощь", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀ Назад", callback_data="back_to_main")]])

def get_payment_keyboard(order_id, amount_stars, amount_ton):
    keyboard = [
        [InlineKeyboardButton(f"💎 Stars ({amount_stars}⭐)", callback_data=f"stars_{order_id}")],
        [InlineKeyboardButton(f"🪙 TON ({amount_ton})", callback_data=f"crypto_{order_id}")],
        [InlineKeyboardButton(f"🎮 FunPay", callback_data=f"funpay_{order_id}")],
        [InlineKeyboardButton("◀ Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_funpay_details_keyboard(order_id):
    keyboard = [
        [InlineKeyboardButton("📖 Перейти к лоту", url=FUNPAY_LOT_URL)],
        [InlineKeyboardButton("✅ Я оплатил", callback_data=f"confirm_funpay_{order_id}")],
        [InlineKeyboardButton("◀ Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📋 Все заказы", callback_data="admin_all_orders")],
        [InlineKeyboardButton("⏳ В ожидании", callback_data="admin_pending")],
        [InlineKeyboardButton("✅ Оплаченные", callback_data="admin_paid")],
        [InlineKeyboardButton("🎉 Выполненные", callback_data="admin_completed")],
        [InlineKeyboardButton("❌ Отмененные", callback_data="admin_cancelled")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("⚙ Настройки", callback_data="admin_settings")],
        [InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("◀ Выход", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_completed_order_keyboard(order_id):
    keyboard = [
        [InlineKeyboardButton("⭐ Оставить отзыв", url=f"https://t.me/{FEEDBACK_CONTACT.replace('@', '')}")],
        [InlineKeyboardButton("🔄 Новый заказ", callback_data="order")],
        [InlineKeyboardButton("◀ Главное меню", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_settings_keyboard():
    keyboard = [
        [InlineKeyboardButton(f"💰 Цена: {PRICE_PER_REVIEW_RUB}₽", callback_data="edit_price")],
        [InlineKeyboardButton(f"💎 Stars: {STARS_PER_REVIEW}⭐", callback_data="edit_stars")],
        [InlineKeyboardButton(f"🪙 TON: {TON_PER_REVIEW}", callback_data="edit_ton")],
        [InlineKeyboardButton(f"📦 Диапазон: {MIN_REVIEWS}-{MAX_REVIEWS}", callback_data="edit_reviews")],
        [InlineKeyboardButton(f"📢 Условие: {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽", callback_data="edit_offers")],
        [InlineKeyboardButton(f"💳 TON кошелек", callback_data="edit_wallet")],
        [InlineKeyboardButton("◀ Назад", callback_data="admin_panel")],
        [InlineKeyboardButton("◀ Главное меню", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def format_settings_text():
    return (
        f"🔧 Настройки\n\n"
        f"💰 Цена: {PRICE_PER_REVIEW_RUB}₽\n"
        f"💎 Stars: {STARS_PER_REVIEW}⭐\n"
        f"🪙 TON: {TON_PER_REVIEW}\n\n"
        f"📦 Отзывы: {MIN_REVIEWS} — {MAX_REVIEWS}\n"
        f"📢 Условие: {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n\n"
        f"💳 TON кошелек:\n{CRYPTO_WALLET_TON}"
    )

# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "unknown"
    register_user(user_id, username)
    
    await update.message.reply_text(
        f"Добро пожаловать!\n\n"
        f"💰 Цена: {PRICE_PER_REVIEW_RUB}₽ за отзыв\n"
        f"💎 Stars: {STARS_PER_REVIEW}⭐\n"
        f"🪙 TON: {TON_PER_REVIEW}\n\n"
        f"📋 Условие: на профиле должно быть {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n\n"
        f"⭐ Отзывы: {REVIEWS_CHANNEL}\n"
        f"📞 Поддержка: {SUPPORT_CONTACT}",
        reply_markup=get_main_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if query.data == "back_to_main":
        await query.edit_message_text("Главное меню", reply_markup=get_main_keyboard())
        return
    
    if query.data == "reviews":
        await query.edit_message_text(
            f"Отзывы\n\nКанал с отзывами: {REVIEWS_CHANNEL}",
            reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "order":
        context.user_data['state'] = 'waiting_reviews_count'
        await query.edit_message_text(
            f"Оформление заказа\n\n"
            f"Введите количество отзывов:\n"
            f"Доступно: {MIN_REVIEWS} — {MAX_REVIEWS}\n"
            f"Цена: {PRICE_PER_REVIEW_RUB}₽ за отзыв\n\n"
            f"Условие: {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n\n"
            f"Пример: 50",
            reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "my_orders":
        orders = get_user_orders(user_id)
        if not orders:
            await query.edit_message_text("Нет заказов", reply_markup=get_back_keyboard())
            return
        text = "Ваши заказы\n\n"
        for order in orders:
            status_emoji = {'pending': '⏳', 'paid': '✅', 'completed': '🎉', 'cancelled': '❌'}.get(order[3], '❓')
            text += f"{status_emoji} {order[0]}\n   {order[1]} отз | {order[2]}₽\n   {order[4][:10]}\n\n"
        await query.edit_message_text(text, reply_markup=get_back_keyboard())
        return
    
    if query.data == "help":
        await query.edit_message_text(
            "Помощь\n\n"
            "1. Нажмите «Заказать отзывы»\n"
            "2. Введите количество\n"
            "3. Отправьте ссылку на профиль\n"
            "4. Выберите способ оплаты\n\n"
            f"Оплата: Stars, TON, FunPay\n\n"
            f"Поддержка: {SUPPORT_CONTACT}",
            reply_markup=get_back_keyboard()
        )
        return
    
    # ========== АДМИН ==========
    if query.data == "admin_panel":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        await query.edit_message_text("Админ панель", reply_markup=get_admin_keyboard())
        return
    
    if query.data == "admin_all_orders":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        orders = get_all_orders()
        if not orders:
            await query.edit_message_text("Нет заказов", reply_markup=get_admin_keyboard())
            return
        text = "Все заказы\n\n"
        for order in orders[:20]:
            text += f"{order[0]} | {order[3]} отз | {order[5]}₽\n"
        text += "\n/check ID"
        await query.edit_message_text(text, reply_markup=get_admin_keyboard())
        return
    
    if query.data == "admin_pending":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        orders = get_all_orders('pending')
        if not orders:
            await query.edit_message_text("Нет заказов", reply_markup=get_admin_keyboard())
            return
        text = "Ожидают оплаты\n\n"
        for order in orders:
            text += f"{order[0]} | {order[3]} отз | @{order[2]}\n"
        await query.edit_message_text(text, reply_markup=get_admin_keyboard())
        return
    
    if query.data == "admin_paid":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        orders = get_all_orders('paid')
        if not orders:
            await query.edit_message_text("Нет заказов", reply_markup=get_admin_keyboard())
            return
        text = "Оплаченные\n\n"
        for order in orders:
            text += f"{order[0]} | {order[3]} отз | @{order[2]}\n"
        await query.edit_message_text(text, reply_markup=get_admin_keyboard())
        return
    
    if query.data == "admin_completed":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        orders = get_all_orders('completed')
        if not orders:
            await query.edit_message_text("Нет заказов", reply_markup=get_admin_keyboard())
            return
        text = "Выполненные\n\n"
        for order in orders[:20]:
            text += f"{order[0]} | {order[3]} отз | {order[5]}₽\n"
        await query.edit_message_text(text, reply_markup=get_admin_keyboard())
        return
    
    if query.data == "admin_cancelled":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        orders = get_all_orders('cancelled')
        if not orders:
            await query.edit_message_text("Нет заказов", reply_markup=get_admin_keyboard())
            return
        text = "Отмененные\n\n"
        for order in orders[:20]:
            reason = order[15] if len(order) > 15 else "—"
            text += f"{order[0]} | {order[3]} отз | {reason}\n"
        await query.edit_message_text(text, reply_markup=get_admin_keyboard())
        return
    
    if query.data == "admin_stats":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        stats = get_stats()
        text = (
            f"Статистика\n\n"
            f"👥 Пользователи: {stats['total_users']}\n"
            f"📦 Заказы: {stats['total_orders']}\n"
            f"✅ Оплачено: {stats['paid_orders']}\n"
            f"🎉 Выполнено: {stats['completed_orders']}\n"
            f"❌ Отменено: {stats['cancelled_orders']}\n"
            f"💰 Выручка: {stats['total_revenue']}₽\n"
            f"⭐ Отзывов: {stats['total_reviews']}"
        )
        await query.edit_message_text(text, reply_markup=get_admin_keyboard())
        return
    
    if query.data == "admin_settings":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        await query.edit_message_text(
            format_settings_text(),
            reply_markup=get_settings_keyboard()
        )
        return
    
    if query.data == "admin_broadcast":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        context.user_data['broadcast_mode'] = True
        await query.edit_message_text(
            "Рассылка\n\nВведите текст для рассылки\n/cancel — отмена",
            reply_markup=get_back_keyboard()
        )
        return
    
    # ========== РЕДАКТИРОВАНИЕ ==========
    if query.data == "edit_price":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        context.user_data['edit_mode'] = 'price'
        await query.edit_message_text(
            f"Изменить цену\n\nТекущая: {PRICE_PER_REVIEW_RUB}₽\nВведите новую:",
            reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "edit_stars":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        context.user_data['edit_mode'] = 'stars'
        await query.edit_message_text(
            f"Изменить Stars\n\nТекущие: {STARS_PER_REVIEW}⭐\nВведите новые:",
            reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "edit_ton":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        context.user_data['edit_mode'] = 'ton'
        await query.edit_message_text(
            f"Изменить TON\n\nТекущий: {TON_PER_REVIEW}\nВведите новый:",
            reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "edit_reviews":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        context.user_data['edit_mode'] = 'reviews'
        await query.edit_message_text(
            f"Изменить диапазон\n\nТекущий: {MIN_REVIEWS}-{MAX_REVIEWS}\nВведите: мин макс",
            reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "edit_offers":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        context.user_data['edit_mode'] = 'offers'
        await query.edit_message_text(
            f"Изменить условие\n\nТекущее: {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\nВведите: количество цена",
            reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "edit_wallet":
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        context.user_data['edit_mode'] = 'wallet'
        await query.edit_message_text(
            f"Изменить кошелек\n\nТекущий:\n{CRYPTO_WALLET_TON}\n\nВведите новый:",
            reply_markup=get_back_keyboard()
        )
        return
    
    # ========== ОПЛАТА ==========
    if query.data.startswith("stars_"):
        order_id = query.data.replace("stars_", "")
        order = get_order(order_id)
        if not order or order['status'] != 'pending':
            await query.edit_message_text("Заказ не найден", reply_markup=get_back_keyboard())
            return
        try:
            await context.bot.send_invoice(
                chat_id=user_id, title="Отзывы FunPay",
                description=f"{order['reviews_count']} отзывов\n{order['funpay_link']}\n\nУсловие: {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽",
                payload=f"order_{order_id}", provider_token="", currency="XTR",
                prices=[LabeledPrice(f"{order['reviews_count']} отзывов", order['amount_stars'])],
                start_parameter=f"order_{order_id}"
            )
        except Exception as e:
            await query.edit_message_text(f"Ошибка: {e}", reply_markup=get_back_keyboard())
        return
    
    if query.data.startswith("crypto_"):
        order_id = query.data.replace("crypto_", "")
        order = get_order(order_id)
        if not order or order['status'] != 'pending':
            await query.edit_message_text("Заказ не найден", reply_markup=get_back_keyboard())
            return
        keyboard = [[InlineKeyboardButton("✅ Я оплатил", callback_data=f"confirm_crypto_{order_id}")]]
        await query.edit_message_text(
            f"Оплата TON\n\n"
            f"Сумма: {order['amount_ton']} TON\n\n"
            f"Кошелек:\n{CRYPTO_WALLET_TON}\n\n"
            f"Условие: {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n\n"
            f"ID заказа: {order_id}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    if query.data.startswith("funpay_"):
        order_id = query.data.replace("funpay_", "")
        order = get_order(order_id)
        if not order or order['status'] != 'pending':
            await query.edit_message_text("Заказ не найден", reply_markup=get_back_keyboard())
            return
        
        text = (
            f"Оплата FunPay\n\n"
            f"Сумма: {order['amount_rub']}₽\n"
            f"Отзывы: {order['reviews_count']}\n\n"
            f"1. Нажмите «Перейти к лоту»\n"
            f"2. Ознакомьтесь с описанием\n"
            f"3. Оплатите\n"
            f"4. Нажмите «Я оплатил»\n\n"
            f"ID заказа: {order_id}"
        )
        await query.edit_message_text(
            text,
            reply_markup=get_funpay_details_keyboard(order_id),
            disable_web_page_preview=True
        )
        return
    
    if query.data.startswith("confirm_crypto_"):
        order_id = query.data.replace("confirm_crypto_", "")
        order = get_order(order_id)
        if not order: return
        admin_text = (
            f"Новый заказ (проверка)\n\n"
            f"ID: {order_id}\n"
            f"Пользователь: @{order['username']}\n"
            f"Отзывы: {order['reviews_count']}\n"
            f"Сумма: {order['amount_ton']} TON\n\n"
            f"Проверьте: {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽"
        )
        admin_keyboard = [
            [InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{order_id}")],
            [InlineKeyboardButton("❌ Нет объявлений", callback_data=f"reject_offers_{order_id}")],
            [InlineKeyboardButton("❌ Нет перевода", callback_data=f"reject_payment_{order_id}")]
        ]
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, reply_markup=InlineKeyboardMarkup(admin_keyboard))
        await query.edit_message_text("Заявка отправлена", reply_markup=get_back_keyboard())
        return
    
    if query.data.startswith("confirm_funpay_"):
        order_id = query.data.replace("confirm_funpay_", "")
        order = get_order(order_id)
        if not order: return
        
        update_order_status(order_id, 'waiting_verification')
        
        admin_text = (
            f"Заказ ожидает проверки (FunPay)\n\n"
            f"ID: {order_id}\n"
            f"Пользователь: @{order['username']}\n"
            f"Отзывы: {order['reviews_count']}\n"
            f"Сумма: {order['amount_rub']}₽\n\n"
            f"Лот: {FUNPAY_LOT_URL}"
        )
        admin_keyboard = [
            [InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{order_id}")],
            [InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_funpay_{order_id}")]
        ]
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, reply_markup=InlineKeyboardMarkup(admin_keyboard), disable_web_page_preview=True)
        await query.edit_message_text("Заявка отправлена", reply_markup=get_back_keyboard())
        return
    
    # ========== УПРАВЛЕНИЕ СТАТУСАМИ ==========
    if query.data.startswith("approve_"):
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("approve_", "")
        order = get_order(order_id)
        if not order: return
        update_order_status(order_id, 'paid', paid_at=datetime.now().isoformat())
        await context.bot.send_message(
            chat_id=order['user_id'], 
            text=f"✅ Оплата подтверждена\n\nЗаказ {order_id}\n{order['reviews_count']} отзывов"
        )
        await query.edit_message_text(f"Заказ {order_id} подтвержден", reply_markup=get_admin_keyboard())
        return
    
    if query.data.startswith("complete_"):
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("complete_", "")
        order = get_order(order_id)
        if not order:
            await query.edit_message_text("Заказ не найден")
            return
        update_order_completed(order_id)
        
        await context.bot.send_message(
            chat_id=order['user_id'], 
            text=f"✅ Заказ выполнен\n\n"
                 f"Заказ {order_id}\n"
                 f"{order['reviews_count']} отзывов\n\n"
                 f"⭐ Оставьте отзыв: {FEEDBACK_CONTACT}",
            reply_markup=get_completed_order_keyboard(order_id)
        )
        await query.edit_message_text(f"Заказ {order_id} выполнен", reply_markup=get_admin_keyboard())
        return
    
    if query.data.startswith("reject_offers_"):
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("reject_offers_", "")
        order = get_order(order_id)
        if not order: return
        reason = f"Нет {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽"
        cancel_order(order_id, reason)
        await context.bot.send_message(
            chat_id=order['user_id'],
            text=f"❌ Заказ отклонен\n\nПричина: {reason}\n\nПоддержка: {SUPPORT_CONTACT}"
        )
        await query.edit_message_text(f"Заказ {order_id} отклонен", reply_markup=get_admin_keyboard())
        return
    
    if query.data.startswith("reject_payment_"):
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("reject_payment_", "")
        order = get_order(order_id)
        if not order: return
        reason = "Платеж не получен"
        cancel_order(order_id, reason)
        await context.bot.send_message(
            chat_id=order['user_id'],
            text=f"❌ Заказ отклонен\n\nПричина: {reason}\n\nПоддержка: {SUPPORT_CONTACT}"
        )
        await query.edit_message_text(f"Заказ {order_id} отклонен", reply_markup=get_admin_keyboard())
        return
    
    if query.data.startswith("reject_funpay_"):
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("reject_funpay_", "")
        order = get_order(order_id)
        if not order: return
        reason = "Оплата не подтверждена"
        cancel_order(order_id, reason)
        await context.bot.send_message(
            chat_id=order['user_id'],
            text=f"❌ Заказ отклонен\n\nПричина: {reason}\n\nПоддержка: {SUPPORT_CONTACT}"
        )
        await query.edit_message_text(f"Заказ {order_id} отклонен", reply_markup=get_admin_keyboard())
        return
    
    if query.data.startswith("cancel_"):
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("cancel_", "")
        order = get_order(order_id)
        if not order: return
        reason = "Отменен администратором"
        cancel_order(order_id, reason)
        await context.bot.send_message(
            chat_id=order['user_id'],
            text=f"❌ Заказ отменен\n\nПричина: {reason}\n\nПоддержка: {SUPPORT_CONTACT}"
        )
        await query.edit_message_text(f"Заказ {order_id} отменен", reply_markup=get_admin_keyboard())
        return
    
    if query.data.startswith("refund_"):
        if user_id != ADMIN_ID:
            await query.answer("Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("refund_", "")
        order = get_order(order_id)
        if not order: return
        
        if order['payment_method'] == 'stars' and order['telegram_payment_charge_id']:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"Возврат Stars\n\nЗаказ {order_id}\n{order['amount_stars']}⭐\nCharge: {order['telegram_payment_charge_id']}"
            )
        else:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"Возврат TON\n\nЗаказ {order_id}\n{order['amount_ton']} TON"
            )
        
        cancel_order(order_id, "Возврат средств")
        await context.bot.send_message(
            chat_id=order['user_id'],
            text=f"💰 Возврат средств\n\nЗаказ {order_id}\nСредства возвращаются в течение 24 часов"
        )
        await query.edit_message_text(f"Заказ {order_id} отправлен на возврат", reply_markup=get_admin_keyboard())
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    user_id = update.effective_user.id
    username = update.effective_user.username or "unknown"
    register_user(user_id, username)
    
    # Рассылка
    if context.user_data.get('broadcast_mode') and user_id == ADMIN_ID:
        message_text = update.message.text
        if message_text == "/cancel":
            context.user_data['broadcast_mode'] = False
            await update.message.reply_text("Отменено", reply_markup=get_admin_keyboard())
            return
        
        users = get_all_users()
        success, fail = 0, 0
        await update.message.reply_text(f"Рассылка {len(users)} пользователям...")
        
        for user in users:
            try:
                await context.bot.send_message(
                    chat_id=user[0],
                    text=f"Уведомление\n\n{message_text}"
                )
                success += 1
            except:
                fail += 1
            await asyncio.sleep(0.05)
        
        await update.message.reply_text(f"Готово\n✅ {success}\n❌ {fail}", reply_markup=get_admin_keyboard())
        context.user_data['broadcast_mode'] = False
        return
    
    # Редактирование
    edit_mode = context.user_data.get('edit_mode')
    if edit_mode and user_id == ADMIN_ID:
        try:
            value = update.message.text.strip()
            settings = load_settings()
            
            if edit_mode == 'price':
                settings['price_per_review_rub'] = int(value)
                save_settings(settings)
                await update.message.reply_text(f"✅ Цена: {value}₽")
            elif edit_mode == 'stars':
                settings['stars_per_review'] = int(value)
                save_settings(settings)
                await update.message.reply_text(f"✅ Stars: {value}⭐")
            elif edit_mode == 'ton':
                settings['ton_per_review'] = float(value)
                save_settings(settings)
                await update.message.reply_text(f"✅ TON: {value}")
            elif edit_mode == 'reviews':
                parts = value.split()
                if len(parts) >= 2:
                    settings['min_reviews'] = int(parts[0])
                    settings['max_reviews'] = int(parts[1])
                    save_settings(settings)
                    await update.message.reply_text(f"✅ Диапазон: {parts[0]}-{parts[1]}")
                else:
                    await update.message.reply_text("❌ Формат: мин макс")
                    return
            elif edit_mode == 'offers':
                parts = value.split()
                if len(parts) >= 2:
                    settings['min_offers'] = int(parts[0])
                    settings['min_offer_price'] = int(parts[1])
                    save_settings(settings)
                    await update.message.reply_text(f"✅ Условие: {parts[0]}×{parts[1]}₽")
                else:
                    await update.message.reply_text("❌ Формат: количество цена")
                    return
            elif edit_mode == 'wallet':
                settings['crypto_wallet_ton'] = value
                save_settings(settings)
                await update.message.reply_text(f"✅ Кошелек обновлен")
            
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
                reply_markup=get_settings_keyboard()
            )
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")
        
        context.user_data['edit_mode'] = None
        return
    
    # Заказ
    if state == 'waiting_reviews_count':
        try:
            reviews_count = int(update.message.text)
            if MIN_REVIEWS <= reviews_count <= MAX_REVIEWS:
                context.user_data['reviews_count'] = reviews_count
                context.user_data['state'] = 'waiting_funpay_link'
                await update.message.reply_text(
                    f"✅ {reviews_count} отзывов\n\n"
                    f"Сумма: {reviews_count * PRICE_PER_REVIEW_RUB}₽\n"
                    f"Stars: {reviews_count * STARS_PER_REVIEW}⭐\n"
                    f"TON: {round(reviews_count * TON_PER_REVIEW, 2)}\n\n"
                    f"Условие: {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n\n"
                    f"Отправьте ссылку на профиль:\n"
                    f"https://funpay.com/users/123456/"
                )
            else:
                await update.message.reply_text(f"От {MIN_REVIEWS} до {MAX_REVIEWS}")
        except:
            await update.message.reply_text("Введите число")
        return
    
    if state == 'waiting_funpay_link':
        if "funpay.com" in update.message.text:
            reviews_count = context.user_data['reviews_count']
            order_id = str(uuid.uuid4())[:8]
            save_order(order_id, user_id, username, reviews_count, update.message.text,
                      reviews_count * PRICE_PER_REVIEW_RUB, 
                      reviews_count * STARS_PER_REVIEW, 
                      round(reviews_count * TON_PER_REVIEW, 2), None)
            context.user_data['state'] = None
            await update.message.reply_text(
                f"✅ Заказ {order_id} создан\n\n"
                f"{reviews_count} отзывов\n"
                f"{reviews_count * PRICE_PER_REVIEW_RUB}₽\n\n"
                f"Условие: {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n\n"
                f"Выберите оплату",
                reply_markup=get_payment_keyboard(order_id, 
                    reviews_count * STARS_PER_REVIEW, 
                    round(reviews_count * TON_PER_REVIEW, 2))
            )
        else:
            await update.message.reply_text(
                "❌ Неверная ссылка\n\n"
                "Пример: https://funpay.com/users/123456/"
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
        f"✅ Оплата получена\n\n"
        f"Заказ {order_id}\n"
        f"{order['reviews_count']} отзывов\n\n"
        f"Администратор проверит заказ"
    )
    admin_text = (
        f"Новый заказ\n\n"
        f"ID: {order_id}\n"
        f"Пользователь: @{order['username']}\n"
        f"Отзывы: {order['reviews_count']}\n"
        f"Сумма: {payment.total_amount}⭐\n\n"
        f"Условие: {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽"
    )
    await context.bot.send_message(
        chat_id=ADMIN_ID, 
        text=admin_text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{order_id}")],
            [InlineKeyboardButton("❌ Нет объявлений", callback_data=f"reject_offers_{order_id}")],
            [InlineKeyboardButton("💰 Вернуть", callback_data=f"refund_{order_id}")],
            [InlineKeyboardButton("🎉 Выполнен", callback_data=f"complete_{order_id}")]
        ])
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа")
        return
    await update.message.reply_text("Админ панель", reply_markup=get_admin_keyboard())

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text("Использование: /check ID\n\nПример: /check a1b2c3d4")
        return
    
    order_id = args[0]
    order = get_order(order_id)
    
    if not order:
        await update.message.reply_text(f"Заказ {order_id} не найден")
        return
    
    status_text = {
        'pending': '⏳ Ожидает оплаты',
        'paid': '✅ Оплачен',
        'completed': '🎉 Выполнен',
        'cancelled': '❌ Отменен'
    }.get(order['status'], '❓ Неизвестно')
    
    text = (
        f"Заказ {order_id}\n\n"
        f"👤 Пользователь: @{order['username']}\n"
        f"📦 Отзывы: {order['reviews_count']}\n"
        f"🔗 Ссылка: {order['funpay_link']}\n"
        f"💰 Сумма: {order['amount_rub']}₽\n"
        f"💎 Stars: {order['amount_stars']}⭐\n"
        f"🪙 TON: {order['amount_ton']}\n"
        f"📊 Статус: {status_text}\n"
        f"📅 Создан: {order['created_at'][:19]}"
    )
    
    if order['paid_at']:
        text += f"\n✅ Оплачен: {order['paid_at'][:19]}"
    if order['completed_at']:
        text += f"\n🎉 Выполнен: {order['completed_at'][:19]}"
    if order['cancelled_at']:
        text += f"\n❌ Отменен: {order['cancelled_at'][:19]}\nПричина: {order['cancel_reason']}"
    
    keyboard = []
    if order['status'] == 'pending':
        keyboard.append([InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{order_id}")])
        keyboard.append([InlineKeyboardButton("❌ Нет объявлений", callback_data=f"reject_offers_{order_id}")])
        keyboard.append([InlineKeyboardButton("❌ Нет перевода", callback_data=f"reject_payment_{order_id}")])
        keyboard.append([InlineKeyboardButton("🗑 Отменить", callback_data=f"cancel_{order_id}")])
    if order['status'] == 'paid':
        keyboard.append([InlineKeyboardButton("🎉 Выполнен", callback_data=f"complete_{order_id}")])
        keyboard.append([InlineKeyboardButton("💰 Вернуть", callback_data=f"refund_{order_id}")])
        keyboard.append([InlineKeyboardButton("🗑 Отменить", callback_data=f"cancel_{order_id}")])
    
    if keyboard:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text)

# ========== FLASK И АВТО-ПИНГ ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return "OK", 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

def start_self_ping():
    def ping_loop():
        url = f"http://localhost:10000/"
        while True:
            try:
                requests.get(url, timeout=5)
            except:
                pass
            time.sleep(240)
    ping_thread = threading.Thread(target=ping_loop, daemon=True)
    ping_thread.start()
    logger.info("Self-ping запущен")

async def run_bot():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    logger.info("Бот запущен")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    while True:
        await asyncio.sleep(3600)

def main():
    init_db()
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    start_self_ping()
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")

if __name__ == "__main__":
    main()
