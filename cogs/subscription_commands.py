#!/usr/bin/env python3
"""
Subscription System Commands (Slash Commands)

Bot-owner controlled monetization system for organizers. Disabled by default
(SubscriptionSettings.subscription_required = False); enabling it does not
affect any server until the owner explicitly runs /togglesubscriptions enable.

Trial/subscription status is tracked globally per-user (UserSubscription),
since organizers may run trains across multiple servers. Individual servers
can be granted free access regardless of subscription state via
TrustedServerAccess (managed separately by the bot owner).
"""

import logging
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from database import DatabaseSession
from models import SubscriptionSettings, UserSubscription, TrustedServerAccess
from utils.slash_permissions import is_bot_owner

logger = logging.getLogger('discord_bot.subscription_commands')


def get_or_create_settings(session) -> SubscriptionSettings:
    """Fetch the single SubscriptionSettings row, creating it with safe defaults if missing."""
    settings = session.query(SubscriptionSettings).first()
    if not settings:
        settings = SubscriptionSettings(subscription_required=False)
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


def guild_has_trusted_access(session, guild_id: int) -> bool:
    access = session.query(TrustedServerAccess).filter_by(guild_id=guild_id, enabled=True).first()
    return access is not None


async def user_has_active_access(user_id: int, guild_id: int = None) -> bool:
    """Return True if the user (or their server) currently has access to organizer features.

    Access is granted if:
    - The subscription system is not required (global off-switch), OR
    - The guild has explicit trusted/free access, OR
    - The user has an active paid subscription, OR
    - The user is within their free trial window.
    """
    with DatabaseSession() as session:
        settings = get_or_create_settings(session)
        if not settings.subscription_required:
            return True

        if guild_id and guild_has_trusted_access(session, guild_id):
            return True

        sub = session.query(UserSubscription).filter_by(user_id=user_id).first()
        if not sub:
            return False

        if sub.is_active:
            return True

        if sub.is_trial and sub.trial_end and sub.trial_end > datetime.utcnow():
            return True

        return False


