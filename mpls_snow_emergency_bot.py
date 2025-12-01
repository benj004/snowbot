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

APPROACHES:
This bot uses multiple methods to detect snow emergencies:
1. Scrapes the Minneapolis homepage for the snow emergency banner
2. Checks snowmpls.com for current status
3. Can call the snow emergency hotline (612-348-7669) for status

The bot checks every 15 minutes and posts updates when status changes.
"""

import discord
from discord.ext import commands, tasks
import aiohttp
from bs4 import BeautifulSoup
import os
from datetime import datetime
import asyncio
from typing import Optional, Dict
import re

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

TEST_MODE = True
BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
CHANNEL_ID = int(os.getenv('DISCORD_CHANNEL_ID', 0))

# URLs to check
MINNEAPOLIS_HOMEPAGE = "https://www.minneapolismn.gov/"
SNOW_MPLS = "https://snowmpls.com/"
SNOW_INFO_PAGE = "https://www.minneapolismn.gov/getting-around/snow/snow-emergencies/"

# Create bot with intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Store the current snow emergency status
current_status = {
    'active': False,
    'last_check': None,
    'details': None,
    'day': None
}


async def check_minneapolis_homepage() -> Optional[Dict]:
    """
    Check the Minneapolis homepage for snow emergency banner.
    Returns dict with status info if emergency is active.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(MINNEAPOLIS_HOMEPAGE, timeout=10) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Look for snow emergency banner/alert
                    # The city puts a banner at the top when there's an emergency
                    alert_keywords = ['snow emergency', 'Snow Emergency', 'SNOW EMERGENCY', 'Snow emergency']
                    
                    # Check for alert banners or notices
                    for keyword in alert_keywords:
                        if keyword in html:
                            # Found indication of snow emergency
                            return {
                                'active': True,
                                'source': 'minneapolis.gov homepage',
                                'detected_at': datetime.now()
                            }
                    
                    return {'active': False}
                    
    except Exception as e:
        print(f"Error checking Minneapolis homepage: {e}")
        return None


async def check_snowmpls_com() -> Optional[Dict]:
    """
    Check snowmpls.com - a third-party site that tracks snow emergencies.
    This site has a clean API-like structure.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SNOW_MPLS, timeout=10) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Check page title - it changes to "Snow Emergency Active" when active
                    title = soup.find('title')
                    if title and 'Active' in title.text:
                        # Extract current day and rules
                        body_text = soup.get_text()
                        
                        return {
                            'active': True,
                            'source': 'snowmpls.com',
                            'detected_at': datetime.now(),
                            'page_content': body_text[:500]  # First 500 chars
                        }
                    
                    return {'active': False}
                    
    except Exception as e:
        print(f"Error checking snowmpls.com: {e}")
        return None


async def get_snow_emergency_details() -> Optional[Dict]:
    """
    Get detailed information about current snow emergency from city website.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(SNOW_INFO_PAGE, timeout=10) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    # Extract any visible emergency information
                    text = soup.get_text()
                    
                    # Look for day indicators
                    day_match = re.search(r'Day (\d)', text)
                    day = day_match.group(1) if day_match else None
                    
                    return {
                        'day': day,
                        'page_text': text[:1000]
                    }
                    
    except Exception as e:
        print(f"Error getting details: {e}")
        return None


