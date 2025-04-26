import gspread
import json
import os
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Load environment variables from .env file
load_dotenv()

# ====== Config ======

# Read credentials from environment variables
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
GOOGLE_CREDENTIALS = os.getenv('GOOGLE_CREDENTIALS')

# Parse Google credentials from JSON string
if GOOGLE_CREDENTIALS:
    credentials_dict = json.loads(GOOGLE_CREDENTIALS)
else:
    raise ValueError("GOOGLE_CREDENTIALS environment variable is not set")

# ====== Google Sheets Setup ======

scope = ["https://spreadsheets.google.com/feeds", 
         "https://www.googleapis.com/auth/spreadsheets", 
         "https://www.googleapis.com/auth/drive.file", 
         "https://www.googleapis.com/auth/drive"]

creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(GOOGLE_SHEET_ID).sheet1

def initialize_sheet():
    """Initialize sheet with required columns if empty"""
    if not sheet.get_all_records():
        headers = ['user_id', 'username', 'amount', 'note']
        sheet.append_row(headers)
        print("Sheet initialized with headers:", headers)

# Initialize sheet on startup
initialize_sheet()

# ====== Functions ======

def add_expense_to_sheet(user_id, username, amount, note):
    sheet.append_row([str(user_id), username, str(amount), note])

def get_all_expenses(user_id):
    records = sheet.get_all_records()
    user_expenses = [rec for rec in records if str(rec['user_id']) == str(user_id)]
    return user_expenses

# ====== Telegram Bot Handlers ======

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Hello! 🧾\nUse /add <amount> <note> to add an expense.\nUse /list to view the list, /total to view the total amount.')

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text('Invalid syntax! Use: /add <amount> <note>')
        return
    try:
        amount = float(args[0])
        note = ' '.join(args[1:])
        user_id = update.effective_user.id
        username = update.effective_user.first_name or update.effective_user.username or "Unknown"
        add_expense_to_sheet(user_id, username, amount, note)
        await update.message.reply_text(f'✅ Added: {amount} - {note}')
    except ValueError:
        await update.message.reply_text('❌ Invalid amount!')

async def list_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    expenses = get_all_expenses(user_id)
    if not expenses:
        await update.message.reply_text('No expenses yet.')
        return
    message = "\n".join([f"{idx+1}. {item['amount']} - {item['note']}" for idx, item in enumerate(expenses)])
    await update.message.reply_text(message)

async def total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    expenses = get_all_expenses(user_id)
    total_amount = sum(float(item['amount']) for item in expenses)
    await update.message.reply_text(f'💵 Total expenses: {total_amount}')

# ====== Main ======

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('add', add))
    app.add_handler(CommandHandler('list', list_expenses))
    app.add_handler(CommandHandler('total', total))

    print("Bot running...")
    app.run_polling()

if __name__ == '__main__':
    main()
