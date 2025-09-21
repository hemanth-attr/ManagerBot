import os
import threading
import asyncio
import json
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, ChatJoinRequestHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, CommandHandler
)
from flask import Flask

# ----------------- Config -----------------
TOKEN = os.getenv("TOKEN")
WARNINGS_FILE = "warnings.json"
WARN_THRESHOLD = 3
MUTE_DURATION_HOURS = 24
BAN_AFTER_WARN = False

# Load warnings from file
if os.path.exists(WARNINGS_FILE):
    with open(WARNINGS_FILE, "r") as f:
        try:
            warnings = json.load(f)
            # Convert string dates back to datetime objects
            for chat_id in warnings:
                for user_id in warnings[chat_id]:
                    warnings[chat_id][user_id]["expires_at"] = datetime.fromisoformat(
                        warnings[chat_id][user_id]["expires_at"]
                    )
        except json.JSONDecodeError:
            print("Warning: warnings.json is corrupted or empty. Starting with an empty dictionary.")
            warnings = {}
else:
    warnings = {}

# ----------------- Flask Keepalive -----------------
app_flask = Flask(__name__)
@app_flask.route("/")
def home():
    """A simple Flask app to keep the bot alive on services like Render."""
    return "✅ ManagerBot is running!"

def run_flask():
    """Runs the Flask application in a separate thread."""
    port = int(os.environ.get("PORT", 5000))
    app_flask.run(host="0.0.0.0", port=port, threaded=True)

# ----------------- Utility -----------------
def save_warnings():
    """Persists warnings to a JSON file."""
    serializable = {}
    for chat_id in warnings:
        serializable[chat_id] = {}
        for user_id in warnings[chat_id]:
            entry = warnings[chat_id][user_id].copy()
            entry["expires_at"] = entry["expires_at"].isoformat()
            serializable[chat_id][user_id] = entry
    with open(WARNINGS_FILE, "w") as f:
        json.dump(serializable, f, indent=4)

async def clean_expired_warnings():
    """Removes expired warnings periodically."""
    while True:
        now = datetime.now()
        for chat_id in list(warnings.keys()):
            for user_id in list(warnings[chat_id].keys()):
                if warnings[chat_id][user_id]["expires_at"] < now:
                    del warnings[chat_id][user_id]
            if not warnings[chat_id]:
                del warnings[chat_id]
        save_warnings()
        await asyncio.sleep(3600)  # Run every hour

def get_offense_type(message):
    """Determines the type of offense from a message."""
    text = message.text.lower() if message.text else ""
    if message.forward_from:
        return "forwarded message"
    elif re.search(r'https?://\S+|t\.me/\S+', text):
        return "link"
    elif not message.from_user.username:
        return "no username"
    return "other"

