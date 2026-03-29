import discord
from discord.ext import commands
import sqlite3
import os

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

DB_PATH = "/data/config.db"

# ========================= DATABASE =========================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS configs (
                    guild_id INTEGER PRIMARY KEY,
                    support_role_id INTEGER,
                    log_channel_id INTEGER,
                    panel_title TEXT DEFAULT "🎟️ Support Tickets",
                    panel_desc TEXT DEFAULT "Choose the type of ticket you need help with:",
                    panel_color INTEGER DEFAULT 5793266
                 )''')
    c.execute('''CREATE TABLE IF NOT EXISTS ticket_types (
                    guild_id INTEGER,
                    type_key TEXT,
                    label TEXT,
                    emoji TEXT,
                    welcome_title TEXT,
                    welcome_desc TEXT,
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
    c.execute("SELECT support_role_id, log_channel_id, panel_title, panel_desc, panel_color FROM configs WHERE guild_id=?", (guild_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "staff": row[0], "log": row[1],
            "panel_title": row[2], "panel_desc": row[3],
            "panel_color": discord.Color(row[4])
        }
    return {"staff": None, "log": None, "panel_title": "🎟️ Support Tickets", "panel_desc": "Choose the type of ticket you need help with:", "panel_color": discord.Color.blurple()}

def save_config(guild_id, staff=None, log=None, panel_title=None, panel_desc=None, panel_color=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO configs 
                 (guild_id, support_role_id, log_channel_id, panel_title, panel_desc, panel_color)
                 VALUES (?, COALESCE(?, (SELECT support_role_id FROM configs WHERE guild_id=?)),
                           COALESCE(?, (SELECT log_channel_id FROM configs WHERE guild_id=?)),
                           COALESCE(?, (SELECT panel_title FROM configs WHERE guild_id=?)),
                           COALESCE(?, (SELECT panel_desc FROM configs WHERE guild_id=?)),
                           COALESCE(?, (SELECT panel_color FROM configs WHERE guild_id=?)))''',
              (guild_id, staff, guild_id, log, guild_id, panel_title, guild_id, panel_desc, guild_id, panel_color.value if isinstance(panel_color, discord.Color) else panel_color, guild_id))
    conn.commit()
    conn.close()

def add_ticket_type(guild_id, type_key, label, emoji, welcome_title, welcome_desc, color, category_id, prefix):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO ticket_types 
                 (guild_id, type_key, label, emoji, welcome_title, welcome_desc, color, category_id, prefix)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (guild_id, type_key.lower(), label, emoji, welcome_title, welcome_desc, color.value if isinstance(color, discord.Color) else color, category_id, prefix.lower()))
    conn.commit()
    conn.close()

def get_ticket_types(guild_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT type_key, label, emoji, welcome_title, welcome_desc, color, category_id, prefix FROM ticket_types WHERE guild_id=?", (guild_id,))
    rows = c.fetchall()
    conn.close()
    types = {}
    for row in rows:
        types[row[0]] = {
            "label": row[1], "emoji": row[2],
            "welcome_title": row[3], "welcome_desc": row[4],
            "color": discord.Color(row[5]) if row[5] else discord.Color.blurple(),
            "category": row[6], "prefix": row[7]
        }
    return types

def remove_ticket_type(guild_id, type_key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM ticket_types WHERE guild_id=? AND type_key=?", (guild_id, type_key.lower()))
    conn.commit()
    conn.close()

# ========================= VIEWS =========================
class TicketSelect(discord.ui.Select):
    def __init__(self, ticket_types):
        options = [discord.SelectOption(label=data["label"], emoji=data["emoji"], value=key, description=data["welcome_desc"][:100]) for key, data in ticket_types.items()]
        super().__init__(placeholder="Select ticket type...", options=options, custom_id="ticket_select")

    async def callback(self, interaction: discord.Interaction):
        ticket_types = get_ticket_types(interaction.guild_id)
        data = ticket_types[self.values[0]]
        guild = interaction.guild
        user = interaction.user
        category = guild.get_channel(data["category"])
        if not category:
            return await interaction.response.send_message("❌ Category for this type no longer exists!", ephemeral=True)

        # Prevent duplicate
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
            topic=f"{data['welcome_title']} • {user}"
        )

        embed = discord.Embed(
            title=f"{data['emoji']} {data['welcome_title']}",
            description=f"{user.mention} {data['welcome_desc']}",
            color=data["color"]
        )

        await channel.send(embed=embed, view=TicketControlView(config["staff"], user.id))
        await interaction.response.send_message(f"✅ Ticket created → {channel.mention}", ephemeral=True)

class TicketSelectView(discord.ui.View):
    def __init__(self, ticket_types):
        super().__init__(timeout=None)
        self.add_item(TicketSelect(ticket_types))

class TicketControlView(discord.ui.View):
    def __init__(self, staff_role_id, creator_id):
        super().__init__(timeout=None)
        self.staff_role_id = staff_role_id
        self.creator_id = creator_id

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.primary, emoji="✅", custom_id="claim_ticket")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.staff_role_id and interaction.user.get_role(self.staff_role_id) is None:
            return await interaction.response.send_message("❌ Only support staff can claim tickets!", ephemeral=True)

        await interaction.response.defer()

        # Make ticket private to only creator + claimer
        overwrites = interaction.channel.overwrites
        guild = interaction.guild
        creator = guild.get_member(self.creator_id)

        # Remove broad staff role access
        if self.staff_role_id:
            role = guild.get_role(self.staff_role_id)
            if role and role in overwrites:
                overwrites[role].view_channel = False
                overwrites[role].read_messages = False

        # Give claimer access
        overwrites[interaction.user] = discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True)

        await interaction.channel.edit(overwrites=overwrites, name=f"✅{interaction.channel.name}")

        # Update embed
        embed = interaction.message.embeds[0]
        embed.description += f"\n\n✅ **Claimed by {interaction.user.mention}**"
        embed.color = discord.Color.green()

        # Disable claim button
        new_view = TicketControlView(self.staff_role_id, self.creator_id)
        new_view.claim.disabled = True
        new_view.claim.label = f"Claimed by {interaction.user.name}"

        await interaction.message.edit(embed=embed, view=new_view)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.red, emoji="🔒", custom_id="close_ticket")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        config = get_config(interaction.guild_id)
        messages = [f"[{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {msg.author}: {msg.content}" async for msg in interaction.channel.history(limit=1000)]
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
@bot.tree.command(name="setpanel", description="Customize the main /setup panel embed")
@commands.has_permissions(administrator=True)
async def setpanel(interaction: discord.Interaction, title: str, description: str, color: str = "blurple"):
    color_map = {"red": discord.Color.red(), "green": discord.Color.green(), "blue": discord.Color.blue(),
                 "yellow": discord.Color.yellow(), "purple": discord.Color.purple(), "blurple": discord.Color.blurple()}
    col = color_map.get(color.lower(), discord.Color.blurple())
    save_config(interaction.guild_id, panel_title=title, panel_desc=description, panel_color=col)
    await interaction.response.send_message(f"✅ Main panel embed updated!\n**Title:** {title}\n**Description:** {description}\n**Color:** {color}", ephemeral=True)

@bot.tree.command(name="addtickettype", description="Add/edit a ticket type + fully customize its welcome embed")
@commands.has_permissions(administrator=True)
async def addtickettype(
    interaction: discord.Interaction,
    type_key: str,
    label: str,
    emoji: str,
    category: discord.CategoryChannel,
    prefix: str,
    welcome_title: str,
    welcome_desc: str,
    color: str = "blurple"
):
    color_map = {"red": discord.Color.red(), "green": discord.Color.green(), "blue": discord.Color.blue(),
                 "yellow": discord.Color.yellow(), "purple": discord.Color.purple(), "blurple": discord.Color.blurple()}
    col = color_map.get(color.lower(), discord.Color.blurple())
    add_ticket_type(interaction.guild_id, type_key, label, emoji, welcome_title, welcome_desc, col, category.id, prefix)
    await interaction.response.send_message(f"✅ Ticket type **{label}** saved!\nWelcome title: {welcome_title}\nWelcome desc: {welcome_desc[:100]}...", ephemeral=True)

@bot.tree.command(name="removetickettype", description="Remove a ticket type")
@commands.has_permissions(administrator=True)
async def removetickettype(interaction: discord.Interaction, type_key: str):
    remove_ticket_type(interaction.guild_id, type_key)
    await interaction.response.send_message(f"✅ Removed `{type_key}`", ephemeral=True)

@bot.tree.command(name="listtickettypes", description="List all ticket types")
@commands.has_permissions(administrator=True)
async def listtickettypes(interaction: discord.Interaction):
    types = get_ticket_types(interaction.guild_id)
    if not types:
        return await interaction.response.send_message("No ticket types yet. Use `/addtickettype`", ephemeral=True)
    embed = discord.Embed(title="Your Ticket Types", color=discord.Color.blurple())
    for key, data in types.items():
        cat = interaction.guild.get_channel(data["category"])
        embed.add_field(name=f"{data['emoji']} {data['label']} (`{key}`)", value=f"Category: {cat.name if cat else 'Deleted'}\nPrefix: `{data['prefix']}`\nWelcome: {data['welcome_title']}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="tlogs", description="Set logs channel")
@commands.has_permissions(administrator=True)
async def tlogs(interaction: discord.Interaction, channel: discord.TextChannel):
    save_config(interaction.guild_id, log=channel.id)
    await interaction.response.send_message(f"✅ Logs → {channel.mention}", ephemeral=True)

@bot.tree.command(name="setstaff", description="Set support staff role")
@commands.has_permissions(administrator=True)
async def setstaff(interaction: discord.Interaction, role: discord.Role):
    save_config(interaction.guild_id, staff=role.id)
    await interaction.response.send_message(f"✅ Staff role → **{role.name}**", ephemeral=True)

@bot.tree.command(name="setup", description="Post the customizable ticket panel")
@commands.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    types = get_ticket_types(interaction.guild_id)
    if not types:
        return await interaction.response.send_message("❌ Add ticket types first with `/addtickettype`", ephemeral=True)
    config = get_config(interaction.guild_id)
    embed = discord.Embed(title=config["panel_title"], description=config["panel_desc"], color=config["panel_color"])
    await interaction.response.send_message(embed=embed, view=TicketSelectView(types))

@bot.event
async def on_ready():
    init_db()
    bot.add_view(TicketControlView(0, 0))  # persistent claim + close
    await bot.tree.sync()
    print(f"✅ {bot.user} is online and ready!")

bot.run(os.getenv("DISCORD_TOKEN"))
