"""
Minneapolis Snow Emergency Discord Bot (Production Ready)
==========================================================
"""
import os
import re
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict
# Note: You may need to run 'pip install python-dateutil' if ZoneInfo isn't available
from zoneinfo import ZoneInfo 

import aiohttp
import discord
from discord.ext import commands, tasks
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()
# Add this line to the import section (Requires Python 3.9+)
from zoneinfo import ZoneInfo 

# Define the Timezone constant (Add this after the CONFIGURATION section)
MPLS_TZ = ZoneInfo("America/Chicago")

def get_mpls_time() -> datetime:
    """Returns current time in Minneapolis (timezone-aware)."""
    return datetime.now(MPLS_TZ)
# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
TEST_MODE = False  # CHANGED: Set to False for production, set to True for testing
ENABLE_MENTIONS = False  # Set to False to disable @everyone mentions
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", 0))

# URLs
MPLS_BASE_URL = "https://www.minneapolismn.gov"
MPLS_NEWS_PAGE = f"{MPLS_BASE_URL}/news/"
# The most reliable page for status text.
SNOW_UPDATES_PAGE = f"{MPLS_BASE_URL}/getting-around/snow/snow-emergencies/snow-updates/"

# Timezone - CRITICAL for accurate Day 1/2/3 calculation
MPLS_TZ = ZoneInfo("America/Chicago")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# State tracking (Global/Bot instance variables)
current_state = {
    "active": False,
    "declaration_date": None, # datetime object
    "last_alert_sent": None,
}

# -------------------------------------------------------------------
# CORE LOGIC: DATE & DAY CALCULATION
# -------------------------------------------------------------------

def get_mpls_time() -> datetime:
    """Returns current time in Minneapolis."""
    return datetime.now(MPLS_TZ)

