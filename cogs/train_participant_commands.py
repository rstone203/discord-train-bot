"""
Commands for managing train participants - adding/removing people from trains.
"""

import logging
import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
import asyncio
import traceback
import pytz
from datetime import datetime, timedelta
from database import DatabaseSession
from models import TrainSchedule, TrainParticipant, Guild, User, BannedUser
from utils.schedule_cache import get_cache, invalidate_schedule_cache
from utils.slash_permissions import admin_or_trusted

async def notify_owner_of_signup_error(bot, interaction: discord.Interaction, error: Exception, action: str, context: dict = None):
    """Notify bot owner when a user encounters a signup/dropdown error."""
    try:
        app_info = await bot.application_info()
        owner = app_info.owner
        
        if not owner:
            return
        
        error_embed = discord.Embed(
            title="🚨 Dropdown Signup Error Alert",
            description="A user encountered an error during train signup",
            color=0xff0000,
            timestamp=datetime.utcnow()
        )
        
        error_embed.add_field(
            name="👤 Who",
            value=f"{interaction.user.mention} (`{interaction.user}` / ID: {interaction.user.id})",
            inline=False
        )
        
        error_embed.add_field(
            name="🎯 What They Tried",
            value=action,
            inline=False
        )
        
        error_embed.add_field(
            name="🕐 When",
            value=f"<t:{int(datetime.utcnow().timestamp())}:F>",
            inline=False
        )
        
        error_embed.add_field(
            name="❌ Error",
            value=f"```{str(error)[:500]}```",
            inline=False
        )
        
        if context:
            context_str = "\n".join([f"**{k}:** {v}" for k, v in context.items()])
            error_embed.add_field(
                name="📋 Additional Context",
                value=context_str[:1024],
                inline=False
            )
        
        error_embed.add_field(
            name="🔍 Full Traceback",
            value=f"```python\n{traceback.format_exc()[:900]}```",
            inline=False
        )
        
        error_embed.set_footer(text=f"Server: {interaction.guild.name if interaction.guild else 'DM'}")
        
        await owner.send(embed=error_embed)
        
    except Exception as e:
        logging.getLogger("discord_bot.train_participant_commands").error(f"Failed to notify owner of error: {e}")