class SubscriptionCommands(commands.Cog):
    """Slash commands for the subscription/monetization system."""

    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.subscription_commands')

    @app_commands.command(
        name='subscriptioninfo',
        description='View pricing and free trial info for organizer access'
    )
    async def subscription_info(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            with DatabaseSession() as session:
                settings = get_or_create_settings(session)

            embed = discord.Embed(
                title="💳 Organizer Subscription",
                color=discord.Color.blurple()
            )

            if not settings.subscription_required:
                embed.description = (
                    "Subscriptions are **not currently required** — all organizer "
                    "features are free to use right now."
                )
            else:
                price = settings.subscription_price_monthly / 100
                embed.description = (
                    f"**${price:.2f}/month** for full organizer access "
                    f"(train scheduling, Twitch integration, forwarding, and more)."
                )
                embed.add_field(
                    name="Free Trial",
                    value=f"{settings.free_trial_days} days, no card required to start",
                    inline=False
                )
                embed.add_field(
                    name="Get Started",
                    value="Use `/subscribe` to start your free trial or subscribe.",
                    inline=False
                )

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            self.logger.error(f"Error in subscription_info: {e}", exc_info=True)
            await interaction.followup.send("❌ Could not load subscription info.", ephemeral=True)

    @app_commands.command(
        name='subscriptionstatus',
        description='Check your current subscription/trial status'
    )
    async def subscription_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            with DatabaseSession() as session:
                settings = get_or_create_settings(session)
                sub = session.query(UserSubscription).filter_by(user_id=interaction.user.id).first()

                guild_trusted = False
                if interaction.guild:
                    guild_trusted = guild_has_trusted_access(session, interaction.guild.id)

            embed = discord.Embed(title="📋 Your Subscription Status", color=discord.Color.blurple())

            if not settings.subscription_required:
                embed.description = "✅ Subscriptions are not required right now — you have full access."
            elif guild_trusted:
                embed.description = "✅ This server has free trusted access, granted by the bot owner."
            elif not sub:
                embed.description = "You have no active subscription or trial. Use `/subscribe` to get started."
            elif sub.is_active:
                embed.description = "✅ You have an **active subscription**."
                if sub.current_period_end:
                    embed.add_field(name="Renews", value=sub.current_period_end.strftime('%Y-%m-%d'), inline=False)
            elif sub.is_trial and sub.trial_end and sub.trial_end > datetime.utcnow():
                days_left = (sub.trial_end - datetime.utcnow()).days
                embed.description = f"🕒 You're on a **free trial** — {days_left} day(s) remaining."
                embed.add_field(name="Trial Ends", value=sub.trial_end.strftime('%Y-%m-%d'), inline=False)
            else:
                embed.description = "❌ Your trial/subscription has expired. Use `/subscribe` to reactivate."

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            self.logger.error(f"Error in subscription_status: {e}", exc_info=True)
            await interaction.followup.send("❌ Could not load your subscription status.", ephemeral=True)

    @app_commands.command(
        name='subscribe',
        description='Start your free trial or get a subscription link'
    )
    async def subscribe(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            with DatabaseSession() as session:
                settings = get_or_create_settings(session)

                if not settings.subscription_required:
                    await interaction.followup.send(
                        "✅ Subscriptions aren't required right now — you already have full access!",
                        ephemeral=True
                    )
                    return

                sub = session.query(UserSubscription).filter_by(user_id=interaction.user.id).first()

                if sub and (sub.is_active or (sub.is_trial and sub.trial_end and sub.trial_end > datetime.utcnow())):
                    await interaction.followup.send(
                        "You already have active access — check `/subscriptionstatus` for details.",
                        ephemeral=True
                    )
                    return

                if sub and sub.is_trial:
                    # Trial already used previously; direct to paid checkout instead of a new trial
                    await interaction.followup.send(
                        "Your free trial has already been used. Payment checkout isn't set up yet — "
                        "please contact the bot owner to subscribe.",
                        ephemeral=True
                    )
                    return

                trial_end = datetime.utcnow() + timedelta(days=settings.free_trial_days)
                if sub:
                    sub.is_trial = True
                    sub.trial_start = datetime.utcnow()
                    sub.trial_end = trial_end
                    sub.trial_first_used_guild_id = interaction.guild.id if interaction.guild else None
                else:
                    sub = UserSubscription(
                        user_id=interaction.user.id,
                        username=str(interaction.user),
                        is_trial=True,
                        trial_start=datetime.utcnow(),
                        trial_end=trial_end,
                        trial_first_used_guild_id=interaction.guild.id if interaction.guild else None
                    )
                    session.add(sub)
                session.commit()

            await interaction.followup.send(
                f"🎉 Your free {settings.free_trial_days}-day trial has started! "
                f"It ends on **{trial_end.strftime('%Y-%m-%d')}**.",
                ephemeral=True
            )
        except Exception as e:
            self.logger.error(f"Error in subscribe: {e}", exc_info=True)
            await interaction.followup.send("❌ Could not start your trial. Please try again later.", ephemeral=True)

    @app_commands.command(
        name='togglesubscriptions',
        description='[Owner Only] Enable or disable the subscription requirement bot-wide'
    )
    @app_commands.describe(state="enable or disable")
    @app_commands.choices(state=[
        app_commands.Choice(name='enable', value='enable'),
        app_commands.Choice(name='disable', value='disable'),
    ])
    async def toggle_subscriptions(self, interaction: discord.Interaction, state: app_commands.Choice[str]):
        if not await is_bot_owner(interaction):
            await interaction.response.send_message("❌ Only the bot owner can use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            enable = state.value == 'enable'
            with DatabaseSession() as session:
                settings = get_or_create_settings(session)
                settings.subscription_required = enable
                settings.updated_by = interaction.user.id
                settings.updated_at = datetime.utcnow()
                if enable:
                    settings.enabled_at = datetime.utcnow()
                session.commit()

            status = "ENABLED ✅" if enable else "DISABLED ⛔"
            await interaction.followup.send(
                f"Subscription requirement is now **{status}**.",
                ephemeral=True
            )
            self.logger.info(f"Subscription requirement set to {enable} by {interaction.user} ({interaction.user.id})")
        except Exception as e:
            self.logger.error(f"Error in toggle_subscriptions: {e}", exc_info=True)
            await interaction.followup.send("❌ Could not update subscription settings.", ephemeral=True)

    @app_commands.command(
        name='substats',
        description='[Owner Only] View subscription system stats'
    )
    async def sub_stats(self, interaction: discord.Interaction):
        if not await is_bot_owner(interaction):
            await interaction.response.send_message("❌ Only the bot owner can use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with DatabaseSession() as session:
                settings = get_or_create_settings(session)
                total_subs = session.query(UserSubscription).count()
                active_paid = session.query(UserSubscription).filter_by(is_active=True).count()
                active_trials = session.query(UserSubscription).filter(
                    UserSubscription.is_trial == True,
                    UserSubscription.trial_end > datetime.utcnow()
                ).count()
                trusted_servers = session.query(TrustedServerAccess).filter_by(enabled=True).count()

            embed = discord.Embed(title="📊 Subscription System Stats", color=discord.Color.gold())
            embed.add_field(
                name="System Status",
                value="ENABLED ✅" if settings.subscription_required else "DISABLED ⛔",
                inline=False
            )
            embed.add_field(name="Total Users Tracked", value=str(total_subs), inline=True)
            embed.add_field(name="Active Paid Subscribers", value=str(active_paid), inline=True)
            embed.add_field(name="Active Trials", value=str(active_trials), inline=True)
            embed.add_field(name="Trusted Free Servers", value=str(trusted_servers), inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            self.logger.error(f"Error in sub_stats: {e}", exc_info=True)
            await interaction.followup.send("❌ Could not load subscription stats.", ephemeral=True)

    @app_commands.command(
        name='grantserveraccess',
        description='[Owner Only] Grant a server free organizer access regardless of subscription status'
    )
    @app_commands.describe(revoke="Set to True to revoke instead of grant")
    async def grant_server_access(self, interaction: discord.Interaction, revoke: bool = False):
        if not await is_bot_owner(interaction):
            await interaction.response.send_message("❌ Only the bot owner can use this command.", ephemeral=True)
            return

        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            with DatabaseSession() as session:
                access = session.query(TrustedServerAccess).filter_by(guild_id=interaction.guild.id).first()
                if revoke:
                    if access:
                        access.enabled = False
                        session.commit()
                    await interaction.followup.send(
                        f"Trusted free access revoked for **{interaction.guild.name}**.",
                        ephemeral=True
                    )
                else:
                    if access:
                        access.enabled = True
                    else:
                        access = TrustedServerAccess(
                            guild_id=interaction.guild.id,
                            enabled=True,
                            enabled_by=interaction.user.id
                        )
                        session.add(access)
                    session.commit()
                    await interaction.followup.send(
                        f"✅ **{interaction.guild.name}** now has free trusted organizer access.",
                        ephemeral=True
                    )
        except Exception as e:
            self.logger.error(f"Error in grant_server_access: {e}", exc_info=True)
            await interaction.followup.send("❌ Could not update server access.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(SubscriptionCommands(bot))
    logger.info("Subscription commands cog loaded")
