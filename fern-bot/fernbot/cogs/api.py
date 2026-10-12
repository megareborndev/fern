import json
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
import config

GREEN = 0x2ecc71

class API(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.base = config.API_BASE_URL
        self._load_key()

    def _load_key(self):
        try:
            with open(config.API_KEY_FILE) as f:
                self.key = json.load(f)["key"]
        except Exception:
            self.key = ""

    def _headers(self):
        return {"X-Admin-Key": self.key}

    async def cog_unload(self):
        await self.session.close()

    def admin_check(self, interaction: discord.Interaction):
        if interaction.user.guild_permissions.administrator:
            return True
        if config.ADMIN_ROLE_ID:
            role = discord.utils.get(interaction.user.roles, id=config.ADMIN_ROLE_ID)
            if role:
                return True
        return False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not self.admin_check(interaction):
            await interaction.response.send_message("Admin access required.", ephemeral=True)
            return False
        return True

    @app_commands.command(name="admin-data", description="Fetch admin data from the API")
    async def admin_data(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.session.get(f"{self.base}/api/admin/data", headers=self._headers()) as resp:
                if resp.status == 503:
                    await interaction.followup.send("License server is unavailable.", ephemeral=True)
                    return
                data = await resp.json()
                embed = discord.Embed(title="Admin Data", color=GREEN)
                embed.description = f"```json\n{json.dumps(data, indent=2)[:4000]}\n```"
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Request failed: {e}", ephemeral=True)

    @app_commands.command(name="admin-status", description="View current status config")
    async def admin_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.session.get(f"{self.base}/api/admin/status", headers=self._headers()) as resp:
                data = await resp.json()
                embed = discord.Embed(title="System Status", color=GREEN)
                embed.description = f"```json\n{json.dumps(data, indent=2)[:4000]}\n```"
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Request failed: {e}", ephemeral=True)

    @app_commands.command(name="admin-status-set", description="Update status config")
    @app_commands.describe(
        banner_text="Banner text",
        banner_level="Banner level (info/warn/critical)",
        service="Service name (api/ui/discord)",
        override="Override status (up/degraded/down/clear)"
    )
    async def admin_status_set(self, interaction: discord.Interaction, banner_text: str = None, banner_level: str = "info", service: str = None, override: str = None):
        await interaction.response.defer(ephemeral=True)
        payload = {}
        if banner_text:
            payload["banner"] = {"text": banner_text, "level": banner_level}
        if service and override:
            override_val = "" if override.lower() == "clear" else override.lower()
            payload["services"] = {service.lower(): {"override": override_val, "note": ""}}
        try:
            async with self.session.post(f"{self.base}/api/admin/status", headers=self._headers(), json=payload) as resp:
                data = await resp.json()
                embed = discord.Embed(title="Status Updated", color=GREEN)
                embed.description = f"```json\n{json.dumps(data, indent=2)[:4000]}\n```"
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Request failed: {e}", ephemeral=True)

    @app_commands.command(name="admin-finance", description="View finance data")
    async def admin_finance(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.session.get(f"{self.base}/api/admin/finance", headers=self._headers()) as resp:
                data = await resp.json()
                embed = discord.Embed(title="Finance Overview", color=GREEN)
                cfg = data.get("config", {})
                embed.add_field(name="Monthly Price", value=f"${cfg.get('monthly_price', 0)}", inline=True)
                embed.add_field(name="Lifetime Price", value=f"${cfg.get('lifetime_price', 0)}", inline=True)
                embed.add_field(name="Expenses %", value=f"{cfg.get('expenses_pct', 0)}%", inline=True)
                weeks = data.get("weeks", [])
                if weeks:
                    latest = weeks[-1]
                    embed.add_field(name="Latest Week", value=latest.get("week", "N/A"), inline=False)
                    embed.add_field(name="Gross", value=f"${latest.get('gross', 0)}", inline=True)
                    embed.add_field(name="Net", value=f"${latest.get('net', 0)}", inline=True)
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Request failed: {e}", ephemeral=True)

    @app_commands.command(name="admin-finance-set", description="Update finance config")
    @app_commands.describe(monthly_price="Monthly price", lifetime_price="Lifetime price", expenses_pct="Expenses percentage")
    async def admin_finance_set(self, interaction: discord.Interaction, monthly_price: float = None, lifetime_price: float = None, expenses_pct: float = None):
        await interaction.response.defer(ephemeral=True)
        payload = {}
        if monthly_price is not None:
            payload["monthly_price"] = monthly_price
        if lifetime_price is not None:
            payload["lifetime_price"] = lifetime_price
        if expenses_pct is not None:
            payload["expenses_pct"] = expenses_pct
        if not payload:
            await interaction.followup.send("No values provided to update.", ephemeral=True)
            return
        try:
            async with self.session.post(f"{self.base}/api/admin/finance", headers=self._headers(), json=payload) as resp:
                data = await resp.json()
                embed = discord.Embed(title="Finance Updated", color=GREEN)
                embed.description = f"```json\n{json.dumps(data, indent=2)[:4000]}\n```"
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Request failed: {e}", ephemeral=True)

    @app_commands.command(name="admin-accounts", description="List licensed accounts")
    async def admin_accounts(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.session.get(f"{self.base}/api/admin/accounts", headers=self._headers()) as resp:
                data = await resp.json()
                accounts = data.get("accounts", [])
                embed = discord.Embed(title=f"Accounts ({data.get('count', len(accounts))})", color=GREEN)
                for acc in accounts[:5]:
                    uid = acc.get("user_id", "Unknown")
                    licenses = acc.get("licenses", [])
                    types = ", ".join([l.get("type", "unknown") for l in licenses]) or "none"
                    embed.add_field(name=f"User {uid}", value=f"Licenses: {types}", inline=False)
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Request failed: {e}", ephemeral=True)

    @app_commands.command(name="admin-license", description="Generate a new license key")
    @app_commands.describe(days="Days until expiry (0 or omit for lifetime)")
    async def admin_license(self, interaction: discord.Interaction, days: int = 0):
        await interaction.response.defer(ephemeral=True)
        try:
            payload = {"days": days} if days > 0 else {}
            async with self.session.post(f"{self.base}/api/admin/license", headers=self._headers(), json=payload) as resp:
                data = await resp.json()
                embed = discord.Embed(title="License Generated", color=GREEN)
                embed.description = f"```json\n{json.dumps(data, indent=2)[:4000]}\n```"
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Request failed: {e}", ephemeral=True)

    @app_commands.command(name="admin-changelog-add", description="Add a changelog entry")
    @app_commands.describe(title="Entry title", body="Entry body", version="Version string")
    async def admin_changelog_add(self, interaction: discord.Interaction, title: str, body: str, version: str = None):
        await interaction.response.defer(ephemeral=True)
        try:
            payload = {"title": title, "body": body}
            if version:
                payload["version"] = version
            async with self.session.post(f"{self.base}/api/admin/changelog", headers=self._headers(), json=payload) as resp:
                data = await resp.json()
                embed = discord.Embed(title="Changelog Added", color=GREEN)
                embed.description = f"```json\n{json.dumps(data, indent=2)[:4000]}\n```"
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Request failed: {e}", ephemeral=True)

    @app_commands.command(name="admin-changelog-delete", description="Delete a changelog entry")
    @app_commands.describe(entry_id="The ID of the entry to delete")
    async def admin_changelog_delete(self, interaction: discord.Interaction, entry_id: str):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.session.delete(f"{self.base}/api/admin/changelog/{entry_id}", headers=self._headers()) as resp:
                data = await resp.json()
                embed = discord.Embed(title="Changelog Deleted", color=GREEN)
                embed.description = f"```json\n{json.dumps(data, indent=2)[:4000]}\n```"
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Request failed: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(API(bot))