def create_snow_emergency_embed(status_info: Dict) -> discord.Embed:
    """
    Create a Discord embed with snow emergency information.
    """
    if status_info.get('active'):
        embed = discord.Embed(
            title="❄️ SNOW EMERGENCY DECLARED ❄️",
            description="A snow emergency has been declared in Minneapolis!",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        
        embed.add_field(
            name="Source",
            value=status_info.get('source', 'Minneapolis'),
            inline=True
        )
        
        if status_info.get('day'):
            embed.add_field(
                name="Current Day",
                value=f"Day {status_info['day']}",
                inline=True
            )
        
        embed.add_field(
            name="📱 More Info",
            value="• Website: [minneapolismn.gov](https://www.minneapolismn.gov/getting-around/snow/snow-emergencies/)\n"
                  "• Hotline: 612-348-SNOW (7669)\n"
                  "• App: MPLS Parking",
            inline=False
        )
        
        embed.add_field(
            name="⚠️ Remember",
            value="Follow parking rules or risk being ticketed and towed!",
            inline=False
        )
        
        embed.set_footer(text="Check the website for detailed parking rules")
        
    else:
        embed = discord.Embed(
            title="✅ No Snow Emergency",
            description="No snow emergency currently in effect.",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
    
    return embed


@tasks.loop(minutes=15)
async def check_snow_emergency():
    """
    Background task that checks for snow emergencies every 15 minutes.
    """
    global current_status
    
    print(f"[{datetime.now()}] Checking for snow emergency...")
    
    # Check both sources
    homepage_status = await check_minneapolis_homepage()
    snowmpls_status = await check_snowmpls_com()
    
    # Determine if there's an active emergency
    is_active = False
    source = None
    
    if homepage_status and homepage_status.get('active'):
        is_active = True
        source = homepage_status
    elif snowmpls_status and snowmpls_status.get('active'):
        is_active = True
        source = snowmpls_status
    
    # Check if status has changed
    status_changed = current_status['active'] != is_active
    
    if status_changed:
        print(f"Status changed! Active: {is_active}")
        
        # Get details if active
        details = None
        if is_active:
            details = await get_snow_emergency_details()
        
        # Update current status
        current_status['active'] = is_active
        current_status['last_check'] = datetime.now()
        current_status['details'] = details
        current_status['source'] = source
        
        # Send notification to Discord
        if CHANNEL_ID:
            channel = bot.get_channel(CHANNEL_ID)
            if channel:
                status_info = {
                    'active': is_active,
                    'source': source.get('source') if source else 'Minneapolis',
                    'day': details.get('day') if details else None
                }
                
                embed = create_snow_emergency_embed(status_info)
                
                if is_active:
                    if TEST_MODE:
                        await channel.send(embed=embed)
                    else:
                        await channel.send("@here", embed=embed)
                else:
                    await channel.send(embed=embed)
    
    current_status['last_check'] = datetime.now()


@check_snow_emergency.before_loop
async def before_check():
    """Wait for bot to be ready before starting the check loop."""
    await bot.wait_until_ready()
    print("Bot is ready, starting snow emergency checks...")


@bot.event
async def on_ready():
    """Called when bot successfully connects to Discord."""
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print(f'Monitoring channel ID: {CHANNEL_ID}')
    print('------')
    
    # Start the background task
    if not check_snow_emergency.is_running():
        check_snow_emergency.start()


@bot.command(name='snowstatus')
async def snow_status(ctx):
    """
    Manual command to check current snow emergency status.
    Usage: !snowstatus
    """
    # Force a check
    homepage_status = await check_minneapolis_homepage()
    snowmpls_status = await check_snowmpls_com()
    
    is_active = False
    source = None
    
    if homepage_status and homepage_status.get('active'):
        is_active = True
        source = homepage_status
    elif snowmpls_status and snowmpls_status.get('active'):
        is_active = True
        source = snowmpls_status
    
    details = None
    if is_active:
        details = await get_snow_emergency_details()
    
    status_info = {
        'active': is_active,
        'source': source.get('source') if source else 'Minneapolis',
        'day': details.get('day') if details else None
    }
    
    embed = create_snow_emergency_embed(status_info)
    await ctx.send(embed=embed)


@bot.command(name='snowhelp')
async def snow_help(ctx):
    """
    Show information about snow emergency parking rules.
    Usage: !snowhelp
    """
    embed = discord.Embed(
        title="Minneapolis Snow Emergency Parking Rules",
        description="Snow emergencies last 3 days with different rules each day:",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="Day 1 (9 PM - 8 AM next day)",
        value="❌ No parking on EITHER side of Snow Emergency routes",
        inline=False
    )
    
    embed.add_field(
        name="Day 2 (8 AM - 8 PM)",
        value="❌ No parking on EVEN numbered side of non-emergency routes\n"
              "❌ No parking on EITHER side of parkways",
        inline=False
    )
    
    embed.add_field(
        name="Day 3 (8 AM - 8 PM)",
        value="❌ No parking on ODD numbered side of non-emergency routes",
        inline=False
    )
    
    embed.add_field(
        name="Resources",
        value="• [Snow Emergency Map](https://www.minneapolismn.gov/getting-around/snow/snow-emergencies/)\n"
              "• Call: 612-348-SNOW (7669)\n"
              "• Download: MPLS Parking app",
        inline=False
    )
    
    await ctx.send(embed=embed)


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not found in .env file")
        exit(1)
    
    if not CHANNEL_ID:
        print("WARNING: DISCORD_CHANNEL_ID not set. Bot will start but won't post automatic alerts.")
        print("You can still use the !snowstatus command manually.")
    
    print("Starting Minneapolis Snow Emergency Bot...")
    print(f"Checking every 15 minutes")
    
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        print(f"Failed to start bot: {e}")
