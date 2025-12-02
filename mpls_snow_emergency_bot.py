"""
Minneapolis Snow Emergency Discord Bot
========================================
This bot monitors the City of Minneapolis website for snow emergency declarations
and posts alerts to a Discord channel.

SETUP:
1. Install requirements:
   pip install discord.py beautifulsoup4 requests python-dotenv aiohttp

2. Create a .env file with:
   DISCORD_BOT_TOKEN=your_bot_token
   DISCORD_CHANNEL_ID=your_channel_id

3. Invite bot to server with permissions:
   - Send Messages
   - Embed Links
   - Read Message History
"""

import os
import re
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict

import aiohttp
import discord
from discord.ext import commands, tasks
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------

TEST_MODE = True  # Set to False in production

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", 0))

MINNEAPOLIS_HOMEPAGE = "https://www.minneapolismn.gov/"
SNOW_MPLS = "https://snowmpls.com/"
SNOW_UPDATES_PAGE = "https://www.minneapolismn.gov/getting-around/snow/snow-emergencies/snow-updates/"
SNOW_NEWS_PAGE = "https://www.minneapolismn.gov/news/"  # News section for announcements
SNOW_ANNOUNCEMENT_PAGE = "https://www.minneapolismn.gov/news/2025/november/nov-30-snow-emergency/"  # Latest announcement

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

current_status = {
    "active": False,
    "last_check": None,
    "details": None,
    "day": None,
    "source": None,
}

# -------------------------------------------------------------------
# DATE CALCULATION
# -------------------------------------------------------------------


def calculate_current_day_from_declaration(declaration_date_str: str) -> Optional[str]:
    """
    Given a declaration date string like 'November 30, 2025', calculate
    which Snow Emergency day we are on *right now* based on the official rules:

    - Day 1: 9 p.m. on declaration date → 8 a.m. next day
    - Day 2: 8 a.m. next day → 8 p.m. that same day
    - Day 3: 8 a.m. two days after declaration → 8 p.m. that day
    """
    try:
        declaration_date = datetime.strptime(declaration_date_str, "%B %d, %Y")

        now = datetime.now()

        # Build explicit timeline boundaries in local time
        day1_start = declaration_date.replace(hour=21, minute=0, second=0, microsecond=0)
        day2_start = (declaration_date + timedelta(days=1)).replace(
            hour=8, minute=0, second=0, microsecond=0
        )
        day3_start = (declaration_date + timedelta(days=2)).replace(
            hour=8, minute=0, second=0, microsecond=0
        )
        day3_end = (declaration_date + timedelta(days=2)).replace(
            hour=20, minute=0, second=0, microsecond=0
        )

        print(
            f"[Day Calc] Declaration date: {declaration_date_str}, "
            f"now: {now}, "
            f"day1_start: {day1_start}, day2_start: {day2_start}, "
            f"day3_start: {day3_start}, day3_end: {day3_end}"
        )

        # Before 9 p.m. on declaration date: rules haven't started yet
        if now < day1_start:
            return None

        # Day 1: 9 p.m. declaration date → 8 a.m. next morning
        if day1_start <= now < day2_start:
            return "1"

        # Day 2: 8 a.m. next day → 8 p.m. that same day
        if day2_start <= now < day3_start:
            return "2"

        # Day 3: 8 a.m. two days after declaration → 8 p.m. that day
        if day3_start <= now <= day3_end:
            return "3"

        # After Day 3 window: snow emergency should be considered over
        return None

    except Exception as e:
        print(f"[Day Calc] Error: {e}")
        return None


