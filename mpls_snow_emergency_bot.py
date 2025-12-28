"""
Minneapolis Snow Emergency Discord Bot (Production Ready)
==========================================================
UPDATED: Added optional Selenium support for JavaScript banner detection
"""
import os
import re
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict
from zoneinfo import ZoneInfo 

import aiohttp
import discord
from discord.ext import commands, tasks
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# NEW: Optional Selenium imports
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
    print("✓ Selenium is available - enhanced banner detection enabled!")
except ImportError:
    SELENIUM_AVAILABLE = False
    print("⚠ Selenium not installed - using standard detection methods")

load_dotenv()

# Define the Timezone constant
MPLS_TZ = ZoneInfo("America/Chicago")

def get_mpls_time() -> datetime:
    """Returns current time in Minneapolis (timezone-aware)."""
    return datetime.now(MPLS_TZ)

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
TEST_MODE = False  # Set to False for production, set to True for testing
ENABLE_MENTIONS = True  # Set to False to disable @snowemergency mentions
USE_SELENIUM = True  # NEW: Set to False to disable Selenium even if installed
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", 0))

# URLs
MPLS_BASE_URL = "https://www.minneapolismn.gov"
MPLS_NEWS_PAGE = f"{MPLS_BASE_URL}/news/"
SNOW_UPDATES_PAGE = f"{MPLS_BASE_URL}/getting-around/snow/snow-emergencies/snow-updates/"

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# State tracking
current_state = {
    "active": False,
    "declaration_date": None,
    "last_alert_sent": None,
}

# -------------------------------------------------------------------
# CORE LOGIC: DATE & DAY CALCULATION
# -------------------------------------------------------------------

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

    # --- Debug Logging ---
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
    
    # If the current time is outside all windows
    return None

def parse_date_from_text(text: str) -> Optional[datetime]:
    """
    Extracts a date like "Nov. 30" and converts to a localized datetime object.
    Infers the correct year based on the current date.
    """
    match = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z.]*\s+(\d{1,2})", text, re.IGNORECASE)
    if not match:
        return None
    
    month_str, day_str = match.groups()
    
    try:
        current_year = get_mpls_time().year
        date_str = f"{month_str} {day_str} {current_year}"
        parsed_date_naive = datetime.strptime(date_str, "%b %d %Y")
        
        # Check for year rollover
        now_month = get_mpls_time().month
        parsed_month = parsed_date_naive.month
        
        if now_month < 3 and parsed_month > 10:
            parsed_date_naive = parsed_date_naive.replace(year=current_year - 1)
        elif now_month > 10 and parsed_month < 3:
             parsed_date_naive = parsed_date_naive.replace(year=current_year + 1)
            
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
                string=lambda t: t and "snow emergency" in t.lower()
            )

            if card_title:
                # Traverse up to the card container
                card = card_title.find_parent("div", class_="molecule--news-card")
                
                if card:
                    # Extract the month and day from the structured date element
                    month_span = card.find("span", class_="month")
                    day_span = card.find("span", class_="day")

                    if month_span and day_span:
                        month_text = month_span.get_text(strip=True)
                        day_text = day_span.get_text(strip=True)
                        combined_text = f"{month_text} {day_text}"
                        
                        print(f"[News Scraper] Found date text: '{combined_text}'")
                        
                        # Use parse_date_from_text to convert to a datetime object
                        return parse_date_from_text(combined_text)
                        
                        # Make timezone-aware
                        return parsed_date_naive.replace(tzinfo=MPLS_TZ)
    except Exception as e:
        print(f"Error scraping news page: {e}")
    
    return None

# NEW: Selenium-based banner detection
def check_banner_with_selenium() -> bool:
    """
    Uses Selenium to actually load the page and execute JavaScript,
    allowing us to see the dynamically-loaded banner.
    """
    if not SELENIUM_AVAILABLE or not USE_SELENIUM:
        return False
    
    driver = None
    try:
        # Set up headless Chrome
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        
        print("[Selenium] Starting browser...")
        driver = webdriver.Chrome(options=chrome_options)
        driver.get(MPLS_BASE_URL)
        
        # Wait for JavaScript to execute
        import time
        time.sleep(2)
        
        # Get the rendered page
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text().lower()
        
        # Look for banner keywords
        keywords = [
            "snow emergency declared",
            "snow emergency has been declared",
            "declares snow emergency"
        ]
        
        for keyword in keywords:
            if keyword in page_text:
                print(f"[Selenium] ✓ Found banner: '{keyword}'")
                return True
        
        return False
        
    except Exception as e:
        print(f"[Selenium] Error: {e}")
        return False
    finally:
        if driver:
            driver.quit()