# ----------------- Join Request Handler -----------------
async def join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles new chat join requests by sending a DM to the user with group rules.
    If the DM fails, the request is auto-approved.
    """
    user = update.chat_join_request.from_user
    chat_id = update.chat_join_request.chat.id
    
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
        await context.bot.send_message(
            chat_id=user.id,
            text=rules_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        print(f"Failed to send welcome message to user {user.id}: {e}. Auto-approving request.")
        await context.bot.approve_chat_join_request(chat_id, user.id)


# ----------------- Button Handler -----------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles all button clicks, including join requests and admin actions."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith(("accept_", "reject_")):
        action, user_id, chat_id = data.split("_")
        user_id, chat_id = int(user_id), int(chat_id)
        try:
            if action == "accept":
                await context.bot.approve_chat_join_request(chat_id, user_id)
                # Prepare welcome message for the group
                welcome_keyboard = [
                    [
                        InlineKeyboardButton("Contact Admin", url="https://t.me/admin_username"),
                        InlineKeyboardButton("Go to Chat", url=f"https://t.me/c/{str(chat_id)[4:]}")
                    ]
                ]
                # Send welcome message to the group
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Welcome to the group, {query.from_user.first_name}! ✅ Please use DVA for safe deals.",
                    reply_markup=InlineKeyboardMarkup(welcome_keyboard)
                )
                # Edit the original DM to a confirmation message
                await query.edit_message_text("✅ You accepted the rules. Welcome to the group!")
            else:
                await context.bot.decline_chat_join_request(chat_id, user_id)
                await query.edit_message_text("❌ You did not accept the rules. Request declined.")
        except Exception as e:
            print(f"Error handling join request button: {e}")
    
    elif data.startswith(("cancel_warn_", "ban_user_")):
        action, user_id, chat_id = data.split('_')
        user_id, chat_id = int(user_id), int(chat_id)
        chat_admins = await context.bot.get_chat_administrators(chat_id)
        if query.from_user.id not in [a.user.id for a in chat_admins]:
            await query.answer("Only admins can perform this action.", show_alert=True)
            return
        if action == "cancel_warn":
            if str(chat_id) in warnings and str(user_id) in warnings[str(chat_id)]:
                del warnings[str(chat_id)][str(user_id)]
                save_warnings()
                await query.edit_message_text(f"✅ Warning for user [{user_id}] has been canceled.")
            else:
                await query.edit_message_text("❌ No active warning found for this user.")
        elif action == "ban_user":
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
                await query.edit_message_text(f"🚫 User [{user_id}] has been banned.")
            except Exception as e:
                await query.edit_message_text(f"❌ Could not ban user [{user_id}]. Reason: {e}")

# ----------------- Admin Commands -----------------
async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually warns a user via command."""
    user = update.effective_user
    chat = update.effective_chat
    chat_id = str(chat.id)
    chat_admins = await context.bot.get_chat_administrators(chat.id)
    if user.id not in [admin.user.id for admin in chat_admins]:
        await update.message.reply_text("You are not an administrator.")
        return

    if not context.args:
        await update.message.reply_text("Usage: `/warn <user_id> [reason]`", parse_mode=ParseMode.MARKDOWN)
        return
        
    try:
        warned_user_id = str(context.args[0])
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else "No reason provided."
    except (ValueError, IndexError):
        await update.message.reply_text("Invalid user ID. Please provide a valid integer.")
        return
    
    if chat_id not in warnings:
        warnings[chat_id] = {}
    if warned_user_id not in warnings[chat_id]:
        warnings[chat_id][warned_user_id] = {"count": 0, "expires_at": datetime.now(), "offense_msg_id": 0}

    warnings[chat_id][warned_user_id]["count"] += 1
    warnings[chat_id][warned_user_id]["expires_at"] = datetime.now() + timedelta(days=1)
    warn_count = warnings[chat_id][warned_user_id]["count"]
    save_warnings()
    
    await update.message.reply_text(f"User [{warned_user_id}] has been warned. ⚠ ({warn_count}/{WARN_THRESHOLD}) Reason: {reason}")
    
