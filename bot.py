import discord
from discord.ext import commands
import sqlite3
import os

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

DB_PATH = "/data/config.db"
guild_configs = {}  # cache: guild_id -> {"category": int, "log": int|None, "staff": int}

# ================= TICKET TYPES (customize here if you want) =================
TICKET_TYPES = {
    "general": {"label": "General Support", "emoji": "❔", "prefix": "general", "desc": "General questions or help"},
    "billing": {"label": "Billing & Payments", "emoji": "💰", "prefix": "billing", "desc": "Payments, invoices, subscriptions"},
    "bug": {"label": "Bug Report", "emoji": "🐛", "prefix": "bug", "desc": "Report a bug or technical issue"},
    "partnership": {"label": "Partnership / Collab", "emoji": "🤝", "prefix": "partner", "desc": "Business or collaboration"},
    "other": {"label": "Other", "emoji": "📌", "prefix": "other", "desc": "Anything else"}
}

# ========================= DATABASE =========================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS configs (
                    guild_id INTEGER PRIMARY KEY,
                    ticket_category_id INTEGER,
                    log_channel_id INTEGER,
                    support_role_id INTEGER
                 )''')
    conn.commit()
    conn.close()

def save_config(guild_id, category=None, log=None, staff=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO configs 
                 (guild_id, ticket_category_id, log_channel_id, support_role_id)
                 VALUES (?, COALESCE(?, (SELECT ticket_category_id FROM configs WHERE guild_id=?)),
                           COALESCE(?, (SELECT log_channel_id FROM configs WHERE guild_id=?)),
                           COALESCE(?, (SELECT support_role_id FROM configs WHERE guild_id=?)))''',
              (guild_id, category, guild_id, log, guild_id, staff, guild_id))
    conn.commit()
    conn.close()
    # update cache
    load_config(guild_id)

def load_config(guild_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT ticket_category_id, log_channel_id, support_role_id FROM configs WHERE guild_id=?", (guild_id,))
    row = c.fetchone()
    conn.close()
    if row:
        guild_configs[guild_id] = {
            "category": row[0],
            "log": row[1],
            "staff": row[2]
        }
    else:
        guild_configs[guild_id] = {"category": None, "log": None, "staff": None}

def get_config(guild_id):
    if guild_id not in guild_configs:
        load_config(guild_id)
    return guild_configs[guild_id]

# ========================= VIEWS =========================
class TicketSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=data["label"], emoji=data["emoji"], value=key, description=data["desc"])
            for key, data in TICKET_TYPES.items()
        ]
        super().__init__(placeholder="Select the reason for your ticket...", min_values=1, max_values=1, options=options, custom_id="ticket_select")

    async def callback(self, interaction: discord.Interaction):
        config = get_config(interaction.guild_id)
        if not config or not config["category"]:
            return await interaction.response.send_message("❌ Ticket system not configured yet!\nUse `/tchannel` first.", ephemeral=True)

        ticket_type = self.values[0]
        data = TICKET_TYPES[ticket_type]

        guild = interaction.guild
        user = interaction.user
        category = guild.get_channel(config["category"])

        # Prevent duplicate tickets of same type
        for ch in category.channels:
            if ch.name.startswith(f"{data['prefix']}-{user.id}"):
                return await interaction.response.send_message("❌ You already have an open ticket of this type!", ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True),
        }
        if config["staff"]:
            support_role = guild.get_role(config["staff"])
            if support_role:
                overwrites[support_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True)

        channel = await category.create_text_channel(
            name=f"{data['prefix']}-{user.id}",
            overwrites=overwrites,
            topic=f"{data['label']} ticket • {user} (ID: {user.id})"
        )

        embed = discord.Embed(
            title=f"{data['emoji']} {data['label']} Ticket",
            description=f"{user.mention} Welcome!\n\n"
                        f"You selected **{data['label'].lower()}**.\n"
                        f"Our team will be with you shortly.\n\n"
                        f"Please describe your issue below 👇",
            color=discord.Color.blurple()
        )

        await channel.send(embed=embed, view=TicketCloseView())

        await interaction.response.send_message(f"✅ **{data['label']}** ticket created → {channel.mention}", ephemeral=True)

class TicketSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, emoji="🔒", custom_id="close_ticket")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

        config = get_config(interaction.guild_id)
        messages = []
        async for msg in interaction.channel.history(limit=1000):
            time = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            messages.append(f"[{time}] {msg.author}: {msg.content}")
        transcript = "\n".join(reversed(messages))

        if config and config["log"]:
            log_channel = interaction.guild.get_channel(config["log"])
            if log_channel:
                await log_channel.send(f"**Ticket Closed** • {interaction.channel.name}\nClosed by: {interaction.user.mention}")
                if len(transcript) > 1990:
                    await log_channel.send(file=discord.File(
                        fp=discord.utils.BytesIO(transcript.encode("utf-8")),
                        filename=f"transcript-{interaction.channel.name}.txt"
                    ))
                else:
                    await log_channel.send(f"```Transcript:\n{transcript}```")

        await interaction.channel.delete()

# ========================= COMMANDS =========================
@bot.tree.command(name="tchannel", description="Set the category where tickets will be created")
@commands.has_permissions(administrator=True)
async def tchannel(interaction: discord.Interaction, category: discord.CategoryChannel):
    save_config(interaction.guild_id, category=category.id)
    await interaction.response.send_message(f"✅ Ticket category set to **{category.name}**", ephemeral=True)

@bot.tree.command(name="tlogs", description="Set the channel where ticket transcripts will be sent")
@commands.has_permissions(administrator=True)
async def tlogs(interaction: discord.Interaction, channel: discord.TextChannel):
    save_config(interaction.guild_id, log=channel.id)
    await interaction.response.send_message(f"✅ Log channel set to **{channel.mention}**", ephemeral=True)

@bot.tree.command(name="setstaff", description="Set the support staff role that can see tickets")
@commands.has_permissions(administrator=True)
async def setstaff(interaction: discord.Interaction, role: discord.Role):
    save_config(interaction.guild_id, staff=role.id)
    await interaction.response.send_message(f"✅ Support role set to **{role.name}**", ephemeral=True)

@bot.tree.command(name="setup", description="Post the ticket creation panel")
@commands.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    config = get_config(interaction.guild_id)
    if not config or not config["category"]:
        await interaction.response.send_message("❌ Please configure the ticket system first:\n"
                                                "• `/tchannel` → ticket category\n"
                                                "• `/setstaff` → support role\n"
                                                "• (optional) `/tlogs` → logs channel", ephemeral=True)
        return

    embed = discord.Embed(
        title="🎟️ Support Tickets",
        description="Select the reason for your ticket using the dropdown below.",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, view=TicketSelectView())

@bot.event
async def on_ready():
    init_db()
    # Load all existing configs
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT guild_id FROM configs")
    for (guild_id,) in c.fetchall():
        load_config(guild_id)
    conn.close()

    bot.add_view(TicketSelectView())
    bot.add_view(TicketCloseView())
    await bot.tree.sync()
    print(f"✅ {bot.user} is online and ready! Use /tchannel, /tlogs, /setstaff to configure.")

bot.run(os.getenv("DISCORD_TOKEN"))
