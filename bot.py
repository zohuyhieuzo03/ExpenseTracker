import gspread
import json
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
import google.generativeai as genai
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

# Predefined expense categories
EXPENSE_CATEGORIES = [
    '🍔 Food & Dining',
    '🏠 Housing',
    '🚗 Transportation',
    '🛍️ Shopping',
    '💊 Healthcare',
    '🎮 Entertainment',
    '📱 Utilities',
    '📚 Education',
    '✈️ Travel',
    '🎁 Gifts',
    '💰 Income',
    '📦 Other'
]

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
        headers = ['id', 'user_id', 'username', 'amount', 'note', 'category', 'timestamp']
        sheet.append_row(headers)
        print("Sheet initialized with headers:", headers)

def get_next_id():
    """Get the next available ID for a new expense"""
    records = sheet.get_all_records()
    if not records or len(records) <= 1:  # If no records or only header
        return 1
    # Skip the header row by starting from index 1
    return max(int(record['id']) for record in records[1:]) + 1

def add_expense_to_sheet(user_id, username, amount, note, category='📦 Other'):
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    next_id = get_next_id()
    sheet.append_row([str(next_id), str(user_id), username, str(amount), note, category, current_time])
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

def get_category_keyboard():
    """Create keyboard for category selection"""
    keyboard = []
    # Create rows of 2 buttons each
    for i in range(0, len(EXPENSE_CATEGORIES), 2):
        row = []
        row.append(InlineKeyboardButton(EXPENSE_CATEGORIES[i], callback_data=f'category_{EXPENSE_CATEGORIES[i]}'))
        if i + 1 < len(EXPENSE_CATEGORIES):
            row.append(InlineKeyboardButton(EXPENSE_CATEGORIES[i + 1], callback_data=f'category_{EXPENSE_CATEGORIES[i + 1]}'))
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def parse_expense_with_gemini(input_text: str) -> tuple[float, str, str]:
    """
    Parse expense text using Gemini AI to extract amount, note and category
    Returns a tuple of (amount, note, category)
    """
    prompt = f"""Parse the following expense text into amount, note and category. 
    The amount should be a number (can be in thousands with 'k' or millions with 'm').
    The note should be a description of the expense.
    The category should be one of these: {', '.join(EXPENSE_CATEGORIES)}
    Return ONLY a JSON object with 'amount', 'note' and 'category' fields, nothing else.
    
    Text: {input_text}
    
    Example output:
    {{
        "amount": 50000,
        "note": "lunch with friends",
        "category": "🍔 Food & Dining"
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
        return float(parsed['amount']), parsed['note'], parsed['category']
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

def update_expense(expense_id, amount=None, note=None, category=None):
    """Update an expense's amount, note, and/or category"""
    records = sheet.get_all_records()
    for idx, record in enumerate(records, start=2):  # start=2 because row 1 is header
        if str(record['id']) == str(expense_id):
            if amount is not None:
                sheet.update_cell(idx, 4, str(amount))  # Column 4 is amount
            if note is not None:
                sheet.update_cell(idx, 5, note)  # Column 5 is note
            if category is not None:
                sheet.update_cell(idx, 6, category)  # Column 6 is category
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
        message = "\n".join([f"ID: {item['id']} - {item['amount']} - {item['note']} - {item['category']}" for item in expenses])
        await query.message.reply_text(message)
    elif query.data == 'total':
        user_id = query.from_user.id
        expenses = get_all_expenses(user_id)
        total_amount = sum(float(item['amount']) for item in expenses)
        await query.message.reply_text(f'💵 Total expenses: {total_amount}')
    elif query.data == 'help':
        await query.message.reply_text(
            "📝 Available commands:\n"
            "• /add <amount> <note> [category] - Add an expense\n"
            "• /list - View your expenses\n"
            "• /total - View total amount\n"
            "• /addsmart - Add an expense using Gemini AI\n"
            "• /edit <id> <amount> <note> [category] - Edit an expense\n"
        )
    elif query.data.startswith('category_'):
        category = query.data.replace('category_', '')
        
        # Handle category selection for new expense
        if 'pending_expense' in context.user_data:
            pending_expense = context.user_data['pending_expense']
            
            user_id = query.from_user.id
            username = query.from_user.first_name or query.from_user.username or "Unknown"
            expense_id = add_expense_to_sheet(
                user_id, 
                username, 
                pending_expense['amount'], 
                pending_expense['note'], 
                category
            )
            
            # Clear pending expense
            del context.user_data['pending_expense']
            
            await query.message.reply_text(
                f'✅ Added (ID: {expense_id}): {pending_expense["amount"]:,.0f} - {pending_expense["note"]} - {category}'
            )
            
        # Handle category selection for editing
        elif 'pending_edit' in context.user_data:
            pending_edit = context.user_data['pending_edit']
            
            # Update the expense
            if update_expense(pending_edit['expense_id'], pending_edit['amount'], pending_edit['note'], category):
                await query.message.reply_text(
                    f'✅ Updated expense (ID: {pending_edit["expense_id"]}):\n'
                    f'Amount: {pending_edit["amount"]:,.0f}\n'
                    f'Note: {pending_edit["note"]}\n'
                    f'Category: {category}'
                )
            else:
                await query.message.reply_text('❌ Failed to update expense')
            
            # Clear pending edit
            del context.user_data['pending_edit']
        else:
            await query.message.reply_text('❌ No pending operation to add category to.')

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            'Invalid syntax! Use: /add <amount> <note> [category]\n'
            'Available categories:\n' + '\n'.join(EXPENSE_CATEGORIES)
        )
        return
    try:
        amount = float(args[0])
        note = ' '.join(args[1:-1]) if len(args) > 2 else args[1]
        category = args[-1] if len(args) > 2 and args[-1] in EXPENSE_CATEGORIES else None
        
        if category is None:
            # Store amount and note in context for later use
            context.user_data['pending_expense'] = {
                'amount': amount,
                'note': note
            }
            await update.message.reply_text(
                'Please select a category:',
                reply_markup=get_category_keyboard()
            )
            return
        
        user_id = update.effective_user.id
        username = update.effective_user.first_name or update.effective_user.username or "Unknown"
        expense_id = add_expense_to_sheet(user_id, username, amount, note, category)
        await update.message.reply_text(f'✅ Added (ID: {expense_id}): {amount:,.0f} - {note} - {category}')
    except ValueError:
        await update.message.reply_text('❌ Invalid amount!')