async def unwarn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes a user's warning via command."""
    user = update.effective_user
    chat = update.effective_chat
    chat_id = str(chat.id)
    chat_admins = await context.bot.get_chat_administrators(chat.id)
    if user.id not in [admin.user.id for admin in chat_admins]:
        await update.message.reply_text("You are not an administrator.")
        return

    if not context.args or len(context.args) > 1:
        await update.message.reply_text("Usage: `/unwarn <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    
    try:
        user_id = str(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID. Please provide a valid integer.")
        return

    if chat_id in warnings and user_id in warnings[chat_id]:
        del warnings[chat_id][user_id]
        save_warnings()
        await update.message.reply_text(f"✅ Warning for user [{user_id}] has been removed.")
    else:
        await update.message.reply_text(f"User [{user_id}] has no active warnings.")

async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unbans a user via command."""
    user = update.effective_user
    chat = update.effective_chat
    chat_admins = await context.bot.get_chat_administrators(chat.id)
    if user.id not in [admin.user.id for admin in chat_admins]:
        await update.message.reply_text("You are not an administrator.")
        return
        
    if not context.args or len(context.args) > 1:
        await update.message.reply_text("Usage: `/unban <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    
    try:
        user_id = int(context.args[0])
        await context.bot.unban_chat_member(chat.id, user_id)
        await update.message.reply_text(f"✅ User [{user_id}] has been unbanned.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to unban user [{user_id}]. Reason: {e}")

# ----------------- Message Handler -----------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles regular messages, checking for forbidden content like
    links, forwarded messages, and lack of a username.
    """
    if not update.message:
        return

    user = update.effective_user
    chat = update.effective_chat
    chat_id = str(chat.id)
    offense = get_offense_type(update.message)

    chat_admins = await chat.get_administrators()
    if user.id in [a.user.id for a in chat_admins]:
        return

    if offense != "other":
        if chat_id not in warnings:
            warnings[chat_id] = {}
        if str(user.id) not in warnings[chat_id]:
            warnings[chat_id][str(user.id)] = {"count": 0, "expires_at": datetime.now(), "offense_msg_id": 0}
        
        warnings[chat_id][str(user.id)]["count"] += 1
        warnings[chat_id][str(user.id)]["expires_at"] = datetime.now() + timedelta(days=1)
        warnings[chat_id][str(user.id)]["offense_msg_id"] = update.message.message_id
        warn_count = warnings[chat_id][str(user.id)]["count"]
        save_warnings()

        try:
            await update.message.delete()
        except Exception:
            pass

        # Conditional notification: in group for first two warns, in personal for last one
        if warn_count < WARN_THRESHOLD:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠ Warning ({warn_count}/{WARN_THRESHOLD}) for {user.mention_html()} for a {offense}. {WARN_THRESHOLD - warn_count} warning(s) left before action.",
                    parse_mode=ParseMode.HTML,
                    reply_to_message_id=update.message.message_id
                )
            except Exception as e:
                print(f"Failed to send group warning to chat {chat_id}: {e}")
        else: # warn_count >= WARN_THRESHOLD
            action_text = ""
            if BAN_AFTER_WARN:
                action_text = "You have reached the maximum warnings. You will be banned."
            else:
                action_text = f"You have reached the maximum warnings. You will be muted for {MUTE_DURATION_HOURS} hours."
            
            try:
                await context.bot.send_message(
                    chat_id=user.id,
                    text=f"⚠ Warning ({warn_count}/{WARN_THRESHOLD}) for {offense}. {action_text}"
                )
            except Exception:
                pass

        # Take automated action
        if warn_count >= WARN_THRESHOLD:
            if BAN_AFTER_WARN:
                try:
                    await context.bot.ban_chat_member(chat.id, user.id)
                    await context.bot.send_message(chat.id, f"🚫 {user.mention_html()} has been automatically banned.", parse_mode=ParseMode.HTML)
                except Exception as e:
                    print(f"Failed to ban user {user.id}: {e}")
            else:
                try:
                    await context.bot.restrict_chat_member(
                        chat.id,
                        user.id,
                        permissions=ChatPermissions(can_send_messages=False),
                        until_date=datetime.now() + timedelta(hours=MUTE_DURATION_HOURS)
                    )
                    await context.bot.send_message(chat.id, f"🚫 {user.mention_html()} has been muted for {MUTE_DURATION_HOURS} hours.", parse_mode=ParseMode.HTML)
                except Exception as e:
                    print(f"Failed to mute user {user.id}: {e}")

        # Notify admins ONLY on the final warning
        if warn_count >= WARN_THRESHOLD:
            warn_until_str = warnings[chat_id][str(user.id)]["expires_at"].strftime("%d/%m/%Y %H:%M")
            for admin in chat_admins:
                try:
                    await context.bot.send_message(
                        chat_id=admin.user.id,
                        text=f"@{user.username or user.first_name} [{user.id}] sent '{offense}'. ⚠ Final Warn ({warn_count}/{WARN_THRESHOLD}) until {warn_until_str}. Action was taken.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔗 Go to Message", url=f"https://t.me/c/{str(chat.id)[4:]}/{update.message.message_id}")],
                            [InlineKeyboardButton("❌ Cancel Warning", callback_data=f"cancel_warn_{user.id}_{chat.id}"),
                             InlineKeyboardButton("🚫 Ban User", callback_data=f"ban_user_{user.id}_{chat.id}")]
                        ]),
                        parse_mode=ParseMode.MARKDOWN
                    )
                except Exception as e:
                    print(f"Failed to notify admin {admin.user.id}: {e}")

# ----------------- Main -----------------
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    
    application = ApplicationBuilder().token(TOKEN).build()
    
    # Schedule the async cleanup task to run in the background
    asyncio.get_event_loop().create_task(clean_expired_warnings())
    
    # Add handlers
    application.add_handler(ChatJoinRequestHandler(join_request))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, message_handler))
    application.add_handler(CommandHandler("warn", warn_command))
    application.add_handler(CommandHandler("unwarn", unwarn_command))
    application.add_handler(CommandHandler("unban", unban_command))
    
    application.run_polling()