async def check_active_status(session: aiohttp.ClientSession) -> bool:
    """
    Checks the Snow Updates page for the text "A snow emergency is in effect."
    NOW ENHANCED: Also checks with Selenium if available.
    """
    # NEW: Try Selenium first if available
    if SELENIUM_AVAILABLE and USE_SELENIUM:
        print("[Check] Using Selenium for banner detection...")
        selenium_result = await asyncio.to_thread(check_banner_with_selenium)
        if selenium_result:
            print("[Check] ✓ Selenium detected active emergency")
            return True
    
    # Original check (updates page)
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
        print("Status: No active snow emergency detected.")
        # Reset state if there's no active emergency
        current_state["active"] = False
        current_state["declaration_date"] = None
        return
    
    print("Status: Active snow emergency detected!")
    
    # 2. Get declaration date (use the news scraper)
    decl_date = await get_declaration_date_from_news(session)
    
    # NEW: Check if the found date is expired (past Day 3)
    if decl_date:
        now = get_mpls_time()
        decl_midnight = decl_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=MPLS_TZ)
        day3_end = (decl_midnight + timedelta(days=2)).replace(hour=20)  # 8 PM two days after declaration
        
        if now > day3_end:
            print(f"[WARNING] Found news article dated {decl_date.strftime('%m/%d/%Y')} but it has expired (Day 3 ended {day3_end.strftime('%m/%d %I:%M %p')})")
            print(f"[WARNING] Emergency is active but old news article found - using TODAY's date as fallback")
            decl_date = get_mpls_time().replace(hour=0, minute=0, second=0, microsecond=0)
    
    # If we still don't have a date after checking, use today as last resort
    if not decl_date:
        print("[WARNING] Active emergency detected but no declaration date found!")
        print("[WARNING] Using today's date as fallback")
        decl_date = get_mpls_time().replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Update state
    current_state["active"] = True
    current_state["declaration_date"] = decl_date
    
    # 3. Calculate what snow emergency day we're in
    day_num = calculate_snow_day(decl_date)
    
    # Calculate Day 1 start time for pre-declaration alerts
    now = get_mpls_time()
    decl_midnight = decl_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=MPLS_TZ)
    day1_start = decl_midnight.replace(hour=21)
    
    # Special case: On declaration day before 9 PM, send an initial alert
    is_declaration_day_before_d1 = (now.date() == current_state["declaration_date"].date() and now < day1_start)
    
    if day_num or is_declaration_day_before_d1:
        # 4. Post to Discord (Only if channel is set)
        if CHANNEL_ID:
            channel = bot.get_channel(CHANNEL_ID)
            if channel:
                # For declaration day alerts, use "Declaration" as the key
                if is_declaration_day_before_d1 and not day_num:
                    state_key = f"{current_state['declaration_date'].date()}-Declaration"
                    alert_day = 0  # Special "declaration" alert
                else:
                    state_key = f"{current_state['declaration_date'].date()}-Day{day_num}"
                    alert_day = day_num
                
                if current_state["last_alert_sent"] != state_key:
                    embed = create_embed(alert_day, current_state["declaration_date"])
                    
                    # --- CONDITIONAL MENTION LOGIC ---
                    if TEST_MODE:
                        if alert_day == 0:
                            mention_content = f"🚨 **TEST MODE ALERT (Snow Emergency DECLARED)**"
                        else:
                            mention_content = f"🚨 **TEST MODE ALERT (Day {alert_day})**"
                        print("TEST MODE: Alert prepared, but skipped.")
                    elif ENABLE_MENTIONS:
                        mention_content ="🚨 **Snow Emergency Update!**"
                        if alert_day == 0:
                            print(f"Sending mention for Declaration")
                        else:
                            print(f"Sending mention for Day {alert_day}")
                    else:
                        if alert_day == 0:
                            mention_content = f"@everyone 🚨 **Snow Emergency DECLARED!**"
                        else:
                            mention_content = f"🚨 **Snow Emergency Update! (Day {alert_day})**"
                        print(f"PRODUCTION MODE (mentions disabled): Sending alert for {'Declaration' if alert_day == 0 else f'Day {alert_day}'}")
                    
                    # Send the message using the determined content
                    await channel.send(content=mention_content, embed=embed)
                    # --- END CONDITIONAL MENTION LOGIC ---
                    
                    current_state["last_alert_sent"] = state_key
                    print(f"Sent alert for {state_key}")        
    else:
        print("Status: Active, but currently outside of Day 1/2/3 time windows (9PM-8AM). No alert needed.")


