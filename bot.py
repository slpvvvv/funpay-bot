import uuid
import sqlite3
import logging
import threading
import os
import asyncio
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

# Цены
PRICE_PER_REVIEW_RUB = 33
STARS_PER_REVIEW = 35
TON_PER_REVIEW = 0.3

CRYPTO_WALLET_TON = "UQCRGaqAqG72vK-B869dvLrA0znKYUfcW-MK9K5765oeVlD-"
MIN_REVIEWS = 1
MAX_REVIEWS = 500
MIN_OFFERS = 1
MIN_OFFER_PRICE = 1

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
                  created_at TEXT, paid_at TEXT, completed_at TEXT)''')
    conn.commit()
    conn.close()
    logger.info("База данных готова")

def save_order(order_id, user_id, username, reviews_count, funpay_link, amount_rub, amount_stars, amount_ton, payment_method):
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    c.execute('''INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (order_id, user_id, username, reviews_count, funpay_link, amount_rub, amount_stars, amount_ton, 
               payment_method, None, 'pending', datetime.now().isoformat(), None, None))
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
    c.execute('SELECT * FROM orders WHERE order_id=?', (order_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {'order_id': row[0], 'user_id': row[1], 'username': row[2], 'reviews_count': row[3],
                'funpay_link': row[4], 'amount_rub': row[5], 'amount_stars': row[6], 'amount_ton': row[7],
                'payment_method': row[8], 'telegram_payment_charge_id': row[9], 'status': row[10],
                'created_at': row[11], 'paid_at': row[12], 'completed_at': row[13]}
    return None

def get_user_orders(user_id):
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    c.execute('SELECT order_id, reviews_count, amount_rub, status, created_at FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT 10', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("📝 Оставить отзыв", callback_data="order")],
        [InlineKeyboardButton("📊 Мои заказы", callback_data="my_orders")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]])