def calculate_declaration_date(current_day: Optional[str]) -> str:
    """
    Calculate the declaration date based on the current day.
    
    Snow emergency timeline:
    - Day 1: Starts at 9 PM on declaration date
    - Day 2: Starts at 8 AM the next morning (declaration date + 1 day)
    - Day 3: Starts at 8 AM the following morning (declaration date + 2 days)
    
    So if we detect "Day 2", the declaration was yesterday.
    If we detect "Day 3", the declaration was 2 days ago.
    """
    now = datetime.now()
    
    if not current_day:
        print("[Date Calc] No day provided, using today")
        return now.strftime("%B %d, %Y")
    
    try:
        day_num = int(current_day)
        print(f"[Date Calc] Current day: {day_num}, Today is: {now.strftime('%B %d, %Y')}")
        
        if day_num == 1:
            # Day 1 starts at 9 PM on the declaration date
            # If it's currently Day 1, today is the declaration date
            declaration_date = now
            print(f"[Date Calc] Day 1 detected, declaration date is today")
        elif day_num == 2:
            # Day 2 starts at 8 AM the morning after Day 1
            # So declaration was yesterday
            declaration_date = now - timedelta(days=1)
            print(f"[Date Calc] Day 2 detected, declaration date is yesterday: {declaration_date.strftime('%B %d, %Y')}")
        elif day_num == 3:
            # Day 3 starts at 8 AM, two mornings after Day 1
            # So declaration was 2 days ago
            declaration_date = now - timedelta(days=2)
            print(f"[Date Calc] Day 3 detected, declaration date is 2 days ago: {declaration_date.strftime('%B %d, %Y')}")
        else:
            declaration_date = now
            print(f"[Date Calc] Unknown day ({day_num}), using today")
            
        result = declaration_date.strftime("%B %d, %Y")
        print(f"[Date Calc] FINAL RESULT: Day {day_num} → Declaration date: {result}")
        return result
    except (ValueError, TypeError) as e:
        print(f"[Date Calc] Error parsing day '{current_day}': {e}")
        return now.strftime("%B %d, %Y")


# -------------------------------------------------------------------
# SCRAPERS
# -------------------------------------------------------------------


async def check_minneapolis_homepage() -> Optional[Dict]:
    """
    Check the Minneapolis homepage for a snow emergency banner.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(MINNEAPOLIS_HOMEPAGE, timeout=10) as response:
                if response.status != 200:
                    return None

                html = await response.text()
                
                print(f"[Homepage] Fetched {len(html)} characters of HTML")

                # Try multiple patterns to find the day - including "Snow Emergency Day X"
                day_number = None
                patterns = [
                    r"Snow\s+Emergency\s+Day\s*(\d)",  # "Snow Emergency Day 2"
                    r"Day\s*(\d)",  # "Day 2"
                ]
                
                for pattern in patterns:
                    day_match = re.search(pattern, html, re.IGNORECASE)
                    if day_match:
                        day_number = day_match.group(1)
                        print(f"[Homepage] ✓ Found day using pattern '{pattern}': {day_number}")
                        # Print surrounding context
                        start = max(0, day_match.start() - 50)
                        end = min(len(html), day_match.end() + 50)
                        print(f"[Homepage] Context: ...{html[start:end]}...")
                        break

                if "snow emergency" in html.lower():
                    if not day_number:
                        print("[Homepage] ⚠ 'snow emergency' found but no day number!")
                        # Search for ANY occurrence of "Day" with a number nearby
                        all_day_matches = re.findall(r".{0,50}[Dd]ay.{0,50}", html)
                        print(f"[Homepage] Found {len(all_day_matches)} potential 'day' mentions")
                        for i, match in enumerate(all_day_matches[:3]):  # Show first 3
                            print(f"[Homepage]   Match {i+1}: {match}")
                    
                    return {
                        "active": True,
                        "source": "minneapolis.gov homepage",
                        "detected_at": datetime.now(),
                        "day": day_number,
                    }

                return {"active": False}

    except Exception as e:
        print(f"Error checking Minneapolis homepage: {e}")
        return None


async def check_snowmpls_com() -> Optional[Dict]:
    """
    Check snowmpls.com for current status.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SNOW_MPLS, timeout=10) as response:
                if response.status != 200:
                    return None

                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")

                title = soup.find("title")
                if title and "active" in title.text.lower():
                    body_text = soup.get_text()
                    
                    day_match = re.search(r"Day\s*(1|2|3)", body_text, re.IGNORECASE)
                    day_number = day_match.group(1) if day_match else None
                    
                    print(f"[Snowmpls] Detected snow emergency, Day: {day_number}")
                    return {
                        "active": True,
                        "source": "snowmpls.com",
                        "detected_at": datetime.now(),
                        "page_content": body_text[:500],
                        "day": day_number,
                    }

                return {"active": False}

    except Exception as e:
        print(f"Error checking snowmpls.com: {e}")
        return None


