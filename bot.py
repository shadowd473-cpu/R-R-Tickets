import discord
from discord.ext import commands
import sqlite3
import os
import json

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

DB_PATH = "/data/config.db"

# ========================= DATABASE =========================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Global config
    c.execute('''CREATE TABLE IF NOT EXISTS configs (
                    guild_id INTEGER PRIMARY KEY,
                    support_role_id INTEGER,
                    log_channel_id INTEGER
                 )''')
    
    # Ticket types (fully customizable)
    c.execute('''CREATE TABLE IF NOT EXISTS ticket_types (
                    guild_id INTEGER,
                    type_key TEXT,
                    label TEXT,
                    emoji TEXT,
                    description TEXT,
                    color INTEGER,
                    category_id INTEGER,
                    prefix TEXT,
                    PRIMARY KEY (guild_id, type_key)
                 )''')
    conn.commit()
    conn.close()

def get_config(guild_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT support_role_id, log_channel_id FROM configs WHERE guild_id=?", (guild_id,))
    row = c.fetchone()
    conn.close()
    return {"staff": row[0] if row else None, "log": row[1] if row else None}

def save_config(guild_id, staff=None, log=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO configs (guild_id, support_role_id, log_channel_id)
                 VALUES (?, COALESCE(?, (SELECT support_role_id FROM configs WHERE guild_id=?)),
                           COALESCE(?, (SELECT log_channel_id FROM configs WHERE guild_id=?)))''',
              (guild_id, staff, guild_id, log, guild_id))
    conn.commit()
    conn.close()

def add_ticket_type(guild_id, type_key, label, emoji, description, color, category_id, prefix):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO ticket_types 
                 (guild_id, type_key, label, emoji, description, color, category_id, prefix)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
              (guild_id, type_key, label, emoji, description, color.value if isinstance(color, discord.Color) else color, category_id, prefix))
    conn.commit()
    conn.close()

def get_ticket_types(guild_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT type_key, label, emoji, description, color, category_id, prefix FROM ticket_types WHERE guild_id=?", (guild_id,))
    rows = c.fetchall()
    conn.close()
    types = {}
    for row in rows:
        types[row[0]] = {
            "label": row[1],
            "emoji": row[2],
            "desc": row[3],
            "color": discord.Color(row[4]) if row[4] else discord.Color.blurple(),
            "category": row[5],
            "prefix": row[6]
        }
    return types

def remove_ticket_type(guild_id, type_key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM ticket_types WHERE guild_id=? AND type_key=?", (guild_id, type_key))
    conn.commit()
    conn.close()

# ========================= VIEWS =========================
class TicketSelect(discord.ui.Select):
    def __init__(self, ticket_types):
        options = [
            discord.SelectOption(label=data["label"], emoji=data["emoji"], value=key, description=data["desc"][:100])
            for key, data in ticket_types.items()
        ]
        super().__init__(placeholder="Select ticket type...", min_values=1, max_values=1, options=options, custom_id="ticket_select")

    async def callback(self, interaction: discord.Interaction):
        ticket_types = get_ticket_types(interaction.guild_id)
        if not ticket_types:
            return await interaction.response.send_message("No ticket types configured!", ephemeral=True)

        data = ticket_types[self.values[0]]
        guild = interaction.guild
        user = interaction.user
        category = guild.get_channel(data["category"])

        if not category:
            return await interaction.response.send_message("❌ The category for this ticket type no longer exists!", ephemeral=True)

        # Prevent duplicates of same type
        for ch in category.channels:
            if ch.name.startswith(f"{data['prefix']}-{user.id}"):
                return await interaction.response.send_message("❌ You already have an open ticket of this type!", ephemeral=True)

        config = get_config(guild.id)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True),
        }
        if config["staff"]:
            role = guild.get_role(config["staff"])
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True)

        channel = await category.create_text_channel(
            name=f"{data['prefix']}-{user.id}",
            overwrites=overwrites,
            topic=f"{data['label']} • {user}"
        )

        embed = discord.Embed(
            title=f"{data['emoji']} {data['label']}",
            description=f"{user.mention} {data['desc']}\n\nPlease describe your issue below.",
            color=data["color"]
        )

        await channel.send(embed=embed, view=TicketCloseView())

        await interaction.response.send_message(f"✅ Ticket created → {channel.mention}", ephemeral=True)

class TicketSelectView(discord.ui.View):
    def __init__(self, ticket_types):
        super().__init__(timeout=None)
        self.add_item(TicketSelect(ticket_types))

class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, emoji="🔒", custom_id="close_ticket")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        config = get_config(interaction.guild_id)
        messages = [f"[{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {msg.author}: {msg.content}" 
                    async for msg in interaction.channel.history(limit=1000)]
        transcript = "\n".join(reversed(messages))

        if config["log"]:
            log_ch = interaction.guild.get_channel(config["log"])
            if log_ch:
                await log_ch.send(f"**Ticket Closed** • {interaction.channel.name}\nClosed by: {interaction.user.mention}")
                if len(transcript) > 1990:
                    await log_ch.send(file=discord.File(fp=discord.utils.BytesIO(transcript.encode()), filename=f"transcript-{interaction.channel.name}.txt"))
                else:
                    await log_ch.send(f"```Transcript:\n{transcript}```")

        await interaction.channel.delete()

# ========================= COMMANDS =========================
@bot.tree.command(name="addtickettype", description="Add or edit a custom ticket type")
@commands.has_permissions(administrator=True)
async def addtickettype(
    interaction: discord.Interaction,
    type_key: str,                    # short identifier e.g. "billing"
    label: str,                       # displayed name
    emoji: str,                       # emoji for dropdown
    category: discord.CategoryChannel,
    prefix: str,                      # channel name prefix e.g. "bill"
    description: str = "Please describe your issue.",
    color: str = "blurple"            # blurple, red, green, blue, etc.
):
    color_map = {
        "red": discord.Color.red(), "green": discord.Color.green(),
        "blue": discord.Color.blue(), "yellow": discord.Color.yellow(),
        "purple": discord.Color.purple(), "blurple": discord.Color.blurple()
    }
    col = color_map.get(color.lower(), discord.Color.blurple())

    add_ticket_type(interaction.guild_id, type_key.lower(), label, emoji, description, col, category.id, prefix.lower())
    await interaction.response.send_message(f"✅ Ticket type **{label}** added/updated!\nCategory: {category.name}\nPrefix: `{prefix}`", ephemeral=True)

@bot.tree.command(name="removetickettype", description="Remove a ticket type")
@commands.has_permissions(administrator=True)
async def removetickettype(interaction: discord.Interaction, type_key: str):
    remove_ticket_type(interaction.guild_id, type_key.lower())
    await interaction.response.send_message(f"✅ Removed ticket type `{type_key}`", ephemeral=True)

@bot.tree.command(name="listtickettypes", description="List all configured ticket types")
@commands.has_permissions(administrator=True)
async def listtickettypes(interaction: discord.Interaction):
    types = get_ticket_types(interaction.guild_id)
    if not types:
        return await interaction.response.send_message("No ticket types configured yet. Use `/addtickettype`", ephemeral=True)

    embed = discord.Embed(title="Current Ticket Types", color=discord.Color.blurple())
    for key, data in types.items():
        cat = interaction.guild.get_channel(data["category"])
        embed.add_field(
            name=f"{data['emoji']} {data['label']} (`{key}`)",
            value=f"Category: {cat.name if cat else 'Deleted'}\nPrefix: `{data['prefix']}`\nDesc: {data['desc'][:100]}...",
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="tlogs", description="Set ticket logs channel")
@commands.has_permissions(administrator=True)
async def tlogs(interaction: discord.Interaction, channel: discord.TextChannel):
    save_config(interaction.guild_id, log=channel.id)
    await interaction.response.send_message(f"✅ Logs set to {channel.mention}", ephemeral=True)

@bot.tree.command(name="setstaff", description="Set support staff role")
@commands.has_permissions(administrator=True)
async def setstaff(interaction: discord.Interaction, role: discord.Role):
    save_config(interaction.guild_id, staff=role.id)
    await interaction.response.send_message(f"✅ Staff role set to **{role.name}**", ephemeral=True)

@bot.tree.command(name="setup", description="Post the customizable ticket panel")
@commands.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    types = get_ticket_types(interaction.guild_id)
    if not types:
        await interaction.response.send_message("❌ No ticket types configured! Use `/addtickettype` first.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🎟️ Support Tickets",
        description="Choose the type of ticket you need help with:",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, view=TicketSelectView(types))

@bot.event
async def on_ready():
    init_db()
    bot.add_view(TicketCloseView())  # persistent close button
    await bot.tree.sync()
    print(f"✅ {bot.user} is online and ready!")

bot.run(os.getenv("DISCORD_TOKEN"))
