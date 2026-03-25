import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ================= CONFIG =================
BOT_TOKEN = "8783570683:AAHQQaqRGhF3HLVsBuh8cVpmmkAl7aZXf2Q"
API_KEY = "ed7194ab-bbb4-4db8-927c-ce8678fc31fe"
# ==========================================


# -------- START COMMAND --------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏏 Cricket Bot Live!\n\nUse /score to get match info"
    )


# -------- PREDICTION LOGIC --------
def predict(status):
    status = status.lower()

    if "need" in status:
        return "📈 Chasing team advantage ↑"
    elif "won" in status:
        return "✅ Match finished"
    elif "trail" in status:
        return "📉 Batting team under pressure ↓"
    else:
        return "⚖️ Match balanced"


# -------- GET MATCH --------
def get_india_match(matches):
    for match in matches:
        if "India" in match["name"]:
            return match
    return None


# -------- SCORE COMMAND --------
async def score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Step 1: Try live matches
        url = f"https://api.cricapi.com/v1/currentMatches?apikey={API_KEY}&offset=0"
        response = requests.get(url)
        data = response.json()

        # Step 2: If no live matches → fallback
        if not data.get("data"):
            url2 = f"https://api.cricapi.com/v1/matches?apikey={API_KEY}&offset=0"
            response2 = requests.get(url2)
            data2 = response2.json()

            match = get_india_match(data2.get("data", []))

            if not match:
                await update.message.reply_text("😴 No India matches found")
                return

            text = (
                f"📅 Upcoming Match\n\n"
                f"{match['name']}\n"
                f"🕒 {match['dateTimeGMT']}\n"
                f"📍 {match['venue']}"
            )

            await update.message.reply_text(text)
            return

        # Step 3: Live match
        match = get_india_match(data.get("data", []))

        if not match:
            await update.message.reply_text("😴 No India live match right now")
            return

        status = match.get("status", "No status")
        prediction = predict(status)

        text = (
            f"🔥 LIVE MATCH\n\n"
            f"{match['name']}\n\n"
            f"📊 {status}\n\n"
            f"🤖 Prediction: {prediction}"
        )

        await update.message.reply_text(text)

    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")


# -------- MAIN --------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("score", score))

    print("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