async def add_smart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Smart version of add command that uses Gemini AI to parse the input"""
    if not context.args:
        await update.message.reply_text(
            'Please provide the expense details. Example: /addsmart 50k lunch with friends\n'
            'Available categories:\n' + '\n'.join(EXPENSE_CATEGORIES)
        )
        return

    try:
        # Combine all arguments into a single string
        input_text = ' '.join(context.args)
        
        # Parse using Gemini AI
        amount, note, category = parse_expense_with_gemini(input_text)
        
        user_id = update.effective_user.id
        username = update.effective_user.first_name or update.effective_user.username or "Unknown"
        expense_id = add_expense_to_sheet(user_id, username, amount, note, category)
        await update.message.reply_text(f'✅ Added (ID: {expense_id}): {amount:,.0f} - {note} - {category}')
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
        message = "\n".join([f"ID: {item['id']} - {item['amount']} - {item['note']} - {item['category']}" for item in expenses])
        await update.message.reply_text(message)
        return
    
    time_filter = context.args[0].lower()
    
    if time_filter in ['today', 'week', 'month']:
        expenses = get_expenses_by_time_range(user_id, time_filter)
        if not expenses:
            await update.message.reply_text(f'No expenses for {time_filter}.')
            return
        message = f"📋 Expenses for {time_filter}:\n"
        message += "\n".join([f"ID: {item['id']} - {item['amount']} - {item['note']} - {item['category']}" for item in expenses])
        await update.message.reply_text(message)
    elif '/' in time_filter:
        if len(time_filter.split('/')) == 2:  # MM/YYYY format
            expenses = get_expenses_by_month(user_id, time_filter)
            if not expenses:
                await update.message.reply_text(f'No expenses for {time_filter}.')
                return
            message = f"📋 Expenses for {time_filter}:\n"
            message += "\n".join([f"ID: {item['id']} - {item['amount']} - {item['note']} - {item['category']}" for item in expenses])
            await update.message.reply_text(message)
        elif len(time_filter.split('/')) == 3:  # DD/MM/YYYY format
            expenses = get_expenses_by_date(user_id, time_filter)
            if not expenses:
                await update.message.reply_text(f'No expenses for {time_filter}.')
                return
            message = f"📋 Expenses for {time_filter}:\n"
            message += "\n".join([f"ID: {item['id']} - {item['amount']} - {item['note']} - {item['category']}" for item in expenses])
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
            "Invalid syntax! Use: /edit <id> <amount> <note> [category]\n"
            "Example: /edit 1 50000 lunch with friends 🍔 Food & Dining\n"
            "Available categories:\n" + '\n'.join(EXPENSE_CATEGORIES)
        )
        return

    try:
        expense_id = context.args[0]
        amount = float(context.args[1])
        note = ' '.join(context.args[2:-1]) if len(context.args) > 3 else context.args[2]
        category = context.args[-1] if len(context.args) > 3 and context.args[-1] in EXPENSE_CATEGORIES else None

        # Check if expense exists
        expense = get_expense_by_id(expense_id)
        if not expense:
            await update.message.reply_text(f'❌ No expense found with ID: {expense_id}')
            return

        # Check if user owns this expense
        if str(expense['user_id']) != str(update.effective_user.id):
            await update.message.reply_text('❌ You can only edit your own expenses!')
            return

        if category is None:
            # Store expense details in context for later use
            context.user_data['pending_edit'] = {
                'expense_id': expense_id,
                'amount': amount,
                'note': note,
                'current_category': expense['category']
            }
            await update.message.reply_text(
                'Please select a category:',
                reply_markup=get_category_keyboard()
            )
            return

        # Update the expense
        if update_expense(expense_id, amount, note, category):
            await update.message.reply_text(
                f'✅ Updated expense (ID: {expense_id}):\n'
                f'Amount: {amount:,.0f}\n'
                f'Note: {note}\n'
                f'Category: {category}'
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
    app.add_handler(CommandHandler('a', add))  # Alias for add
    app.add_handler(CommandHandler('addsmart', add_smart))
    app.add_handler(CommandHandler('as', add_smart))  # Alias for addsmart
    app.add_handler(CommandHandler('list', list_expenses))
    app.add_handler(CommandHandler('l', list_expenses))  # Alias for list
    app.add_handler(CommandHandler('total', total))
    app.add_handler(CommandHandler('t', total))  # Alias for total
    app.add_handler(CommandHandler('edit', edit))
    app.add_handler(CommandHandler('e', edit))  # Alias for edit
    app.add_handler(CallbackQueryHandler(button_callback))

    print("Bot running...")
    app.run_polling()

if __name__ == '__main__':
    main()
