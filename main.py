import os
import json
import logging
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

print("Starting Food Log bot...", flush=True)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ALLOWED_USER_ID = int(os.environ["ALLOWED_USER_ID"])
GOOGLE_SHEET_NAME = os.environ.get("GOOGLE_SHEET_NAME", "Food Log")
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

genai.configure(api_key=GEMINI_API_KEY)
print("✓ All config loaded", flush=True)

def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open(GOOGLE_SHEET_NAME).sheet1

def ensure_headers(sheet):
    headers = sheet.row_values(1)
    if not headers or headers[0] != "Date":
        sheet.insert_row(["Date", "Meal", "Foods", "Portions"], 1)

def get_last_date(sheet):
    all_values = sheet.get_all_values()
    for row in reversed(all_values):
        if row and row[0] and row[0] != "Date":
            return row[0]
    return None

def insert_blank_row_if_new_day(sheet, date):
    last_date = get_last_date(sheet)
    if last_date and last_date != date:
        sheet.append_row(["", "", "", ""])

def resolve_date(date_intent: str) -> str:
    today = datetime.now()
    intent = date_intent.lower().strip()
    if intent in ["today", "σήμερα", ""]:
        return today.strftime("%Y-%m-%d")
    elif intent in ["yesterday", "χθες", "χθεσινή", "χθεσινος"]:
        return (today - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        # Try to find day of week
        days_el = ["δευτέρα", "τρίτη", "τετάρτη", "πέμπτη", "παρασκευή", "σάββατο", "κυριακή"]
        days_en = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        for i, day in enumerate(days_el + days_en):
            if day in intent:
                target_weekday = i % 7
                current_weekday = today.weekday()
                days_back = (current_weekday - target_weekday) % 7
                if days_back == 0:
                    days_back = 7
                return (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
        return today.strftime("%Y-%m-%d")

def append_meals(meals, date):
    sheet = get_sheet()
    ensure_headers(sheet)
    insert_blank_row_if_new_day(sheet, date)
    for meal in meals:
        sheet.append_row([
            date,
            meal.get("meal", ""),
            meal.get("foods", ""),
            meal.get("portions", "")
        ])

def extract_meals(message: str) -> dict:
    prompt = f"""You are a food log assistant. Extract all meals and the intended date from this message.
Today is {datetime.now().strftime("%Y-%m-%d")} ({datetime.now().strftime("%A")}).

The message may contain one or multiple meals. Meal names in Greek include:
πρωινό, δεκατιανό, μεσημεριανό, απογευματινό, βραδινό, βράδυ, σνακ, and variations without accents.

Also check if the message mentions registering for a different date (e.g. "χθες", "yesterday", "Παρασκευή", "Friday").

Return ONLY a JSON object with:
  "date_intent": string (e.g. "today", "yesterday", "Παρασκευή", "Friday" — use "today" if not mentioned)
  "meals": array where each item has:
    "meal": string (the meal name, capitalize first letter, e.g. "Πρωινό")
    "foods": string (comma-separated list of food items)
    "portions": string (comma-separated portions matching each food item, use "-" if unknown)

Example output:
{{
  "date_intent": "yesterday",
  "meals": [
    {{"meal": "Βραδινό", "foods": "κοτόπουλο, σαλάτα", "portions": "200γρ, -"}}
  ]
}}

If you cannot find any meal, return: {{"date_intent": "today", "meals": []}}

Message: {message}"""

    model = genai.GenerativeModel("gemini-2.5-flash-lite")
    response = model.generate_content(prompt)
    raw = response.text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ALLOWED_USER_ID:
        logger.warning(f"Unauthorized user {user_id}")
        return

    text = update.message.text
    logger.info(f"Received: {text}")
    await update.message.reply_text("⏳ Επεξεργασία...")

    try:
        result = extract_meals(text)
        meals = result.get("meals", [])
        date_intent = result.get("date_intent", "today")
        date = resolve_date(date_intent)

        if not meals:
            await update.message.reply_text(
                "❌ Δεν βρήκα γεύματα. Δοκίμασε κάτι σαν:\n"
                "• 'Πρωινό: 2 αυγά, 1 φέτα ψωμί'\n"
                "• 'Μεσημεριανό 150γρ κοτόπουλο, 250γρ αρακά'"
            )
            return

        append_meals(meals, date)

        date_label = "σήμερα" if date == datetime.now().strftime("%Y-%m-%d") else date
        reply = f"✅ Καταγράφηκε για {date_label}!\n\n"
        for meal in meals:
            reply += f"🍽 *{meal['meal']}*\n"
            foods = meal['foods'].split(',')
            portions = meal['portions'].split(',')
            for i, food in enumerate(foods):
                portion = portions[i].strip() if i < len(portions) else "-"
                food = food.strip()
                if portion and portion != "-":
                    reply += f"  • {food} — {portion}\n"
                else:
                    reply += f"  • {food}\n"
            reply += "\n"

        await update.message.reply_text(reply, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"❌ Κάτι πήγε στραβά: {str(e)}")

def main():
    print("Initializing Telegram app...", flush=True)
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Food Log bot is running...", flush=True)
    app.run_polling()

if __name__ == "__main__":
    main()
