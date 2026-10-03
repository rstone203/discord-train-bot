"""
Helper functions for sending Twitch link reminders and proactive outreach
when users are added to train schedules.
"""

import discord
import logging
import os
import re
from database import DatabaseSession
from utils.oauth_tokens import create_oauth_binding_token
from models import User, TrainParticipant, TrainSchedule, TwitchLinkOutreach, TwitchOAuthToken
from datetime import datetime, timedelta
from models import get_est_time

logger = logging.getLogger('discord_bot.auto_link')

TWITCH_USERNAME_PATTERN = re.compile(r'^[a-zA-Z0-9_]{4,25}$')


async def send_link_reminder_if_needed(bot, user_or_id, guild_id: int) -> bool:
    """
    Check if a user has Twitch linked, and if not, send them a reminder DM.
    """
    try:
        if hasattr(user_or_id, 'id'):
            user_id = user_or_id.id
            user_obj = user_or_id
        else:
            user_id = user_or_id
            user_obj = None
        
        with DatabaseSession() as session:
            existing_user = session.query(User).filter(
                User.id == user_id,
                User.guild_id == guild_id,
                User.twitch_login.isnot(None)
            ).first()
            
            if existing_user:
                logger.debug(f"User {user_id} already has Twitch linked")
                return False
        
        if user_obj is None:
            try:
                user = await bot.fetch_user(user_id)
            except Exception as e:
                logger.error(f"Could not fetch user {user_id}: {e}")
                return False
        else:
            user = user_obj
        
        dm_sent = await send_link_reminder_dm(user)
        
        if dm_sent:
            logger.info(f"Sent Twitch link reminder to {user.name}")
            return True
        else:
            logger.debug(f"Could not send reminder to {user.name} (DMs disabled)")
            return False
            
    except Exception as e:
        logger.error(f"Error in link reminder check: {e}", exc_info=True)
        return False


async def send_link_reminder_dm(user: discord.User) -> bool:
    """
    Send a DM to the user reminding them to link their Twitch account.
    """
    try:
        embed = discord.Embed(
            title="You've Been Added to a Train!",
            description="You were just added to a train schedule. To get the most out of your experience, link your Twitch account!",
            color=0x9146ff
        )
        
        embed.add_field(
            name="Why Link Your Twitch?",
            value=(
                "- **Automatic attendance tracking** during raid trains\n"
                "- **Real-time chat monitoring** - Your presence tracked automatically\n"
                "- **Enhanced reports** - Shows your actual participation\n"
                "- **Train notifications** display your Twitch username\n"
                "- **Auto-shoutouts** when people chat in your stream"
            ),
            inline=False
        )
        
        embed.add_field(
            name="How to Link",
            value="Use: `/linktwitch <your_twitch_username>`\n\nExample: `/linktwitch coolstreamer123`",
            inline=False
        )
        
        embed.add_field(
            name="It's Quick & Easy!",
            value="Takes just 30 seconds and makes train participation seamless. No manual check-ins needed!",
            inline=False
        )
        
        embed.set_footer(text="You can link your account anytime - Use /linktwitch to get started")
        
        try:
            dm_channel = await user.create_dm()
            await dm_channel.send(embed=embed)
            logger.info(f"Sent link reminder DM to {user.name}")
            return True
            
        except discord.Forbidden:
            logger.debug(f"Could not send DM to {user.name} - DMs disabled")
            return False
        
    except Exception as e:
        logger.error(f"Error sending link reminder DM: {e}", exc_info=True)
        return False