def calculate_snow_day(declaration_date: datetime) -> Optional[int]:
    """
    Determines if we are in Day 1, 2, or 3 based on the declaration date.
    All calculations are based on the Minneapolis (America/Chicago) timezone.
    """
    now = get_mpls_time()
    
    # Ensure declaration_date is at midnight CST/CDT for consistent calculation
    decl_midnight = declaration_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=MPLS_TZ)

    # --- Define Time Windows (All in MPLS_TZ) ---

    # Day 1: 9 PM (Declared Day) -> 8 AM (Next Day)
    day1_start = decl_midnight.replace(hour=21)
    day1_end = (decl_midnight + timedelta(days=1)).replace(hour=8)

    # Day 2: 8 AM (Next Day) -> 8 PM (Next Day)
    day2_start = day1_end
    day2_end = (decl_midnight + timedelta(days=1)).replace(hour=20)

    # Day 3: 8 AM (Day After Next) -> 8 PM (Day After Next)
    day3_start = (decl_midnight + timedelta(days=2)).replace(hour=8)
    day3_end = (decl_midnight + timedelta(days=2)).replace(hour=20)

    # --- Debug Logging (New) ---
    print(f"[Day Calc] Decl Date: {declaration_date.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"[Day Calc] NOW Time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("-" * 30)
    print(f"[Day Calc] D1 Start: {day1_start.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"[Day Calc] D2 Start: {day2_start.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"[Day Calc] D3 Start: {day3_start.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"[Day Calc] D3 End: {day3_end.strftime('%Y-%m-%d %H:%M %Z')}")
    print("-" * 30)
    
    # --- Check Windows ---

    if day1_start <= now < day1_end:
        return 1
    elif day2_start <= now < day2_end:
        return 2
    elif day3_start <= now < day3_end:
        return 3
    
    # If the current time is outside all windows (i.e., past 8 PM Day 3)
    return None

def parse_date_from_text(text: str) -> Optional[datetime]:
    """
    Extracts a date like "Nov. 30" and converts to a localized datetime object.
    Infers the correct year based on the current date.
    """
    # Look for Month Name + Day Number (e.g., "Nov. 30" or "November 30")
    # This regex is robust against periods/short forms like "Dec. 1"
    match = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z.]*\s+(\d{1,2})", text, re.IGNORECASE)
    if not match:
        return None
    
    month_str, day_str = match.groups()
    
    try:
        # Create a naive date object using the current year as a base
        current_year = get_mpls_time().year
        # We use a known format that includes the year
        date_str = f"{month_str} {day_str} {current_year}"
        parsed_date_naive = datetime.strptime(date_str, "%b %d %Y")
        
        # Check for year rollover (e.g., in Jan 2026, finding a Dec 30 article means it's 2025)
        now_month = get_mpls_time().month
        parsed_month = parsed_date_naive.month
        
        # If current month is Jan/Feb, and parsed month is Oct/Nov/Dec, subtract one year
        if now_month < 3 and parsed_month > 10:
            parsed_date_naive = parsed_date_naive.replace(year=current_year - 1)
        # If current month is Oct/Nov/Dec, and parsed month is Jan/Feb, add one year (for future pre-announcement)
        elif now_month > 10 and parsed_month < 3:
             parsed_date_naive = parsed_date_naive.replace(year=current_year + 1)
            
        # Make the resulting date timezone-aware (set to midnight of the date)
        return parsed_date_naive.replace(tzinfo=MPLS_TZ)
        
    except ValueError:
        return None

# -------------------------------------------------------------------
# DYNAMIC SCRAPERS
# -------------------------------------------------------------------

async def get_declaration_date_from_news(session: aiohttp.ClientSession) -> Optional[datetime]:
    """
    Scrapes the News listing page to find the latest snow emergency article and extracts the date
    by parsing the structured 'news-card' HTML elements.
    """
    try:
        async with session.get(MPLS_NEWS_PAGE, timeout=10) as resp:
            if resp.status != 200: return None
            soup = BeautifulSoup(await resp.text(), "html.parser")
            
            # Find the most recent news card explicitly mentioning a snow emergency
            card_title = soup.find(
                "h3", 
                text=lambda t: t and "snow emergency" in t.lower()
            )

            if card_title:
                # Traverse up to the card container
                card = card_title.find_parent("div", class_="molecule--news-card")
                
                if card:
                    # Extract the month and day from the structured date element
                    month_span = card.find("span", class_="month")
                    day_span = card.find("span", class_="day")

                    if month_span and day_span:
                        month_str = month_span.get_text(strip=True)
                        day_str = day_span.get_text(strip=True)
                        
                        # Calculate the year (handling rollover for Dec/Jan)
                        current_year = get_mpls_time().year
                        date_str = f"{month_str} {day_str} {current_year}"
                        
                        parsed_date_naive = datetime.strptime(date_str, "%B %d %Y")
                        
                        # Year rollover check (from Nov/Dec to next Jan/Feb)
                        now_month = get_mpls_time().month
                        parsed_month = parsed_date_naive.month
                        
                        if now_month < 3 and parsed_month > 10:
                            parsed_date_naive = parsed_date_naive.replace(year=current_year - 1)
                        elif now_month > 10 and parsed_month < 3:
                            parsed_date_naive = parsed_date_naive.replace(year=current_year + 1)
                        
                        # Make timezone-aware
                        return parsed_date_naive.replace(tzinfo=MPLS_TZ)
    except Exception as e:
        print(f"Error scraping news page: {e}")
    
    return None

async def check_active_status(session: aiohttp.ClientSession) -> bool:
    """
    Checks the Snow Updates page for the text "A snow emergency is in effect."
    Robust against text formatting changes.
    """
    try:
        async with session.get(SNOW_UPDATES_PAGE, timeout=10) as resp:
            if resp.status != 200:
                return False
            
            text = await resp.text()
            soup = BeautifulSoup(text, "html.parser")
            
            # Convert entire page to lowercase, stripped text
            page_text = soup.get_text().lower()
            
            # Look for key phrase (with flexible spacing)
            if "snow emergency is in effect" in page_text or "snow emergency has been declared" in page_text:
                return True
    except Exception as e:
        print(f"Error checking active status: {e}")
    
    return False

# -------------------------------------------------------------------
# TASK LOOP
# -------------------------------------------------------------------

@tasks.loop(minutes=15)
async def check_snow_emergency():
    print(f"\n[{get_mpls_time()}] Running Check...")
    
    # Use the session stored in the bot instance
    session = bot.http._session
    
    # 1. Check Active Status
    is_active = await check_active_status(session)
    
    if not is_active:
        current_state["active"] = False
        # Do NOT clear declaration_date unless we are sure it's fully over (Day 3 + 8PM)
        print("Status: Inactive")
        return

    # 2. If active, get the Declaration Date (Source of Truth)
    decl_date = await get_declaration_date_from_news(session)
    
    if decl_date:
        current_state["declaration_date"] = decl_date
        # Check if the fetched date is too old (e.g., from last year) and discard if necessary
        if (get_mpls_time() - decl_date).days > 7 and not calculate_snow_day(decl_date):
            print("Status: Active, but Declaration Date is very old and rules aren't running. Skipping.")
            current_state["active"] = False
            return
            
        print(f"Status: Active. Declared: {decl_date.strftime('%B %d, %Y')}")
    elif current_state["declaration_date"]:
        print("Status: Active. Using cached declaration date.")
    else:
        # Emergency is active, but we failed to find the news article (rare, but possible).
        # We can't calculate Day 1/2/3 reliably, so we must rely on the manual check.
        print("Status: Active. FAILED to find declaration date for Day calculation. Use !snowstatus for manual check.")
        return # Skip posting alert if we can't calculate the day

    # 3. Calculate Day (only if declaration_date is set)
    day_num = calculate_snow_day(current_state["declaration_date"])
    
    # FIXED: Check if emergency has completely ended (past Day 3 @ 8PM)
    if day_num is None:
        # Calculate Day 3 end time
        decl_midnight = current_state["declaration_date"].replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=MPLS_TZ)
        day3_end = (decl_midnight + timedelta(days=2)).replace(hour=20)
        
        if get_mpls_time() > day3_end:
            print(f"Status: Emergency has ENDED (Day 3 ended at {day3_end.strftime('%Y-%m-%d %H:%M %Z')})")
            current_state["active"] = False
            return
    
    current_state["active"] = True
    
    if day_num:
        # 4. Post to Discord (Only if channel is set AND we are in a parking rule window)
        if CHANNEL_ID:
            channel = bot.get_channel(CHANNEL_ID)
            if channel:
                # Simple deduping: Don't repost the exact same day repeatedly
                state_key = f"{current_state['declaration_date'].date()}-Day{day_num}"
                
                if current_state["last_alert_sent"] != state_key:
                    embed = create_embed(day_num, current_state["declaration_date"])
                    
                    # --- CONDITIONAL MENTION LOGIC ---
                    if TEST_MODE:
                        mention_content = f"🚨 **TEST MODE ALERT (Day {day_num})**"
                        print("TEST MODE: Alert prepared, but @everyone skipped.")
                    elif ENABLE_MENTIONS:
                        # This sends the live, disruptive notification
                        mention_content = "@everyone 🚨 **Snow Emergency Update!**"
                        print(f"PRODUCTION MODE: Sending @everyone mention for Day {day_num}")
                    else:
                        mention_content = f"🚨 **Snow Emergency Update! (Day {day_num})**"
                        print(f"PRODUCTION MODE (mentions disabled): Sending alert for Day {day_num}")
                    
                    # Send the message using the determined content
                    await channel.send(content=mention_content, embed=embed)
                    # --- END CONDITIONAL MENTION LOGIC ---
                    
                    current_state["last_alert_sent"] = state_key
                    print(f"Sent alert for {state_key}")        
    else:
        print("Status: Active, but currently outside of Day 1/2/3 time windows (9PM-8AM). No alert needed.")


def create_embed(day: int, decl_date: datetime) -> discord.Embed:
    rules = {
        1: "🚫 **No parking on Snow Emergency Routes** (marked with blue signs).",
        2: "🚫 **No parking on the EVEN side** of non-emergency routes.\n🚫 **No parking on Parkways**.",
        3: "🚫 **No parking on the ODD side** of non-emergency routes."
    }
    
    # Calculate all the time periods
    decl_midnight = decl_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=MPLS_TZ)
    
    day1_start = decl_midnight.replace(hour=21)  # 9 PM declaration day
    day1_end = (decl_midnight + timedelta(days=1)).replace(hour=8)  # 8 AM next day
    
    day2_start = day1_end  # 8 AM next day
    day2_end = (decl_midnight + timedelta(days=1)).replace(hour=20)  # 8 PM next day
    
    gap_start = day2_end  # 8 PM after Day 2
    gap_end = (decl_midnight + timedelta(days=2)).replace(hour=8)  # 8 AM two days after
    
    day3_start = gap_end  # 8 AM two days after
    day3_end = (decl_midnight + timedelta(days=2)).replace(hour=20)  # 8 PM two days after
    
    # Format the timeline
    timeline = (
        f"**Parking Rules as Estimated by Bot**\n"
        f"Please refer to [Full Rules]({MPLS_BASE_URL}/getting-around/snow/snow-emergencies/snow-parking-rules/) for further information:\n\n"
        f"**Day 1 Rules Active** - {day1_start.strftime('%I:%M %p on %m/%d/%Y')} to {day1_end.strftime('%I:%M %p on %m/%d/%Y')}\n"
        f"**Day 2 Rules Active** - {day2_start.strftime('%I:%M %p on %m/%d/%Y')} to {day2_end.strftime('%I:%M %p on %m/%d/%Y')}\n"
        f"**Gap Between Days 2 and 3** - {gap_start.strftime('%I:%M %p on %m/%d/%Y')} to {gap_end.strftime('%I:%M %p on %m/%d/%Y')}\n"
        f"**Day 3 Rules Active** - {day3_start.strftime('%I:%M %p on %m/%d/%Y')} to {day3_end.strftime('%I:%M %p on %m/%d/%Y')}"
    )
    
    embed = discord.Embed(
        title=f"❄️ Snow Emergency: Day {day} Rules In Effect",
        description=f"Declared on **{decl_date.strftime('%A, %B %d, %Y')}**\n\n{rules.get(day, 'Check official sources for current rules.')}",
        color=discord.Color.red() if day in [1, 2, 3] else discord.Color.blue(),
        timestamp=get_mpls_time()
    )
    embed.add_field(name="Complete Timeline", value=timeline, inline=False)
    embed.add_field(
        name="Additional Resources",
        value=(
            f"• [Snow Emergency Map]({MPLS_BASE_URL}/getting-around/snow/snow-emergencies/snow-parking-rules/snow-emergency-map/)\n"
            f"• Hotline: 612-348-SNOW (7669)\n"
            f"• App: MPLS Parking"
        ),
        inline=False,
    )
    embed.set_footer(text="Always check official sources to confirm parking rules.")
    return embed

