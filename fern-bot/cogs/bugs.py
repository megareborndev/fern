import discord
from discord import app_commands
from discord.ext import commands
import config

GREEN = 0x2ecc71

class BugModal(discord.ui.Modal, title="Report a Bug"):
    title_input = discord.ui.TextInput(label="Bug Title", max_length=100)
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, max_length=2000)
    steps = discord.ui.TextInput(label="Steps to Reproduce", style=discord.TextStyle.paragraph, max_length=1000, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.client.get_channel(config.REVIEW_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("Review channel is not configured.", ephemeral=True)
            return

        embed = discord.Embed(title=f"🐛 Bug Report: {self.title_input.value}", color=GREEN)
        embed.add_field(name="Description", value=self.description.value, inline=False)
        if self.steps.value:
            embed.add_field(name="Steps to Reproduce", value=self.steps.value, inline=False)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        embed.set_footer(text=f"User ID: {interaction.user.id}")

        view = BugAdminView()
        await channel.send(embed=embed, view=view)
        await interaction.response.send_message("Bug report submitted. Thank you!", ephemeral=True)

class BugAdminView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Mark as Reviewed", style=discord.ButtonStyle.success, custom_id="bug_reviewed")
    async def reviewed(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_admin(interaction):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return
        embed = interaction.message.embeds[0]
        embed.title = f"✅ {embed.title}"
        embed.color = 0x1e8449
        button.disabled = True
        button.label = "Reviewed"
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="bug_delete")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_admin(interaction):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return
        await interaction.message.delete()

    def is_admin(self, interaction: discord.Interaction):
        if interaction.user.guild_permissions.administrator:
            return True
        if config.ADMIN_ROLE_ID:
            role = discord.utils.get(interaction.user.roles, id=config.ADMIN_ROLE_ID)
            if role:
                return True
        return False

class Bugs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="bug", description="Submit a bug report")
    async def bug(self, interaction: discord.Interaction):
        await interaction.response.send_modal(BugModal())

async def setup(bot):
    await bot.add_cog(Bugs(bot))