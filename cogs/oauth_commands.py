"""
OAuth management commands for Twitch integration.

Uses Twitch Device Code Grant flow — no redirect server or public URL required.
Users get a short code and visit https://www.twitch.tv/activate to authorize.
"""
import os
import time
import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
import requests
from datetime import datetime, timedelta
from database import DatabaseSession
from models import TwitchOAuthToken, User

logger = logging.getLogger('discord_bot.oauth_commands')

TWITCH_DEVICE_AUTH_URL = "https://id.twitch.tv/oauth2/device"
TWITCH_TOKEN_URL       = "https://id.twitch.tv/oauth2/token"
TWITCH_USERS_URL       = "https://api.twitch.tv/helix/users"
TWITCH_SCOPES = (
    "chat:read chat:edit user:read:email "
    "moderator:read:chatters user:write:chat channel:manage:raids"
)


class OAuthCommands(commands.Cog):
    """Commands for managing OAuth authentication."""

    OAUTH_ALLOWED_ROLE_ID = 1493179566913749052

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # /twitchoauth — Device Code flow
    # ------------------------------------------------------------------

    @app_commands.command(
        name="twitchoauth",
        description="Link your Twitch account for auto-raids and chat monitoring"
    )
    @app_commands.guild_only()
    async def twitch_oauth(self, interaction: discord.Interaction):
        """Start Twitch Device Code authorization — works for everyone, no redirect needed."""
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.errors.NotFound as e:
            if e.code == 10062:
                logger.debug("Interaction already acknowledged by another instance, skipping.")
                return
            raise

        try:
            guild_id = interaction.guild_id
            if not guild_id:
                await interaction.followup.send(
                    "❌ This command must be used inside a server.", ephemeral=True
                )
                return

            member_role_ids = [r.id for r in interaction.user.roles]
            is_admin = interaction.user.guild_permissions.administrator
            if not is_admin and self.OAUTH_ALLOWED_ROLE_ID not in member_role_ids:
                await interaction.followup.send(
                    "❌ You don't have permission to use this command.", ephemeral=True
                )
                return

            client_id = os.getenv('TWITCH_CLIENT_ID')
            client_secret = os.getenv('TWITCH_CLIENT_SECRET')
            if not client_id or not client_secret:
                await interaction.followup.send(
                    "❌ Twitch credentials are not configured. Contact the bot owner.",
                    ephemeral=True
                )
                return

            # Step 1: Request device code from Twitch
            def _start_device_auth():
                return requests.post(TWITCH_DEVICE_AUTH_URL, data={
                    'client_id': client_id,
                    'scopes': TWITCH_SCOPES,
                }, timeout=10)

            resp = await asyncio.to_thread(_start_device_auth)
            if resp.status_code != 200:
                await interaction.followup.send(
                    f"❌ Failed to start Twitch authorization (HTTP {resp.status_code}). Try again later.",
                    ephemeral=True
                )
                return

            data = resp.json()
            device_code      = data['device_code']
            user_code        = data['user_code']
            verification_uri = data.get('verification_uri', 'https://www.twitch.tv/activate')
            expires_in       = data.get('expires_in', 1800)
            interval         = data.get('interval', 5)

            # Step 2: Show the code to the user
            embed = discord.Embed(
                title="🎮 Link Your Twitch Account",
                description=(
                    "Follow these two steps to authorize the bot to start raids from your channel."
                ),
                color=discord.Color.purple()
            )
            embed.add_field(
                name="Step 1 — Open Twitch",
                value=f"Go to **[twitch.tv/activate]({verification_uri})**",
                inline=False
            )
            embed.add_field(
                name="Step 2 — Enter Your Code",
                value=f"```\n{user_code}\n```",
                inline=False
            )
            embed.add_field(
                name="⏰ Code Expires In",
                value=f"{expires_in // 60} minutes",
                inline=False
            )
            embed.set_footer(text="The bot will confirm automatically once you've authorized.")

            await interaction.followup.send(embed=embed, ephemeral=True)

            # Step 3: Poll for completion in background
            asyncio.create_task(self._poll_device_auth(
                interaction=interaction,
                device_code=device_code,
                interval=interval,
                expires_in=expires_in,
                client_id=client_id,
                client_secret=client_secret,
                discord_user_id=str(interaction.user.id),
                guild_id=str(guild_id),
            ))

        except Exception as e:
            logger.error(f"Error in twitchoauth command: {e}")
            try:
                await interaction.followup.send(
                    f"❌ Error starting authorization: {str(e)}", ephemeral=True
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Background polling task
    # ------------------------------------------------------------------

    async def _poll_device_auth(
        self, interaction, device_code, interval, expires_in,
        client_id, client_secret, discord_user_id, guild_id
    ):
        """Poll Twitch token endpoint until user completes authorization or code expires."""
        deadline = time.time() + expires_in

        while time.time() < deadline:
            await asyncio.sleep(interval)

            def _poll():
                return requests.post(TWITCH_TOKEN_URL, data={
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'device_code': device_code,
                    'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
                }, timeout=10)

            try:
                resp = await asyncio.to_thread(_poll)
            except Exception as e:
                logger.warning(f"Device auth poll error: {e}")
                continue

            token_data = resp.json()

            if resp.status_code == 200 and 'access_token' in token_data:
                await self._handle_token_success(
                    token_data, client_id, discord_user_id, guild_id, interaction
                )
                return

            error = token_data.get('message') or token_data.get('error', '')
            if 'authorization_pending' in error:
                continue
            elif 'slow_down' in error:
                interval = min(interval + 5, 30)
                continue
            elif 'expired' in error or 'access_denied' in error:
                logger.info(f"Device auth ended for user {discord_user_id}: {error}")
                break
            else:
                logger.warning(f"Device auth unexpected response: {token_data}")
                break

        logger.info(f"Device auth timed out or ended for Discord user {discord_user_id}")

    async def _handle_token_success(self, token_data, client_id, discord_user_id, guild_id, interaction):
        """Save the token and notify the user."""
        access_token  = token_data['access_token']
        refresh_token = token_data.get('refresh_token')
        token_expires = token_data.get('expires_in')

        # Fetch Twitch user info
        def _get_user():
            return requests.get(TWITCH_USERS_URL, headers={
                'Authorization': f'Bearer {access_token}',
                'Client-Id': client_id,
            }, timeout=10)

        try:
            user_resp = await asyncio.to_thread(_get_user)
            user_data = user_resp.json()['data'][0]
            twitch_user_id       = user_data['id']
            twitch_username      = user_data['login']
            twitch_display_name  = user_data.get('display_name', twitch_username)
        except Exception as e:
            logger.error(f"Failed to fetch Twitch user info after device auth: {e}")
            return

        # Save to database
        def _save():
            with DatabaseSession() as session:
                expires_at = (
                    datetime.now() + timedelta(seconds=token_expires)
                    if token_expires else None
                )

                # ── 1. Upsert TwitchOAuthToken ─────────────────────────────
                # Prefer an existing row that matches BOTH user_id AND username;
                # fall back to any row with the same user_id (ordered most-recent
                # first) so we don't accidentally update a stale/inactive row.
                existing = (
                    session.query(TwitchOAuthToken)
                    .filter_by(twitch_user_id=twitch_user_id, twitch_username=twitch_username)
                    .order_by(TwitchOAuthToken.updated_at.desc())
                    .first()
                ) or (
                    session.query(TwitchOAuthToken)
                    .filter_by(twitch_user_id=twitch_user_id)
                    .order_by(TwitchOAuthToken.updated_at.desc())
                    .first()
                )

                # Twitch device-code flow sometimes omits 'scope' from the token
                # response even when scopes were granted. Fall back to the scopes
                # we requested so the DB never stores an empty array.
                raw_scope = token_data.get('scope')
                if raw_scope:
                    saved_scopes = raw_scope if isinstance(raw_scope, list) else raw_scope.split()
                else:
                    saved_scopes = TWITCH_SCOPES.split()

                if existing:
                    existing.access_token    = access_token
                    existing.refresh_token   = refresh_token
                    existing.twitch_username = twitch_username
                    existing.scopes          = saved_scopes
                    existing.expires_at      = expires_at
                    existing.is_active       = True
                    existing.last_used_at    = datetime.now()
                    if discord_user_id:
                        existing.user_id = discord_user_id
                    if guild_id and not existing.guild_id:
                        existing.guild_id = guild_id
                else:
                    session.add(TwitchOAuthToken(
                        user_id=discord_user_id,
                        guild_id=guild_id,
                        access_token=access_token,
                        refresh_token=refresh_token,
                        twitch_user_id=twitch_user_id,
                        twitch_username=twitch_username,
                        scopes=saved_scopes,
                        expires_at=expires_at,
                        is_active=True,
                        last_used_at=datetime.now(),
                    ))

                # ── 2. Link Twitch info back to the User record ────────────
                if discord_user_id:
                    user_record = session.query(User).filter(
                        User.id == int(discord_user_id),
                        User.guild_id == int(guild_id)
                    ).first()
                    if not user_record:
                        # Fall back to any guild if not found in this one
                        user_record = session.query(User).filter(
                            User.id == int(discord_user_id)
                        ).order_by(User.last_seen.desc()).first()
                    if user_record:
                        user_record.twitch_id           = twitch_user_id
                        user_record.twitch_login        = twitch_username
                        user_record.twitch_display_name = twitch_display_name
                        user_record.twitch_linked_at    = datetime.now()
                        user_record.twitch_source       = 'twitch_oauth'
                        user_record.twitch_consent      = True
                        logger.info(
                            f"✅ User record updated: Discord {discord_user_id} "
                            f"→ Twitch {twitch_username} ({twitch_user_id})"
                        )
                    else:
                        logger.warning(
                            f"No User record found for Discord ID {discord_user_id} "
                            f"— token saved but User not linked. "
                            f"They may not have chatted in the server yet."
                        )

        try:
            await asyncio.to_thread(_save)
            logger.info(
                f"✅ Device auth complete: {twitch_username} linked to Discord {discord_user_id} | "
                f"scope={token_data.get('scope')} | expires_in={token_data.get('expires_in')}"
            )
        except Exception as e:
            logger.error(f"Failed to save device auth token: {e}")
            return

        # Notify the user — ephemeral followup in channel AND a DM
        success_msg = f"✅ **{twitch_username}** has been successfully linked! The bot can now auto-raid from your channel."
        try:
            await interaction.followup.send(success_msg, ephemeral=True)
        except Exception:
            pass

        # Always DM the user so they have a persistent confirmation
        try:
            user = await self.bot.fetch_user(int(discord_user_id))
            await user.send(
                f"✅ **Twitch account linked!**\n\n"
                f"Your Twitch account **{twitch_username}** has been successfully connected to the bot. "
                f"Auto-raids and chat monitoring are now active for your channel."
            )
        except Exception as e:
            logger.warning(f"Could not DM user {discord_user_id} after device auth: {e}")

        # Trigger the chat monitor to clear cooldown and reconnect immediately
        try:
            monitor = getattr(self.bot, 'twitch_chat_monitor', None)
            if monitor and hasattr(monitor, 'join_channel_for_user'):
                await monitor.join_channel_for_user(twitch_username)
        except Exception as e:
            logger.debug(f"Could not auto-join Twitch channel after device auth: {e}")

    # ------------------------------------------------------------------
    # /oauthstatus
    # ------------------------------------------------------------------

    @app_commands.command(name="oauthstatus", description="Check Twitch OAuth status for all authorized accounts")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def oauth_status(self, interaction: discord.Interaction):
        """Check the current OAuth token status for all authorized accounts."""
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.errors.NotFound as e:
            if e.code == 10062:
                logger.debug("Interaction already acknowledged by another instance, skipping.")
                return
            raise
        try:
            guild_id = interaction.guild_id
            if not guild_id:
                await interaction.followup.send(
                    "❌ This command must be used inside a server.", ephemeral=True
                )
                return

            def _fetch_status():
                with DatabaseSession() as session:
                    tokens = session.query(TwitchOAuthToken).filter_by(
                        is_active=True, guild_id=guild_id
                    ).all()
                    return [{
                        'twitch_username': t.twitch_username,
                        'twitch_user_id':  t.twitch_user_id,
                        'user_id':         t.user_id,
                        'expires_at':      t.expires_at,
                        'last_used_at':    t.last_used_at,
                    } for t in tokens]

            active_tokens = await asyncio.to_thread(_fetch_status)

            if not active_tokens:
                embed = discord.Embed(
                    title="❌ No Active OAuth Tokens",
                    description="No Twitch accounts are authorized. Use `/twitchoauth` to authorize.",
                    color=discord.Color.red()
                )
            else:
                embed = discord.Embed(
                    title=f"🎮 Twitch OAuth Status ({len(active_tokens)} account{'s' if len(active_tokens) != 1 else ''})",
                    description="Authorized Twitch accounts for auto-raids and chat monitoring:",
                    color=discord.Color.green()
                )
                for token in active_tokens:
                    expires_in = (
                        (token['expires_at'] - datetime.now()).total_seconds()
                        if token['expires_at'] else None
                    )
                    if expires_in and expires_in < 0:
                        status = "❌ Expired"
                    elif expires_in and expires_in < 86400:
                        status = f"⚠️ Expiring in {int(expires_in / 3600)}h"
                    elif expires_in:
                        status = f"✅ Valid ({int(expires_in / 3600)}h left)"
                    else:
                        status = "✅ Valid (no expiry)"

                    last_used    = token['last_used_at'].strftime('%Y-%m-%d %H:%M') if token['last_used_at'] else "Never"
                    discord_link = f"<@{token['user_id']}>" if token['user_id'] else "Not linked"

                    embed.add_field(
                        name=f"📺 {token['twitch_username']}",
                        value=(
                            f"**Status:** {status}\n"
                            f"**Discord:** {discord_link}\n"
                            f"**Last Used:** {last_used}\n"
                            f"**Twitch ID:** {token['twitch_user_id']}"
                        ),
                        inline=True
                    )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in oauth_status command: {e}")
            try:
                await interaction.followup.send(
                    f"❌ Error checking OAuth status: {str(e)}", ephemeral=True
                )
            except Exception:
                pass


async def setup(bot):
    await bot.add_cog(OAuthCommands(bot))
