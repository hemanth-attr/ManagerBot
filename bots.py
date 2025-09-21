from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ChatJoinRequestHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ChatMemberStatus

TOKEN = "YOUR_BOT_TOKEN"

# Track warnings
warnings = {}

# ----------------- Join Request Handler -----------------
async def join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.chat_join_request.from_user
    chat_id = update.chat_join_request.chat.id

    # 1️⃣ Check for username
    if not user.username:
        warnings[user.id] = 1
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"[{user.id}] In order to be accepted in the group, please set up a username.\nAction: Warn (1/3) ❕"
        )
        return

    # 2️⃣ Send rules in DM
    rules_text = f"""👋 {user.mention_html()}, please read the group rules:

1. Don't spam or promote!
2. No abusive language or stickers.
3. Must have a username.
4. Always use DVA for safe deals.

Do you accept the rules?"""

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

    data = query.data.split("_")
    action = data[0]
    user_id = int(data[1])
    chat_id = int(data[2])

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

    # Ignore admins
    chat_admins = await update.effective_chat.get_administrators()
    if user.id in [admin.user.id for admin in chat_admins]:
        return

    # Check if message contains a link
    if "http" in update.message.text.lower():
        warnings[user.id] = warnings.get(user.id, 0) + 1
        warn_count = warnings[user.id]
        await update.message.reply_text(f"{user.first_name} ⚠ Warning ({warn_count}/3)")

        if warn_count >= 3:
            await update.message.reply_text(f"{user.first_name} 🔇 Muted for spamming.")
            await update.message.delete()
            await context.bot.restrict_chat_member(
                update.effective_chat.id, 
                user.id, 
                permissions=None
            )

# ----------------- Main -----------------
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(ChatJoinRequestHandler(join_request))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, spam_handler))

app.run_polling()
