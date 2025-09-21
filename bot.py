import os
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, ChatJoinRequestHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.constants import ChatMemberStatus
from flask import Flask

# ----------------- Config -----------------
TOKEN = os.getenv("TOKEN")  # Set this in Render environment variables
warnings = {}  # Track warnings per user

# ----------------- Flask Keepalive -----------------
app_flask = Flask(__name__)

@app_flask.route("/")
def home():
    return "✅ ManagerBot is running on Render!"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app_flask.run(host="0.0.0.0", port=port)

# ----------------- Join Request Handler -----------------
async def join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.chat_join_request.from_user
    chat_id = update.chat_join_request.chat.id

    if not user.username:
        warnings[user.id] = 1
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"[{user.id}] Please set a username to be accepted.\nAction: Warn (1/3) ❕",
            parse_mode="HTML"
        )
        return

    rules_text = f"""👋 {user.mention_html()}, please read the group rules:

1. Don't spam or promote!
2. No abusive language or stickers.
3. Must have a username.
4. Always use DVA for safe deals.

Do you accept the rules?

Start interacting here: https://t.me/{context.bot.username}?start={user.id}"""

    keyboard = [
        [
            InlineKeyboardButton("❌ I don't accept", callback_data=f"reject_{user.id}_{chat_id}"),
            InlineKeyboardButton("✅ I accept", callback_data=f"accept_{user.id}_{chat_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await context.bot.send_message(chat_id=user.id, text=rules_text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        print(f"Cannot send DM to {user.username}: {e}")

# ----------------- Button Click Handler -----------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, user_id, chat_id = query.data.split("_")
    user_id, chat_id = int(user_id), int(chat_id)

    if action == "accept":
        try:
            await context.bot.approve_chat_join_request(chat_id, user_id)
            await query.edit_message_text("✅ You accepted the rules. Welcome to the group!")
        except Exception as e:
            print(e)
    elif action == "reject":
        try:
            await context.bot.decline_chat_join_request(chat_id, user_id)
            await query.edit_message_text("❌ You did not accept the rules. Request declined.")
        except Exception as e:
            print(e)

# ----------------- Spam Detection -----------------
async def spam_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    chat = update.effective_chat
    chat_admins = await chat.get_administrators()

    if user.id in [admin.user.id for admin in chat_admins]:
        return

    if "http" in update.message.text.lower():
        warnings[user.id] = warnings.get(user.id, 0) + 1
        warn_count = warnings[user.id]
        await update.message.reply_text(f"{user.first_name} ⚠ Warning ({warn_count}/3)")

        if warn_count >= 3:
            await update.message.reply_text(f"{user.first_name} 🔇 Muted for spamming.")
            await update.message.delete()
            await context.bot.restrict_chat_member(
                chat.id,
                user.id,
                permissions=None
            )

# ----------------- Main -----------------
if __name__ == "__main__":
    # Start Flask server in another thread
    threading.Thread(target=run_flask).start()

    # Start Telegram bot
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(ChatJoinRequestHandler(join_request))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, spam_handler))
    application.run_polling()
