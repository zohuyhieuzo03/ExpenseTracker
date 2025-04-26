import gspread
import json
import os
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
import google.generativeai as genai
from datetime import datetime, timedelta

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
        headers = ['id', 'user_id', 'username', 'amount', 'note', 'timestamp']
        sheet.append_row(headers)
        print("Sheet initialized with headers:", headers)

def get_next_id():
    """Get the next available ID for a new expense"""
    records = sheet.get_all_records()
    if not records:
        return 1
    return max(int(record['id']) for record in records) + 1

def add_expense_to_sheet(user_id, username, amount, note):
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    next_id = get_next_id()
    sheet.append_row([str(next_id), str(user_id), username, str(amount), note, current_time])
    return next_id

# Initialize sheet on startup
initialize_sheet()

# ====== Functions ======

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

def get_expenses_by_time_range(user_id, time_range):
    """Get expenses filtered by time range"""
    records = sheet.get_all_records()
    user_expenses = [rec for rec in records if str(rec['user_id']) == str(user_id)]
    
    now = datetime.now()
    filtered_expenses = []
    
    for expense in user_expenses:
        expense_time = datetime.strptime(expense['timestamp'], '%Y-%m-%d %H:%M:%S')
        
        if time_range == 'today':
            if expense_time.date() == now.date():
                filtered_expenses.append(expense)
        elif time_range == 'week':
            week_start = now - timedelta(days=now.weekday())
            if expense_time.date() >= week_start.date():
                filtered_expenses.append(expense)
        elif time_range == 'month':
            if expense_time.month == now.month and expense_time.year == now.year:
                filtered_expenses.append(expense)
    
    return filtered_expenses

def get_expenses_by_date(user_id, date_str):
    """Get expenses for a specific date (format: DD/MM/YYYY)"""
    try:
        target_date = datetime.strptime(date_str, '%d/%m/%Y')
        records = sheet.get_all_records()
        user_expenses = [rec for rec in records if str(rec['user_id']) == str(user_id)]
        
        filtered_expenses = []
        for expense in user_expenses:
            expense_time = datetime.strptime(expense['timestamp'], '%Y-%m-%d %H:%M:%S')
            if expense_time.date() == target_date.date():
                filtered_expenses.append(expense)
        
        return filtered_expenses
    except ValueError:
        return []

def get_expenses_by_month(user_id, month_str):
    """Get expenses for a specific month (format: MM/YYYY)"""
    try:
        target_date = datetime.strptime(month_str, '%m/%Y')
        records = sheet.get_all_records()
        user_expenses = [rec for rec in records if str(rec['user_id']) == str(user_id)]
        
        filtered_expenses = []
        for expense in user_expenses:
            expense_time = datetime.strptime(expense['timestamp'], '%Y-%m-%d %H:%M:%S')
            if expense_time.month == target_date.month and expense_time.year == target_date.year:
                filtered_expenses.append(expense)
        
        return filtered_expenses
    except ValueError:
        return []

def get_expense_by_id(expense_id):
    """Get expense by its ID"""
    records = sheet.get_all_records()
    for record in records:
        if str(record['id']) == str(expense_id):
            return record
    return None

def update_expense(expense_id, amount=None, note=None):
    """Update an expense's amount and/or note"""
    records = sheet.get_all_records()
    for idx, record in enumerate(records, start=2):  # start=2 because row 1 is header
        if str(record['id']) == str(expense_id):
            if amount is not None:
                sheet.update_cell(idx, 4, str(amount))  # Column 4 is amount
            if note is not None:
                sheet.update_cell(idx, 5, note)  # Column 5 is note
            return True
    return False

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
        message = "\n".join([f"ID: {item['id']} - {item['amount']} - {item['note']}" for item in expenses])
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
            "• /edit <id> <amount> <note> - Edit an expense\n"
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
        expense_id = add_expense_to_sheet(user_id, username, amount, note)
        await update.message.reply_text(f'✅ Added (ID: {expense_id}): {amount} - {note}')
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
        expense_id = add_expense_to_sheet(user_id, username, amount, note)
        await update.message.reply_text(f'✅ Added (ID: {expense_id}): {amount:,.0f} - {note}')
    except Exception as e:
        await update.message.reply_text(f'❌ Error: {str(e)}')

