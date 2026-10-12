import discord
from discord import app_commands
from discord.ext import commands
import config

GREEN = 0x2ecc71

class SuggestModal(discord.ui.Modal, title="Submit Suggestion"):
    title_input = discord.ui.TextInput(label="Title", max_length=100)
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, max_length=2000)

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.client.get_channel(config.REVIEW_CHANNEL_ID)
        if not channel:
            await interaction.response.send_message("Review channel is not configured.", ephemeral=True)
            return

        embed = discord.Embed(title=f"💡 Suggestion: {self.title_input.value}", description=self.description.value, color=GREEN)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        embed.set_footer(text=f"User ID: {interaction.user.id}")

        view = SuggestionVoteView()
        msg = await channel.send(embed=embed, view=view)

        cog = interaction.client.get_cog("Suggestions")
        cog.votes[msg.id] = {}

        await interaction.response.send_message("Suggestion submitted for review.", ephemeral=True)

class SuggestionVoteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="0", style=discord.ButtonStyle.success, custom_id="suggest_up", emoji="👍")
    async def upvote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_vote(interaction, 1)

    @discord.ui.button(label="0", style=discord.ButtonStyle.danger, custom_id="suggest_down", emoji="👎")
    async def downvote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_vote(interaction, -1)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="suggest_delete")
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.is_admin(interaction):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return
        await interaction.message.delete()

    async def handle_vote(self, interaction: discord.Interaction, value: int):
        cog = interaction.client.get_cog("Suggestions")
        msg_id = interaction.message.id
        user_id = interaction.user.id

        if msg_id not in cog.votes:
            cog.votes[msg_id] = {}

        current = cog.votes[msg_id].get(user_id, 0)
        if current == value:
            cog.votes[msg_id][user_id] = 0
        else:
            cog.votes[msg_id][user_id] = value

        up = sum(1 for v in cog.votes[msg_id].values() if v == 1)
        down = sum(1 for v in cog.votes[msg_id].values() if v == -1)
        score = sum(cog.votes[msg_id].values())

        for child in self.children:
            if child.custom_id == "suggest_up":
                child.label = str(up)
            elif child.custom_id == "suggest_down":
                child.label = str(down)

        embed = interaction.message.embeds[0]
        if embed.fields and embed.fields[0].name == "Score":
            embed.set_field_at(0, name="Score", value=str(score), inline=True)
        else:
            embed.insert_field_at(0, name="Score", value=str(score), inline=True)

        await interaction.response.edit_message(embed=embed, view=self)

    def is_admin(self, interaction: discord.Interaction):
        if interaction.user.guild_permissions.administrator:
            return True
        if config.ADMIN_ROLE_ID:
            role = discord.utils.get(interaction.user.roles, id=config.ADMIN_ROLE_ID)
            if role:
                return True
        return False

class Suggestions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.votes = {}

    @app_commands.command(name="suggest", description="Submit a suggestion")
    async def suggest(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SuggestModal())

async def setup(bot):
    await bot.add_cog(Suggestions(bot))