async def send_proactive_outreach_dm(bot, user_id: int, guild_id: int, schedule_id: int = None) -> bool:
    """
    Send a proactive DM to a user in a train schedule who hasn't linked their Twitch,
    asking for their Twitch username and offering OAuth authorization.
    """
    try:
        with DatabaseSession() as session:
            existing_user = session.query(User).filter(
                User.id == user_id,
                User.guild_id == guild_id,
                User.twitch_login.isnot(None)
            ).first()
            
            if existing_user:
                logger.debug(f"User {user_id} already has Twitch linked, skipping outreach")
                return False
            
            recent_outreach = session.query(TwitchLinkOutreach).filter(
                TwitchLinkOutreach.user_id == user_id,
                TwitchLinkOutreach.guild_id == guild_id,
                TwitchLinkOutreach.dm_sent_at > get_est_time() - timedelta(days=7)
            ).first()
            
            if recent_outreach:
                logger.debug(f"User {user_id} was already contacted within 7 days, skipping")
                return False

        try:
            user = await bot.fetch_user(user_id)
        except Exception as e:
            logger.error(f"Could not fetch user {user_id}: {e}")
            return False

        replit_domains = os.getenv('REPLIT_DOMAINS', '')
        if replit_domains:
            base_url = f"https://{replit_domains.split(',')[0]}"
        else:
            base_url = "https://rstone203.replit.app"
        
        binding_token = create_oauth_binding_token(user_id)
        oauth_url = f"{base_url}/oauth/twitch/authorize?token={binding_token}"

        schedule_info = ""
        if schedule_id:
            with DatabaseSession() as session:
                schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
                if schedule:
                    schedule_info = f" for **{schedule.name}**"

        embed = discord.Embed(
            title="Hey! We need your Twitch info for the raid train!",
            description=f"You're signed up{schedule_info} but we don't have your Twitch account linked yet. We need this so the bot can track your attendance and manage raids properly.",
            color=0x9146ff
        )

        embed.add_field(
            name="Step 1: Send me your Twitch username",
            value="Just **reply to this message** with your Twitch username.\n\nFor example, if your Twitch is `coolstreamer123`, just type:\n`coolstreamer123`",
            inline=False
        )

        embed.add_field(
            name="Step 2 (Optional): Authorize auto-raids",
            value="After replying with your username, click the button below to authorize auto-raids from your channel!",
            inline=False
        )

        embed.add_field(
            name="Why is this needed?",
            value=(
                "- **Attendance tracking** - We can see who's in chat during the train\n"
                "- **Raid coordination** - The bot can auto-raid the next person\n"
                "- **Notifications** - Your Twitch name shows up in train alerts\n"
                "- **Reports** - Train attendance reports include your participation"
            ),
            inline=False
        )

        embed.set_footer(text="Just reply with your Twitch username - that's it!")

        outreach_view = discord.ui.View()
        outreach_view.add_item(discord.ui.Button(
            label="Authorize Auto-Raids on Twitch",
            url=oauth_url,
            style=discord.ButtonStyle.link,
            emoji="🎮"
        ))

        try:
            dm_channel = await user.create_dm()
            await dm_channel.send(embed=embed, view=outreach_view)
            
            with DatabaseSession() as session:
                outreach = TwitchLinkOutreach(
                    user_id=user_id,
                    guild_id=guild_id,
                    schedule_id=schedule_id,
                    awaiting_reply=True
                )
                session.add(outreach)
                session.commit()
            
            logger.info(f"Sent proactive Twitch outreach DM to {user.name} (ID: {user_id})")
            return True
            
        except discord.Forbidden:
            logger.warning(f"Could not send outreach DM to {user.name} - DMs disabled")
            return False

    except Exception as e:
        logger.error(f"Error in proactive outreach: {e}", exc_info=True)
        return False