async def get_snow_emergency_details() -> Optional[Dict]:
    """
    Get details from the official Snow Updates page and announcement.
    """
    try:
        # First try the announcement page (most reliable)
        async with aiohttp.ClientSession() as session:
            print("[Details] Checking official announcement...")
            try:
                async with session.get(SNOW_ANNOUNCEMENT_PAGE, timeout=10) as response:
                    if response.status == 200:
                        html = await response.text()
                        soup = BeautifulSoup(html, "html.parser")
                        text = soup.get_text(separator=" ")
                        
                        print(f"[Details-Announcement] Fetched {len(text)} characters")
                        
                        # Extract day from patterns like "Dec. 1 (Day 2)" or "Monday, Dec. 1 (Day 2)"
                        day = None
                        day_patterns = [
                            r"Dec\.\s*1\s*\(Day\s*(\d)\)",  # "Dec. 1 (Day 2)"
                            r"December\s*1\s*\(Day\s*(\d)\)",  # "December 1 (Day 2)"
                            r"\(Day\s*(\d)\)",  # Just "(Day 2)"
                        ]
                        
                        for pattern in day_patterns:
                            match = re.search(pattern, text, re.IGNORECASE)
                            if match:
                                day = match.group(1)
                                print(f"[Details-Announcement] ✓ Found day: {day}")
                                break
                        
                        # Extract declaration date - should be "November 30"
                        declaration_date = None
                        date_match = re.search(r"November\s+30", text, re.IGNORECASE)
                        if date_match:
                            declaration_date = "November 30, 2025"
                            print(f"[Details-Announcement] ✓ Found declaration: {declaration_date}")
                        
                        if day or declaration_date:
                            return {
                                "day": day,
                                "declaration_date": declaration_date,
                                "page_text": text[:2000],
                            }
            except Exception as e:
                print(f"[Details-Announcement] Error: {e}")
            
            # Fallback to updates page
            print("[Details] Checking updates page...")
            async with session.get(SNOW_UPDATES_PAGE, timeout=10) as response:
                if response.status != 200:
                    return None

                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")
                text = soup.get_text(separator=" ")
                
                print(f"[Details-Updates] Fetched {len(text)} characters of text")

                # Try multiple patterns to find the day
                day = None
                patterns = [
                    r"Snow\s+Emergency\s+Day\s*(\d)",  # "Snow Emergency Day 2"
                    r"Day\s*(\d)",  # "Day 2"
                    r"\(Day\s*(\d)\)",  # "(Day 2)"
                    r"Dec\.\s*\d+\s*\(Day\s*(\d)\)",  # "Dec. 1 (Day 2)"
                ]
                
                for pattern in patterns:
                    day_match = re.search(pattern, text, re.IGNORECASE)
                    if day_match:
                        day = day_match.group(1)
                        print(f"[Details-Updates] ✓ Found day using pattern '{pattern}': {day}")
                        break
                
                if not day:
                    # Show all mentions of "Day" for debugging
                    all_day_matches = re.findall(r".{0,50}[Dd]ay.{0,50}", text)
                    print(f"[Details-Updates] ⚠ No day found. Found {len(all_day_matches)} 'day' mentions")
                    for i, match in enumerate(all_day_matches[:3]):
                        print(f"[Details-Updates]   Match {i+1}: {match.strip()}")
                
                # Try to find declaration date
                declaration_date = None
                date_patterns = [
                    r"November\s+30(?:,?\s*2025)?",
                    r"Nov\.\s+30(?:,?\s*2025)?",
                    r"Nov\s+30(?:,?\s*2025)?",
                ]
                
                for pattern in date_patterns:
                    matches = re.search(pattern, text, re.IGNORECASE)
                    if matches:
                        declaration_date = "November 30, 2025"
                        print(f"[Details-Updates] ✓ Found declaration date: {declaration_date}")
                        break
                
                if not declaration_date:
                    # Show all date-like mentions
                    date_mentions = re.findall(r"(November|December|Nov\.|Dec\.)\s+\d{1,2}", text, re.IGNORECASE)
                    print(f"[Details-Updates] ⚠ No declaration date. Found {len(date_mentions)} date mentions: {date_mentions[:5]}")
                
                print(f"[Details-Updates] RESULT - Day: {day}, Declaration date: {declaration_date}")

                return {
                    "day": day,
                    "declaration_date": declaration_date,
                    "page_text": text[:2000],
                }

    except Exception as e:
        print(f"Error getting details from snow updates page: {e}")
        return None