class TwitchLinkModal(ui.Modal, title="Link Your Twitch Account"):
    """Modal for collecting Twitch username."""
    
    twitch_username = ui.TextInput(
        label="Twitch Username",
        placeholder="Enter your Twitch username (e.g., YourTwitchName)",
        required=True,
        max_length=25,
        min_length=4
    )
    
    def __init__(self, schedule_id: int, schedule_name: str, cog, parent_dropdown=None):
        super().__init__()
        self.schedule_id = schedule_id
        self.schedule_name = schedule_name
        self.cog = cog
        self.parent_dropdown = parent_dropdown
    
    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission."""
        try:
            username = self.twitch_username.value.strip()
            
            consent_view = TwitchConsentView(
                twitch_username=username,
                schedule_id=self.schedule_id,
                schedule_name=self.schedule_name,
                cog=self.cog,
                parent_dropdown=self.parent_dropdown
            )
            
            embed = discord.Embed(
                title="🔗 Confirm Twitch Account Linking",
                description=f"You're about to link your Twitch account **{username}** to this bot.",
                color=0x9146ff,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📊 What this enables:",
                value="• Automatic attendance tracking during trains\n• Real-time chat monitoring\n• Enhanced train statistics",
                inline=False
            )
            
            embed.add_field(
                name="🔒 Your Privacy:",
                value="Your Twitch username will be stored and linked to your Discord account for train management purposes only.",
                inline=False
            )
            
            embed.add_field(
                name="❓ Confirm Linking",
                value=f"Are you okay with linking **{username}** to the bot?",
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, view=consent_view, ephemeral=True)
            
        except Exception as e:
            self.cog.logger.error(f"❌ Error in Twitch link modal submission: {e}")
            self.cog.logger.error(traceback.format_exc())
            
            context = {
                "Twitch Username Entered": self.twitch_username.value.strip() if self.twitch_username.value else "None",
                "Schedule ID": str(self.schedule_id),
                "Schedule Name": self.schedule_name
            }
            
            await notify_owner_of_signup_error(
                interaction.client,
                interaction,
                e,
                f"Submitted Twitch username in modal for train '{self.schedule_name}'",
                context
            )
            
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {str(e)}\n\nThe bot owner has been notified.", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error: {str(e)}\n\nThe bot owner has been notified.", ephemeral=True)

class TwitchConsentView(ui.View):
    """View for confirming Twitch account linking consent."""
    
    def __init__(self, twitch_username: str, schedule_id: int, schedule_name: str, cog, parent_dropdown=None):
        super().__init__(timeout=300)
        self.twitch_username = twitch_username
        self.schedule_id = schedule_id
        self.schedule_name = schedule_name
        self.cog = cog
        self.parent_dropdown = parent_dropdown
    
    @ui.button(label="✅ Yes, Link My Account", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: ui.Button):
        """Handle consent confirmation."""
        await interaction.response.defer(ephemeral=True)
        
        try:
            with DatabaseSession() as session:
                # Use merge to handle both insert and update atomically
                user_record = session.merge(User(
                    id=interaction.user.id,
                    username=interaction.user.name,
                    display_name=interaction.user.display_name,
                    guild_id=interaction.guild.id,
                    twitch_login=self.twitch_username,
                    twitch_display_name=self.twitch_username,
                    twitch_linked_at=datetime.utcnow(),
                    twitch_source='manual_signup',
                    twitch_consent=True
                ))
                
                session.commit()
                self.cog.logger.info(f"✅ Linked Twitch account {self.twitch_username} for {interaction.user}")
            
            await self.cog._join_train_internal(interaction, self.schedule_id, from_modal=True, send_public=True, parent_dropdown=self.parent_dropdown)
            
            # Delete the consent message to keep things clean
            try:
                await interaction.message.delete()
            except discord.errors.NotFound:
                # Message was already deleted/expired - this is fine, linking already succeeded
                pass
            
        except Exception as e:
            self.cog.logger.error(f"❌ Error linking Twitch account: {e}")
            self.cog.logger.error(traceback.format_exc())
            
            context = {
                "Twitch Username": self.twitch_username,
                "Schedule ID": str(self.schedule_id),
                "Schedule Name": self.schedule_name
            }
            
            await notify_owner_of_signup_error(
                interaction.client,
                interaction,
                e,
                f"Confirmed Twitch account linking for train signup to '{self.schedule_name}'",
                context
            )
            
            await interaction.followup.send(f"❌ Failed to link Twitch account: {str(e)}\n\nThe bot owner has been notified.", ephemeral=True)
    
    @ui.button(label="❌ No, Skip Linking", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        """Handle consent denial."""
        try:
            await interaction.response.defer(ephemeral=True)
            
            await self.cog._join_train_internal(interaction, self.schedule_id, from_modal=True, send_public=True, parent_dropdown=self.parent_dropdown)
            
            # Delete the consent message to keep things clean
            try:
                await interaction.message.delete()
            except discord.errors.NotFound:
                pass
                
        except Exception as e:
            self.cog.logger.error(f"❌ Error skipping Twitch linking: {e}")
            self.cog.logger.error(traceback.format_exc())
            
            context = {
                "Schedule ID": str(self.schedule_id),
                "Schedule Name": self.schedule_name
            }
            
            await notify_owner_of_signup_error(
                interaction.client,
                interaction,
                e,
                f"Clicked 'Skip Linking' button to join train without Twitch link to '{self.schedule_name}'",
                context
            )
            
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {str(e)}\n\nThe bot owner has been notified.", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error: {str(e)}\n\nThe bot owner has been notified.", ephemeral=True)

class JoinConfirmationView(ui.View):
    """View with confirmation button for joining a train - enables Twitch modal!"""
    
    def __init__(self, schedule_id: int, schedule_name: str, parent_dropdown=None, existing_trains=None):
        super().__init__(timeout=60.0)
        self.schedule_id = schedule_id
        self.schedule_name = schedule_name
        self.parent_dropdown = parent_dropdown  # Reference to refresh dropdown after join
        self.existing_trains = existing_trains or []  # List of (schedule_id, name) tuples
    
    @ui.button(label="✅ Confirm Join", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: discord.Interaction, button: ui.Button):
        """Handle confirmation - this is a FRESH interaction that can show modal before deferring!"""
        try:
            cog = interaction.client.get_cog('TrainParticipantCommands')
            if not cog:
                await interaction.response.send_message("❌ Error: Commands not available.", ephemeral=True)
                return
            
            # If user is switching trains, leave all existing trains first
            if self.existing_trains:
                cog.logger.info(f"🔄 Switching: Leaving {len(self.existing_trains)} existing train(s) for {interaction.user}")
                for old_schedule_id, old_name in self.existing_trains:
                    def leave_old_train(sid):
                        with DatabaseSession() as session:
                            participant = session.query(TrainParticipant).filter_by(
                                schedule_id=sid,
                                user_id=interaction.user.id,
                                is_active=True
                            ).first()
                            if participant:
                                participant.is_active = False
                                # Clean up notifications
                                from models import TrainNotification
                                notifications = session.query(TrainNotification).filter_by(
                                    schedule_id=sid,
                                    primary_user_id=participant.user_id
                                ).all()
                                for notification in notifications:
                                    notification.primary_confirmed = False
                                    notification.primary_user_id = None
                                session.commit()
                                return True
                            return False
                    
                    removed = await asyncio.to_thread(leave_old_train, old_schedule_id)
                    if removed:
                        cog.logger.info(f"   ✅ Left {old_name} (ID: {old_schedule_id})")
            
            # THIS IS KEY: Fresh interaction = can check DB and show modal BEFORE deferring!
            # Quick check if user has Twitch linked
            def check_twitch_link():
                with DatabaseSession() as session:
                    user_record = session.query(User).filter(
                        User.id == interaction.user.id,
                        User.guild_id == interaction.guild.id
                    ).first()
                    return not (user_record and user_record.twitch_login)
            
            need_modal = await asyncio.to_thread(check_twitch_link)
            
            # If modal needed, show it immediately (button's FIRST response - no defer yet!)
            if need_modal:
                cog.logger.info(f"✨ Button: Showing Twitch modal for {interaction.user}")
                with DatabaseSession() as session:
                    schedule = session.query(TrainSchedule).filter_by(
                        id=self.schedule_id,
                        guild_id=interaction.guild.id
                    ).first()
                    
                    if not schedule:
                        await interaction.response.send_message("❌ Train not found.", ephemeral=True)
                        return
                    
                    # Show Twitch linking modal (this is the FIRST response!)
                    modal = TwitchLinkModal(schedule.id, schedule.name, cog, parent_dropdown=self.parent_dropdown)
                    await interaction.response.send_modal(modal)
                    
                    # Try to disable button (may fail if message is ephemeral/deleted)
                    try:
                        button.disabled = True
                        await interaction.message.edit(view=self)
                    except discord.NotFound:
                        # Message already deleted - that's okay
                        cog.logger.debug("Confirmation message not found after modal (ephemeral)")
                    return
            
            # User has Twitch linked - defer and complete join
            await interaction.response.defer(ephemeral=False)
            cog.logger.info(f"✅ Button: User {interaction.user} has Twitch, proceeding with join")
            await cog._join_train_internal(interaction, self.schedule_id, from_dropdown=True, parent_dropdown=self.parent_dropdown)
            
            # Try to disable button (may fail if message was deleted/ephemeral)
            try:
                button.disabled = True
                await interaction.message.edit(view=self)
            except discord.NotFound:
                # Message was deleted/ephemeral - that's okay, join already completed
                cog.logger.debug("Confirmation message not found (already deleted/ephemeral)")
            
        except Exception as e:
            cog.logger.error(f"❌ Confirmation button error: {str(e)}")
            cog.logger.error(traceback.format_exc())
            
            context = {
                "Schedule ID": str(self.schedule_id),
                "Schedule Name": self.schedule_name
            }
            
            await notify_owner_of_signup_error(
                interaction.client,
                interaction,
                e,
                f"Clicked 'Confirm Join' button for train '{self.schedule_name}'",
                context
            )
            
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {str(e)}\n\nThe bot owner has been notified.", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error: {str(e)}\n\nThe bot owner has been notified.", ephemeral=True)
    
    @ui.button(label="❌ Cancel", style=discord.ButtonStyle.gray)
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        """Cancel the join."""
        await interaction.response.send_message("❌ Join cancelled.", ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)


class TrainSelectionDropdown(ui.View):
    """Interactive dropdown for selecting train slots."""
    
    def __init__(self, author, available_schedules, action_type="join", participant_counts=None, dropdown_message=None):
        super().__init__(timeout=None)  # No timeout - menu stays until deleted
        self.author = author
        self.action_type = action_type
        participant_counts = participant_counts or {}
        self.dropdown_message = dropdown_message  # Store reference to update it later
        self.available_schedules = available_schedules  # Store for refreshing
        
        # Store schedule ID -> name mapping to avoid database queries
        self.schedule_names = {}
        
        # Pre-check if all Saturday trains are full using pre-fetched data
        all_saturday_full = False
        if any("Sunday" in s['name'] for s in available_schedules):
            saturday_schedules = [s for s in available_schedules if "Saturday" in s['name']]
            if saturday_schedules:
                all_saturday_full = all(
                    participant_counts.get(s['id'], 0) >= (s['max_participants'] or 999)
                    for s in saturday_schedules
                )
        
        # Create dropdown options from available schedules
        options = []
        for schedule in available_schedules[:25]:  # Discord limit of 25 options
            # Skip ALL Sunday trains unless ALL Saturday trains are completely full
            if "Sunday" in schedule['name'] and not all_saturday_full:
                continue
            
            # start_time stored as UK wall clock — localize directly for display
            import pytz
            from datetime import date
            eastern_tz = pytz.timezone('Europe/London')
            uk_datetime = datetime.combine(date.today(), schedule['start_time'])
            est_datetime = eastern_tz.localize(uk_datetime)
            time_str = est_datetime.strftime('%I:%M %p').replace(' 0', ' ').upper()

            # Get current participants from pre-fetched data
            current_participants = participant_counts.get(schedule['id'], 0)
            
            # Determine availability
            max_participants = schedule['max_participants'] or 999
            availability = f"{current_participants}/{max_participants}"
            
            # Create emoji based on availability
            if current_participants >= max_participants:
                emoji = "❌"
                description = f"{time_str} EST - FULL ({availability})"
            elif current_participants == 0:
                emoji = "🆕" 
                description = f"{time_str} EST - Available ({availability})"
            else:
                emoji = "⚠️"
                description = f"{time_str} EST - Limited spots ({availability})"
            
            # Store schedule name in mapping
            self.schedule_names[schedule['id']] = schedule['name']
            
            options.append(discord.SelectOption(
                label=schedule['name'],
                description=description,
                emoji=emoji,
                value=str(schedule['id'])
            ))
        
        if not options:
            options.append(discord.SelectOption(
                label="No trains available",
                description="No train slots found",
                emoji="❌",
                value="none"
            ))
        
        # Add the dropdown
        select = ui.Select(
            placeholder=f"Choose a train slot to {action_type}...",
            options=options
        )
        select.callback = self.train_selected
        self.add_item(select)
    
    async def interaction_check(self, interaction):
        """Allow anyone to use the dropdown."""
        return True  # Anyone can join trains via dropdown
    
    async def refresh_dropdown(self, bot):
        """Refresh the dropdown message with updated participant counts."""
        if not self.dropdown_message:
            return
        
        try:
            from sqlalchemy import func
            
            # Fetch updated participant counts
            with DatabaseSession() as session:
                schedule_ids = [s['id'] for s in self.available_schedules]
                participant_counts = dict(
                    session.query(
                        TrainParticipant.schedule_id,
                        func.count(TrainParticipant.id)
                    ).filter(
                        TrainParticipant.schedule_id.in_(schedule_ids),
                        TrainParticipant.is_active == True
                    ).group_by(TrainParticipant.schedule_id).all()
                )
            
            # Rebuild dropdown with new counts
            self.clear_items()
            options = []
            
            for schedule in self.available_schedules[:25]:
                # start_time stored as UK wall clock — localize directly for display
                import pytz
                from datetime import date
                eastern_tz = pytz.timezone('Europe/London')
                uk_datetime = datetime.combine(date.today(), schedule['start_time'])
                est_datetime = eastern_tz.localize(uk_datetime)
                time_str = est_datetime.strftime('%I:%M %p').replace(' 0', ' ').upper()
                
                # Get current participants from updated data
                current_participants = participant_counts.get(schedule['id'], 0)
                
                # Determine availability
                max_participants = schedule['max_participants'] or 999
                availability = f"{current_participants}/{max_participants}"
                
                # Create emoji based on availability
                if current_participants >= max_participants:
                    emoji = "❌"
                    description = f"{time_str} EST - FULL ({availability})"
                elif current_participants == 0:
                    emoji = "🆕" 
                    description = f"{time_str} EST - Available ({availability})"
                else:
                    emoji = "⚠️"
                    description = f"{time_str} EST - Limited spots ({availability})"
                
                options.append(discord.SelectOption(
                    label=schedule['name'],
                    description=description,
                    emoji=emoji,
                    value=str(schedule['id'])
                ))
            
            if not options:
                options.append(discord.SelectOption(
                    label="No trains available",
                    description="No train slots found",
                    emoji="❌",
                    value="none"
                ))
            
            # Add the refreshed dropdown
            select = ui.Select(
                placeholder=f"Choose a train slot to {self.action_type}...",
                options=options
            )
            select.callback = self.train_selected
            self.add_item(select)
            
            # Update the message
            embed = discord.Embed(
                title="🚂 Join a Train - Select Time Slot",
                description="Choose a train slot from the dropdown below:\n\n🆕 = Available\n⚠️ = Limited spots\n❌ = Full\n\n*Dropdown auto-updates when someone joins!*",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📋 Instructions",
                value="• Select a time slot from the dropdown\n• Anyone can use this menu\n• Counts update automatically",
                inline=False
            )
            
            _uk_now = datetime.now(pytz.timezone('Europe/London'))
            embed.set_footer(text=f"Last updated: {_uk_now.strftime('%I:%M %p %Z')}")
            
            await self.dropdown_message.edit(embed=embed, view=self)
            
        except Exception as e:
            logger = logging.getLogger("discord_bot.train_participant_commands")
            logger.error(f"Failed to refresh dropdown: {e}")
    
    async def train_selected(self, interaction: discord.Interaction):
        """Handle train slot selection - shows confirmation button for joins (enables modal!)."""
        try:
            # IMMEDIATE LOGGING - First thing we do!
            logger = logging.getLogger("discord_bot.train_participant_commands")
            logger.info(f"🔥 DROPDOWN CALLBACK TRIGGERED for user {interaction.user}")
            
            # Get the selected value from the select menu
            select_component = None
            for component in self.children:
                if hasattr(component, 'values') and component.values:
                    select_component = component
                    break
            
            logger.info(f"🔥 Select component found: {select_component is not None}")
            
            if not select_component or not select_component.values:
                logger.warning("❌ No selection made")
                await interaction.response.send_message("❌ No selection made.", ephemeral=True)
                return
                
            schedule_id = select_component.values[0]
            logger.info(f"🔥 Schedule ID selected: {schedule_id}")
            
            if schedule_id == "none":
                await interaction.response.send_message("❌ No train slots available.", ephemeral=True)
                return
            
            cog = interaction.client.get_cog('TrainParticipantCommands')
            if not cog:
                logger.error("❌ Cog not found!")
                await interaction.response.send_message("❌ Error: Commands not available.", ephemeral=True)
                return
            
            logger.info(f"🔥 Action type: {self.action_type}")
            
            # For JOIN actions: Show confirmation button IMMEDIATELY (we'll check ban after!)
            if self.action_type == "join":
                # Get schedule name from our cached mapping (NO DATABASE QUERY!)
                schedule_name = self.schedule_names.get(int(schedule_id), "Unknown Train")
                logger.info(f"🔥 Schedule name from cache: {schedule_name}")
                
                # Check if user is already in any trains (quick query)
                def check_existing_trains():
                    with DatabaseSession() as session:
                        existing = session.query(TrainParticipant, TrainSchedule).join(
                            TrainSchedule, TrainParticipant.schedule_id == TrainSchedule.id
                        ).filter(
                            TrainParticipant.user_id == interaction.user.id,
                            TrainParticipant.is_active == True,
                            TrainSchedule.is_active == True
                        ).all()
                        return [(p.schedule_id, s.name) for p, s in existing]
                
                existing_trains = await asyncio.to_thread(check_existing_trains)
                
                # Show confirmation with button FIRST - respond within 3 seconds!
                logger.info(f"🔥 Creating confirmation view...")
                
                # Check if they're already in this specific train
                already_in_this_train = any(sid == int(schedule_id) for sid, _ in existing_trains)
                
                if already_in_this_train:
                    # They're already in this train - just inform them
                    await interaction.response.send_message(
                        f"ℹ️ **You're already signed up for {schedule_name}!**\n\nUse `/leavetraindropdown` if you want to leave this train.",
                        ephemeral=True
                    )
                    logger.info(f"ℹ️ User {interaction.user} tried to join train they're already in")
                    return
                
                # Create confirmation view (pass existing trains info for switching)
                confirmation_view = JoinConfirmationView(
                    int(schedule_id), 
                    schedule_name, 
                    parent_dropdown=self,
                    existing_trains=existing_trains
                )
                
                # Different message if switching vs new signup
                if existing_trains:
                    train_list = "\n".join([f"• **{name}**" for _, name in existing_trains])
                    message = (
                        f"🔄 **Switch to {schedule_name}?**\n\n"
                        f"You're currently in:\n{train_list}\n\n"
                        f"Confirming will **remove you from all other trains** and sign you up for **{schedule_name}** instead."
                    )
                else:
                    message = f"🚂 **Join {schedule_name}?**\n\nClick confirm below to complete your signup."
                
                logger.info(f"🔥 Sending response...")
                await interaction.response.send_message(
                    message,
                    view=confirmation_view,
                    ephemeral=True
                )
                logger.info(f"✅ Dropdown: {interaction.user} selected {schedule_name}, confirmation shown (switching: {len(existing_trains) > 0})")
                
                # NOW check if banned (after response sent)
                def check_ban():
                    with DatabaseSession() as session:
                        return session.query(BannedUser).filter(
                            BannedUser.user_id == interaction.user.id,
                            BannedUser.is_active == True
                        ).first()
                
                banned = await asyncio.to_thread(check_ban)
                
                if banned:
                    # User is banned - delete the confirmation and show ban message
                    reason_text = f"\n**Reason:** {banned.reason}" if banned.reason else ""
                    try:
                        await interaction.delete_original_response()
                    except:
                        pass
                    
                    await interaction.followup.send(
                        f"🚫 **You are banned from joining trains**{reason_text}\n\n"
                        f"Please contact a server administrator for more information.",
                        ephemeral=True
                    )
                    logger.info(f"🚫 Banned user {interaction.user.id} attempted to join train")
                    return
                
            # For LEAVE actions: Direct processing (no modal needed)
            elif self.action_type == "leave":
                await interaction.response.defer(ephemeral=False)
                await cog._leave_train_internal(interaction, int(schedule_id))
            
            # Disable dropdown after selection
            for item in self.children:
                if hasattr(item, 'disabled'):
                    item.disabled = True
            
        except Exception as e:
            logger.error(f"❌ Dropdown error for {interaction.user}: {e}")
            logger.error(traceback.format_exc())
            
            context = {
                "Action Type": self.action_type,
                "Schedule ID": schedule_id if 'schedule_id' in locals() else "Unknown",
                "Schedule Name": self.schedule_names.get(int(schedule_id), "Unknown") if 'schedule_id' in locals() and schedule_id != "none" else "Unknown"
            }
            
            await notify_owner_of_signup_error(
                interaction.client, 
                interaction, 
                e, 
                f"Selected train from dropdown (action: {self.action_type})",
                context
            )
            
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Error: {str(e)}\n\nThe bot owner has been notified.", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error: {str(e)}\n\nThe bot owner has been notified.", ephemeral=True)

WAITLIST_PANEL_JOIN_ID = "waitlist_panel:join"
WAITLIST_PANEL_LEAVE_ID = "waitlist_panel:leave"


def _slot_label(schedule) -> str:
    """Short human label for a slot, e.g. 'Sat 20:00 — Game Lounge'."""
    try:
        days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        when = ""
        if getattr(schedule, 'specific_date', None):
            when = schedule.specific_date.strftime('%d %b')
        elif schedule.day_of_week is not None:
            when = days[schedule.day_of_week]
        if schedule.start_time is not None:
            when = f"{when} {schedule.start_time.strftime('%H:%M')}".strip()
        name = (schedule.name or f"Train #{schedule.id}")
        return f"{when} — {name}".strip(" —")[:100] if when else name[:100]
    except Exception:
        return (getattr(schedule, 'name', None) or f"Train #{schedule.id}")[:100]


# TEMPORARY: hardcoded channel for "someone left a train" alerts, per owner request.
# Not guild-scoped/configurable yet - revisit if this needs to work beyond testing.
LEAVE_ALERT_CHANNEL_ID = 1522543403957616770


def _time_slot_label(schedule) -> str:
    """Human time-slot label for alerts, e.g. 'Saturday 1:00 PM'."""
    try:
        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        if getattr(schedule, 'specific_date', None):
            when = schedule.specific_date.strftime('%d %b')
        elif schedule.day_of_week is not None:
            when = days[schedule.day_of_week]
        else:
            when = ""
        if schedule.start_time is not None:
            time_str = schedule.start_time.strftime('%I:%M %p').lstrip('0')
            when = f"{when} {time_str}".strip()
        return when or (schedule.name or f"Train #{schedule.id}")
    except Exception:
        return (getattr(schedule, 'name', None) or f"Train #{schedule.id}")


async def _notify_train_leave(bot, guild_id: int, schedule_id: int, schedule_name: str, user_display_name: str):
    """Post a short alert when someone leaves a train, via self-service button or
    admin removal. Best-effort only - never raises, so it can't break the leave flow.

    Includes the time slot (not just the train's name) and flags whether a
    replacement may need to be arranged manually, based on whether anyone is
    on the slot waitlist right now. If someone is waitlisted, they're pinged
    to check their DMs (the open-seat detector will offer them the seat within
    ~60s; this ping is just a heads-up in case that offer doesn't land).
    """
    from database import run_db
    from utils.waitlist import first_waitlister, is_recurring_slot

    try:
        def _work(session):
            schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
            if not schedule:
                return {'time_slot': schedule_name, 'waitlister_id': None}
            time_slot = _time_slot_label(schedule)
            waitlister_id = None
            if is_recurring_slot(schedule):
                waiter = first_waitlister(session, schedule_id)
                if waiter:
                    waitlister_id = waiter.user_id
            return {'time_slot': time_slot, 'waitlister_id': waitlister_id}

        info = await run_db(_work)
        time_slot = info['time_slot']
        waitlister_id = info['waitlister_id']

        if waitlister_id:
            tail = (f"<@{waitlister_id}> — check your DMs, this slot just opened up! "
                    f"If the bot doesn't automatically offer it to you shortly, "
                    f"a replacement may need to be arranged manually.")
        else:
            tail = "No one is currently on the waitlist for this slot — a replacement may need to be arranged manually."

        message = f"🚪 **{user_display_name}** has left the **{time_slot}** slot. {tail}"

        channel = bot.get_channel(LEAVE_ALERT_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(LEAVE_ALERT_CHANNEL_ID)
        if channel:
            await channel.send(message)
    except Exception as e:
        logging.getLogger("discord_bot.train_participant_commands").warning(
            f"Failed to post train-leave alert: {e}")


async def _remove_train_participant(guild_id: int, schedule_id: int, user_id: int):
    """Shared, non-blocking removal used by the admin `!removefromtrain` command and
    the DM 'Leave train' button. Returns a dict describing the outcome:
      {'ok': True, 'schedule_name': str} on success
      {'ok': False, 'reason': 'no_schedule' | 'not_signed_up', 'schedule_name': str|None}
    """
    from database import run_db
    from models import TrainNotification

    def _work(session):
        schedule = session.query(TrainSchedule).filter_by(
            id=schedule_id, guild_id=guild_id).first()
        if not schedule:
            return {'ok': False, 'reason': 'no_schedule', 'schedule_name': None}

        participant = session.query(TrainParticipant).filter_by(
            schedule_id=schedule_id, user_id=user_id, is_active=True).first()
        if not participant:
            return {'ok': False, 'reason': 'not_signed_up', 'schedule_name': schedule.name}

        participant.is_active = False

        notifications = session.query(TrainNotification).filter_by(
            schedule_id=schedule_id, primary_user_id=user_id).all()
        for notification in notifications:
            notification.primary_confirmed = False
            notification.primary_user_id = None

        return {'ok': True, 'reason': None, 'schedule_name': schedule.name}

    return await run_db(_work)


class LeaveTrainButton(
    ui.DynamicItem[ui.Button],
    template=r'leave_train:(?P<sid>\d+):(?P<uid>\d+)'
):
    """Restart-safe 'Leave this train' button sent in the DM when an admin manually
    adds someone to a train, so they don't need admin help to back out again.
    """

    def __init__(self, schedule_id: int, user_id: int):
        self.schedule_id = schedule_id
        self.user_id = user_id
        super().__init__(
            ui.Button(
                label="Leave this train",
                style=discord.ButtonStyle.danger,
                emoji="🚪",
                custom_id=f"leave_train:{schedule_id}:{user_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match['sid']), int(match['uid']))

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "This button isn't for you.", ephemeral=True)
            return
        # Defer immediately - DB work must never risk missing Discord's
        # 3-second response window (see discord-interaction-3s-window lesson).
        await interaction.response.defer()
        try:
            # DM interactions have no guild context, so the schedule's own
            # guild_id (encoded via the row we look up) is used for scoping.
            from models import TrainSchedule as _TS
            from database import run_db

            def _find_guild(session):
                s = session.query(_TS).filter_by(id=self.schedule_id).first()
                return s.guild_id if s else None

            guild_id = await run_db(_find_guild)
            if guild_id is None:
                await interaction.edit_original_response(
                    content="ℹ️ That train no longer exists.", view=None)
                return

            result = await _remove_train_participant(guild_id, self.schedule_id, self.user_id)
            if result['ok']:
                invalidate_schedule_cache(guild_id)
                bot = interaction.client
                cog = bot.get_cog('TrainParticipantCommands')
                if cog:
                    try:
                        await cog.trigger_persistent_updates(guild_id)
                    except Exception as e:
                        logging.getLogger("discord_bot.train_participant_commands").warning(
                            f"Failed to refresh displays after DM leave: {e}")
                await _notify_train_leave(
                    bot, guild_id, self.schedule_id, result['schedule_name'],
                    getattr(interaction.user, 'display_name', interaction.user.name)
                )
                await interaction.edit_original_response(
                    content=f"✅ You've left **{result['schedule_name']}**.", view=None)
            elif result['reason'] == 'not_signed_up':
                await interaction.edit_original_response(
                    content=f"ℹ️ You're not currently signed up for **{result['schedule_name']}**.",
                    view=None)
            else:
                await interaction.edit_original_response(
                    content="ℹ️ That train no longer exists.", view=None)
        except Exception as e:
            logging.getLogger("discord_bot.train_participant_commands").error(
                f"Leave-train DM button error: {e}", exc_info=True)
            try:
                await interaction.edit_original_response(
                    content="❌ Something went wrong. Please try again, or ask an admin to remove you.",
                    view=None)
            except Exception:
                pass


def build_leave_train_view(schedule_id: int, user_id: int) -> ui.View:
    view = ui.View(timeout=None)
    view.add_item(LeaveTrainButton(schedule_id, user_id))
    return view


class _WaitlistSlotSelect(ui.View):
    """Ephemeral, short-lived slot picker spawned from the panel buttons.

    Not restart-safe by design: it is created fresh on each button click and the
    selection is acted on immediately, so it never needs to survive a restart.
    """

    def __init__(self, bot, user_id: int, options, action: str):
        super().__init__(timeout=120)
        self.bot = bot
        self.user_id = user_id
        self.action = action  # 'join' or 'leave'
        select = ui.Select(
            placeholder=("Pick a slot to join its waitlist..." if action == 'join'
                         else "Pick a waitlist to leave..."),
            options=options[:25],
        )
        select.callback = self._on_select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def _on_select(self, interaction: discord.Interaction):
        from utils.waitlist import add_to_waitlist, remove_from_waitlist, is_recurring_slot
        from database import run_db
        schedule_id = int(interaction.data['values'][0])
        guild_id = interaction.guild_id
        user_id = interaction.user.id
        user_name = interaction.user.name
        user_display_name = interaction.user.display_name
        action = self.action
        # Defer the edit so the DB work below can never miss Discord's
        # 3-second response window (that produces "interaction failed").
        await interaction.response.defer()
        try:
            def _work(session):
                schedule = session.query(TrainSchedule).filter_by(
                    id=schedule_id, guild_id=guild_id, is_active=True
                ).first()
                if not schedule or not is_recurring_slot(schedule):
                    return {'ok': False, 'msg': "❌ That slot is no longer available."}
                guild_id_for_msg = schedule.guild_id
                schedule_name = schedule.name or f"Train #{schedule_id}"
                if action == 'join':
                    status, position = add_to_waitlist(
                        session, schedule, user_id, user_name, user_display_name)
                    if status == 'active':
                        msg = f"✅ You're already an active rider for **{schedule_name}**."
                    elif status == 'waiting':
                        msg = f"⏳ You're already on the waitlist for **{schedule_name}** (position #{position})."
                    else:
                        msg = f"✅ Added to the waitlist for **{schedule_name}** at position **#{position}**.\nWe'll DM you if a seat opens up."
                else:
                    removed = remove_from_waitlist(session, schedule_id, user_id)
                    msg = (f"✅ You've left the waitlist for **{schedule_name}**." if removed
                           else f"ℹ️ You weren't on the waitlist for **{schedule_name}**.")
                return {'ok': True, 'msg': msg, 'guild_id': guild_id_for_msg}

            result = await run_db(_work)
            await interaction.edit_original_response(content=result['msg'], embed=None, view=None)
            if result['ok']:
                invalidate_schedule_cache(result['guild_id'])
                cog = self.bot.get_cog('TrainParticipantCommands')
                if cog:
                    await cog.trigger_persistent_updates(result['guild_id'])
        except Exception as e:
            logging.getLogger("discord_bot.train_participant_commands").error(
                f"Waitlist slot select error: {e}", exc_info=True)
            try:
                await interaction.edit_original_response(
                    content="❌ Something went wrong. Please try again.", view=None)
            except Exception:
                pass


class _MyWaitlistsView(ui.View):
    """Ephemeral view for /mywaitlists: leave any waitlist, or accept/decline a
    pending open-seat offer without hunting through DMs.

    Accepting and declining both route through the open-seat detector's shared
    code paths (apply_waitlist_accept / apply_waitlist_decline in
    notification_commands), the exact same code the DM 'Accept'/'Decline' buttons
    run. The detector stays the single owner of *offering* seats; accept only
    completes an offer it already extended, and decline only removes the user so
    the detector can re-offer the freed seat to the next person.
    """

    def __init__(self, bot, user_id: int, leave_options, offer_options):
        super().__init__(timeout=120)
        self.bot = bot
        self.user_id = user_id
        self.logger = logging.getLogger("discord_bot.train_participant_commands")

        if offer_options:
            accept = ui.Select(
                placeholder="Accept your open seat...",
                options=offer_options[:25],
            )
            accept.callback = self._on_accept
            self.add_item(accept)

            decline = ui.Select(
                placeholder="Decline a pending seat offer...",
                options=offer_options[:25],
            )
            decline.callback = self._on_decline
            self.add_item(decline)

        leave = ui.Select(
            placeholder="Leave a waitlist...",
            options=leave_options[:25],
        )
        leave.callback = self._on_leave
        self.add_item(leave)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def _refresh_displays(self, guild_id: int):
        invalidate_schedule_cache(guild_id)
        cog = self.bot.get_cog('TrainParticipantCommands')
        if cog:
            try:
                await cog.trigger_persistent_updates(guild_id)
            except Exception as e:
                self.logger.warning(f"Failed to refresh displays after waitlist change: {e}")

    async def _schedule_name(self, schedule_id: int, guild_id: int) -> str:
        from database import run_db

        def _work(session):
            schedule = session.query(TrainSchedule).filter_by(
                id=schedule_id, guild_id=guild_id).first()
            if schedule and schedule.name:
                return schedule.name
            return None

        name = await run_db(_work)
        return name or f"Train #{schedule_id}"

    async def _on_accept(self, interaction: discord.Interaction):
        from cogs.notification_commands import apply_waitlist_accept
        schedule_id = int(interaction.data['values'][0])
        try:
            # apply_waitlist_accept re-checks the offer (front of queue, not
            # expired, seat still open) and, on success, posts the promotion
            # notice and refreshes persistent displays itself — the same side
            # effects the DM Accept button produces.
            status, schedule_name = await apply_waitlist_accept(
                self.bot, schedule_id, interaction.user.id)
            if status == 'accepted':
                msg = (f"🎉 You're in! You've claimed the open seat for "
                       f"**{schedule_name}**.")
            elif status == 'filled':
                msg = (f"😔 That seat for **{schedule_name}** just filled up. "
                       f"You're still on the waitlist if another opens.")
            elif status == 'disabled':
                msg = "⌛ The waitlist feature isn't enabled on this server anymore."
            elif status == 'expired':
                msg = ("⌛ That offer has expired or it's now someone else's turn. "
                       "You're still on the waitlist if a seat opens again.")
            else:  # not_found
                msg = ("ℹ️ That offer is no longer pending — it may have expired or "
                       "moved on. You're still on the waitlist if a seat opens again.")
            await interaction.response.edit_message(content=msg, embed=None, view=None)
        except Exception as e:
            self.logger.error(f"Accept offer error: {e}", exc_info=True)
            try:
                await interaction.response.edit_message(
                    content="❌ Something went wrong. Please try again.", embed=None, view=None)
            except Exception:
                pass

    async def _on_decline(self, interaction: discord.Interaction):
        from cogs.notification_commands import apply_waitlist_decline
        schedule_id = int(interaction.data['values'][0])
        guild_id = interaction.guild_id
        try:
            schedule_name = await self._schedule_name(schedule_id, guild_id)
            status = await apply_waitlist_decline(self.bot, schedule_id, interaction.user.id)
            if status == 'declined':
                msg = (f"❌ You've declined the open seat for **{schedule_name}** and left "
                       f"its waitlist. We'll offer it to the next person in line.")
                await self._refresh_displays(guild_id)
            elif status == 'disabled':
                msg = "⌛ The waitlist feature isn't enabled on this server anymore."
            else:
                msg = ("ℹ️ That offer is no longer pending — it may have expired or moved "
                       "on. You're still on the waitlist if a seat opens again.")
            await interaction.response.edit_message(content=msg, embed=None, view=None)
        except Exception as e:
            self.logger.error(f"Decline offer error: {e}", exc_info=True)
            try:
                await interaction.response.edit_message(
                    content="❌ Something went wrong. Please try again.", embed=None, view=None)
            except Exception:
                pass

    async def _on_leave(self, interaction: discord.Interaction):
        from utils.waitlist import remove_from_waitlist
        from database import run_db
        schedule_id = int(interaction.data['values'][0])
        guild_id = interaction.guild_id
        user_id = interaction.user.id
        # Defer the edit so the DB work below can never miss Discord's
        # 3-second response window (that produces "interaction failed").
        await interaction.response.defer()
        try:
            def _work(session):
                schedule = session.query(TrainSchedule).filter_by(
                    id=schedule_id, guild_id=guild_id).first()
                schedule_name = (schedule.name if schedule and schedule.name
                                 else f"Train #{schedule_id}")
                removed = remove_from_waitlist(session, schedule_id, user_id)
                return schedule_name, removed

            schedule_name, removed = await run_db(_work)
            msg = (f"✅ You've left the waitlist for **{schedule_name}**." if removed
                   else f"ℹ️ You weren't on the waitlist for **{schedule_name}**.")
            if removed:
                await self._refresh_displays(guild_id)
            await interaction.edit_original_response(content=msg, embed=None, view=None)
        except Exception as e:
            self.logger.error(f"Leave waitlist error: {e}", exc_info=True)
            try:
                await interaction.edit_original_response(
                    content="❌ Something went wrong. Please try again.", embed=None, view=None)
            except Exception:
                pass


class WaitlistPanelView(ui.View):
    """Restart-safe panel with Join/Leave buttons (fixed custom_ids).

    Registered once via bot.add_view() in setup_hook so the buttons keep working
    after a restart. Each click opens an ephemeral slot picker.
    """

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    async def _waitlist_enabled(self, guild_id: int) -> bool:
        perms = getattr(self.bot, 'permissions_manager', None)
        return bool(perms and guild_id and await perms.is_feature_enabled(guild_id, 'waitlist'))

    async def _open_picker(self, interaction: discord.Interaction, action: str):
        # Defer immediately - the DB work below must never risk missing
        # Discord's 3-second initial-response window (that produces the
        # "Unknown interaction" / "interaction failed" error users see).
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not interaction.guild_id or not await self._waitlist_enabled(interaction.guild_id):
            await interaction.followup.send(
                "❌ The waitlist feature isn't enabled on this server.", ephemeral=True)
            return
        try:
            from utils.waitlist import waitlisted_rows, recurring_schedules_query
            from database import run_db

            guild_id = interaction.guild_id
            user_id = interaction.user.id

            def _work(session):
                options = []
                schedules = recurring_schedules_query(
                    session, guild_id
                ).order_by(
                    TrainSchedule.day_of_week.asc(), TrainSchedule.start_time.asc()
                ).all()
                if action == 'leave':
                    waiting_ids = {
                        r.schedule_id for s in schedules
                        for r in waitlisted_rows(session, s.id)
                        if r.user_id == user_id
                    }
                    schedules = [s for s in schedules if s.id in waiting_ids]
                for s in schedules[:25]:
                    options.append(discord.SelectOption(
                        label=_slot_label(s)[:100], value=str(s.id)))
                return options

            options = await run_db(_work)
            if not options:
                txt = ("ℹ️ There are no active slots to join right now." if action == 'join'
                       else "ℹ️ You're not on any waitlists right now.")
                await interaction.followup.send(txt, ephemeral=True)
                return
            view = _WaitlistSlotSelect(self.bot, interaction.user.id, options, action)
            await interaction.followup.send(
                ("Pick a slot to join its waitlist:" if action == 'join'
                 else "Pick a waitlist to leave:"),
                view=view, ephemeral=True)
        except Exception as e:
            logging.getLogger("discord_bot.train_participant_commands").error(
                f"Waitlist panel picker error: {e}", exc_info=True)
            try:
                await interaction.followup.send(
                    "❌ Something went wrong. Please try again.", ephemeral=True)
            except Exception:
                pass

    @ui.button(label="Join a waitlist", style=discord.ButtonStyle.success,
               emoji="➕", custom_id=WAITLIST_PANEL_JOIN_ID)
    async def join_button(self, interaction: discord.Interaction, button: ui.Button):
        await self._open_picker(interaction, 'join')

    @ui.button(label="Leave a waitlist", style=discord.ButtonStyle.secondary,
               emoji="➖", custom_id=WAITLIST_PANEL_LEAVE_ID)
    async def leave_button(self, interaction: discord.Interaction, button: ui.Button):
        await self._open_picker(interaction, 'leave')


class TrainParticipantCommands(commands.Cog):
    """Commands for managing train participants."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("discord_bot.train_participant_commands")

    async def respond(self, interaction, *args, **kwargs):
        """Helper method to safely respond to an interaction."""
        if interaction.response.is_done():
            if 'view' in kwargs and kwargs['view'] is None:
                del kwargs['view']
            return await interaction.followup.send(*args, **kwargs)
        return await interaction.response.send_message(*args, **kwargs)

    async def send_train_signup_dm(self, user: discord.User, schedule, twitch_username: str = None, linked: bool = False):
        """Send DM to user when they sign up for a train, including Twitch linking info."""
        try:
            if linked and twitch_username:
                # User already has Twitch linked
                embed = discord.Embed(
                    title="🚂 Train Signup Confirmation",
                    description=f"You've successfully signed up for **{schedule.name}**!",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="🎯 Train Details",
                    value=f"**Train:** {schedule.name}\n**Duration:** {schedule.duration_minutes} minutes\n**Max Participants:** {schedule.max_participants or 'Unlimited'}",
                    inline=False
                )
                
                embed.add_field(
                    name="🎮 Your Linked Twitch Account",
                    value=f"**Username:** `{twitch_username}`\n✅ Your attendance will be tracked automatically!",
                    inline=False
                )
                
                embed.add_field(
                    name="📋 What's Next",
                    value="• You'll receive notifications before the train starts\n• Your attendance will be automatically tracked\n• Join the stream when the time comes!",
                    inline=False
                )
                
            else:
                # User doesn't have Twitch linked - send the linkusertwitch-style DM
                embed = discord.Embed(
                    title="🚂 Train Signup & 🔗 Twitch Account Linking",
                    description=f"You've signed up for **{schedule.name}**! To get the most out of our raid train system, consider linking your Twitch account for automatic attendance tracking.",
                    color=0x9146ff,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="🎯 Train Details",
                    value=f"**Train:** {schedule.name}\n**Duration:** {schedule.duration_minutes} minutes\n**Max Participants:** {schedule.max_participants or 'Unlimited'}",
                    inline=False
                )
                
                embed.add_field(
                    name="🔗 Link Your Twitch Account (Optional)",
                    value="**What This Means:**\n• Your attendance for raid trains will be tracked automatically\n• You'll appear in raid train notifications with your Twitch username\n• Admins can see your participation in train statistics\n• Your Discord and Twitch accounts will be linked in our system",
                    inline=False
                )
                
                embed.add_field(
                    name="🔒 Your Privacy",
                    value="• Only basic linking information is stored\n• You can skip linking if you prefer to keep accounts separate\n• This won't affect your ability to participate in Discord activities\n• You can request unlinking at any time",
                    inline=False
                )
                
                embed.add_field(
                    name="📝 How to Link Your Twitch",
                    value="If you'd like to link your Twitch account, please **reply to this DM** with just your Twitch username (example: `twitchuser123`).\n\n*Or you can skip this and participate without linking.*",
                    inline=False
                )
            
            embed.add_field(
                name="⏰ Train Reminders",
                value="You'll receive notifications before your train starts. Be ready to stream or participate!",
                inline=False
            )
            
            embed.set_footer(text="This is an automated message from the raid train system")
            
            await user.send(embed=embed)
            self.logger.info(f"Sent train signup DM to {user.name} (Twitch linked: {linked})")
            
        except discord.Forbidden:
            self.logger.warning(f"Could not send DM to {user.name} - DMs disabled")
        except Exception as e:
            self.logger.error(f"Error sending train signup DM to {user.name}: {e}")

    async def send_admin_added_dm(self, user: discord.User, schedule, added_by: discord.abc.User,
                                   twitch_username: str = None):
        """DM someone when an admin/trusted user manually adds them to a train,
        with a restart-safe 'Leave this train' button so they can back out
        themselves without needing to find an admin.
        """
        try:
            embed = discord.Embed(
                title="🚂 You've Been Added to a Train",
                description=f"**{added_by.display_name}** added you to **{schedule.name}**.",
                color=0x00b0f4,
                timestamp=datetime.utcnow()
            )

            embed.add_field(
                name="🎯 Train Details",
                value=(f"**Train:** {schedule.name}\n"
                       f"**Duration:** {schedule.duration_minutes} minutes\n"
                       f"**Max Participants:** {schedule.max_participants or 'Unlimited'}"),
                inline=False
            )

            if twitch_username:
                embed.add_field(
                    name="🎮 Your Linked Twitch Account",
                    value=f"**Username:** `{twitch_username}`\n✅ Your attendance will be tracked automatically!",
                    inline=False
                )
            else:
                embed.add_field(
                    name="🎮 Twitch Account",
                    value="Not linked — link with `/linktwitch` for automatic attendance tracking.",
                    inline=False
                )

            embed.add_field(
                name="🚪 Can't make it?",
                value="Use the button below to leave this train yourself, any time.",
                inline=False
            )

            embed.set_footer(text="This is an automated message from the raid train system")

            view = build_leave_train_view(schedule.id, user.id)
            await user.send(embed=embed, view=view)
            self.logger.info(f"Sent admin-added DM to {user.name} for train {schedule.name}")

        except discord.Forbidden:
            self.logger.warning(f"Could not send admin-added DM to {user.name} - DMs disabled")
        except Exception as e:
            self.logger.error(f"Error sending admin-added DM to {user.name}: {e}")

    async def post_attendance_notification(self, guild, action, data):
        """Post attendance notification to configured channel."""
        try:
            # Only send notifications for joins, not leaves
            if action != "join":
                return
                
            from models import NotificationSettings
            
            # Get attendance channel configuration
            with DatabaseSession() as session:
                settings = session.query(NotificationSettings).filter(
                    NotificationSettings.guild_id == guild.id
                ).first()
                
                if not settings or not settings.attendance_channel_id:
                    # No attendance channel configured, skip notification
                    return
                
                # Get the attendance channel
                channel = guild.get_channel(settings.attendance_channel_id)
                if not channel:
                    self.logger.warning(f"Attendance channel {settings.attendance_channel_id} not found in {guild.name}")
                    return
                
                # Check bot permissions
                bot_member = guild.get_member(self.bot.user.id)
                if not bot_member:
                    return
                    
                channel_perms = channel.permissions_for(bot_member)
                if not (channel_perms.send_messages and channel_perms.embed_links):
                    self.logger.warning(f"Insufficient permissions in attendance channel {channel.name} ({guild.name})")
                    return
                
                # Format the notification embed
                user = data['user']
                schedule = data['schedule']
                twitch_username = data.get('twitch_username')
                
                if action == "join":
                    embed = discord.Embed(
                        title="🚂 Train Joined",
                        color=0x00ff00,
                        timestamp=datetime.utcnow()
                    )
                    
                    embed.add_field(
                        name="👤 Participant",
                        value=f"{user.mention} ({user.display_name})",
                        inline=True
                    )
                    
                    if twitch_username:
                        embed.add_field(
                            name="🎮 Twitch",
                            value=f"**{twitch_username}**",
                            inline=True
                        )
                    else:
                        embed.add_field(
                            name="🎮 Twitch",
                            value="*Not linked*",
                            inline=True
                        )
                    
                    embed.add_field(
                        name="🚂 Train",
                        value=f"**{schedule.name}** (#{schedule.id})",
                        inline=True
                    )
                    
                    participant_count = data.get('participant_count', '?')
                    max_participants = data.get('max_participants')
                    if max_participants:
                        embed.add_field(
                            name="👥 Total Participants",
                            value=f"{participant_count}/{max_participants}",
                            inline=True
                        )
                    else:
                        embed.add_field(
                            name="👥 Total Participants",
                            value=f"{participant_count}",
                            inline=True
                        )
                    
                    # Format train details
                    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                    day_name = days[schedule.day_of_week]
                    import pytz as _pytz_tp1
                    _uk1 = _pytz_tp1.timezone('Europe/London')
                    _uk1_dt = _uk1.localize(datetime.combine(datetime.today(), schedule.start_time))
                    start_time = _uk1_dt.strftime('%I:%M %p %Z').lstrip('0')
                    
                    embed.add_field(
                        name="📅 Train Time",
                        value=f"{day_name} at {start_time}",
                        inline=True
                    )
                    
                    if data.get('notes'):
                        embed.add_field(
                            name="📝 Notes",
                            value=data['notes'][:100] + ("..." if len(data['notes']) > 100 else ""),
                            inline=False
                        )
                        
                elif action == "leave":
                    embed = discord.Embed(
                        title="🚪 Train Left",
                        color=0xff9900,
                        timestamp=datetime.utcnow()
                    )
                    
                    embed.add_field(
                        name="👤 Participant",
                        value=f"{user.mention} ({user.display_name})",
                        inline=True
                    )
                    
                    if twitch_username:
                        embed.add_field(
                            name="🎮 Twitch",
                            value=f"**{twitch_username}**",
                            inline=True
                        )
                    else:
                        embed.add_field(
                            name="🎮 Twitch",
                            value="*Not linked*",
                            inline=True
                        )
                    
                    embed.add_field(
                        name="🚂 Train",
                        value=f"**{schedule.name}** (#{schedule.id})",
                        inline=True
                    )
                    
                    participant_count = data.get('participant_count', '?')
                    embed.add_field(
                        name="👥 Remaining Participants",
                        value=f"{participant_count}",
                        inline=True
                    )
                
                # Send the notification
                await channel.send(embed=embed)
                self.logger.info(f"Posted {action} attendance notification for {user} in {guild.name}")
                
        except Exception as e:
            self.logger.error(f"Failed to post attendance notification: {e}")
            # Don't raise the error - attendance notifications are supplementary

    def is_admin_or_trusted():
        """Check if user has manage_guild permission OR is a trusted user."""
        async def predicate(ctx):
            # Check manage_guild permission first
            if ctx.author.guild_permissions.manage_guild:
                return True
            # Check if user is trusted, scoped to the current guild so that
            # the guild-level trusted_access_enabled flag is enforced.
            guild_id = ctx.guild.id if ctx.guild else None
            return await ctx.bot.is_trusted_user(ctx.author.id, guild_id)
        return commands.check(predicate)

    async def _join_train_internal(self, interaction: discord.Interaction, schedule_id: int, notes: str = "", from_modal: bool = False, from_dropdown: bool = False, send_public: bool = True, parent_dropdown=None):
        """Internal method to join a train session by schedule ID.
        
        Args:
            send_public: Whether to send the success message publicly (False for modal flow where interaction is already ephemeral)
            parent_dropdown: Reference to the TrainSelectionDropdown view to refresh after join
        """
        
        # Defer immediately to prevent timeout (unless we need to show modal)
        if not from_modal and not from_dropdown and not interaction.response.is_done():
            await interaction.response.defer(ephemeral=False)
        
        try:
            # Check if we need to show a modal (only for /jointrain command, not dropdown)
            need_modal = False
            if not from_modal and not from_dropdown:
                # Quick check if user has Twitch linked (offload to thread to prevent timeout)
                def check_twitch_link():
                    with DatabaseSession() as session:
                        user_record = session.query(User).filter(
                            User.id == interaction.user.id,
                            User.guild_id == interaction.guild.id
                        ).first()
                        return not (user_record and user_record.twitch_login)
                
                need_modal = await asyncio.to_thread(check_twitch_link)
            
            # If we need modal, show it (only for /jointrain, dropdown skips this)
            if need_modal:
                self.logger.info(f"No Twitch linked for {interaction.user} - showing modal")
                with DatabaseSession() as session:
                    schedule = session.query(TrainSchedule).filter_by(
                        id=schedule_id,
                        guild_id=interaction.guild.id,
                        is_active=True
                    ).first()
                    if not schedule:
                        await self.respond(interaction, f"❌ Train schedule #{schedule_id} not found or not active.", ephemeral=True)
                        return
                    modal = TwitchLinkModal(schedule_id, schedule.name, self)
                    await interaction.response.send_modal(modal)
                return
            
            # If we don't need modal, defer now to prevent timeout
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=False)
            
            with DatabaseSession() as session:
                # Check if schedule exists and is active
                schedule = session.query(TrainSchedule).filter_by(
                    id=schedule_id,
                    guild_id=interaction.guild.id,
                    is_active=True
                ).first()
                
                if not schedule:
                    await self.respond(interaction, f"❌ Train schedule #{schedule_id} not found or not active.", ephemeral=True)
                    return
                
                # Check if user is already signed up
                existing = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule_id,
                    user_id=interaction.user.id,
                    is_active=True
                ).first()
                
                if existing:
                    await self.respond(interaction, f"⚠️ You're already signed up for **{schedule.name}**!", ephemeral=True)
                    return
                
                # Beta: signup waitlist (per-guild flag, default OFF)
                perms = getattr(self.bot, 'permissions_manager', None)
                waitlist_on = bool(perms and await perms.is_feature_enabled(interaction.guild.id, 'waitlist'))
                joining_waitlist = False
                waitlist_position = None
                
                # If waitlist is on, block double-joining the waitlist too
                if waitlist_on:
                    existing_wait = session.query(TrainParticipant).filter_by(
                        schedule_id=schedule_id,
                        user_id=interaction.user.id,
                        is_waitlisted=True,
                        is_active=False
                    ).first()
                    if existing_wait:
                        pos = getattr(existing_wait, 'waitlist_position', None)
                        pos_text = f" (position #{pos})" if pos else ""
                        await self.respond(interaction, f"⏳ You're already on the waitlist for **{schedule.name}**{pos_text}.", ephemeral=True)
                        return
                
                # Check participant limit
                if schedule.max_participants:
                    current_count = session.query(TrainParticipant).filter_by(
                        schedule_id=schedule_id,
                        is_active=True
                    ).count()
                    
                    if current_count >= schedule.max_participants:
                        if waitlist_on:
                            joining_waitlist = True
                            # Use max(position)+1 rather than count+1 so positions stay
                            # unique even after earlier waitlisters are promoted/leave
                            # (which would otherwise let count+1 collide with an
                            # existing position number).
                            from sqlalchemy import func as _wl_func
                            max_pos = session.query(
                                _wl_func.max(TrainParticipant.waitlist_position)
                            ).filter_by(
                                schedule_id=schedule_id,
                                is_waitlisted=True,
                                is_active=False
                            ).scalar()
                            waitlist_position = (max_pos or 0) + 1
                        else:
                            await self.respond(interaction, f"❌ **{schedule.name}** is full! ({current_count}/{schedule.max_participants} participants)", ephemeral=True)
                            return
                
                # Get Twitch username (we already checked above)
                twitch_username = None
                twitch_link_prompt = ""
                
                try:
                    user_record = session.query(User).filter(
                        User.id == interaction.user.id,
                        User.guild_id == interaction.guild.id
                    ).first()
                    
                    if user_record and user_record.twitch_login:
                        twitch_username = user_record.twitch_login
                        self.logger.info(f"Found linked Twitch account for {interaction.user}: {twitch_username}")
                        if not joining_waitlist:
                            await self.send_train_signup_dm(interaction.user, schedule, twitch_username, linked=True)
                    else:
                        # User joined without linking (came from modal's "No" button)
                        twitch_link_prompt = "\n🔗 **Tip:** Link your Twitch account with `/linktwitch` for automatic attendance tracking!"
                        self.logger.info(f"User {interaction.user} joined without linking Twitch")
                        if not joining_waitlist:
                            await self.send_train_signup_dm(interaction.user, schedule, None, linked=False)
                        
                except Exception as e:
                    self.logger.warning(f"Error checking Twitch link for {interaction.user}: {e}")
                    # Continue without Twitch info if lookup fails

                # Add participant (waitlisted rows are is_active=False so existing
                # count/roster queries naturally exclude them)
                participant = TrainParticipant(
                    schedule_id=schedule_id,
                    guild_id=interaction.guild.id,
                    user_id=interaction.user.id,
                    username=interaction.user.name,
                    display_name=interaction.user.display_name,
                    notes=notes if notes else None,
                    signed_up_at=datetime.utcnow(),
                    is_active=(not joining_waitlist),
                    is_waitlisted=joining_waitlist,
                    waitlist_position=waitlist_position,
                    twitch_username=twitch_username  # Automatically populated if linked
                )
                
                session.add(participant)
                session.commit()
                
                # Invalidate cache since participant data changed
                invalidate_schedule_cache(schedule.guild_id)
                
                # Waitlist path: notify the user and stop (they are not on the active roster)
                if joining_waitlist:
                    wl_embed = discord.Embed(
                        title="⏳ Added to Waitlist",
                        description=(
                            f"**{schedule.name}** is full, so you've been added to the "
                            f"waitlist at **position #{waitlist_position}**.\n"
                            f"If a seat opens up, we'll DM you an offer — accept it to take the spot."
                        ),
                        color=0xffaa00,
                        timestamp=datetime.utcnow()
                    )
                    wl_embed.set_footer(text=f"Use /leavetrain {schedule_id} to leave the waitlist")
                    try:
                        if interaction.response.is_done():
                            await interaction.followup.send(embed=wl_embed, ephemeral=True)
                        else:
                            await interaction.response.send_message(embed=wl_embed, ephemeral=True)
                    except Exception as e:
                        self.logger.warning(f"Failed to send waitlist confirmation: {e}")
                    self.logger.info(f"User {interaction.user} waitlisted for {schedule.name} at position {waitlist_position}")
                    # Refresh any live displays so counts/states stay accurate
                    if parent_dropdown:
                        try:
                            await parent_dropdown.refresh_dropdown(self.bot)
                        except Exception as e:
                            self.logger.warning(f"Failed to refresh dropdown: {e}")
                    try:
                        await self._update_all_persistent_displays()
                    except Exception as e:
                        self.logger.warning(f"Failed to refresh persistent displays: {e}")
                    return
                
                # Get current participant count
                new_count = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule_id,
                    is_active=True
                ).count()
                
                embed = discord.Embed(
                    title="🚂 Joined Train!",
                    description=f"Successfully joined **{schedule.name}**{twitch_link_prompt}",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                # Format schedule details
                days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                day_name = days[schedule.day_of_week]
                
                # start_time stored as UK wall clock — localize directly for display
                import pytz
                from datetime import date
                eastern_tz = pytz.timezone('Europe/London')
                uk_datetime = datetime.combine(date.today(), schedule.start_time)
                est_datetime = eastern_tz.localize(uk_datetime)
                start_time = est_datetime.strftime(f'%I:%M %p {est_datetime.strftime("%Z")}')
                
                embed.add_field(
                    name="📅 Train Details",
                    value=f"**Day:** {day_name}\n**Time:** {start_time}\n**Duration:** {schedule.duration_minutes} min",
                    inline=True
                )
                
                max_text = f"/{schedule.max_participants}" if schedule.max_participants else ""
                embed.add_field(
                    name="👥 Participants",
                    value=f"**{new_count}{max_text}** signed up",
                    inline=True
                )
                
                # Show Twitch info if available
                if twitch_username:
                    embed.add_field(
                        name="🎮 Twitch Account",
                        value=f"✅ **{twitch_username}**\nAttendance tracking enabled!",
                        inline=True
                    )
                else:
                    embed.add_field(
                        name="🎮 Twitch Account",
                        value="❌ Not linked\nUse `/linktwitch` to enable tracking",
                        inline=True
                    )
                
                if notes:
                    embed.add_field(
                        name="📝 Your Notes",
                        value=notes,
                        inline=False
                    )
                
                embed.set_footer(text=f"Use /leavetrain {schedule_id} to leave this train")
                
                # Send success confirmation (public or ephemeral based on context)
                if send_public:
                    # For public messages, check if we can use interaction or need channel.send
                    if interaction.response.is_done():
                        # If deferred as ephemeral (from modal), use channel.send for public message
                        await interaction.channel.send(embed=embed)
                        # Also send ephemeral confirmation
                        await interaction.followup.send("✅ Successfully joined the train!", ephemeral=True)
                    else:
                        await interaction.response.send_message(embed=embed, ephemeral=False)
                else:
                    # Ephemeral message (for modal flow)
                    if interaction.response.is_done():
                        await interaction.followup.send(embed=embed, ephemeral=True)
                    else:
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                self.logger.info(f"User {interaction.user} joined train {schedule.name} (ID: {schedule_id})")
                
                # Post attendance notification if configured
                await self.post_attendance_notification(interaction.guild, "join", {
                    'user': interaction.user,
                    'schedule': schedule,
                    'twitch_username': twitch_username,
                    'participant_count': new_count,
                    'max_participants': schedule.max_participants,
                    'notes': notes
                })
                
                # Send Twitch chat notification if enabled
                try:
                    from utils.twitch_notifications import notify_twitch_participant_joined
                    await notify_twitch_participant_joined(
                        self.bot,
                        interaction.user.display_name,
                        twitch_username or interaction.user.display_name,
                        schedule.name,
                        new_count
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to send Twitch join notification: {e}")
                
                # Auto-update dropdown menu if it exists
                if parent_dropdown:
                    try:
                        await parent_dropdown.refresh_dropdown(self.bot)
                        self.logger.info("✅ Dropdown menu refreshed with updated counts")
                    except Exception as e:
                        self.logger.warning(f"Failed to refresh dropdown: {e}")
                
                # Trigger persistent display update
                try:
                    await self._update_all_persistent_displays()
                    self.logger.info("✅ Persistent displays refreshed")
                except Exception as e:
                    self.logger.warning(f"Failed to refresh persistent displays: {e}")
                
        except Exception as e:
            self.logger.error(f"Error joining train: {e}")
            await self.respond(interaction, f"❌ Failed to join train: {str(e)}", ephemeral=True)


    def _renumber_waitlist(self, session, schedule_id: int):
        """Re-index remaining waitlisters to a contiguous 1..N sequence.

        Called after a promotion or a waitlist-leave so positions don't drift
        into gaps (e.g. 3, 5, 8) that confuse users. Ordered by the current
        waitlist_position (falling back to signup time) to preserve queue order.
        Caller is responsible for committing the session.
        """
        from utils.waitlist import renumber_waitlist
        renumber_waitlist(session, schedule_id)

    async def _leave_train_internal(self, interaction: discord.Interaction, schedule_id: int):
        """Internal method to leave a train session."""
        try:
            with DatabaseSession() as session:
                # Find the user's participation
                participant = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule_id,
                    user_id=interaction.user.id,
                    is_active=True
                ).first()
                
                # Beta: allow leaving the waitlist too (waitlisted rows are is_active=False).
                # Only touch the beta columns when the waitlist feature is enabled, so the
                # normal leave path is unchanged (and never references beta columns) when OFF.
                left_waitlist = False
                perms = getattr(self.bot, 'permissions_manager', None)
                waitlist_on = bool(perms and await perms.is_feature_enabled(interaction.guild.id, 'waitlist'))
                if not participant and waitlist_on:
                    wl_entry = session.query(TrainParticipant).filter_by(
                        schedule_id=schedule_id,
                        user_id=interaction.user.id,
                        is_waitlisted=True,
                        is_active=False
                    ).first()
                    if wl_entry:
                        wl_entry.is_waitlisted = False
                        wl_entry.waitlist_position = None
                        # Re-index remaining waitlisters so positions stay 1..N
                        self._renumber_waitlist(session, schedule_id)
                        session.commit()
                        invalidate_schedule_cache(wl_entry.guild_id)
                        await self.respond(interaction, f"✅ You've left the waitlist for train #{schedule_id}.", ephemeral=True)
                        left_waitlist = True
                
                if not participant:
                    if not left_waitlist:
                        await self.respond(interaction, f"❌ You're not signed up for train schedule #{schedule_id}.", ephemeral=True)
                    return
                
                # Get schedule info before removing
                schedule = session.query(TrainSchedule).filter_by(id=schedule_id).first()
                
                # Remove participant
                participant.is_active = False
                
                # Clean up any notification records for this user to prevent ghost pings
                from models import TrainNotification
                notifications = session.query(TrainNotification).filter_by(
                    schedule_id=schedule_id,
                    primary_user_id=participant.user_id
                ).all()
                for notification in notifications:
                    notification.primary_confirmed = False
                    notification.primary_user_id = None
                
                # Beta (waitlist): the freed seat is intentionally left open here.
                # The open-seat detector (NotificationCommands.check_waitlist_offers)
                # owns all waitlist promotions via the DM Accept/Decline offer flow,
                # so seats are only filled with the next rider's explicit consent.
                
                session.commit()
                
                # Invalidate cache since participant data changed
                if schedule:
                    invalidate_schedule_cache(schedule.guild_id)
                
                embed = discord.Embed(
                    title="🚪 Left Train",
                    description=f"You've left **{schedule.name if schedule else f'Train #{schedule_id}'}**",
                    color=0xff9900,
                    timestamp=datetime.utcnow()
                )
                
                embed.set_footer(text=f"Use /jointrain {schedule_id} to rejoin")
                
                # Send success confirmation to channel (not ephemeral)
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=False)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=False)
                self.logger.info(f"User {interaction.user} left train {schedule.name if schedule else schedule_id}")
                
                # Get updated participant count
                new_count = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule_id,
                    is_active=True
                ).count()
                
                # Post leave attendance notification if configured
                await self.post_attendance_notification(interaction.guild, "leave", {
                    'user': interaction.user,
                    'schedule': schedule,
                    'twitch_username': participant.twitch_username,
                    'participant_count': new_count
                })
                
                # Send Twitch chat notification if enabled
                try:
                    from utils.twitch_notifications import notify_twitch_participant_left
                    # Get user's Twitch username
                    user_record = session.query(User).filter_by(
                        id=interaction.user.id,
                        guild_id=interaction.guild.id
                    ).first()
                    twitch_username = user_record.twitch_login if user_record else None
                    
                    await notify_twitch_participant_left(
                        self.bot,
                        interaction.user.display_name,
                        twitch_username or interaction.user.display_name,
                        schedule.name if schedule else f"Train #{schedule_id}",
                        new_count
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to send Twitch leave notification: {e}")
                
        except Exception as e:
            self.logger.error(f"Error leaving train: {e}")
            await self.respond(interaction, f"❌ Failed to leave train: {str(e)}", ephemeral=True)





    @staticmethod
    def _compute_attendance_streaks(server_dates, attended_dates):
        """Return (current_streak, longest_streak) of attended sessions.

        server_dates: ascending list of every distinct session date the server held.
        attended_dates: set of dates this user was present.
        Current streak = the unbroken run ending at the most recent session.
        Longest streak = the best run anywhere in history.
        """
        longest_streak = 0
        run = 0
        for d in server_dates:
            if d in attended_dates:
                run += 1
                longest_streak = max(longest_streak, run)
            else:
                run = 0
        current_streak = 0
        for d in reversed(server_dates):
            if d in attended_dates:
                current_streak += 1
            else:
                break
        return current_streak, longest_streak

    @app_commands.command(name='mywaitlists', description='[Beta] See the slot waitlists you are on and leave any you no longer want')
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: (i.guild_id, i.user.id))
    async def my_waitlists(self, interaction: discord.Interaction):
        """List the slots the caller is waiting on (with position) and let them leave any."""
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        perms = getattr(self.bot, 'permissions_manager', None)
        if perms and not await perms.is_feature_enabled(interaction.guild.id, 'waitlist'):
            await interaction.response.send_message("❌ The waitlist feature isn't enabled on this server.", ephemeral=True)
            return

        try:
            from utils.waitlist import (waitlisted_rows, recurring_schedules_query,
                                        OFFER_TIMEOUT_MINUTES)

            leave_options = []
            offer_options = []
            lines = []
            now = datetime.utcnow()
            with DatabaseSession() as session:
                schedules = recurring_schedules_query(
                    session, interaction.guild.id
                ).order_by(
                    TrainSchedule.day_of_week.asc(), TrainSchedule.start_time.asc()
                ).all()
                for s in schedules:
                    for r in waitlisted_rows(session, s.id):
                        if r.user_id == interaction.user.id:
                            label = _slot_label(s)
                            pos = r.waitlist_position
                            pos_text = f"position **#{pos}**" if pos else "queued"
                            # A pending offer is set only on the front-of-queue row and
                            # expires after OFFER_TIMEOUT_MINUTES.
                            pending_mins = None
                            if r.offer_sent_at:
                                remaining = (OFFER_TIMEOUT_MINUTES * 60
                                             - (now - r.offer_sent_at).total_seconds())
                                if remaining > 0:
                                    pending_mins = max(1, int((remaining + 59) // 60))
                            if pending_mins is not None:
                                lines.append(
                                    f"• {label} — 🎟️ **Pending offer!** ~{pending_mins} min "
                                    f"left (accept or decline below, or use your DMs)")
                                offer_options.append(discord.SelectOption(
                                    label=label[:100], value=str(s.id),
                                    description=f"~{pending_mins} min left to decide"[:100]))
                            else:
                                lines.append(f"• {label} — {pos_text}")
                            leave_options.append(discord.SelectOption(
                                label=label[:100], value=str(s.id)))
                            break

            if not lines:
                await interaction.response.send_message(
                    "ℹ️ You're not on any waitlists right now.", ephemeral=True)
                return

            embed = discord.Embed(
                title="⏳ Your Slot Waitlists",
                description="\n".join(lines[:25]),
                color=0x667eea,
                timestamp=datetime.utcnow()
            )
            if offer_options:
                embed.set_footer(
                    text="You have a pending seat offer — accept it below to claim "
                         "your seat, or decline it to pass it on.")
            else:
                embed.set_footer(text="Pick a slot below to leave its waitlist")
            view = _MyWaitlistsView(
                self.bot, interaction.user.id, leave_options, offer_options)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            self.logger.error(f"Error in mywaitlists: {e}", exc_info=True)
            await self.respond(interaction, f"❌ Failed to load your waitlists: {str(e)}", ephemeral=True)

    @app_commands.command(name='trainstats', description='[Beta] Your train attendance stats and the server leaderboard')
    @app_commands.checks.cooldown(1, 15.0, key=lambda i: (i.guild_id, i.user.id))
    async def train_stats(self, interaction: discord.Interaction):
        """Show the caller's attendance count plus a top-10 server leaderboard."""
        if not interaction.guild:
            await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
            return

        perms = getattr(self.bot, 'permissions_manager', None)
        if perms and not await perms.is_feature_enabled(interaction.guild.id, 'stats'):
            await interaction.response.send_message("❌ Stats & leaderboard aren't enabled in this server yet.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            from models import TwitchChatAttendance
            from sqlalchemy import func
            guild_id = interaction.guild.id
            user_id = interaction.user.id

            with DatabaseSession() as session:
                # Per-user total attended sessions (present in chat)
                my_count = session.query(TwitchChatAttendance).filter_by(
                    guild_id=guild_id,
                    user_id=user_id,
                    was_present_in_chat=True
                ).count()

                # Streak: walk the server's distinct train dates and measure how many
                # consecutive sessions this user attended. "Current" = the unbroken run
                # ending at the most recent session; "longest" = best run ever.
                server_dates = [
                    r[0] for r in session.query(TwitchChatAttendance.train_date)
                    .filter_by(guild_id=guild_id)
                    .filter(TwitchChatAttendance.train_date.isnot(None))
                    .distinct()
                    .order_by(TwitchChatAttendance.train_date.asc())
                    .all()
                ]
                attended_dates = {
                    r[0] for r in session.query(TwitchChatAttendance.train_date)
                    .filter_by(guild_id=guild_id, user_id=user_id, was_present_in_chat=True)
                    .filter(TwitchChatAttendance.train_date.isnot(None))
                    .all()
                }

                current_streak, longest_streak = self._compute_attendance_streaks(
                    server_dates, attended_dates
                )

                # Leaderboard: top 10 by attended sessions
                rows = session.query(
                    TwitchChatAttendance.user_id,
                    func.count(TwitchChatAttendance.id).label('attended')
                ).filter_by(
                    guild_id=guild_id,
                    was_present_in_chat=True
                ).group_by(
                    TwitchChatAttendance.user_id
                ).order_by(
                    func.count(TwitchChatAttendance.id).desc()
                ).limit(10).all()

            embed = discord.Embed(
                title="🏅 Train Attendance Stats",
                description=(
                    f"{interaction.user.mention}, you've attended **{my_count}** train session(s).\n"
                    f"🔥 Current streak: **{current_streak}** • 🏆 Longest streak: **{longest_streak}**"
                ),
                color=0x667eea,
                timestamp=datetime.utcnow()
            )

            if rows:
                medals = ['🥇', '🥈', '🥉']
                lines = []
                for idx, (uid, attended) in enumerate(rows):
                    member = interaction.guild.get_member(uid)
                    name = member.display_name if member else f"User {uid}"
                    rank = medals[idx] if idx < 3 else f"`#{idx + 1}`"
                    lines.append(f"{rank} **{name}** — {attended}")
                embed.add_field(name="Top Attendees", value="\n".join(lines), inline=False)
            else:
                embed.add_field(name="Top Attendees", value="No attendance recorded yet.", inline=False)

            embed.set_footer(text="Attendance is tracked via Twitch chat presence during trains")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            self.logger.error(f"Error in trainstats: {e}", exc_info=True)
            await self.respond(interaction, f"❌ Failed to load stats: {str(e)}", ephemeral=True)

    @app_commands.command(name='trainroster', description='Show participants for a train session')
    @app_commands.checks.cooldown(1, 30.0, key=lambda i: (i.guild_id, i.user.id))
    async def train_roster(self, interaction: discord.Interaction, schedule_id: int):
        """Show who's signed up for a specific train."""
        
        # Defer response to prevent timeout
        await interaction.response.defer()
            
        try:
            with DatabaseSession() as session:
                # Get schedule info
                schedule = session.query(TrainSchedule).filter_by(
                    id=schedule_id,
                    guild_id=interaction.guild.id,
                    is_active=True
                ).first()
                
                if not schedule:
                    await self.respond(interaction, f"❌ Train schedule #{schedule_id} not found.", ephemeral=True)
                    return
                
                # Get participants
                participants = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule_id,
                    is_active=True
                ).order_by(TrainParticipant.signed_up_at).all()
                
                embed = discord.Embed(
                    title=f"🚂 {schedule.name} - Roster",
                    description=f"Participants signed up for this train",
                    color=0x667eea,
                    timestamp=datetime.utcnow()
                )
                
                # Schedule details
                days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                day_name = days[schedule.day_of_week]
                import pytz as _pytz_tp2
                _uk2 = _pytz_tp2.timezone('Europe/London')
                _uk2_dt = _uk2.localize(datetime.combine(datetime.today(), schedule.start_time))
                start_time = _uk2_dt.strftime('%I:%M %p %Z').lstrip('0')
                
                embed.add_field(
                    name="📅 Train Info",
                    value=f"**Day:** {day_name}\n**Time:** {start_time}\n**Duration:** {schedule.duration_minutes} min",
                    inline=True
                )
                
                max_text = f"/{schedule.max_participants}" if schedule.max_participants else ""
                embed.add_field(
                    name="👥 Capacity",
                    value=f"**{len(participants)}{max_text}** signed up",
                    inline=True
                )
                
                if participants:
                    participant_list = []
                    for i, p in enumerate(participants, 1):
                        host_marker = " 🎯" if p.is_host else ""
                        twitch_info = f" ({p.twitch_username})" if p.twitch_username else ""
                        participant_list.append(f"{i}. **{p.display_name}**{host_marker}{twitch_info}")
                    
                    # Split into chunks if too many participants
                    chunk_size = 10
                    for i in range(0, len(participant_list), chunk_size):
                        chunk = participant_list[i:i+chunk_size]
                        field_name = "👥 Participants" if i == 0 else "👥 Participants (cont.)"
                        embed.add_field(
                            name=field_name,
                            value="\n".join(chunk),
                            inline=False
                        )
                else:
                    embed.add_field(
                        name="📋 No Participants",
                        value="No one signed up yet.\nUse `/jointrain " + str(schedule_id) + "` to join!",
                        inline=False
                    )
                
                embed.set_footer(text="🚂 Game Lounge Train Bot • Live Roster Tracking • Twitch Integration")
                # Send response to channel (not ephemeral)
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=False)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=False)
                
        except Exception as e:
            self.logger.error(f"Error showing train roster: {e}")
            await self.respond(interaction, f"❌ Failed to get train roster: {str(e)}", ephemeral=True)

    @is_admin_or_trusted()
    @commands.command(name='addtotrain', help='Add someone to a train (Admin)')
    async def add_to_train(self, ctx, schedule_id: int, user: discord.Member, *, notes: str = "Added by admin"):
        """Add someone else to a train (admin command)."""
        # Handle both interaction and regular command contexts
        is_interaction = hasattr(ctx, 'interaction') and ctx.interaction is not None
        
        try:
            with DatabaseSession() as session:
                # Check if schedule exists
                schedule = session.query(TrainSchedule).filter_by(
                    id=schedule_id,
                    guild_id=ctx.guild.id,
                    is_active=True
                ).first()
                
                if not schedule:
                    if is_interaction:
                        await self.respond(ctx.interaction, f"❌ Train schedule #{schedule_id} not found.", ephemeral=True)
                    else:
                        await ctx.send(f"❌ Train schedule #{schedule_id} not found.")
                    return
                
                # Check if user is already signed up
                existing = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule_id,
                    user_id=user.id,
                    is_active=True
                ).first()
                
                if existing:
                    if is_interaction:
                        await self.respond(ctx.interaction, f"⚠️ {user.display_name} is already signed up for **{schedule.name}**!", ephemeral=True)
                    else:
                        await ctx.send(f"⚠️ {user.display_name} is already signed up for **{schedule.name}**!")
                    return
                
                # Check participant limit
                if schedule.max_participants:
                    current_count = session.query(TrainParticipant).filter_by(
                        schedule_id=schedule_id,
                        is_active=True
                    ).count()
                    
                    if current_count >= schedule.max_participants:
                        if is_interaction:
                            await self.respond(ctx.interaction, f"❌ **{schedule.name}** is full! ({current_count}/{schedule.max_participants})", ephemeral=True)
                        else:
                            await ctx.send(f"❌ **{schedule.name}** is full! ({current_count}/{schedule.max_participants})")
                        return
                
                # Check for linked Twitch account for the target user
                twitch_username = None
                
                # Look up user's Twitch info
                try:
                    user_record = session.query(User).filter(
                        User.id == user.id,
                        User.guild_id == ctx.guild.id
                    ).first()
                    
                    if user_record and user_record.twitch_login:
                        twitch_username = user_record.twitch_login
                        self.logger.info(f"Found linked Twitch account for {user}: {twitch_username}")
                    else:
                        self.logger.info(f"No linked Twitch account found for {user}")
                        
                except Exception as e:
                    self.logger.warning(f"Error checking Twitch link for {user}: {e}")
                    # Continue without Twitch info if lookup fails

                # Add participant
                participant = TrainParticipant(
                    schedule_id=schedule_id,
                    guild_id=ctx.guild.id,
                    user_id=user.id,
                    username=user.name,
                    display_name=user.display_name,
                    notes=notes,
                    signed_up_at=datetime.utcnow(),
                    is_active=True,
                    twitch_username=twitch_username  # Automatically populated if linked
                )
                
                session.add(participant)
                session.commit()
                
                # Invalidate cache since participant data changed
                invalidate_schedule_cache(schedule.guild_id)
                
                # Get current participant count for notifications
                new_count = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule_id,
                    is_active=True
                ).count()
                
                embed = discord.Embed(
                    title="✅ Added to Train",
                    description=f"Added **{user.display_name}** to **{schedule.name}**",
                    color=0x00ff00,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="👤 Added User",
                    value=f"{user.mention}",
                    inline=True
                )
                
                embed.add_field(
                    name="🔧 Added By",
                    value=f"{ctx.author.mention}",
                    inline=True
                )
                
                # Show Twitch info if available
                if twitch_username:
                    embed.add_field(
                        name="🎮 Twitch Account",
                        value=f"✅ **{twitch_username}**\nAttendance tracking enabled!",
                        inline=True
                    )
                else:
                    embed.add_field(
                        name="🎮 Twitch Account",
                        value="❌ Not linked\nUser can link with `/linktwitch`",
                        inline=True
                    )
                
                # Show participant count
                max_text = f"/{schedule.max_participants}" if schedule.max_participants else ""
                embed.add_field(
                    name="👥 Total Participants",
                    value=f"**{new_count}{max_text}** signed up",
                    inline=True
                )
                
                if notes != "Added by admin":
                    embed.add_field(
                        name="📝 Notes",
                        value=notes,
                        inline=False
                    )
                
                if is_interaction:
                    await self.respond(ctx.interaction, embed=embed, ephemeral=False)
                else:
                    await ctx.send(embed=embed)
                self.logger.info(f"Admin {ctx.author} added {user} to train {schedule.name}")
                
                # Post attendance notification if configured
                await self.post_attendance_notification(ctx.guild, "join", {
                    'user': user,
                    'schedule': schedule,
                    'twitch_username': twitch_username,
                    'participant_count': new_count,
                    'max_participants': schedule.max_participants,
                    'notes': notes
                })

                # Let the user know they've been added and give them a
                # self-service way to back out, so they don't need to track
                # down an admin if they can't make it after all.
                await self.send_admin_added_dm(user, schedule, ctx.author, twitch_username)
                
        except Exception as e:
            self.logger.error(f"Error adding user to train: {e}")
            try:
                if is_interaction:
                    if not ctx.interaction.response.is_done():
                        await self.respond(ctx.interaction, f"❌ Failed to add user to train: {str(e)}", ephemeral=True)
                else:
                    await ctx.send(f"❌ Failed to add user to train: {str(e)}")
            except:
                # Interaction already responded to - ignore
                pass

    @is_admin_or_trusted()
    @commands.command(name='removefromtrain', help='Remove someone from a train (Admin)')
    async def remove_from_train(self, ctx, schedule_id: int, user: discord.Member):
        """Remove someone from a train (admin command)."""
        try:
            with DatabaseSession() as session:
                # Verify the schedule belongs to the current guild before proceeding
                schedule = session.query(TrainSchedule).filter_by(
                    id=schedule_id,
                    guild_id=ctx.guild.id
                ).first()

                if not schedule:
                    await ctx.send(f"❌ Train #{schedule_id} not found in this server.")
                    return

                # Find the participation scoped to the current guild's schedule
                participant = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule_id,
                    user_id=user.id,
                    is_active=True
                ).first()
                
                if not participant:
                    await ctx.send(f"❌ {user.display_name} is not signed up for train #{schedule_id}.")
                    return
                
                # Remove participant
                participant.is_active = False
                
                # Clean up any notification records for this user to prevent ghost pings
                from models import TrainNotification
                notifications = session.query(TrainNotification).filter_by(
                    schedule_id=schedule_id,
                    primary_user_id=participant.user_id
                ).all()
                for notification in notifications:
                    notification.primary_confirmed = False
                    notification.primary_user_id = None
                
                session.commit()
                
                embed = discord.Embed(
                    title="🚪 Removed from Train",
                    description=f"Removed **{user.display_name}** from **{schedule.name if schedule else f'Train #{schedule_id}'}**",
                    color=0xff9900,
                    timestamp=datetime.utcnow()
                )
                
                embed.add_field(
                    name="👤 Removed User",
                    value=f"{user.mention}",
                    inline=True
                )
                
                embed.add_field(
                    name="🔧 Removed By",
                    value=f"{ctx.author.mention}",
                    inline=True
                )
                
                await ctx.send(embed=embed)
                self.logger.info(f"Admin {ctx.author} removed {user} from train {schedule.name if schedule else schedule_id}")

                await _notify_train_leave(
                    self.bot, ctx.guild.id, schedule_id,
                    schedule.name if schedule else f"Train #{schedule_id}",
                    user.display_name
                )

                # Task #37 — immediately offer the freed seat to the next
                # waitlister rather than waiting for the next detector tick.
                notification_cog = self.bot.cogs.get('NotificationCommands')
                if notification_cog:
                    asyncio.create_task(
                        notification_cog.check_waitlist_for_slot(
                            schedule_id, ctx.guild.id
                        )
                    )

        except Exception as e:
            self.logger.error(f"Error removing user from train: {e}")
            await ctx.send(f"❌ Failed to remove user from train: {str(e)}")

    @app_commands.command(name='timeslots', description='Show all train time slots')
    @app_commands.checks.cooldown(1, 15.0, key=lambda i: (i.guild_id, i.user.id))
    async def time_slots(self, interaction: discord.Interaction):
        """List all configured time slots."""
        try:
            # Defer response to prevent timeout
            await interaction.response.defer()
            
            # Check if command is used in a guild
            if not interaction.guild:
                await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
                return
            
            # Add debug logging
            self.logger.info(f"Timeslots command called in guild: {interaction.guild.name} (ID: {interaction.guild.id})")
            
            with DatabaseSession() as session:
                # Get all schedules first, then sort by EST time
                schedules = session.query(TrainSchedule).filter_by(
                    guild_id=interaction.guild.id, 
                    is_active=True
                ).all()
                
                # Sort schedules by EST time for proper chronological order
                import pytz
                eastern_tz = pytz.timezone('Europe/London')
                
                def get_utc_sorting_key(schedule):
                    # Sort by UTC time but put early morning (next day) slots after regular Saturday slots
                    utc_time = schedule.start_time
                    from datetime import datetime, timedelta, date
                    
                    # If it's early morning UTC (before 12:00), treat as next day for sorting
                    if utc_time.hour < 12:
                        # Create a datetime with today's date, then add 24 hours to sort after regular times
                        base_datetime = datetime.combine(date.today(), utc_time)
                        next_day_datetime = base_datetime + timedelta(hours=24)
                        return next_day_datetime.time()
                    else:
                        return utc_time
                
                schedules = sorted(schedules, key=get_utc_sorting_key)
                
                self.logger.info(f"Found {len(schedules)} active schedules for guild {interaction.guild.id}")
                
                embed = discord.Embed(
                    title='⏰ Train Schedule',
                    description='Scheduled raid train time slots',
                    color=0x9146ff,
                    timestamp=datetime.utcnow()
                )
                
                if schedules:
                    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                    
                    # Discord has a limit of 25 fields per embed, so we limit to 24 to leave room for "more" field
                    max_display = 24
                    total_schedules = len(schedules)
                    display_schedules = schedules[:max_display]
                    
                    for schedule in display_schedules:
                        day_name = days[schedule.day_of_week] if schedule.day_of_week < len(days) else "Unknown"
                        
                        # start_time is stored as UK wall clock — localise directly as Europe/London
                        from datetime import date as _date
                        import pytz as _pytz
                        _uk_tz = _pytz.timezone('Europe/London')
                        _uk_dt = _uk_tz.localize(datetime.combine(_date.today(), schedule.start_time))
                        _uk_abbr = _uk_dt.strftime('%Z')  # BST or GMT
                        
                        time_display = schedule.start_time.strftime(f'%I:%M %p {_uk_abbr}').lstrip('0')
                        duration = schedule.duration_minutes
                        
                        # Get signed up participants
                        signed_participants = session.query(TrainParticipant).filter_by(
                            schedule_id=schedule.id,
                            guild_id=interaction.guild.id,
                            is_active=True
                        ).all()
                        
                        # Get assigned participants from participant_ids field with Twitch info
                        assigned_participants = []
                        if schedule.participant_ids is not None and len(schedule.participant_ids) > 0 and interaction.guild:
                            for user_id in schedule.participant_ids:
                                try:
                                    self.logger.info(f"Trying to resolve user ID: {user_id}")
                                    
                                    # Convert user_id to string first, then int
                                    user_id_str = str(user_id)
                                    user_id_int = int(user_id_str)
                                    
                                    # Get Twitch info from database
                                    from models import User, TwitchLinkRequest
                                    user_db = session.query(User).filter_by(
                                        id=user_id_int, 
                                        guild_id=interaction.guild.id
                                    ).first()
                                    
                                    twitch_info = ""
                                    if user_db and user_db.twitch_login:
                                        # User has linked Twitch account
                                        twitch_info = f" ({user_db.twitch_login})"
                                    else:
                                        # Check for pending authorization
                                        pending_request = session.query(TwitchLinkRequest).filter_by(
                                            target_user_id=user_id_int,
                                            guild_id=interaction.guild.id,
                                            status='pending'
                                        ).first()
                                        if pending_request:
                                            twitch_info = f" (pending authorization: {pending_request.requested_twitch_username})"
                                    
                                    # Try to get member from guild first
                                    user = interaction.guild.get_member(user_id_int)
                                    if user:
                                        self.logger.info(f"Found guild member: {user.display_name}")
                                        assigned_participants.append(f"{user.display_name}{twitch_info}")
                                        continue
                                    
                                    # Try to fetch user from Discord API
                                    try:
                                        user = await self.bot.fetch_user(user_id_int)
                                        if user:
                                            self.logger.info(f"Fetched user from API: {user.display_name}")
                                            assigned_participants.append(f"{user.display_name}{twitch_info}")
                                            continue
                                    except discord.NotFound:
                                        self.logger.warning(f"User {user_id} not found via API")
                                    except discord.HTTPException as e:
                                        self.logger.warning(f"HTTP error fetching user {user_id}: {e}")
                                    
                                    # Try bot's user cache
                                    user = self.bot.get_user(user_id_int)
                                    if user:
                                        self.logger.info(f"Found in bot cache: {user.display_name}")
                                        assigned_participants.append(f"{user.display_name}{twitch_info}")
                                        continue
                                    
                                    # If all else fails, show formatted ID
                                    self.logger.warning(f"Could not resolve user ID {user_id}")
                                    assigned_participants.append(f"User#{user_id_str[-4:]}{twitch_info}")
                                    
                                except (ValueError, AttributeError) as e:
                                    self.logger.error(f"Error processing user ID {user_id}: {e}")
                                    assigned_participants.append(f"Invalid ID: {str(user_id)}")
                        
                        # Format participant information - simplified display
                        max_participants = schedule.max_participants if schedule.max_participants is not None else "∞"
                        participant_count = len(signed_participants)
                        
                        if signed_participants:
                            # Get display names with Twitch info
                            from models import User, TwitchLinkRequest
                            participant_names = []
                            for p in signed_participants:
                                try:
                                    display_name = p.display_name if hasattr(p, 'display_name') else str(p)
                                    
                                    # Get Twitch info - prefer from train_participants table first
                                    twitch_info = ""
                                    if hasattr(p, 'twitch_username') and p.twitch_username:
                                        twitch_info = f" ({p.twitch_username})"
                                    
                                    participant_names.append(f"**{display_name}**{twitch_info}")
                                except:
                                    participant_names.append("**Unknown User**")
                            
                            participants_info = f"**Participants ({participant_count}/{max_participants}):**\n{', '.join(participant_names)}"
                        else:
                            participants_info = f"**Participants (0/{max_participants}):**\n*No one signed up yet*"
                        
                        embed.add_field(
                            name=f"🚂 {schedule.name} (ID: {schedule.id})",
                            value=f"**Day:** {day_name}\n**Time:** {time_display}\n**Duration:** {duration} min\n{participants_info}",
                            inline=True
                        )
                    
                    # If there are more schedules than we can display, add a summary field
                    if total_schedules > max_display:
                        remaining = total_schedules - max_display
                        embed.add_field(
                            name=f"📋 +{remaining} More Schedules",
                            value=f"This server has **{total_schedules}** total train schedules.\nShowing first {max_display}. Use `/listschedules` for a complete list.",
                            inline=False
                        )
                        
                else:
                    embed.add_field(
                        name="📋 No Schedule Found",
                        value=f"No schedule found for this server (Server ID: {interaction.guild.id if interaction.guild else 'Unknown'})\nUse admin commands to set up schedules for this server.",
                        inline=False
                    )
                
                embed.set_footer(text="🚂 Game Lounge Train Bot • Automated Scheduling • Twitch Attendance Tracking • /jointrain to join")
                await interaction.followup.send(embed=embed)
                
        except Exception as e:
            self.logger.error(f"Error showing time slots: {e}")
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ Failed to get time slots: {str(e)}", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ Failed to get time slots: {str(e)}", ephemeral=True)
    
    @app_commands.command(name='jointrain', description='[BETA] Sign yourself up for a train time slot')
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: (i.guild_id, i.user.id))
    async def join_train(self, interaction: discord.Interaction):
        """Public self-service signup - shows a dropdown of available trains.

        Gated behind the `self_signup` beta feature flag (default OFF for all
        servers). Only guilds that have explicitly had this beta enabled via
        /managefeatures can use this command - this keeps it scoped to
        beta/testing servers without touching live/main bot behavior.
        """
        try:
            await interaction.response.defer(ephemeral=True)

            if not interaction.guild:
                await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
                return

            permissions_manager = getattr(self.bot, 'permissions_manager', None)
            if not permissions_manager or not await permissions_manager.is_feature_enabled(interaction.guild.id, 'self_signup'):
                await interaction.followup.send(
                    "🧪 Self-service signup is currently in **beta testing** and isn't enabled on this server yet.\n"
                    "Ask a train host to add you for now - self-signup will roll out here once testing is complete.",
                    ephemeral=True
                )
                return

            from sqlalchemy import func
            import pytz

            def _fetch():
                with DatabaseSession() as session:
                    uk_tz = pytz.timezone('Europe/London')
                    now = datetime.now(uk_tz)
                    current_day = now.weekday()  # 0=Monday, 6=Sunday
                    next_day = (current_day + 1) % 7

                    current_day_schedules = session.query(TrainSchedule).filter_by(
                        guild_id=interaction.guild.id,
                        day_of_week=current_day,
                        is_active=True
                    ).all()

                    next_day_schedules = session.query(TrainSchedule).filter_by(
                        guild_id=interaction.guild.id,
                        day_of_week=next_day,
                        is_active=True
                    ).all()

                    # Include next-day schedules only if they're early UK times
                    # (post-midnight slots that roll over from "today" to the next day)
                    next_day_early = [s for s in next_day_schedules if s.start_time.hour < 5]

                    today = now.date()
                    tomorrow = today + timedelta(days=1)

                    def is_relevant(schedule):
                        if schedule.specific_date is None:
                            return True  # Recurring — always relevant for its matching day
                        sd = schedule.specific_date
                        if isinstance(sd, str):
                            sd = datetime.strptime(sd, '%Y-%m-%d').date()
                        return sd == today or sd == tomorrow

                    schedules = [s for s in (current_day_schedules + next_day_early) if is_relevant(s)]
                    schedule_ids = [s.id for s in schedules]
                    participant_counts = dict(
                        session.query(
                            TrainParticipant.schedule_id,
                            func.count(TrainParticipant.id)
                        ).filter(
                            TrainParticipant.schedule_id.in_(schedule_ids),
                            TrainParticipant.is_active == True
                        ).group_by(TrainParticipant.schedule_id).all()
                    ) if schedule_ids else {}

                    available_schedules = [
                        {
                            'id': s.id,
                            'name': s.name,
                            'start_time': s.start_time,
                            'max_participants': s.max_participants,
                        }
                        for s in schedules
                    ]
                    return available_schedules, participant_counts

            available_schedules, participant_counts = await asyncio.to_thread(_fetch)

            if not available_schedules:
                await interaction.followup.send(
                    "❌ No active train schedules found for this server yet.",
                    ephemeral=True
                )
                return

            dropdown = TrainSelectionDropdown(
                interaction.user,
                available_schedules,
                action_type="join",
                participant_counts=participant_counts
            )

            embed = discord.Embed(
                title="🚂 Join a Train - Select Time Slot",
                description="🧪 **Beta feature** - Choose a train slot from the dropdown below:\n\n"
                            "🆕 = Available\n⚠️ = Limited spots\n❌ = Full",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )

            await interaction.followup.send(embed=embed, view=dropdown, ephemeral=True)

        except Exception as e:
            self.logger.error(f"Error in /jointrain: {e}", exc_info=True)
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ Failed to load train signup: {str(e)}", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ Failed to load train signup: {str(e)}", ephemeral=True)

    @app_commands.command(name='setuppersistenttimeslots', description='Set up auto-updating persistent timeslots display')
    @app_commands.describe(
        channel='Channel for the persistent timeslots display (defaults to current channel)',
        update_interval='Update interval in minutes (default: 10 minutes)'
    )
    @admin_or_trusted()
    async def setup_persistent_timeslots(
        self, 
        interaction: discord.Interaction, 
        channel: discord.TextChannel = None,
        update_interval: int = 10
    ):
        """Set up a persistent auto-updating timeslots display."""
        try:
            # Defer response to prevent timeout
            await interaction.response.defer()
            
            # Check if command is used in a guild
            if not interaction.guild:
                await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
                return
            
            # Validate update interval
            if update_interval < 1 or update_interval > 60:
                await interaction.followup.send("❌ Update interval must be between 1 and 60 minutes.", ephemeral=True)
                return
            
            # Use current channel if none specified
            target_channel = channel or interaction.channel
            
            # Check bot permissions in target channel
            bot_permissions = target_channel.permissions_for(interaction.guild.me)
            if not bot_permissions.send_messages or not bot_permissions.read_messages:
                await interaction.followup.send(
                    f"❌ I don't have permission to send messages in {target_channel.mention}. "
                    f"Please make sure I have 'Send Messages' and 'View Channel' permissions.", 
                    ephemeral=True
                )
                return
            
            with DatabaseSession() as session:
                from models import PersistentMessageDisplay
                
                # Check if persistent display already exists for this guild
                existing_display = session.query(PersistentMessageDisplay).filter_by(
                    guild_id=interaction.guild.id,
                    display_type='timeslots'
                ).first()
                
                if existing_display:
                    # Update existing display settings
                    existing_display.channel_id = target_channel.id
                    existing_display.update_interval_minutes = update_interval
                    existing_display.is_active = True
                    existing_display.auto_update_enabled = True
                    existing_display.last_updated_at = None  # Force next update
                    existing_display.update_error_count = 0
                    existing_display.last_error_message = None
                    
                    # Delete old message if it exists and channel changed
                    if existing_display.message_id and existing_display.channel_id != target_channel.id:
                        try:
                            old_channel = interaction.guild.get_channel(existing_display.channel_id)
                            if old_channel:
                                old_message = await old_channel.fetch_message(existing_display.message_id)
                                if old_message:
                                    await old_message.delete()
                        except (discord.NotFound, discord.Forbidden):
                            pass  # Old message already gone or no permission
                        
                        existing_display.message_id = None
                    
                    session.commit()
                    display = existing_display
                else:
                    # Create new persistent display
                    display = PersistentMessageDisplay(
                        guild_id=interaction.guild.id,
                        channel_id=target_channel.id,
                        display_type='timeslots',
                        title='⏰ Live Train Schedule',
                        auto_update_enabled=True,
                        update_interval_minutes=update_interval,
                        show_twitch_usernames=True,
                        show_participant_counts=True,
                        max_schedules_displayed=24
                    )
                    
                    session.add(display)
                    session.commit()
                    session.refresh(display)
                
                # Generate the initial timeslots content
                embed = await self._generate_timeslots_embed(session, interaction.guild.id)
                
                # Send or update the persistent message
                if display.message_id:
                    try:
                        # Try to update existing message
                        message = await target_channel.fetch_message(display.message_id)
                        await message.edit(embed=embed)
                        self.logger.info(f"Updated existing persistent timeslots message {display.message_id} in {target_channel.name}")
                    except (discord.NotFound, discord.Forbidden):
                        # Message doesn't exist or no permission, send new one
                        message = await target_channel.send(embed=embed)
                        display.message_id = message.id
                        session.commit()
                        self.logger.info(f"Created new persistent timeslots message {message.id} in {target_channel.name}")
                else:
                    # Send new message
                    message = await target_channel.send(embed=embed)
                    display.message_id = message.id
                    session.commit()
                    self.logger.info(f"Created persistent timeslots message {message.id} in {target_channel.name}")
                
                # Update timestamp
                display.last_updated_at = datetime.utcnow()
                session.commit()
            
            # Send confirmation to user
            embed = discord.Embed(
                title="✅ Persistent Timeslots Display Setup Complete",
                description=f"Auto-updating timeslots display has been set up in {target_channel.mention}.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="⚙️ Settings",
                value=f"**Update Interval:** {update_interval} minutes\n**Auto-Update:** Enabled\n**Twitch Usernames:** Shown\n**Participant Counts:** Shown",
                inline=False
            )
            
            embed.add_field(
                name="🔄 Updates",
                value="The display will automatically update:\n• Every few minutes with current data\n• Immediately when users link/unlink Twitch accounts\n• When schedules are added/removed",
                inline=False
            )
            
            embed.add_field(
                name="📝 Management",
                value="Use `/removepersistenttimeslots` to disable auto-updating.\nUse this command again to change settings.",
                inline=False
            )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            self.logger.info(f"Persistent timeslots setup completed for guild {interaction.guild.id} in channel {target_channel.id}")
            
        except Exception as e:
            self.logger.error(f"Error setting up persistent timeslots: {e}")
            await interaction.followup.send(f"❌ Failed to set up persistent timeslots display: {str(e)}", ephemeral=True)
    
    @app_commands.command(name='removepersistenttimeslots', description='Remove auto-updating persistent timeslots display')
    @admin_or_trusted()
    async def remove_persistent_timeslots(self, interaction: discord.Interaction):
        """Remove a persistent auto-updating timeslots display."""
        try:
            # Defer response to prevent timeout
            await interaction.response.defer(ephemeral=True)
            
            # Check if command is used in a guild
            if not interaction.guild:
                await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
                return
            
            with DatabaseSession() as session:
                from models import PersistentMessageDisplay
                
                # Find existing persistent display for this guild
                display = session.query(PersistentMessageDisplay).filter_by(
                    guild_id=interaction.guild.id,
                    display_type='timeslots'
                ).first()
                
                if not display:
                    await interaction.followup.send("❌ No persistent timeslots display found for this server.", ephemeral=True)
                    return
                
                # Get channel info for confirmation
                channel = interaction.guild.get_channel(display.channel_id)
                channel_mention = channel.mention if channel else f"<#{display.channel_id}>"
                
                # Try to delete the message if it exists
                message_deleted = False
                if display.message_id and channel:
                    try:
                        message = await channel.fetch_message(display.message_id)
                        await message.delete()
                        message_deleted = True
                    except (discord.NotFound, discord.Forbidden):
                        pass  # Message already gone or no permission
                
                # Remove from database
                session.delete(display)
                session.commit()
                
                # Send confirmation
                embed = discord.Embed(
                    title="✅ Persistent Timeslots Display Removed",
                    description=f"Auto-updating timeslots display has been removed from {channel_mention}.",
                    color=0xff6b35,
                    timestamp=datetime.utcnow()
                )
                
                if message_deleted:
                    embed.add_field(
                        name="🗑️ Message Deleted",
                        value="The persistent message has been deleted from the channel.",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="⚠️ Message Not Deleted",
                        value="The persistent message could not be deleted automatically. You may need to delete it manually.",
                        inline=False
                    )
                
                embed.add_field(
                    name="📝 Note",
                    value="Use `/setuppersistenttimeslots` to set up a new auto-updating display.",
                    inline=False
                )
                
                await interaction.followup.send(embed=embed, ephemeral=True)
                self.logger.info(f"Removed persistent timeslots display for guild {interaction.guild.id}")
                
        except Exception as e:
            self.logger.error(f"Error removing persistent timeslots: {e}")
            await interaction.followup.send(f"❌ Failed to remove persistent timeslots display: {str(e)}", ephemeral=True)
    
    async def _generate_timeslots_embed(self, session, guild_id: int) -> discord.Embed:
        """Generate the timeslots embed for persistent display."""
        from models import TrainSchedule, TrainParticipant, User, TwitchLinkRequest
        import pytz
        
        # Get all schedules first, then sort by EST time
        schedules = session.query(TrainSchedule).filter_by(
            guild_id=guild_id, 
            is_active=True
        ).all()
        
        self.logger.info(f"📋 _generate_timeslots_embed: found {len(schedules)} active schedules for guild {guild_id}")
        
        # Sort schedules by EST time for proper chronological order
        eastern_tz = pytz.timezone('Europe/London')
        
        def get_utc_sorting_key(schedule):
            # Sort by UTC time but put early morning (next day) slots after regular Saturday slots
            utc_time = schedule.start_time
            from datetime import datetime, timedelta, date
            
            # If it's early morning UTC (before 12:00), treat as next day for sorting
            if utc_time.hour < 12:
                # Create a datetime with today's date, then add 24 hours to sort after regular times
                base_datetime = datetime.combine(date.today(), utc_time)
                next_day_datetime = base_datetime + timedelta(hours=24)
                return next_day_datetime.time()
            else:
                return utc_time
        
        schedules = sorted(schedules, key=get_utc_sorting_key)
        
        embed = discord.Embed(
            title='⏰ Live Train Schedule',
            description='Real-time raid train time slots (auto-updating)',
            color=0x9146ff,
            timestamp=datetime.utcnow()
        )
        
        if schedules:
            days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            
            # Discord has a limit of 25 fields per embed, so we limit to 24 to leave room for "more" field
            max_display = 24
            total_schedules = len(schedules)
            display_schedules = schedules[:max_display]
            
            for schedule in display_schedules:
                day_name = days[schedule.day_of_week] if schedule.day_of_week < len(days) else "Unknown"
                
                # start_time stored as UK wall clock — localize directly for display
                from datetime import date
                uk_naive = datetime.combine(date.today(), schedule.start_time)
                est_datetime = eastern_tz.localize(uk_naive)

                # Display time with correct timezone abbreviation (BST or GMT)
                time_display = est_datetime.strftime(f'%I:%M %p {est_datetime.strftime("%Z")}')
                duration = schedule.duration_minutes
                
                # Get signed up participants
                signed_participants = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule.id,
                    guild_id=guild_id,
                    is_active=True
                ).all()
                
                # Get signed participants with Discord names and Twitch info
                signed_participant_lines = []
                guild = self.bot.get_guild(guild_id)
                
                for participant in signed_participants:
                    try:
                        # Get Twitch status info
                        twitch_status = ""
                        if participant.twitch_username:
                            # User has linked Twitch account
                            twitch_status = f"🟢 {participant.twitch_username}"
                        else:
                            user_db = session.query(User).filter_by(
                                id=participant.user_id, 
                                guild_id=guild_id
                            ).first()
                            
                            if user_db and user_db.twitch_login:
                                twitch_status = f"🟢 {user_db.twitch_login}"
                            else:
                                cross_guild_user = session.query(User).filter(
                                    User.id == participant.user_id,
                                    User.twitch_login.isnot(None),
                                    User.twitch_login != ''
                                ).first()
                                
                                if cross_guild_user and cross_guild_user.twitch_login:
                                    twitch_status = f"🟢 {cross_guild_user.twitch_login}"
                                else:
                                    pending_request = session.query(TwitchLinkRequest).filter_by(
                                        target_user_id=participant.user_id,
                                        status='pending'
                                    ).first()
                                    if pending_request:
                                        twitch_status = "⏳ pending authorization"
                                    else:
                                        twitch_status = "⏳ pending authorization"
                        
                        # Use display name from participant record (contains Discord display name)
                        discord_name = participant.display_name or participant.username
                        signed_participant_lines.append(f"**{discord_name}** - {twitch_status}")
                        
                    except Exception as e:
                        # Fallback to basic info if error
                        discord_name = participant.display_name or participant.username or "Unknown"
                        signed_participant_lines.append(f"**{discord_name}** - ⏳ pending authorization")
                
                # Get assigned participants from participant_ids field (if any)
                assigned_participants = []
                if schedule.participant_ids is not None and len(schedule.participant_ids) > 0:
                    if guild:
                        for user_id in schedule.participant_ids:
                            try:
                                user_id_int = int(str(user_id))
                                user = guild.get_member(user_id_int)
                                if user:
                                    # Get Twitch info for assigned user
                                    user_db = session.query(User).filter_by(
                                        id=user_id_int, 
                                        guild_id=guild_id
                                    ).first()
                                    
                                    if user_db and user_db.twitch_login:
                                        twitch_status = f"🟢 {user_db.twitch_login}"
                                    else:
                                        twitch_status = "⏳ pending authorization"
                                    
                                    assigned_participants.append(f"**{user.display_name}** - {twitch_status}")
                                
                            except (ValueError, AttributeError):
                                pass
                
                # Format participant information
                max_participants = schedule.max_participants if schedule.max_participants is not None else "∞"
                signed_count = len(signed_participants)
                
                participants_info = f"**Signed Up:** {signed_count}/{max_participants}"
                
                # Show signed participants with Discord names and Twitch status
                if signed_participant_lines:
                    participants_info += f"\n{', '.join(signed_participant_lines)}"
                
                # Also show assigned participants if any exist (from admin assignments)
                if assigned_participants:
                    participants_info += f"\n**Assigned:** {', '.join(assigned_participants)}"
                
                # Add schedule field
                embed.add_field(
                    name=f"🎯 **{schedule.name}** ({day_name})",
                    value=f"**Time:** {time_display}\n**Duration:** {duration} minutes\n{participants_info}",
                    inline=True
                )
            
            if total_schedules > max_display:
                embed.add_field(
                    name=f"➕ And {total_schedules - max_display} More",
                    value=f"Use `/listschedules` to see all {total_schedules} schedules.",
                    inline=False
                )
        else:
            embed.add_field(
                name="📝 No Schedules",
                value="No active train schedules found for this server.",
                inline=False
            )
        
        embed.set_footer(text=f"🔄 Auto-updating every few minutes • Last updated")
        return embed
    
    async def _generate_waitlist_panel_embed(self, session, guild_id: int) -> discord.Embed:
        """Build the standing waitlist panel embed (per-slot waiting counts)."""
        from utils.waitlist import (slot_capacity, active_rider_count, waitlisted_rows,
                                    recurring_schedules_query)
        schedules = recurring_schedules_query(session, guild_id).order_by(
            TrainSchedule.day_of_week.asc(), TrainSchedule.start_time.asc()
        ).all()

        embed = discord.Embed(
            title="🎟️ Slot Waitlist",
            description=(
                "Want a spot on a full slot? Tap **Join a waitlist** below and pick "
                "the slot. If a seat opens up, we'll DM you an offer — accept it and "
                "you're in. Tap **Leave a waitlist** to drop off."
            ),
            color=0x5865F2,
            timestamp=datetime.utcnow(),
        )

        lines = []
        for s in schedules[:25]:
            active = active_rider_count(session, s.id)
            cap = slot_capacity(s)
            waiting = len(waitlisted_rows(session, s.id))
            seat = "🟢 open" if active < cap else "🔴 full"
            wait_txt = f" • ⏳ {waiting} waiting" if waiting else ""
            lines.append(f"**{_slot_label(s)}** — {active}/{cap} {seat}{wait_txt}")

        embed.add_field(
            name="Slots",
            value="\n".join(lines) if lines else "No active slots yet.",
            inline=False,
        )
        embed.set_footer(text="Updates automatically • Waitlist (beta)")
        return embed

    @is_admin_or_trusted()
    @commands.command(name='notifytrainslots', help='DM all train hosts about their slot with a Leave button. Usage: !notifytrainslots [day] e.g. !notifytrainslots sunday')
    async def notify_train_slots(self, ctx, day_filter: str = None):
        """Send each host a DM about their scheduled slot, adding them as a participant
        first if needed, so they get the restart-safe Leave button.
        """
        if not ctx.guild:
            await ctx.send("❌ Server only.")
            return

        days_map = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6,
            'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6,
        }
        day_of_week_filter = None
        if day_filter:
            day_of_week_filter = days_map.get(day_filter.lower())
            if day_of_week_filter is None:
                await ctx.send("❌ Invalid day. Use e.g. `sunday`, `monday` etc.")
                return

        await ctx.send("⏳ Fetching schedules and sending DMs…")

        try:
            from models import get_est_time
            from utils.waitlist import recurring_schedules_query

            with DatabaseSession() as session:
                q = recurring_schedules_query(session, ctx.guild.id)
                if day_of_week_filter is not None:
                    q = q.filter(TrainSchedule.day_of_week == day_of_week_filter)
                schedules = q.order_by(TrainSchedule.day_of_week, TrainSchedule.start_time).all()

                if not schedules:
                    await ctx.send("ℹ️ No active recurring schedules found for that filter.")
                    return

                # Collect what we need while session is open
                slot_data = []
                for s in schedules:
                    if not s.host_user_id:
                        continue
                    already = session.query(TrainParticipant).filter_by(
                        schedule_id=s.id, user_id=s.host_user_id, is_active=True
                    ).first()
                    if not already:
                        session.add(TrainParticipant(
                            schedule_id=s.id,
                            guild_id=s.guild_id,
                            user_id=s.host_user_id,
                            username=str(s.host_user_id),
                            display_name=str(s.host_user_id),
                            signed_up_at=get_est_time(),
                            is_active=True,
                            is_waitlisted=False,
                        ))
                    slot_data.append({
                        'schedule_id': s.id,
                        'schedule_name': s.name,
                        'host_user_id': s.host_user_id,
                        'duration_minutes': s.duration_minutes,
                        'max_participants': s.max_participants,
                        'start_time': s.start_time,
                        'day_of_week': s.day_of_week,
                    })
                session.commit()

            day_names = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
            sent, failed, skipped = [], [], []

            for slot in slot_data:
                try:
                    user = await ctx.bot.fetch_user(slot['host_user_id'])
                except Exception:
                    failed.append(f"ID {slot['host_user_id']} (couldn't fetch user)")
                    continue

                try:
                    day_label = day_names[slot['day_of_week']]
                    t = slot['start_time']
                    time_label = t.strftime('%I:%M %p') if t else 'TBD'

                    embed = discord.Embed(
                        title="🚂 Your Train Slot",
                        description=(
                            f"Hey **{user.display_name}**! Here are the details for your upcoming train slot.\n"
                            f"Use the button below if you need to cancel."
                        ),
                        color=0x00b0f4,
                        timestamp=datetime.utcnow()
                    )
                    embed.add_field(
                        name="📅 Slot Details",
                        value=(
                            f"**Train:** {slot['schedule_name']}\n"
                            f"**Day:** {day_label}\n"
                            f"**Time:** {time_label} BST\n"
                            f"**Duration:** {slot['duration_minutes']} minutes"
                        ),
                        inline=False
                    )
                    embed.add_field(
                        name="❓ Can't make it?",
                        value="Hit **Cancel my slot** below and someone on the waitlist will be offered your spot automatically.",
                        inline=False
                    )
                    embed.set_footer(text="Game Lounge Train bot")

                    view = build_leave_train_view(slot['schedule_id'], user.id)
                    # Rename button label to be more intuitive in this context
                    for item in view.children:
                        if hasattr(item, 'label'):
                            item.label = "Cancel my slot"

                    await user.send(embed=embed, view=view)
                    sent.append(user.display_name)
                except discord.Forbidden:
                    failed.append(f"{user.display_name} (DMs closed)")
                except Exception as e:
                    failed.append(f"{user.display_name} ({e})")

            lines = []
            if sent:
                lines.append(f"✅ **DMed ({len(sent)}):** {', '.join(sent)}")
            if failed:
                lines.append(f"⚠️ **Failed ({len(failed)}):** {', '.join(failed)}")
            if skipped:
                lines.append(f"⏭️ **Skipped:** {', '.join(skipped)}")
            await ctx.send("\n".join(lines) or "Nothing to send.")

        except Exception as e:
            self.logger.error(f"notifytrainslots error: {e}", exc_info=True)
            await ctx.send(f"❌ Error: {e}")

    @is_admin_or_trusted()
    @commands.command(name='setwaitlistpanel', help='Post/refresh the standing waitlist panel (Admin)')
    async def set_waitlist_panel(self, ctx, channel: discord.TextChannel = None):
        """Create or move the persistent waitlist panel for this server."""
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
        perms = getattr(self.bot, 'permissions_manager', None)
        if not (perms and await perms.is_feature_enabled(ctx.guild.id, 'waitlist')):
            await ctx.send("❌ The waitlist feature isn't enabled on this server. Enable it with `/managefeatures` first.")
            return

        target = channel or ctx.channel
        bot_perms = target.permissions_for(ctx.guild.me)
        if not bot_perms.send_messages or not bot_perms.view_channel:
            await ctx.send(f"❌ I need permission to send messages in {target.mention}.")
            return

        try:
            from models import PersistentMessageDisplay
            with DatabaseSession() as session:
                display = session.query(PersistentMessageDisplay).filter_by(
                    guild_id=ctx.guild.id, display_type='waitlist_panel'
                ).first()
                if display:
                    # If moving channels, remove the old message first.
                    if display.message_id and display.channel_id != target.id:
                        try:
                            old_ch = ctx.guild.get_channel(display.channel_id)
                            if old_ch:
                                old_msg = await old_ch.fetch_message(display.message_id)
                                await old_msg.delete()
                        except (discord.NotFound, discord.Forbidden):
                            pass
                        display.message_id = None
                    display.channel_id = target.id
                    display.is_active = True
                    display.auto_update_enabled = True
                    display.update_error_count = 0
                    display.last_error_message = None
                else:
                    display = PersistentMessageDisplay(
                        guild_id=ctx.guild.id,
                        channel_id=target.id,
                        display_type='waitlist_panel',
                        title='Slot Waitlist',
                        auto_update_enabled=True,
                        update_interval_minutes=5,
                    )
                    session.add(display)
                session.commit()
                session.refresh(display)

                embed = await self._generate_waitlist_panel_embed(session, ctx.guild.id)
                view = WaitlistPanelView(self.bot)
                message = None
                if display.message_id:
                    try:
                        message = await target.fetch_message(display.message_id)
                        await message.edit(embed=embed, view=view)
                    except (discord.NotFound, discord.Forbidden):
                        message = None
                if not message:
                    message = await target.send(embed=embed, view=view)
                    display.message_id = message.id
                display.last_updated_at = datetime.utcnow()
                session.commit()

            await ctx.send(f"✅ Waitlist panel is live in {target.mention}.")
            self.logger.info(f"Waitlist panel set for guild {ctx.guild.id} in channel {target.id}")
        except Exception as e:
            self.logger.error(f"Error setting waitlist panel: {e}", exc_info=True)
            await ctx.send(f"❌ Failed to set up the waitlist panel: {e}")

    @is_admin_or_trusted()
    @commands.command(name='removewaitlistpanel', help='Remove the standing waitlist panel (Admin)')
    async def remove_waitlist_panel(self, ctx):
        """Remove the persistent waitlist panel for this server."""
        if not ctx.guild:
            await ctx.send("❌ This command can only be used in a server.")
            return
        try:
            from models import PersistentMessageDisplay
            with DatabaseSession() as session:
                display = session.query(PersistentMessageDisplay).filter_by(
                    guild_id=ctx.guild.id, display_type='waitlist_panel'
                ).first()
                if not display:
                    await ctx.send("❌ No waitlist panel found for this server.")
                    return
                if display.message_id:
                    ch = ctx.guild.get_channel(display.channel_id)
                    if ch:
                        try:
                            msg = await ch.fetch_message(display.message_id)
                            await msg.delete()
                        except (discord.NotFound, discord.Forbidden):
                            pass
                session.delete(display)
                session.commit()
            await ctx.send("✅ Waitlist panel removed.")
        except Exception as e:
            self.logger.error(f"Error removing waitlist panel: {e}", exc_info=True)
            await ctx.send(f"❌ Failed to remove the waitlist panel: {e}")

    async def cog_load(self):
        """Initialize background tasks when cog loads."""
        self.persistent_updater.start()
        self.logger.info("Persistent message updater started")
    
    async def cog_unload(self):
        """Cleanup when cog unloads."""
        self.persistent_updater.cancel()
        self.logger.info("Persistent message updater stopped")
    
    @tasks.loop(minutes=5)
    async def persistent_updater(self):
        """Background task to update persistent message displays."""
        try:
            self.logger.info("🔄 Persistent updater task running...")
            with DatabaseSession() as session:
                from models import PersistentMessageDisplay
                from datetime import datetime, timedelta
                
                # Get all active persistent displays
                displays = session.query(PersistentMessageDisplay).filter_by(
                    is_active=True,
                    auto_update_enabled=True
                ).all()
                
                self.logger.info(f"Found {len(displays)} active persistent displays to check")
                current_time = datetime.utcnow()
                
                for display in displays:
                    try:
                        # Check if update is needed based on interval
                        if display.last_updated_at:
                            next_update = display.last_updated_at + timedelta(minutes=display.update_interval_minutes)
                            if current_time < next_update:
                                continue  # Not time to update yet
                        
                        # Try to update the message
                        success = await self._update_persistent_message(display, session)
                        
                        if success:
                            display.last_updated_at = current_time
                            display.update_error_count = 0
                            display.last_error_message = None
                        else:
                            display.update_error_count = (display.update_error_count or 0) + 1
                            
                            # Disable if too many errors
                            if display.update_error_count >= 10:
                                display.auto_update_enabled = False
                                display.last_error_message = "Too many update failures - auto-update disabled"
                                self.logger.warning(f"Disabled persistent display {display.id} due to repeated failures")
                        
                        session.commit()
                        
                    except Exception as e:
                        self.logger.error(f"Error updating persistent display {display.id}: {e}")
                        display.update_error_count = (display.update_error_count or 0) + 1
                        display.last_error_message = str(e)
                        session.commit()
                        
                        # Add small delay between updates to avoid rate limiting
                        await asyncio.sleep(1)
                
                if displays:
                    self.logger.debug(f"Checked {len(displays)} persistent displays for updates")
                    
        except Exception as e:
            self.logger.error(f"❌ Error in persistent_updater task: {e}", exc_info=True)
    
    @persistent_updater.error
    async def persistent_updater_error(self, error):
        """Handle errors in persistent_updater and restart it."""
        self.logger.error(f"⚠️ persistent_updater crashed with error: {error}", exc_info=True)
        self.logger.info("🔄 Restarting persistent_updater in 60 seconds...")
        await asyncio.sleep(60)
        self.persistent_updater.restart()
    
    async def _update_persistent_message(self, display, session) -> bool:
        """Update a single persistent message display."""
        try:
            # Get the guild and channel
            guild = self.bot.get_guild(display.guild_id)
            if not guild:
                self.logger.warning(f"Guild {display.guild_id} not found for persistent display {display.id}")
                return False
            
            channel = guild.get_channel(display.channel_id)
            if not channel:
                self.logger.warning(f"Channel {display.channel_id} not found for persistent display {display.id}")
                return False
            
            # Check bot permissions
            bot_permissions = channel.permissions_for(guild.me)
            if not bot_permissions.send_messages or not bot_permissions.read_messages:
                self.logger.warning(f"No permission to update message in channel {display.channel_id}")
                return False
            
            # Generate the updated embed (and any attached view)
            view = None
            if display.display_type == 'timeslots':
                embed = await self._generate_timeslots_embed(session, display.guild_id)
            elif display.display_type == 'waitlist_panel':
                embed = await self._generate_waitlist_panel_embed(session, display.guild_id)
                view = WaitlistPanelView(self.bot)
            else:
                self.logger.warning(f"Unknown display type: {display.display_type}")
                return False
            
            # Try to update existing message or create new one. Editing only the
            # embed keeps the existing (persistent) buttons intact; recreating
            # re-attaches the view so the panel stays interactive.
            if display.message_id:
                try:
                    message = await channel.fetch_message(display.message_id)
                    await message.edit(embed=embed)
                    self.logger.info(f"✅ Persistent display {display.id} updated in #{channel.name} ({guild.name})")
                    return True
                except (discord.NotFound, discord.Forbidden):
                    # Message doesn't exist or no permission, create new one
                    message = await channel.send(embed=embed, view=view) if view else await channel.send(embed=embed)
                    display.message_id = message.id
                    self.logger.info(f"✅ Persistent display {display.id} re-created in #{channel.name} ({guild.name})")
                    return True
            else:
                # No existing message, create new one
                message = await channel.send(embed=embed, view=view) if view else await channel.send(embed=embed)
                display.message_id = message.id
                return True
                
        except Exception as e:
            self.logger.error(f"Error updating persistent message {display.id}: {e}")
            return False
    
    @persistent_updater.before_loop
    async def before_persistent_updater(self):
        """Wait for bot to be ready before starting the updater."""
        await self.bot.wait_until_ready()
    
    async def trigger_persistent_updates(self, guild_id: int):
        """Immediately trigger updates for persistent displays in a specific guild."""
        try:
            with DatabaseSession() as session:
                from models import PersistentMessageDisplay
                
                displays = session.query(PersistentMessageDisplay).filter_by(
                    guild_id=guild_id,
                    is_active=True,
                    auto_update_enabled=True
                ).all()
                
                for display in displays:
                    try:
                        success = await self._update_persistent_message(display, session)
                        if success:
                            display.last_updated_at = datetime.utcnow()
                            display.update_error_count = 0
                            display.last_error_message = None
                            session.commit()
                            
                        # Add small delay to avoid rate limiting
                        await asyncio.sleep(0.5)
                        
                    except Exception as e:
                        self.logger.error(f"Error in immediate update for display {display.id}: {e}")
                
                if displays:
                    self.logger.info(f"Triggered immediate update for {len(displays)} persistent displays in guild {guild_id}")
                    
        except Exception as e:
            self.logger.error(f"Error triggering persistent updates for guild {guild_id}: {e}")

async def setup(bot):
    await bot.add_cog(TrainParticipantCommands(bot))