@check_snow_emergency.before_loop
async def before_check():
    """Sets up the persistent aiohttp session."""
    # We rely on the internal session for persistence, which discord.py manages
    await bot.wait_until_ready()
    # Note: bot.http._session is the aiohttp.ClientSession instance used by discord.py
    # We will pass this session to our scraper functions.

# -------------------------------------------------------------------
# COMMANDS
# -------------------------------------------------------------------

@bot.event
async def on_ready():
    # Make sure the bot has a valid session to use for scraping
    if not hasattr(bot.http, '_session'):
        bot.http._session = aiohttp.ClientSession()
        
    print(f"Logged in as {bot.user}")
    print(f"Configuration: TEST_MODE={TEST_MODE}, ENABLE_MENTIONS={ENABLE_MENTIONS}")
    print(f"Target Channel ID: {CHANNEL_ID}")
    if not check_snow_emergency.is_running():
        check_snow_emergency.start()

@bot.command()
async def snowstatus(ctx):
    """Manual check command."""
    await ctx.defer() # Acknowledge command immediately

    # Run the checks once, using the existing session
    session = bot.http._session
    is_active = await check_active_status(session)
    decl_date = await get_declaration_date_from_news(session)

    if not is_active:
        await ctx.send("✅ **No snow emergency** is currently active. Normal parking rules apply.")
        return
    
    if decl_date:
        day = calculate_snow_day(decl_date) or "Rules Not Active (Check site)"
        embed = create_embed(day, decl_date)
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"⚠️ **Snow Emergency is Active**, but the bot could not find the declaration date to calculate the current day. Please check the official Minneapolis website: {SNOW_UPDATES_PAGE}")


if __name__ == "__main__":
    if BOT_TOKEN:
        bot.run(BOT_TOKEN)
    else:
        print("ERROR: DISCORD_BOT_TOKEN not found in .env file")
        raise SystemExit(1)