async def list_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not context.args:
        # Default list all expenses
        expenses = get_all_expenses(user_id)
        if not expenses:
            await update.message.reply_text('No expenses yet.')
            return
        message = "\n".join([f"ID: {item['id']} - {item['amount']} - {item['note']}" for item in expenses])
        await update.message.reply_text(message)
        return
    
    time_filter = context.args[0].lower()
    
    if time_filter in ['today', 'week', 'month']:
        expenses = get_expenses_by_time_range(user_id, time_filter)
        if not expenses:
            await update.message.reply_text(f'No expenses for {time_filter}.')
            return
        message = f"📋 Expenses for {time_filter}:\n"
        message += "\n".join([f"ID: {item['id']} - {item['amount']} - {item['note']}" for item in expenses])
        await update.message.reply_text(message)
    elif '/' in time_filter:
        if len(time_filter.split('/')) == 2:  # MM/YYYY format
            expenses = get_expenses_by_month(user_id, time_filter)
            if not expenses:
                await update.message.reply_text(f'No expenses for {time_filter}.')
                return
            message = f"📋 Expenses for {time_filter}:\n"
            message += "\n".join([f"ID: {item['id']} - {item['amount']} - {item['note']}" for item in expenses])
            await update.message.reply_text(message)
        elif len(time_filter.split('/')) == 3:  # DD/MM/YYYY format
            expenses = get_expenses_by_date(user_id, time_filter)
            if not expenses:
                await update.message.reply_text(f'No expenses for {time_filter}.')
                return
            message = f"📋 Expenses for {time_filter}:\n"
            message += "\n".join([f"ID: {item['id']} - {item['amount']} - {item['note']}" for item in expenses])
            await update.message.reply_text(message)
    else:
        await update.message.reply_text(
            "Invalid time filter! Use:\n"
            "• /list today\n"
            "• /list week\n"
            "• /list month\n"
            "• /list DD/MM/YYYY\n"
            "• /list MM/YYYY"
        )

async def total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not context.args:
        # Default total for all time
        expenses = get_all_expenses(user_id)
        total_amount = sum(float(item['amount']) for item in expenses)
        await update.message.reply_text(f'💵 Total expenses (all time): {total_amount:,.0f}')
        return
    
    time_filter = context.args[0].lower()
    
    if time_filter in ['today', 'week', 'month']:
        expenses = get_expenses_by_time_range(user_id, time_filter)
        total_amount = sum(float(item['amount']) for item in expenses)
        await update.message.reply_text(f'💵 Total expenses ({time_filter}): {total_amount:,.0f}')
    elif '/' in time_filter:
        if len(time_filter.split('/')) == 2:  # MM/YYYY format
            expenses = get_expenses_by_month(user_id, time_filter)
            total_amount = sum(float(item['amount']) for item in expenses)
            await update.message.reply_text(f'💵 Total expenses for {time_filter}: {total_amount:,.0f}')
        elif len(time_filter.split('/')) == 3:  # DD/MM/YYYY format
            expenses = get_expenses_by_date(user_id, time_filter)
            total_amount = sum(float(item['amount']) for item in expenses)
            await update.message.reply_text(f'💵 Total expenses for {time_filter}: {total_amount:,.0f}')
    else:
        await update.message.reply_text(
            "Invalid time filter! Use:\n"
            "• /total today\n"
            "• /total week\n"
            "• /total month\n"
            "• /total DD/MM/YYYY\n"
            "• /total MM/YYYY"
        )

async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit an expense by its ID"""
    if len(context.args) < 3:
        await update.message.reply_text(
            "Invalid syntax! Use: /edit <id> <amount> <note>\n"
            "Example: /edit 1 50000 lunch with friends"
        )
        return

    try:
        expense_id = context.args[0]
        amount = float(context.args[1])
        note = ' '.join(context.args[2:])

        # Check if expense exists
        expense = get_expense_by_id(expense_id)
        if not expense:
            await update.message.reply_text(f'❌ No expense found with ID: {expense_id}')
            return

        # Check if user owns this expense
        if str(expense['user_id']) != str(update.effective_user.id):
            await update.message.reply_text('❌ You can only edit your own expenses!')
            return

        # Update the expense
        if update_expense(expense_id, amount, note):
            await update.message.reply_text(
                f'✅ Updated expense (ID: {expense_id}):\n'
                f'Amount: {amount:,.0f}\n'
                f'Note: {note}'
            )
        else:
            await update.message.reply_text('❌ Failed to update expense')
    except ValueError:
        await update.message.reply_text('❌ Invalid amount!')
    except Exception as e:
        await update.message.reply_text(f'❌ Error: {str(e)}')

# ====== Main ======

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('add', add))
    app.add_handler(CommandHandler('addsmart', add_smart))
    app.add_handler(CommandHandler('list', list_expenses))
    app.add_handler(CommandHandler('total', total))
    app.add_handler(CommandHandler('edit', edit))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Bot running...")
    app.run_polling()

if __name__ == '__main__':
    main()
