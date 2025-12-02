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
from datetime import datetime
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
# DATE EXTRACTION: pick the MOST RECENT date on the page
# -------------------------------------------------------------------


def extract_snow_emergency_date(text: str) -> Optional[str]:
    """
    Extract the most recent-looking date from snow emergency text.

    This avoids grabbing old example dates like "December 19, 2024"
    if newer ones like "December 1" (this year) also appear.
    """
    date_patterns = [
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(,?\s*\d{4})?",
        r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{1,2}(,?\s*\d{4})?",
        r"\d{1,2}/\d{1,2}/\d{4}",
        r"\d{4}-\d{2}-\d{2}",
    ]

    def parse_candidate_date(s: str) -> Optional[datetime]:
        s_clean = s.strip().replace("\xa0", " ")
        formats_with_year = [
            "%B %d, %Y",
            "%B %d %Y",
            "%b %d, %Y",
            "%b %d %Y",
            "%m/%d/%Y",
            "%Y-%m-%d",
        ]
        formats_without_year = [
            "%B %d",
            "%b %d",
        ]

        # Try patterns that include a year
        for fmt in formats_with_year:
            try:
                return datetime.strptime(s_clean, fmt)
            except ValueError:
                pass

        # Try patterns without a year: assume current year
        for fmt in formats_without_year:
            try:
                dt = datetime.strptime(s_clean, fmt)
                now = datetime.now()
                return dt.replace(year=now.year)
            except ValueError:
                pass

        return None

    candidates = set()
    for pattern in date_patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            candidates.add(m.group(0))

    best_date: Optional[datetime] = None
    for raw in candidates:
        dt = parse_candidate_date(raw)
        if not dt:
            continue
        if best_date is None or dt > best_date:
            best_date = dt

    if not best_date:
        return None

    # Format nicely: "December 1, 2025"
    month_name = best_date.strftime("%B")
    return f"{month_name} {best_date.day}, {best_date.year}"


# -------------------------------------------------------------------
# SCRAPERS
# -------------------------------------------------------------------


async def check_minneapolis_homepage() -> Optional[Dict]:
    """
    Check the Minneapolis homepage for a snow emergency banner and, if present,
    try to read which Day (1/2/3) is currently in effect from the banner text.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(MINNEAPOLIS_HOMEPAGE, timeout=10) as response:
                if response.status != 200:
                    return None

                html = await response.text()

                # Try to find "Day 1", "Day 2", "Day 3" in the homepage banner.
                day_match = re.search(r"Day\s*(1|2|3)", html, re.IGNORECASE)
                day_number = day_match.group(1) if day_match else None

                # Check for "snow emergency" text at all.
                if "snow emergency" in html.lower():
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
    Check snowmpls.com – a third-party site that often reflects current status.
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
                    return {
                        "active": True,
                        "source": "snowmpls.com",
                        "detected_at": datetime.now(),
                        "page_content": body_text[:500],
                    }

                return {"active": False}

    except Exception as e:
        print(f"Error checking snowmpls.com: {e}")
        return None


async def get_snow_emergency_details() -> Optional[Dict]:
    """
    Get *current* snow emergency details from the official Snow Updates page.
    This is where they list things like:
      "December 1. Snow Emergency in effect. Snow Emergency Day 2 rules..."
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SNOW_UPDATES_PAGE, timeout=10) as response:
                if response.status != 200:
                    return None

                html = await response.text()
                soup = BeautifulSoup(html, "html.parser")
                text = soup.get_text(separator=" ")

                # Find "Day 1/2/3" anywhere in the updates text.
                day_match = re.search(r"Day\s*(1|2|3)", text, re.IGNORECASE)
                day = day_match.group(1) if day_match else None

                # Extract the most recent date from the text.
                declared_date = extract_snow_emergency_date(text)

                return {
                    "day": day,
                    "declared_date": declared_date,
                    "page_text": text[:1000],
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
    Shows both Declared Date and Current Day when available.
    """
    active = status_info.get("active")

    if active:
        embed = discord.Embed(
            title="❄️ SNOW EMERGENCY DECLARED ❄️",
            description="A snow emergency has been declared in Minneapolis!",
            color=discord.Color.red(),
            timestamp=datetime.now(),
        )

        embed.add_field(
            name="Source",
            value=status_info.get("source", "Minneapolis"),
            inline=True,
        )

        # Declared date, if we could scrape it.
        if status_info.get("declared_date"):
            embed.add_field(
                name="🗓 Declared On",
                value=status_info["declared_date"],
                inline=True,
            )

        # Current Day (1/2/3)
        if status_info.get("day"):
            embed.add_field(
                name="Current Day",
                value=f"Day {status_info['day']}",
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

    print(f"[{datetime.now()}] Checking for snow emergency...")

    homepage_status = await check_minneapolis_homepage()
    snowmpls_status = await check_snowmpls_com()

    is_active = False
    source = None

    if homepage_status and homepage_status.get("active"):
        is_active = True
        source = homepage_status
    elif snowmpls_status and snowmpls_status.get("active"):
        is_active = True
        source = snowmpls_status

    status_changed = current_status["active"] != is_active

    details = None
    final_day = None
    final_date = None

    if status_changed:
        print(f"Status changed! Active: {is_active}")

        if is_active:
            details = await get_snow_emergency_details()

        homepage_day = homepage_status.get("day") if homepage_status else None
        details_day = details.get("day") if details else None if details else None
        final_day = homepage_day or details_day

        final_date = details.get("declared_date") if details else None

        current_status["active"] = is_active
        current_status["last_check"] = datetime.now()
        current_status["details"] = details
        current_status["source"] = source

        if CHANNEL_ID:
            channel = bot.get_channel(CHANNEL_ID)
            if channel:
                status_info = {
                    "active": is_active,
                    "source": source.get("source") if source else "Minneapolis",
                    "day": final_day,
                    "declared_date": final_date,
                }

                embed = create_snow_emergency_embed(status_info)

                # Respect TEST_MODE: no @here while testing
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
    homepage_status = await check_minneapolis_homepage()
    snowmpls_status = await check_snowmpls_com()

    is_active = False
    source = None

    if homepage_status and homepage_status.get("active"):
        is_active = True
        source = homepage_status
    elif snowmpls_status and snowmpls_status.get("active"):
        is_active = True
        source = snowmpls_status

    details = None
    final_day = None
    final_date = None

    if is_active:
        details = await get_snow_emergency_details()

    homepage_day = homepage_status.get("day") if homepage_status else None
    details_day = details.get("day") if details else None if details else None
    final_day = homepage_day or details_day

    final_date = details.get("declared_date") if details else None

    status_info = {
        "active": is_active,
        "source": source.get("source") if source else "Minneapolis",
        "day": final_day,
        "declared_date": final_date,
    }

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

