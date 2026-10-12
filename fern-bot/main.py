import discord
from discord.ext import commands
import config

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.load_extension("cogs.api")
        await self.load_extension("cogs.tickets")
        await self.load_extension("cogs.suggestions")
        await self.load_extension("cogs.bugs")

        from cogs.tickets import TicketPanelView, TicketAdminView
        from cogs.suggestions import SuggestionVoteView
        from cogs.bugs import BugAdminView

        self.add_view(TicketPanelView())
        self.add_view(TicketAdminView())
        self.add_view(SuggestionVoteView())
        self.add_view(BugAdminView())

        guild = discord.Object(id=config.GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)

    async def on_ready(self):
        print(f"Ready as {self.user}")

bot = Bot()
bot.run(config.BOT_TOKEN)