# -------------------------------------------------------------------
# EMBED BUILDER
# -------------------------------------------------------------------


def create_snow_emergency_embed(status_info: Dict) -> discord.Embed:
    """
    Create a Discord embed with snow emergency information.
    """
    active = status_info.get("active")

    if active:
        embed = discord.Embed(
            title="❄️ SNOW EMERGENCY DECLARED ❄️",
            description="A snow emergency has been declared in Minneapolis!",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )

        day = status_info.get("day")
        declared_date = status_info.get("declared_date")
        
        print(f"[Embed] Creating with Day: {day}, Declared: {declared_date}")

        # Declared date - explicitly labeled as "Declared on"
        if declared_date:
            embed.add_field(
                name="📅 Declared on",
                value=declared_date,
                inline=True,
            )
        else:
            print("[Embed] WARNING: No declared_date provided!")

        # Current Day in "Day X of 3" format
        if day:
            embed.add_field(
                name="Current Status",
                value=f"Day {day} of 3",
                inline=True,
            )
        else:
            print("[Embed] WARNING: No day provided!")

        embed.add_field(
            name="Source",
            value=status_info.get("source", "Minneapolis"),
            inline=True,
        )

        embed.add_field(
            name="📱 More Info",
            value=(
                "• Website: https://www.minneapolismn.gov/getting-around/snow/snow-emergencies/\n"
                "• Parking rules: https://www.minneapolismn.gov/getting-around/snow/snow-emergencies/snow-parking-rules/\n"
                "• Hotline: 612-348-SNOW (7669)\n"
                "• App: MPLS Parking"
            ),
            inline=False,
        )

        embed.add_field(
            name="⚠️ Remember",
            value="Follow parking rules or risk being ticketed and towed!",
            inline=False,
        )

        embed.set_footer(text="Always check the City website for the latest info.")

    else:
        embed = discord.Embed(
            title="✅ No Snow Emergency",
            description="No snow emergency currently in effect.",
            color=discord.Color.green(),
            timestamp=datetime.now(),
        )

    return embed


# -------------------------------------------------------------------
# BACKGROUND LOOP: CHECK EVERY 15 MINUTES
# -------------------------------------------------------------------


