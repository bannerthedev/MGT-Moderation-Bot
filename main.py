import os
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ========== CONFIG ==========

CLIENT_ID = 1504948194293842135  # optional

# IDs for your servers/channels — change these
GUILD_EPL_ID = 1501126256849322135
GUILD_APPEAL_ID = 1504881417513865398
APPEAL_LOG_CHANNEL_ID = 1504881915277344808

MAIN_SERVER_INVITE = "https://discord.gg/YvTxrv7VFa"
APPEAL_SERVER_INVITE = "https://discord.gg/yKWsmVZ3jM"

APPEAL_COOLDOWN = timedelta(days=30)
MAX_APPEALS = 3

# in-memory appeal tracking: user_id -> {"count": int, "last_time": datetime}
appeal_data: dict[int, dict] = {}

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True
intents.dm_messages = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ------------ Helper functions ------------

async def unban_from_epl(user: discord.User):
    guild = bot.get_guild(GUILD_EPL_ID)
    if guild is None:
        return False, "Bot is not in the EPL server or cannot see it."

    try:
        await guild.unban(user, reason="Appeal accepted")
        return True, None
    except discord.NotFound:
        return False, "User is not banned in GTPL."
    except discord.Forbidden:
        return False, "Bot does not have permission to unban in GTPL."
    except Exception as e:
        return False, f"Unban failed: {e}"


async def kick_from_appeal_server(user: discord.User):
    guild = bot.get_guild(GUILD_APPEAL_ID)
    if guild is None:
        return False, "Bot is not in the appeals server or cannot see it."

    member = guild.get_member(user.id)
    if member is None:
        return False, "User is not in the appeals server."

    try:
        await member.kick(reason="Appeal accepted – removed from appeals server")
        return True, None
    except discord.Forbidden:
        return False, "Bot does not have permission to kick in the appeals server."
    except Exception as e:
        return False, f"Kick failed: {e}"


def can_submit_appeal(user_id: int):
    now = datetime.utcnow()
    record = appeal_data.get(user_id, {"count": 0, "last_time": datetime.utcfromtimestamp(0)})
    if record["count"] >= MAX_APPEALS:
        return False, "You have reached the maximum number of appeals (3). You can no longer appeal."

    if record["count"] > 0 and now - record["last_time"] < APPEAL_COOLDOWN:
        remaining = APPEAL_COOLDOWN - (now - record["last_time"])
        days = remaining.days + (1 if remaining.seconds > 0 else 0)
        return False, (
            "You can only submit one appeal every 1 month. "
            f"Please wait about **{days} day(s)** before trying again."
        )

    return True, record


def record_appeal(user_id: int):
    now = datetime.utcnow()
    record = appeal_data.get(user_id, {"count": 0, "last_time": datetime.utcfromtimestamp(0)})
    record["count"] += 1
    record["last_time"] = now
    appeal_data[user_id] = record
    return record


# ------------ Views / Modals ------------

class AppealAgreementView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="I Agree", style=discord.ButtonStyle.primary, custom_id="appeal_agree")
    async def agree_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AppealModal()
        await interaction.response.send_modal(modal)


class AppealModal(discord.ui.Modal, title="Ban Appeal Form"):
    ban_data = discord.ui.TextInput(
        label="DATA of ban and reason",
        style=discord.TextStyle.paragraph,
        required=True
    )
    incident_explanation = discord.ui.TextInput(
        label="Explanation of incident",
        style=discord.TextStyle.paragraph,
        required=True
    )
    appeal_reason = discord.ui.TextInput(
        label="Reason for appeal / changes since ban",
        style=discord.TextStyle.paragraph,
        required=True
    )
    commitments = discord.ui.TextInput(
        label="Commitments to future behavior",
        style=discord.TextStyle.paragraph,
        required=True
    )
    extra_comments = discord.ui.TextInput(
        label="Any additional comments?",
        style=discord.TextStyle.paragraph,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        user = interaction.user
        ok, record_or_msg = can_submit_appeal(user.id)
        if not ok:
            return await interaction.response.send_message(record_or_msg, ephemeral=True)

        record = record_appeal(user.id)

        embed = discord.Embed(
            title="New Ban Appeal",
            color=discord.Color.green()
        )
        embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=False)
        embed.add_field(name="DATA of ban and reason", value=str(self.ban_data), inline=False)
        embed.add_field(name="Explanation of incident", value=str(self.incident_explanation), inline=False)
        embed.add_field(name="Reason for appeal / changes since ban", value=str(self.appeal_reason), inline=False)
        embed.add_field(name="Commitments to future behavior", value=str(self.commitments), inline=False)
        embed.add_field(name="Additional comments", value=str(self.extra_comments) or "None", inline=False)
        embed.add_field(name="Appeal Count", value=f"{record['count']}/{MAX_APPEALS}", inline=True)
        embed.timestamp = discord.utils.utcnow()

        log_channel = bot.get_channel(APPEAL_LOG_CHANNEL_ID)
        if log_channel is None or not isinstance(log_channel, discord.TextChannel):
            await interaction.response.send_message(
                "Appeal received, but the appeals log channel is not set up correctly. Please tell an admin.",
                ephemeral=True
            )
            return

        view = StaffAppealView(target_user_id=user.id)
        await log_channel.send(embed=embed, view=view)

        await interaction.response.send_message(
            "Your appeal has been submitted. Staff will review it soon.",
            ephemeral=True
        )


