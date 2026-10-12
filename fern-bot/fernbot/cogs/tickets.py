import time
import discord
from discord import app_commands
from discord.ext import commands
import config

GREEN = 0x2ecc71
DARK_GREEN = 0x27ae60

class TicketModal(discord.ui.Modal, title="Open Support Ticket"):
    subject = discord.ui.TextInput(label="Subject", max_length=100)
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        category = guild.get_channel(config.TICKET_CATEGORY_ID)
        if not category or not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send("Ticket system is not configured properly.", ephemeral=True)
            return

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        if config.ADMIN_ROLE_ID:
            role = guild.get_role(config.ADMIN_ROLE_ID)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            category=category,
            overwrites=overwrites,
            topic=f"Ticket by {interaction.user.id}"
        )

        embed = discord.Embed(title=f"Ticket: {self.subject.value}", description=self.description.value, color=GREEN)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()

        view = TicketAdminView()
        await channel.send(f"{interaction.user.mention}", embed=embed, view=view)
        await interaction.followup.send(f"Ticket created: {channel.mention}", ephemeral=True)

class TicketAdminView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, custom_id="ticket_close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_admin(interaction):
            await interaction.response.send_message("Only admins can close tickets.", ephemeral=True)
            return
        await interaction.response.defer()
        await interaction.channel.edit(name=f"closed-{interaction.channel.name.replace('ticket-', '')}")
        await interaction.channel.send(embed=discord.Embed(description="This ticket has been closed.", color=DARK_GREEN))
        button.disabled = True
        for child in self.children:
            if child.custom_id == "ticket_delete":
                child.disabled = False
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="ticket_delete", disabled=True)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_admin(interaction):
            await interaction.response.send_message("Only admins can delete tickets.", ephemeral=True)
            return
        await interaction.response.defer()
        await interaction.channel.delete()

    def is_admin(self, interaction: discord.Interaction):
        if interaction.user.guild_permissions.administrator:
            return True
        if config.ADMIN_ROLE_ID:
            role = discord.utils.get(interaction.user.roles, id=config.ADMIN_ROLE_ID)
            if role:
                return True
        return False

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.success, custom_id="ticket_open", emoji="🎫")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Tickets")
        if interaction.user.id in cog.cooldowns:
            remaining = cog.cooldowns[interaction.user.id] - time.time()
            if remaining > 0:
                await interaction.response.send_message(f"Please wait {int(remaining)} seconds before opening another ticket.", ephemeral=True)
                return
        cog.cooldowns[interaction.user.id] = time.time() + config.TICKET_COOLDOWN
        await interaction.response.send_modal(TicketModal())

class Tickets(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cooldowns = {}

    @app_commands.command(name="ticket-panel", description="Post the ticket panel")
    @app_commands.checks.has_permissions(administrator=True)
    async def ticket_panel(self, interaction: discord.Interaction):
        embed = discord.Embed(title="Support Tickets", description="Click the button below to open a support ticket.", color=GREEN)
        await interaction.response.send_message(embed=embed, view=TicketPanelView())

    @app_commands.command(name="ticket-close", description="Close the current ticket channel")
    async def ticket_close_cmd(self, interaction: discord.Interaction):
        if not interaction.channel.name.startswith("ticket-"):
            await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
            return
        if not self.is_admin(interaction):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return
        await interaction.response.defer()
        await interaction.channel.edit(name=f"closed-{interaction.channel.name.replace('ticket-', '')}")
        await interaction.channel.send(embed=discord.Embed(description="This ticket has been closed.", color=DARK_GREEN))

    @app_commands.command(name="ticket-delete", description="Delete the current ticket channel")
    async def ticket_delete_cmd(self, interaction: discord.Interaction):
        if not interaction.channel.name.startswith(("ticket-", "closed-")):
            await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
            return
        if not self.is_admin(interaction):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return
        await interaction.response.defer()
        await interaction.channel.delete()

    def is_admin(self, interaction: discord.Interaction):
        if interaction.user.guild_permissions.administrator:
            return True
        if config.ADMIN_ROLE_ID:
            role = discord.utils.get(interaction.user.roles, id=config.ADMIN_ROLE_ID)
            if role:
                return True
        return False

async def setup(bot):
    await bot.add_cog(Tickets(bot))