@tasks.loop(minutes=15)
async def check_snow_emergency():
    """
    Background task that checks for snow emergencies every 15 minutes.
    """
    global current_status

    print(f"\n[{datetime.now()}] Checking for snow emergency...")

    homepage_status = await check_minneapolis_homepage()
    snowmpls_status = await check_snowmpls_com()

    is_active = False
    source = None
    detected_day = None

    # Check homepage first (most reliable)
    if homepage_status and homepage_status.get("active"):
        is_active = True
        source = homepage_status
        detected_day = homepage_status.get("day")
        print(f"[Main] Active from homepage, Day detected: '{detected_day}'")
        if not detected_day:
            print("[Main] WARNING: Homepage shows active but no day number found!")
    elif snowmpls_status and snowmpls_status.get("active"):
        is_active = True
        source = snowmpls_status
        detected_day = snowmpls_status.get("day")
        print(f"[Main] Active from snowmpls, Day detected: '{detected_day}'")
        if not detected_day:
            print("[Main] WARNING: Snowmpls shows active but no day number found!")

    status_changed = current_status["active"] != is_active

    if status_changed:
        print(f"[Main] Status changed! Active: {is_active}")

        final_day = detected_day
        declared_date = None
        
        # Get details from the page (might have declaration date and/or day)
        details = None
        if is_active:
            details = await get_snow_emergency_details()
            if details:
                details_day = details.get("day")
                details_date = details.get("declaration_date")
                
                # Use details day if we don't have one
                if not final_day and details_day:
                    final_day = details_day
                    print(f"[Main] Got day from details page: {final_day}")
                
                # Get declaration date from details
                if details_date:
                    declared_date = details_date
                    print(f"[Main] Got declaration date from details: {declared_date}")
        
        # If we have a day but no declaration date, calculate backwards
        if final_day and not declared_date:
            declared_date = calculate_declaration_date(final_day)
            print(f"[Main] Calculated declaration date from day: {declared_date}")
        
        # If we have a declaration date but no day, calculate forward
        if declared_date and not final_day:
            final_day = calculate_current_day_from_declaration(declared_date)
            print(f"[Main] Calculated current day from declaration date: {final_day}")
        
        # Last resort: if we still don't have a declaration date, use today
        if not declared_date:
            declared_date = datetime.now().strftime("%B %d, %Y")
            print(f"[Main] Using today as declaration date: {declared_date}")
        
        print(f"[Main] FINAL VALUES - Day: '{final_day}', Declared Date: '{declared_date}'")

                # FINAL sanity check:
        # If we have a declaration date at this point, always recompute the
        # current day from that date using the official time windows.
        if declared_date:
            recalculated_day = calculate_current_day_from_declaration(declared_date)
            if recalculated_day:
                print(
                    f"[Main] Recalculated day from declaration date {declared_date}: "
                    f"{recalculated_day} (was {final_day})"
                )
                final_day = recalculated_day
            else:
                print(
                    f"[Main] Recalculated day from declaration date {declared_date} "
                    "is None (snow emergency window may be over)."
                )

        
        # Verify these aren't None or empty
        if is_active:
            if not final_day:
                print("[Main] ERROR: Active emergency but no day detected!")
            if not declared_date:
                print("[Main] ERROR: Active emergency but no declared date!")

        current_status["active"] = is_active
        current_status["last_check"] = datetime.now()
        current_status["day"] = final_day
        current_status["source"] = source

        if CHANNEL_ID:
            channel = bot.get_channel(CHANNEL_ID)
            if channel:
                status_info = {
                    "active": is_active,
                    "source": source.get("source") if source else "Minneapolis",
                    "day": final_day,
                    "declared_date": declared_date,
                }

                print(f"[Main] ABOUT TO CREATE EMBED with status_info: {status_info}")
                embed = create_snow_emergency_embed(status_info)

                if is_active:
                    if TEST_MODE:
                        await channel.send(embed=embed)
                    else:
                        await channel.send("@here", embed=embed)
                else:
                    await channel.send(embed=embed)

    current_status["last_check"] = datetime.now()


@check_snow_emergency.before_loop
async def before_check():
    """Wait for bot to be ready before starting the check loop."""
    await bot.wait_until_ready()
    print("Bot is ready, starting snow emergency checks...")


# -------------------------------------------------------------------
# COMMANDS
# -------------------------------------------------------------------


@bot.event
async def on_ready():
    """Called when bot successfully connects to Discord."""
    print(f"Logged in as {bot.user.name} ({bot.user.id})")
    print(f"Monitoring channel ID: {CHANNEL_ID}")
    print("------")

    if not check_snow_emergency.is_running():
        check_snow_emergency.start()

