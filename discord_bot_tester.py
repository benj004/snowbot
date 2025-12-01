"""
Discord Bot Testing Suite
==========================
Run this script to test your bot setup before deploying to your server.

This will test:
1. Environment variables are loaded correctly
2. Discord bot token is valid
3. Bot can connect to Discord
4. Web scraping functions work
5. Channel permissions (if channel ID provided)

Usage:
    python test_bot.py
"""

import asyncio
import os
from dotenv import load_dotenv
import sys

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def print_success(msg):
    print(f"{GREEN}✓{RESET} {msg}")

def print_error(msg):
    print(f"{RED}✗{RESET} {msg}")

def print_warning(msg):
    print(f"{YELLOW}⚠{RESET} {msg}")

def print_info(msg):
    print(f"{BLUE}ℹ{RESET} {msg}")

def print_header(msg):
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}{msg}{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")


# Test 1: Environment Variables
def test_env_vars():
    print_header("TEST 1: Environment Variables")
    
    load_dotenv()
    
    bot_token = os.getenv('DISCORD_BOT_TOKEN')
    channel_id = os.getenv('DISCORD_CHANNEL_ID')
    guild_id = os.getenv('DISCORD_GUILD_ID')
    
    if bot_token:
        print_success(f"DISCORD_BOT_TOKEN found (starts with: {bot_token[:10]}...)")
    else:
        print_error("DISCORD_BOT_TOKEN not found in .env file")
        return False
    
    if channel_id:
        print_success(f"DISCORD_CHANNEL_ID found: {channel_id}")
    else:
        print_warning("DISCORD_CHANNEL_ID not set (optional for testing)")
    
    if guild_id:
        print_success(f"DISCORD_GUILD_ID found: {guild_id}")
    else:
        print_warning("DISCORD_GUILD_ID not set (optional for snow bot)")
    
    return bool(bot_token)


# Test 2: Required Packages
def test_packages():
    print_header("TEST 2: Required Packages")
    
    packages = [
        ('discord', 'discord.py'),
        ('aiohttp', 'aiohttp'),
        ('bs4', 'beautifulsoup4'),
        ('dotenv', 'python-dotenv'),
        ('requests', 'requests')
    ]
    
    all_installed = True
    
    for import_name, package_name in packages:
        try:
            __import__(import_name)
            print_success(f"{package_name} is installed")
        except ImportError:
            print_error(f"{package_name} is NOT installed")
            print_info(f"   Install with: pip install {package_name}")
            all_installed = False
    
    return all_installed


# Test 3: Discord Connection
async def test_discord_connection():
    print_header("TEST 3: Discord API Connection")
    
    try:
        import discord
        from discord.ext import commands
        
        load_dotenv()
        token = os.getenv('DISCORD_BOT_TOKEN')
        
        if not token:
            print_error("No bot token to test")
            return False
        
        print_info("Attempting to connect to Discord...")
        
        intents = discord.Intents.default()
        intents.message_content = True
        bot = commands.Bot(command_prefix='!', intents=intents)
        
        @bot.event
        async def on_ready():
            print_success(f"Successfully connected as {bot.user.name} (ID: {bot.user.id})")
            
            # Check guilds (servers) the bot is in
            if len(bot.guilds) == 0:
                print_warning("Bot is not in any servers yet!")
                print_info("   Use the OAuth2 URL to invite your bot to a server")
            else:
                print_success(f"Bot is in {len(bot.guilds)} server(s):")
                for guild in bot.guilds:
                    print(f"   - {guild.name} (ID: {guild.id})")
            
            await bot.close()
        
        # Try to connect with a timeout
        try:
            await asyncio.wait_for(bot.start(token), timeout=10.0)
            return True
        except asyncio.TimeoutError:
            print_error("Connection timed out")
            return False
        except discord.LoginFailure:
            print_error("Invalid bot token - check your .env file")
            return False
        
    except Exception as e:
        print_error(f"Discord connection failed: {e}")
        return False


# Test 4: Channel Access
async def test_channel_access():
    print_header("TEST 4: Channel Access Test")
    
    try:
        import discord
        from discord.ext import commands
        
        load_dotenv()
        token = os.getenv('DISCORD_BOT_TOKEN')
        channel_id = os.getenv('DISCORD_CHANNEL_ID')
        
        if not channel_id:
            print_warning("DISCORD_CHANNEL_ID not set - skipping channel test")
            return True
        
        channel_id = int(channel_id)
        
        intents = discord.Intents.default()
        intents.message_content = True
        bot = commands.Bot(command_prefix='!', intents=intents)
        
        @bot.event
        async def on_ready():
            channel = bot.get_channel(channel_id)
            
            if channel:
                print_success(f"Found channel: #{channel.name}")
                
                # Check permissions
                permissions = channel.permissions_for(channel.guild.me)
                
                required_perms = {
                    'send_messages': permissions.send_messages,
                    'embed_links': permissions.embed_links,
                    'read_message_history': permissions.read_message_history
                }
                
                all_perms = True
                for perm, has_perm in required_perms.items():
                    if has_perm:
                        print_success(f"   Has permission: {perm}")
                    else:
                        print_error(f"   Missing permission: {perm}")
                        all_perms = False
                
                if not all_perms:
                    print_warning("Bot needs additional permissions in this channel")
                    
            else:
                print_error(f"Cannot access channel ID {channel_id}")
                print_info("   Make sure the bot is in the server and can see this channel")
            
            await bot.close()
        
        await asyncio.wait_for(bot.start(token), timeout=10.0)
        return True
        
    except Exception as e:
        print_error(f"Channel access test failed: {e}")
        return False


# Test 5: Web Scraping Functions
async def test_web_scraping():
    print_header("TEST 5: Web Scraping Functions")
    
    try:
        import aiohttp
        from bs4 import BeautifulSoup
        
        print_info("Testing Minneapolis homepage access...")
        
        async with aiohttp.ClientSession() as session:
            # Test Minneapolis homepage
            async with session.get("https://www.minneapolismn.gov/", timeout=10) as response:
                if response.status == 200:
                    print_success("Can access Minneapolis homepage")
                else:
                    print_error(f"Minneapolis homepage returned status {response.status}")
            
            # Test snowmpls.com
            print_info("Testing snowmpls.com access...")
            async with session.get("https://snowmpls.com/", timeout=10) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    title = soup.find('title')
                    if title:
                        print_success(f"Can access snowmpls.com - Title: {title.text}")
                else:
                    print_error(f"snowmpls.com returned status {response.status}")
        
        return True
        
    except Exception as e:
        print_error(f"Web scraping test failed: {e}")
        return False


# Test 6: Quick Bot Command Test
async def test_bot_commands():
    print_header("TEST 6: Bot Commands Test")
    
    try:
        import discord
        from discord.ext import commands
        
        load_dotenv()
        token = os.getenv('DISCORD_BOT_TOKEN')
        
        intents = discord.Intents.default()
        intents.message_content = True
        bot = commands.Bot(command_prefix='!', intents=intents)
        
        @bot.command(name='test')
        async def test_command(ctx):
            await ctx.send("Test successful!")
        
        @bot.event
        async def on_ready():
            print_success("Bot commands are registered:")
            for command in bot.commands:
                print(f"   !{command.name}")
            await bot.close()
        
        await asyncio.wait_for(bot.start(token), timeout=10.0)
        return True
        
    except Exception as e:
        print_error(f"Command test failed: {e}")
        return False


# Main test runner
async def run_all_tests():
    print(f"\n{BLUE}{'='*60}")
    print("Discord Bot Test Suite")
    print(f"{'='*60}{RESET}\n")
    
    results = []
    
    # Synchronous tests
    results.append(("Environment Variables", test_env_vars()))
    results.append(("Required Packages", test_packages()))
    
    # Async tests
    if results[0][1] and results[1][1]:  # Only run if previous tests passed
        results.append(("Discord Connection", await test_discord_connection()))
        results.append(("Channel Access", await test_channel_access()))
        results.append(("Web Scraping", await test_web_scraping()))
        results.append(("Bot Commands", await test_bot_commands()))
    
    # Summary
    print_header("TEST SUMMARY")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        if result:
            print_success(f"{test_name}")
        else:
            print_error(f"{test_name}")
    
    print(f"\n{BLUE}Results: {passed}/{total} tests passed{RESET}\n")
    
    if passed == total:
        print_success("All tests passed! Your bot is ready to deploy! 🎉")
        print_info("\nNext steps:")
        print("   1. Run your bot: python mpls_snow_bot.py")
        print("   2. Test commands in Discord: !snowstatus")
        print("   3. Bot will check for snow emergencies every 15 minutes")
    else:
        print_warning("Some tests failed. Fix the issues above before deploying.")
        
        if not results[0][1]:
            print_info("\n🔧 Fix: Check your .env file has DISCORD_BOT_TOKEN set")
        if not results[1][1]:
            print_info("\n🔧 Fix: Install missing packages with pip install <package_name>")
        if len(results) > 2 and not results[2][1]:
            print_info("\n🔧 Fix: Verify your bot token is correct")
    
    return passed == total


if __name__ == "__main__":
    try:
        success = asyncio.run(run_all_tests())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Tests interrupted by user{RESET}")
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        sys.exit(1)