class StaffAppealView(discord.ui.View):
    def __init__(self, target_user_id: int):
        super().__init__(timeout=None)
        self.target_user_id = target_user_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="appeal_accept")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("You cannot manage appeals.", ephemeral=True)

        user = bot.get_user(self.target_user_id) or await bot.fetch_user(self.target_user_id)
        if user is None:
            return await interaction.response.send_message("User not found.", ephemeral=True)

        unban_ok, unban_err = await unban_from_epl(user)
        kick_ok, kick_err = await kick_from_appeal_server(user)

        msg = f"Your ban appeal has been **accepted**.\nHere is the server invite: {MAIN_SERVER_INVITE}"

        if not unban_ok and unban_err:
            msg += (
                "\n\nHowever, there was an issue unbanning you automatically. "
                f"Please contact staff.\n`{unban_err}`"
            )

        if not kick_ok and kick_err and "not in the appeals server" not in kick_err:
            msg += f"\n\nThere was also an issue removing you from the appeals server:\n`{kick_err}`"

        try:
            await user.send(msg)
        except discord.Forbidden:
            pass

        text_parts = [f"✅ Appeal for {user} has been accepted."]
        if unban_ok:
            text_parts.append("User was **unbanned from EPL**.")
        else:
            text_parts.append(f"Unban issue: `{unban_err}`")

        if kick_ok:
            text_parts.append("User was **kicked from the appeals server**.")
        else:
            if kick_err and "not in the appeals server" not in kick_err:
                text_parts.append(f"Kick issue: `{kick_err}`")

        await interaction.response.send_message(" ".join(text_parts), ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="appeal_deny")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("You cannot manage appeals.", ephemeral=True)

        user = bot.get_user(self.target_user_id) or await bot.fetch_user(self.target_user_id)
        if user is None:
            return await interaction.response.send_message("User not found.", ephemeral=True)

        await interaction.response.send_message(
            f"❌ Appeal for {user} has been denied. (User will not be notified.)",
            ephemeral=True
        )

    @discord.ui.button(label="Chat with Person", style=discord.ButtonStyle.secondary, custom_id="appeal_chat")
    async def chat_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("You cannot manage appeals.", ephemeral=True)

        user = bot.get_user(self.target_user_id) or await bot.fetch_user(self.target_user_id)
        if user is None:
            return await interaction.response.send_message("User not found.", ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("Cannot create a chat thread here.", ephemeral=True)

        thread = await channel.create_thread(
            name=f"Appeal Chat - {user.name}",
            auto_archive_duration=1440,  # minutes (24h)
            type=discord.ChannelType.public_thread
        )

        await thread.send(
            f"{user.mention}, staff would like to chat with you about your appeal.\n"
            f"Only you and staff can talk here."
        )
        await interaction.response.send_message(f"🧵 Created chat thread: {thread.mention}", ephemeral=True)


# ------------ Slash commands ------------

# Register as GUILD commands
@tree.command(
    name="ban",
    description="Ban a member and DM them the appeal link.",
    guild=discord.Object(id=GUILD_EPL_ID)
)
@app_commands.describe(
    member="Member to ban",
    reason="Reason for ban",
    unban_time="When (if ever) they will be unbanned (e.g. never, 7d)"
)
async def ban_command(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
    unban_time: str
):
    if interaction.guild_id != GUILD_EPL_ID:
        return await interaction.response.send_message(
            "This command can only be used in EPL.",
            ephemeral=True
        )

    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True
        )

    try:
        embed = discord.Embed(
            title="You have been banned from EPL",
            color=discord.Color.red()
        )
        embed.add_field(name="Server", value="EPL", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Unbanned", value=unban_time, inline=False)
        embed.add_field(
            name="Appeal",
            value=f"If you want to get unbanned, here is the appeals server:\n{MAIN_SERVER_INVITE}",
            inline=False
        )
        await member.send(content=f"{member.mention}", embed=embed)
    except discord.Forbidden:
        pass

    await member.ban(reason=f"{reason} | Unban: {unban_time}")
    await interaction.response.send_message(
        f"✅ {member} has been banned.\nReason: **{reason}**\nUnbanned: **{unban_time}**",
        ephemeral=True
    )


@tree.command(
    name="appeal",
    description="Start a ban appeal.",
    guild=discord.Object(id=GUILD_APPEAL_ID)
)
async def appeal_command(interaction: discord.Interaction):
    if interaction.guild_id != GUILD_APPEAL_ID:
        return await interaction.response.send_message(
            "This command can only be used in the appeals server.",
            ephemeral=True
        )

    ok, record_or_msg = can_submit_appeal(interaction.user.id)
    if not ok:
        return await interaction.response.send_message(record_or_msg, ephemeral=True)

    embed = discord.Embed(
        title="Ban Appeal Agreement",
        color=discord.Color.blurple()
    )
    embed.description = (
        "By submitting this ban appeal, you agree to the following terms:\n\n"
        "• Only one unban request every 1 month.\n"
        "• There is a maximum appeal of 3 – if you are not accepted by the 3rd appeal, you cannot appeal anymore.\n"
        "• Honesty is required. Dishonesty = immediate voiding of the appeal.\n"
        "• Submitting an appeal does not guarantee an unban.\n\n"
        "Click the button below to proceed."
    )

    view = AppealAgreementView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ------------ Bot events ------------

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        # Sync guild commands to the specific guilds
        synced_epl = await tree.sync(guild=discord.Object(id=GUILD_EPL_ID))
        synced_appeal = await tree.sync(guild=discord.Object(id=GUILD_APPEAL_ID))

        print(f"EPL guild commands: {[c.name for c in synced_epl]}")
        print(f"Appeal guild commands: {[c.name for c in synced_appeal]}")
    except Exception as e:
        print("Sync error:", e)

else:
    bot.run(os.getenv("TOKEN"))