async def handle_twitch_username_reply(bot, message) -> bool:
    """
    Handle a DM reply that might be a Twitch username from a user we've contacted.
    Returns True if the message was handled as a Twitch link response.
    """
    try:
        user_id = message.author.id
        content = message.content.strip()
        
        with DatabaseSession() as session:
            pending_outreach = session.query(TwitchLinkOutreach).filter(
                TwitchLinkOutreach.user_id == user_id,
                TwitchLinkOutreach.awaiting_reply == True
            ).order_by(TwitchLinkOutreach.dm_sent_at.desc()).first()
            
            if not pending_outreach:
                return False
            
            guild_id = pending_outreach.guild_id

        if content.startswith('/') or content.startswith('!'):
            return False

        username = content.split()[0] if content else ""
        username = username.lstrip('@').lower()
        
        if not TWITCH_USERNAME_PATTERN.match(username):
            try:
                await message.channel.send(
                    f"Hmm, `{content}` doesn't look like a valid Twitch username. "
                    f"Twitch usernames are 4-25 characters and only use letters, numbers, and underscores.\n\n"
                    f"Please try again with just your Twitch username (example: `coolstreamer123`)"
                )
            except discord.Forbidden:
                pass
            return True

        # Record the claimed username and close the outreach record so that future
        # DMs are not mistakenly treated as additional Twitch-link replies.
        # User.twitch_login is NOT set here — ownership must be proven via Twitch
        # OAuth first, which the verification link below initiates.
        with DatabaseSession() as session:
            outreach_records = session.query(TwitchLinkOutreach).filter(
                TwitchLinkOutreach.user_id == user_id,
                TwitchLinkOutreach.awaiting_reply == True
            ).all()
            for record in outreach_records:
                record.awaiting_reply = False
                record.responded = True
                record.responded_at = get_est_time()
                record.twitch_username_provided = username
            session.commit()

        replit_domains = os.getenv('REPLIT_DOMAINS', '')
        if replit_domains:
            base_url = f"https://{replit_domains.split(',')[0]}"
        else:
            base_url = "https://rstone203.replit.app"

        binding_token = create_oauth_binding_token(user_id)
        oauth_url = f"{base_url}/oauth/twitch/authorize?token={binding_token}"

        verify_embed = discord.Embed(
            title="One More Step — Verify Your Twitch Account",
            description=(
                f"Thanks! To finish linking **{username}** to your Discord account, "
                f"please verify you own that channel by signing in with Twitch below.\n\n"
                f"This keeps your identity secure and prevents others from claiming your channel."
            ),
            color=0x9146ff
        )
        verify_embed.add_field(
            name="What happens after verification",
            value=(
                "- Your attendance will be tracked automatically during trains\n"
                "- Your Twitch username will show in train notifications\n"
                "- You'll get auto-shoutouts when chatting in others' streams"
            ),
            inline=False
        )
        verify_embed.set_footer(text="Click the button below to verify with Twitch")

        verify_view = discord.ui.View()
        verify_view.add_item(discord.ui.Button(
            label="Verify & Link with Twitch",
            url=oauth_url,
            style=discord.ButtonStyle.link,
            emoji="🎮"
        ))

        try:
            await message.channel.send(embed=verify_embed, view=verify_view)
        except discord.Forbidden:
            pass

        logger.info(f"User {message.author.name} (ID: {user_id}) claimed Twitch '{username}' via DM — sent OAuth verification link")

        return True

    except Exception as e:
        logger.error(f"Error handling Twitch username reply: {e}", exc_info=True)
        return False


async def check_and_outreach_unlinked_participants(bot):
    """
    Check all upcoming train schedules for participants without linked Twitch
    accounts and send them outreach DMs.
    """
    try:
        from models import get_est_time
        current_time = get_est_time()
        current_day = current_time.weekday()
        contacted = 0
        skipped = 0
        
        with DatabaseSession() as session:
            upcoming_schedules = session.query(TrainSchedule).filter(
                TrainSchedule.is_active == True
            ).all()
            
            schedule_data = []
            for s in upcoming_schedules:
                schedule_data.append({
                    'id': s.id,
                    'guild_id': s.guild_id,
                    'name': s.name,
                    'day_of_week': s.day_of_week
                })
        
        users_to_contact = []
        
        for sched in schedule_data:
            with DatabaseSession() as session:
                participants = session.query(TrainParticipant).filter(
                    TrainParticipant.schedule_id == sched['id'],
                    TrainParticipant.is_active == True
                ).all()
                
                for p in participants:
                    user = session.query(User).filter(
                        User.id == p.user_id,
                        User.guild_id == sched['guild_id']
                    ).first()
                    
                    has_twitch = False
                    if p.twitch_username:
                        has_twitch = True
                    elif user and user.twitch_login:
                        has_twitch = True
                    
                    if has_twitch:
                        continue
                    
                    recent = session.query(TwitchLinkOutreach).filter(
                        TwitchLinkOutreach.user_id == p.user_id,
                        TwitchLinkOutreach.guild_id == sched['guild_id'],
                        TwitchLinkOutreach.dm_sent_at > get_est_time() - timedelta(days=7)
                    ).first()
                    
                    if recent:
                        skipped += 1
                        continue
                    
                    users_to_contact.append({
                        'user_id': p.user_id,
                        'guild_id': sched['guild_id'],
                        'schedule_id': sched['id']
                    })
        
        for entry in users_to_contact:
            sent = await send_proactive_outreach_dm(bot, entry['user_id'], entry['guild_id'], entry['schedule_id'])
            if sent:
                contacted += 1
        
        logger.info(f"Outreach check complete: {contacted} contacted, {skipped} skipped (already contacted recently)")
        return contacted
        
    except Exception as e:
        logger.error(f"Error in outreach check: {e}", exc_info=True)
        return 0