def get_payment_keyboard(order_id, amount_stars, amount_ton):
    keyboard = [
        [InlineKeyboardButton(f"💎 Telegram Stars ({amount_stars}⭐)", callback_data=f"stars_{order_id}")],
        [InlineKeyboardButton(f"🪙 TON ({amount_ton} TON)", callback_data=f"crypto_{order_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("📋 Все заказы", callback_data="admin_all_orders")],
        [InlineKeyboardButton("⏳ Ожидают оплаты", callback_data="admin_pending")],
        [InlineKeyboardButton("✅ Оплаченные", callback_data="admin_paid")],
        [InlineKeyboardButton("🎉 Выполненные", callback_data="admin_completed")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("🔙 В главное меню", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ========== ОБРАБОТЧИКИ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🌟 *Добро пожаловать в сервис накрутки отзывов FunPay!*\n\n"
        f"💰 *Цена:* {PRICE_PER_REVIEW_RUB}₽ за 1 отзыв\n"
        f"💎 *Stars:* {STARS_PER_REVIEW}⭐ за отзыв\n"
        f"🪙 *TON:* {TON_PER_REVIEW} TON за отзыв\n\n"
        f"⚠️ *ВАЖНОЕ УСЛОВИЕ:*\n"
        f"• На вашем профиле FunPay должно быть минимум *{MIN_OFFERS} объявлений*\n"
        f"• Цена каждого объявления от *{MIN_OFFER_PRICE}₽*\n\n"
        f"📞 Поддержка: {SUPPORT_CONTACT}",
        parse_mode='Markdown', reply_markup=get_main_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if query.data == "back_to_main":
        await query.edit_message_text("🌟 *Главное меню*", parse_mode='Markdown', reply_markup=get_main_keyboard())
        return
    
    if query.data == "order":
        context.user_data['state'] = 'waiting_reviews_count'
        await query.edit_message_text(
            f"📝 *Введите количество отзывов*\n\n"
            f"Доступно: от {MIN_REVIEWS} до {MAX_REVIEWS}\n"
            f"Цена: {PRICE_PER_REVIEW_RUB}₽ за отзыв\n\n"
            f"⚠️ *Напоминание:* на вашем профиле должно быть минимум {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n\n"
            f"Пример: `50`",
            parse_mode='Markdown', reply_markup=get_back_keyboard()
        )
        return
    
    if query.data == "my_orders":
        orders = get_user_orders(user_id)
        if not orders:
            await query.edit_message_text("📭 *У вас пока нет заказов*", parse_mode='Markdown', reply_markup=get_back_keyboard())
            return
        text = "📊 *Ваши заказы:*\n\n"
        for order in orders:
            status_emoji = {'pending': '⏳', 'paid': '✅', 'completed': '🎉'}.get(order[3], '❓')
            text += f"{status_emoji} #{order[0]}\n   📦 {order[1]} отз | {order[2]}₽\n   📅 {order[4][:10]}\n\n"
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=get_back_keyboard())
        return
    
    if query.data == "help":
        await query.edit_message_text(
            "🆘 *Помощь*\n\n"
            "*Как оставить заказ:*\n"
            "1️⃣ Нажмите «Оставить отзыв»\n"
            "2️⃣ Введите количество отзывов\n"
            "3️⃣ Отправьте ссылку на профиль FunPay\n"
            "4️⃣ Оплатите удобным способом\n\n"
            f"⚠️ *Важное условие:*\n"
            f"• На вашем профиле FunPay должно быть минимум *{MIN_OFFERS} объявлений*\n"
            f"• Цена каждого объявления от *{MIN_OFFER_PRICE}₽*\n\n"
            f"📞 *Поддержка:* {SUPPORT_CONTACT}",
            parse_mode='Markdown', reply_markup=get_back_keyboard()
        )
        return
    
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
        keyboard = [[InlineKeyboardButton("✅ Я оплатил", callback_data=f"confirm_crypto_{order_id}")]]
        await query.edit_message_text(
            f"🪙 *Оплата TON*\n\n"
            f"💰 Сумма: *{order['amount_ton']} TON*\n\n"
            f"📤 *Кошелек:*\n`{CRYPTO_WALLET_TON}`\n\n"
            f"⚠️ *Важное условие:* на вашем профиле должно быть минимум {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n\n"
            f"⚠️ *После перевода нажмите «Я оплатил»*",
            parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard)
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
            f"⚠️ *Проверьте условие:*\n"
            f"• {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽"
        )
        admin_keyboard = [
            [InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{order_id}")],
            [InlineKeyboardButton("❌ Отклонить (нет объявлений)", callback_data=f"reject_{order_id}")]
        ]
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(admin_keyboard))
        await query.edit_message_text("✅ Заявка отправлена! Администратор проверит платеж и наличие объявлений.", reply_markup=get_back_keyboard())
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
            text=f"✅ *Оплата подтверждена!*\n\n🆔 Заказ #{order_id}\n📦 {order['reviews_count']} отзывов\n\nАдминистратор проверил наличие {MIN_OFFERS} объявлений и приступит к выполнению.",
            parse_mode='Markdown'
        )
        await query.edit_message_text(f"✅ Заказ #{order_id} подтвержден!")
        return
    
    if query.data.startswith("reject_"):
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        order_id = query.data.replace("reject_", "")
        order = get_order(order_id)
        update_order_status(order_id, 'cancelled')
        await context.bot.send_message(
            chat_id=order['user_id'], 
            text=f"❌ *Заказ отклонен*\n\n🆔 Заказ #{order_id}\n\nПричина: на вашем профиле FunPay нет минимальных {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽.\n\nСоздайте объявления и попробуйте снова.\n\n📞 Поддержка: {SUPPORT_CONTACT}",
            parse_mode='Markdown'
        )
        await query.edit_message_text(f"❌ Заказ #{order_id} отклонен (нет объявлений).")
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
            text=f"🎉 *ЗАКАЗ ВЫПОЛНЕН!*\n\n🆔 Заказ #{order_id}\n📦 {order['reviews_count']} отзывов накручено!\n\nСпасибо, что воспользовались нашим сервисом!",
            parse_mode='Markdown'
        )
        await query.edit_message_text(f"🎉 Заказ #{order_id} выполнен!")
        return
    
    if query.data in ["admin_all_orders", "admin_pending", "admin_paid", "admin_completed", "admin_stats"]:
        if user_id != ADMIN_ID:
            await query.answer("⛔ Нет доступа", show_alert=True)
            return
        await query.edit_message_text("👑 *Админ панель*", parse_mode='Markdown', reply_markup=get_admin_keyboard())
        return

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    user_id = update.effective_user.id
    username = update.effective_user.username or "unknown"
    
    if state == 'waiting_reviews_count':
        try:
            reviews_count = int(update.message.text)
            if MIN_REVIEWS <= reviews_count <= MAX_REVIEWS:
                context.user_data['reviews_count'] = reviews_count
                context.user_data['state'] = 'waiting_funpay_link'
                await update.message.reply_text(
                    f"✅ *{reviews_count} отзывов*\n\n"
                    f"💰 *Сумма:* {reviews_count * 33}₽\n"
                    f"💎 *Stars:* {reviews_count * 35}⭐\n"
                    f"🪙 *TON:* {round(reviews_count * 0.3, 2)} TON\n\n"
                    f"⚠️ *Напоминание:* на вашем профиле должно быть минимум {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n\n"
                    f"🔗 *Отправьте ссылку на профиль FunPay:*\n\n"
                    f"Пример: `https://funpay.com/users/123456/`",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(f"❌ От {MIN_REVIEWS} до {MAX_REVIEWS}")
        except:
            await update.message.reply_text("❌ Введите число")
        return
    
    if state == 'waiting_funpay_link':
        if "funpay.com" in update.message.text:
            reviews_count = context.user_data['reviews_count']
            order_id = str(uuid.uuid4())[:8]
            save_order(order_id, user_id, username, reviews_count, update.message.text,
                      reviews_count*33, reviews_count*35, round(reviews_count*0.3,2), None)
            context.user_data['state'] = None
            await update.message.reply_text(
                f"🆔 *Заказ #{order_id} создан!*\n\n"
                f"📦 {reviews_count} отзывов\n"
                f"💰 {reviews_count*33}₽\n\n"
                f"⚠️ *Условие:* {MIN_OFFERS} объявлений по {MIN_OFFER_PRICE}₽\n\n"
                f"*Выберите способ оплаты:*",
                parse_mode='Markdown',
                reply_markup=get_payment_keyboard(order_id, reviews_count*35, round(reviews_count*0.3,2))
            )
        else:
            await update.message.reply_text("❌ Неверная ссылка. Пример: https://funpay.com/users/123456/")
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
        f"✅ *Оплата успешно получена!*\n\n"
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
            [InlineKeyboardButton("✅ Подтвердить", callback_data=f"approve_{order_id}")],
            [InlineKeyboardButton("❌ Отклонить (нет объявлений)", callback_data=f"reject_{order_id}")],
            [InlineKeyboardButton("🎉 Выполнен", callback_data=f"complete_{order_id}")]
        ])
    )

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /check ID - проверка заказа (только админ)"""
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа")
        return
    
    args = context.args
    if not args:
        await update.message.reply_text(
            "📝 *Использование:* `/check ORDER_ID`\n\n"
            "Пример: `/check a1b2c3d4`",
            parse_mode='Markdown'
        )
        return
    
    order_id = args[0]
    order = get_order(order_id)
    
    if not order:
        await update.message.reply_text(f"❌ Заказ `{order_id}` не найден", parse_mode='Markdown')
        return
    
    # Статусы
    status_text = {
        'pending': '⏳ Ожидает оплаты',
        'paid': '✅ Оплачен, в работе',
        'completed': '🎉 Выполнен',
        'cancelled': '❌ Отменен'
    }.get(order['status'], '❓ Неизвестно')
    
    text = (
        f"📋 *Детали заказа*\n\n"
        f"🆔 ID: `{order_id}`\n"
        f"👤 Пользователь: @{order['username']}\n"
        f"🆔 User ID: `{order['user_id']}`\n"
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
        text += f"🎉 Выполнен: {order['completed_at'][:19]}"
    
    await update.message.reply_text(text, parse_mode='Markdown')

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет доступа")
        return
    await update.message.reply_text("👑 *Админ панель*", parse_mode='Markdown', reply_markup=get_admin_keyboard())

# ========== FLASK ДЛЯ KEEP-ALIVE ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def index():
    return jsonify({"status": "Bot is running!", "time": datetime.now().isoformat()})

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))

# ========== ЗАПУСК ==========
async def run_bot():
    """Асинхронный запуск бота"""
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    
    logger.info("🤖 Бот запущен!")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    # Держим бота запущенным
    while True:
        await asyncio.sleep(3600)

def main():
    init_db()
    
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask keep-alive запущен на порту 10000")
    
    # Запускаем бота в асинхронном режиме
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Бот остановлен")

if __name__ == "__main__":
    main()