def create_embed(day: int, decl_date: datetime) -> discord.Embed:
    rules = {
        0: "⚠️ **Snow Emergency has been declared!**\n\n"
           "Day 1 parking restrictions begin at **9:00 PM tonight**.\n"
           "Please move your vehicle from Snow Emergency Routes (marked with signs) before 9 PM.",
        1: "🚫 **No parking on Snow Emergency Routes (marked with signs).** \n✅Parking **ALLOWED** on parkways and streets that are **NOT** snow emergency routes.",
        2: "🚫 **No parking on the EVEN (address #) side** streets that are not-emergency routes.\n🚫 **No parking on Parkways**. \n✅Parking is **ALLOWED** on **ODD** side of streets that are **NOT** snow emergency routes. \n✅Parking is **ALLOWEDII on both sides of snow emergency routes where applicable.",
        3: "🚫 **No parking on the ODD (address #) side** of non-emergency routes. \n✅Parking is **ALLOWED** on the **EVEN** side of streets that are **NOT** snow emergency routes. Parking is **ALLOWED** on both sides of snow emergency routes and parkways where applicable."
    }
    
    # Calculate all the time periods
    decl_midnight = decl_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=MPLS_TZ)
    
    day1_start = decl_midnight.replace(hour=21)
    day1_end = (decl_midnight + timedelta(days=1)).replace(hour=8)
    
    day2_start = day1_end
    day2_end = (decl_midnight + timedelta(days=1)).replace(hour=20)
    
    gap_start = day2_end
    gap_end = (decl_midnight + timedelta(days=2)).replace(hour=8)
    
    day3_start = gap_end
    day3_end = (decl_midnight + timedelta(days=2)).replace(hour=20)
    
    # Format the timeline
    timeline = (
        f"Please refer to [Full City Website Rules]({MPLS_BASE_URL}/getting-around/snow/snow-emergencies/snow-parking-rules/) for further information:\n\n"
        f"**Day 1 Rules Active** - {day1_start.strftime('%I:%M %p on %m/%d/%Y')} to {day1_end.strftime('%I:%M %p on %m/%d/%Y')}\n"
        f"**Day 2 Rules Active** - {day2_start.strftime('%I:%M %p on %m/%d/%Y')} to {day2_end.strftime('%I:%M %p on %m/%d/%Y')}\n"
        f"**Gap Between Days 2 and 3** - {gap_start.strftime('%I:%M %p on %m/%d/%Y')} to {gap_end.strftime('%I:%M %p on %m/%d/%Y')}\n"
        f"**Day 3 Rules Active** - {day3_start.strftime('%I:%M %p on %m/%d/%Y')} to {day3_end.strftime('%I:%M %p on %m/%d/%Y')}"
    )
    
    # Set title based on day
    if day == 0:
        title = f"❄️ @everyone Snow Emergency DECLARED"
        color = discord.Color.orange()
    else:
        title = f"❄️ Snow Emergency: Day {day} Rules In Effect"
        color = discord.Color.red() if day in [1, 2, 3] else discord.Color.blue()
    
    embed = discord.Embed(
        title=title,
        description=f"Declared on **{decl_date.strftime('%A, %B %d, %Y')}**",
        color=color,
        timestamp=get_mpls_time()
    )
    embed.add_field(name="Current Rules" if day > 0 else "What You Need to Know", value=rules[day], inline=False)
    embed.add_field(name="Estimated Timeline, confirm with City", value=timeline, inline=False)
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
    await bot.wait_until_ready()

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
    if SELENIUM_AVAILABLE and USE_SELENIUM:
        print("Selenium: ENABLED - Will check JavaScript banners")
    elif SELENIUM_AVAILABLE:
        print("Selenium: Available but disabled in config")
    else:
        print("Selenium: Not installed (pip install selenium to enable)")
    if not check_snow_emergency.is_running():
        check_snow_emergency.start()

@bot.command()
async def snowstatus(ctx):
    """Manual check command."""
    await ctx.defer()

    session = bot.http._session
    is_active = await check_active_status(session)
    decl_date = await get_declaration_date_from_news(session)

    if not is_active:
        await ctx.send("✅ **No snow emergency** is currently active. Normal parking rules apply.")
        return
    
    if decl_date:
        day = calculate_snow_day(decl_date)
        
        decl_midnight = decl_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=MPLS_TZ)
        day1_start = decl_midnight.replace(hour=21)
        now = get_mpls_time()
        
        if day is None and now.date() == decl_date.date() and now < day1_start:
            day = 0
        elif day is None:
            day = "Rules Not Active (Check site)"
            
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
