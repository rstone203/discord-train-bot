"""Comprehensive report commands - removed."""

import discord
from discord.ext import commands


class ComprehensiveReportCommands(commands.Cog):
    """Placeholder - comprehensive attendance reports have been removed."""
    
    def __init__(self, bot):
        self.bot = bot


async def setup(bot):
    await bot.add_cog(ComprehensiveReportCommands(bot))
