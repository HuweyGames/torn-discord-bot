import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import requests
from dotenv import load_dotenv
import asyncio

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
ROLE_NAME = os.getenv("ROLE_NAME", "FactionMember")
JOIN_CHANNEL_NAME = os.getenv("JOIN_CHANNEL_NAME", "join")
FACTION_FILE = "faction.txt"  # file to save faction name persistently

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# === Helper to read/write faction name to file ===
def read_faction():
    if os.path.isfile(FACTION_FILE):
        with open(FACTION_FILE, "r") as f:
            return f.read().strip()
    return None

def write_faction(name: str):
    with open(FACTION_FILE, "w") as f:
        f.write(name.strip())

# === Torn API helper ===
def get_torn_profile(user_id, api_key):
    url = f"https://api.torn.com/user/{user_id}?selections=profile&key={api_key}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.ok:
            return resp.json()
    except Exception:
        pass
    return None

# === Background task to check all faction members and remove role if needed ===
@tasks.loop(hours=1)
async def faction_members_check():
    await bot.wait_until_ready()
    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    faction = read_faction()
    if not faction:
        return

    role = discord.utils.get(guild.roles, name=ROLE_NAME)
    if not role:
        return

    print(f"[Checker] Starting role verification for faction '{faction}'")

    for member in role.members:
        try:
            # Here you must have a way to get each member's Torn API key
            # This example assumes you store user API keys somewhere,
            # but this bot doesn't persist them, so this is just placeholder logic
            # You can extend this to store API keys in a database or file
            # For now, skip removing role (since no API key stored)
            # You can optionally notify user to re-verify with /join

            # SKIP role removal due to missing API key storage
            pass

        except Exception as e:
            print(f"Error checking {member}: {e}")

    print("[Checker] Finished role verification")

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
        # Ask faction name in the join channel on startup
        guild = bot.guilds[0] if bot.guilds else None
        if guild:
            join_channel = discord.utils.get(guild.text_channels, name=JOIN_CHANNEL_NAME)
            if join_channel:
                await join_channel.send(
                    embed=discord.Embed(
                        title="Faction Setup Required",
                        description=(
                            "Hi! I don’t know your Torn City faction name yet.\n"
                            f"Please **type the exact faction name** you want me to verify against in this channel (`{JOIN_CHANNEL_NAME}`).\n\n"
                            "Only server admins can set this."
                        ),
                        color=discord.Color.orange(),
                    )
                )
    faction_members_check.start()

# Command for admins to set faction manually
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

# Listen for faction name if not set (only in join channel, from admins)
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    faction = read_faction()
    if not faction:
        if message.channel.name == JOIN_CHANNEL_NAME:
            if message.author.guild_permissions.administrator:
                # Save faction name
                write_faction(message.content.strip())
                await message.channel.send(
                    f"✅ Faction name set to **{message.content.strip()}**. Players will now be verified against this."
                )
            else:
                await message.channel.send("❌ Only admins can set the faction name.")
        return

    await bot.process_commands(message)

# The /join command
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

    # DM the user for their Torn API key
    try:
        await interaction.user.send(
            embed=discord.Embed(
                title="Torn API Key Required",
                description="Please reply with your Torn City API key to verify your faction membership.",
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

        # Validate API key by fetching basic info to get player ID
        user_data = requests.get(f"https://api.torn.com/user/?selections=basic&key={api_key}").json()
        if "player_id" not in user_data:
            await interaction.user.send("❌ Invalid API key or error. Please try again.")
            return

        player_id = user_data["player_id"]

        # Fetch profile including faction
        profile = get_torn_profile(player_id, api_key)
        if not profile or "faction" not in profile:
            await interaction.user.send("❌ Could not fetch faction data. Check your API key.")
            return

        player_faction = profile["faction"].get("faction_name", "None")
        player_name = profile.get("name", "Unknown")

        guild = interaction.guild
        role = discord.utils.get(guild.roles, name=ROLE_NAME)
        member = guild.get_member(interaction.user.id)
        join_channel = discord.utils.get(guild.text_channels, name=JOIN_CHANNEL_NAME)

        if player_faction == faction:
            if role and role not in member.roles:
                await member.add_roles(role)

            if join_channel:
                await join_channel.send(
                    embed=discord.Embed(
                        description=f"🎉 Welcome **{player_name}** [{ROLE_NAME}]!",
                        color=discord.Color.green()
                    )
                )

            await interaction.user.send(
                embed=discord.Embed(
                    description=f"✅ Welcome, **{player_name}**! You now have access to the faction channels.",
                    color=discord.Color.green()
                )
            )
        else:
            await interaction.user.send(
                embed=discord.Embed(
                    description=f"❌ You are not in the required faction (**{faction}**). Access denied.",
                    color=discord.Color.red()
                )
            )

    except asyncio.TimeoutError:
        await interaction.user.send("❌ Timeout: You took too long to reply. Please try /join again.")
    except Exception as e:
        await interaction.user.send("❌ An error occurred. Please try again later.")
        print(f"Error in join command: {e}")

