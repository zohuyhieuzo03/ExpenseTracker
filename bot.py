import gspread
import json
import os
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
import google.generativeai as genai
from datetime import datetime

# Load environment variables from .env file
load_dotenv()

# Initialize Gemini AI
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.0-flash')

# ====== Config ======

# Read credentials from environment variables
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
GOOGLE_CREDENTIALS = os.getenv('GOOGLE_CREDENTIALS')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

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

def get_main_keyboard():
    """Create the main menu keyboard"""
    keyboard = [
        [
            InlineKeyboardButton("➕ Add Expense", callback_data='add_expense'),
            InlineKeyboardButton("📋 List Expenses", callback_data='list_expenses')
        ],
        [
            InlineKeyboardButton("💰 Total", callback_data='total'),
            InlineKeyboardButton("❓ Help", callback_data='help')
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def parse_expense_with_gemini(input_text: str) -> tuple[float, str]:
    """
    Parse expense text using Gemini AI to extract amount and note
    Returns a tuple of (amount, note)
    """
    prompt = f"""Parse the following expense text into amount and note. 
    The amount should be a number (can be in thousands with 'k' or millions with 'm').
    The note should be a description of the expense.
    Return ONLY a JSON object with 'amount' and 'note' fields, nothing else.
    
    Text: {input_text}
    
    Example output:
    {{
        "amount": 50000,
        "note": "lunch with friends"
    }}"""
    
    response = model.generate_content(prompt)
    result = response.text.strip()
    
    # Clean up the response to ensure it's valid JSON
    if result.startswith('```json'):
        result = result[7:]
    if result.endswith('```'):
        result = result[:-3]
    result = result.strip()
    
    try:
        # Parse the JSON response
        parsed = json.loads(result)
        return float(parsed['amount']), parsed['note']
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}")
        print(f"Raw response: {result}")
        raise ValueError("Failed to parse expense details. Please try again with a different format.")
    except KeyError as e:
        print(f"Missing key in JSON: {e}")
        print(f"Raw response: {result}")
        raise ValueError("Failed to parse expense details. Please try again with a different format.")
    except Exception as e:
        print(f"Unexpected error: {e}")
        print(f"Raw response: {result}")
        raise ValueError("An unexpected error occurred. Please try again.")

# ====== Telegram Bot Handlers ======

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 Welcome to Expense Tracker Bot!\n\n"
        "Use the buttons below or commands:\n"
        "• /add <amount> <note> - Add an expense\n"
        "• /list - View your expenses\n"
        "• /total - View total amount"
    )
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard())

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()

    if query.data == 'add_expense':
        await query.message.reply_text('Use /add <amount> <note> to add an expense')
    elif query.data == 'list_expenses':
        user_id = query.from_user.id
        expenses = get_all_expenses(user_id)
        if not expenses:
            await query.message.reply_text('No expenses yet.')
            return
        message = "\n".join([f"{idx+1}. {item['amount']} - {item['note']}" for idx, item in enumerate(expenses)])
        await query.message.reply_text(message)
    elif query.data == 'total':
        user_id = query.from_user.id
        expenses = get_all_expenses(user_id)
        total_amount = sum(float(item['amount']) for item in expenses)
        await query.message.reply_text(f'💵 Total expenses: {total_amount}')
    elif query.data == 'help':
        await query.message.reply_text(
            "📝 Available commands:\n"
            "• /add <amount> <note> - Add an expense\n"
            "• /list - View your expenses\n"
            "• /total - View total amount\n"
            "• /addsmart - Add an expense using Gemini AI\n"
        )

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

async def add_smart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Smart version of add command that uses Gemini AI to parse the input"""
    if not context.args:
        await update.message.reply_text('Please provide the expense details. Example: /addsmart 50k lunch with friends')
        return

    try:
        # Combine all arguments into a single string
        input_text = ' '.join(context.args)
        
        # Parse using Gemini AI
        amount, note = parse_expense_with_gemini(input_text)
        
        user_id = update.effective_user.id
        username = update.effective_user.first_name or update.effective_user.username or "Unknown"
        add_expense_to_sheet(user_id, username, amount, note)
        await update.message.reply_text(f'✅ Added: {amount:,.0f} - {note}')
    except Exception as e:
        await update.message.reply_text(f'❌ Error: {str(e)}')

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
    app.add_handler(CommandHandler('addsmart', add_smart))
    app.add_handler(CommandHandler('list', list_expenses))
    app.add_handler(CommandHandler('total', total))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Bot running...")
    app.run_polling()

if __name__ == '__main__':
    main()
