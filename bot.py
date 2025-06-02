import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import requests
from dotenv import load_dotenv
import asyncio
import json
import time

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
ROLE_PREFIX = os.getenv("ROLE_PREFIX", "Faction")  # e.g., Faction Leader, Faction Member
JOIN_CHANNEL_NAME = os.getenv("JOIN_CHANNEL_NAME", "join")
FACTION_FILE = "faction.txt"
API_KEYS_FILE = "api_keys.json"
TORN_API_RATE_LIMIT = 0.6  # Seconds between API calls (100 req/min = ~0.6s)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# === Helper Functions ===
def read_faction():
    if os.path.isfile(FACTION_FILE):
        with open(FACTION_FILE, "r") as f:
            return f.read().strip()
    return None

def write_faction(name: str):
    with open(FACTION_FILE, "w") as f:
        f.write(name.strip())

def load_api_keys():
    if os.path.isfile(API_KEYS_FILE):
        with open(API_KEYS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_api_key(discord_id: str, api_key: str):
    api_keys = load_api_keys()
    api_keys[discord_id] = api_key
    with open(API_KEYS_FILE, "w") as f:
        json.dump(api_keys, f, indent=4)

def get_torn_profile(user_id, api_key):
    url = f"https://api.torn.com/user/{user_id}?selections=profile&key={api_key}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.ok:
            return resp.json()
        elif resp.status_code == 429:
            print("Torn API rate limit hit")
            return None
    except Exception as e:
        print(f"Torn API error: {e}")
        return None
    return None

# === Background Task to Check Faction Membership ===
@tasks.loop(hours=1)
async def faction_members_check():
    await bot.wait_until_ready()
    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        print("No guild found")
        return

    faction = read_faction()
    if not faction:
        print("No faction set")
        return

    print(f"[Checker] Starting role verification for faction '{faction}'")
    api_keys = load_api_keys()

    for member in guild.members:
        discord_id = str(member.id)
        if discord_id not in api_keys:
            continue

        try:
            api_key = api_keys[discord_id]
            user_data = requests.get(f"https://api.torn.com/user/?selections=basic&key={api_key}").json()
            time.sleep(TORN_API_RATE_LIMIT)
            if "player_id" not in user_data:
                await member.send("⚠️ Invalid API key. Please re-verify with /join.")
                await remove_roles(member, guild)
                del api_keys[discord_id]
                with open(API_KEYS_FILE, "w") as f:
                    json.dump(api_keys, f, indent=4)
                continue

            player_id = user_data["player_id"]
            profile = get_torn_profile(player_id, api_key)
            time.sleep(TORN_API_RATE_LIMIT)
            if not profile or "faction" not in profile:
                await member.send("⚠️ Could not fetch faction data. Please re-verify with /join.")
                await remove_roles(member, guild)
                del api_keys[discord_id]
                with open(API_KEYS_FILE, "w") as f:
                    json.dump(api_keys, f, indent=4)
                continue

            player_faction = profile["faction"].get("faction_name", "None")
            if player_faction != faction:
                await member.send(f"⚠️ You are no longer in {faction}. Roles removed.")
                await remove_roles(member, guild)
                del api_keys[discord_id]
                with open(API_KEYS_FILE, "w") as f:
                    json.dump(api_keys, f, indent=4)

        except Exception as e:
            print(f"Error checking {member}: {e}")

    print("[Checker] Finished role verification")

async def remove_roles(member, guild):
    for role in member.roles:
        if role.name.startswith(ROLE_PREFIX):
            await member.remove_roles(role)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        await bot.tree.sync()
        print("✅ Slash commands synced.")
    except Exception as e:
        print(f"Sync error: {e}")

    faction = read_faction()
    if not faction:
        guild = bot.guilds[0] if bot.guilds else None
        if guild:
            join_channel = discord.utils.get(guild.text_channels, name=JOIN_CHANNEL_NAME)
            if join_channel:
                await join_channel.send(
                    embed=discord.Embed(
                        title="Faction Setup Required",
                        description=(
                            f"Please **type the exact faction name** you want to verify against in this channel (`{JOIN_CHANNEL_NAME}`).\n\n"
                            "Only server admins can set this."
                        ),
                        color=discord.Color.orange(),
                    )
                )
    faction_members_check.start()

# === Admin Command to Set Faction ===
@bot.tree.command(name="setfaction", description="Set the Torn City faction name to verify players against (admin only).")
@app_commands.checks.has_permissions(administrator=True)
async def setfaction(interaction: discord.Interaction, faction_name: str):
    write_faction(faction_name)
    await interaction.response.send_message(
        f"✅ Faction name set to **{faction_name}**. The bot will use this to verify players.", ephemeral=True
    )

@setfaction.error
async def setfaction_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ You must be an admin to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Error: {error}", ephemeral=True)

# === Listen for Faction Name ===
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    faction = read_faction()
    if not faction and message.channel.name == JOIN_CHANNEL_NAME:
        if message.author.guild_permissions.administrator:
            write_faction(message.content.strip())
            await message.channel.send(
                f"✅ Faction name set to **{message.content.strip()}**. Players will now be verified against this."
            )
        else:
            await message.channel.send("❌ Only admins can set the faction name.")
        return

    await bot.process_commands(message)

# === Join Command ===
@bot.tree.command(name="join", description="Join faction channels if you're in the faction")
async def join(interaction: discord.Interaction):
    faction = read_faction()
    if not faction:
        await interaction.response.send_message(
            "❌ The faction name is not set yet. Please wait for an admin to set it.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        "✅ I’ve sent you a DM! Please check your messages.", ephemeral=True
    )

    try:
        await interaction.user.send(
            embed=discord.Embed(
                title="Torn API Key Required",
                description="Please reply with your **public** Torn City API key to verify your faction membership.",
                color=discord.Color.blue(),
            )
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ I couldn't DM you. Please enable DMs and try again.", ephemeral=True
        )
        return

    def check(m):
        return m.author == interaction.user and isinstance(m.channel, discord.DMChannel)

    try:
        msg = await bot.wait_for('message', check=check, timeout=120)
        api_key = msg.content.strip()

        user_data = requests.get(f"https://api.torn.com/user/?selections=basic&key={api_key}").json()
        time.sleep(TORN_API_RATE_LIMIT)
        if "player_id" not in user_data:
            await interaction.user.send("❌ Invalid API key or error. Please try again.")
            return

        player_id = user_data["player_id"]
        profile = get_torn_profile(player_id, api_key)
        time.sleep(TORN_API_RATE_LIMIT)
        if not profile or "faction" not in profile:
            await interaction.user.send("❌ Could not fetch faction data. Check your API key.")
            return

        player_faction = profile["faction"].get("faction_name", "None")
        player_name = profile.get("name", "Unknown")
        faction_position = profile["faction"].get("position", "Member")

        guild = interaction.guild
        member = guild.get_member(interaction.user.id)
        join_channel = discord.utils.get(guild.text_channels, name=JOIN_CHANNEL_NAME)

        if player_faction != faction:
            await interaction.user.send(
                embed=discord.Embed(
                    description=f"❌ You are not in the required faction (**{faction}**). Access denied.",
                    color=discord.Color.red()
                )
            )
            return

        # Save API key
        save_api_key(str(member.id), api_key)

        # Update nickname
        try:
            new_nickname = f"{member.name} ({player_name})"
            await member.edit(nick=new_nickname)
        except Exception as e:
            print(f"Error updating nickname for {member}: {e}")

        # Assign role based on faction position
        role_name = f"{ROLE_PREFIX} {faction_position}"
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            try:
                role = await guild.create_role(name=role_name, mentionable=True)
            except Exception as e:
                await interaction.user.send("❌ Could not create role. Please contact an admin.")
                print(f"Error creating role {role_name}: {e}")
                return

        await member.add_roles(role)

        if join_channel:
            await join_channel.send(
                embed=discord.Embed(
                    description=f"🎉 Welcome **{player_name}** [{role_name}]!",
                    color=discord.Color.green()
                )
            )

        await interaction.user.send(
            embed=discord.Embed(
                description=f"✅ Welcome, **{player_name}**! You now have access to the faction channels as a **{faction_position}**.",
                color=discord.Color.green()
            )
        )

    except asyncio.TimeoutError:
        await interaction.user.send("❌ Timeout: You took too long to reply. Please try /join again.")
    except Exception as e:
        await interaction.user.send("❌ An error occurred. Please try again later.")
        print(f"Error in join command: {e}")

# Run the bot
bot.run(DISCORD_TOKEN)