@bot.command(name="snowstatus")
async def snow_status(ctx):
    """
    Manual command to check current snow emergency status.
    Usage: !snowstatus
    """
    print(f"\n[Command] !snowstatus called by {ctx.author}")
    
    homepage_status = await check_minneapolis_homepage()
    snowmpls_status = await check_snowmpls_com()

    is_active = False
    source = None
    detected_day = None

    if homepage_status and homepage_status.get("active"):
        is_active = True
        source = homepage_status
        detected_day = homepage_status.get("day")
    elif snowmpls_status and snowmpls_status.get("active"):
        is_active = True
        source = snowmpls_status
        detected_day = snowmpls_status.get("day")

    final_day = detected_day
    declared_date = None

    # Always try to enrich with details page if active
    if is_active:
        print("[Command] Checking details page for declaration date / day...")
        details = await get_snow_emergency_details()
        if details:
            details_day = details.get("day")
            details_date = details.get("declaration_date")

            if not final_day and details_day:
                final_day = details_day
                print(f"[Command] Got day from details page: {final_day}")

            if details_date:
                declared_date = details_date
                print(f"[Command] Got declaration date from details: {declared_date}")

    # If we still don't have a declaration date, infer it from whatever day we think it is
    if not declared_date:
        declared_date = calculate_declaration_date(final_day)
        print(f"[Command] Calculated declaration date from day: {declared_date}")

    # FINAL sanity check: recompute day based on declaration date + official schedule
    if declared_date:
        recalculated_day = calculate_current_day_from_declaration(declared_date)
        if recalculated_day:
            print(
                f"[Command] Recalculated day from declaration date {declared_date}: "
                f"{recalculated_day} (was {final_day})"
            )
            final_day = recalculated_day
        else:
            print(
                f"[Command] Recalculated day from declaration date {declared_date} "
                "is None (snow emergency window may be over)."
            )

    print(f"[Command] FINAL VALUES - Active: {is_active}, Day: '{final_day}', Date: '{declared_date}'")
    
    # Verify values before creating embed
    if is_active:
        if not final_day:
            print("[Command] ERROR: Active emergency but no day detected!")
        if not declared_date:
            print("[Command] ERROR: Active emergency but no declared date!")

    status_info = {
        "active": is_active,
        "source": source.get("source") if source else "Minneapolis",
        "day": final_day,
        "declared_date": declared_date,
    }

    print(f"[Command] ABOUT TO CREATE EMBED with status_info: {status_info}")
    embed = create_snow_emergency_embed(status_info)
    await ctx.send(embed=embed)


@bot.command(name="snowhelp")
async def snow_help(ctx):
    """
    Show information about snow emergency parking rules.
    Usage: !snowhelp
    """
    embed = discord.Embed(
        title="Minneapolis Snow Emergency Parking Rules",
        description="Snow emergencies last 3 days with different rules each day:",
        color=discord.Color.blue(),
    )

    embed.add_field(
        name="Day 1 (9 PM - 8 AM next day)",
        value="❌ No parking on EITHER side of Snow Emergency routes",
        inline=False,
    )

    embed.add_field(
        name="Day 2 (8 AM - 8 PM)",
        value=(
            "❌ No parking on EVEN numbered side of non-emergency routes\n"
            "❌ No parking on EITHER side of parkways"
        ),
        inline=False,
    )

    embed.add_field(
        name="Day 3 (8 AM - 8 PM)",
        value="❌ No parking on ODD numbered side of non-emergency routes",
        inline=False,
    )

    embed.add_field(
        name="Resources",
        value=(
            "• Snow Emergencies: https://www.minneapolismn.gov/getting-around/snow/snow-emergencies/\n"
            "• Snow Emergency Map: https://www.minneapolismn.gov/getting-around/snow/snow-emergencies/snow-parking-rules/snow-emergency-map/\n"
            "• Call: 612-348-SNOW (7669)\n"
            "• Download: MPLS Parking app"
        ),
        inline=False,
    )

    await ctx.send(embed=embed)


# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not found in .env file")
        raise SystemExit(1)

    if not CHANNEL_ID:
        print("WARNING: DISCORD_CHANNEL_ID not set. Bot will start but won't post automatic alerts.")
        print("You can still use the !snowstatus command manually.")

    print("Starting Minneapolis Snow Emergency Bot...")
    print("Checking every 15 minutes")

    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        print(f"Failed to start bot: {e}")
