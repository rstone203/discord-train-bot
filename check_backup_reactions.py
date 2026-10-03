"""Check backup reactions and send Twitch linking DMs to users without Twitch."""
import discord
from discord.ext import commands
import asyncio
from database import DatabaseSession
from models import User as DBUser, NotificationSettings
import os

async def check_and_send_twitch_dms():
    """Check backup signup reactions and send DMs to users without Twitch linked."""
    
    # Create bot instance
    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True
    intents.message_content = True
    intents.reactions = True
    
    bot = commands.Bot(command_prefix="!", intents=intents)
    
    @bot.event
    async def on_ready():
        print(f"✅ Bot logged in as {bot.user}")
        
        try:
            with DatabaseSession() as session:
                # Get backup signup message info
                settings = session.query(NotificationSettings).filter(
                    NotificationSettings.backup_signup_message_id.isnot(None)
                ).first()
                
                if not settings:
                    print("❌ No backup signup message configured")
                    await bot.close()
                    return
                
                message_id = settings.backup_signup_message_id
                guild_id = settings.guild_id
                emoji = settings.backup_signup_emoji
                
                print(f"🔍 Looking for message {message_id} in guild {guild_id}")
                
                # Get the guild
                guild = bot.get_guild(guild_id)
                if not guild:
                    print(f"❌ Guild {guild_id} not found")
                    await bot.close()
                    return
                
                # Search for the message in all text channels
                message = None
                for channel in guild.text_channels:
                    try:
                        message = await channel.fetch_message(message_id)
                        print(f"✅ Found message in #{channel.name}")
                        break
                    except discord.NotFound:
                        continue
                    except discord.Forbidden:
                        continue
                
                if not message:
                    print(f"❌ Message {message_id} not found in any channel")
                    await bot.close()
                    return
                
                # Get reactions
                reaction = discord.utils.get(message.reactions, emoji=emoji)
                if not reaction:
                    print(f"❌ No {emoji} reactions found on message")
                    await bot.close()
                    return
                
                print(f"📊 Found {reaction.count} reactions")
                
                # Get all users who reacted
                users = [user async for user in reaction.users() if not user.bot]
                print(f"👥 {len(users)} users reacted (excluding bots)")
                
                # Check each user's Twitch status
                sent_count = 0
                for user in users:
                    # Check if user has Twitch linked
                    db_user = session.query(DBUser).filter_by(discord_id=user.id).first()
                    has_twitch = db_user and db_user.twitch_username
                    
                    if has_twitch:
                        print(f"✅ {user.name} has Twitch linked: {db_user.twitch_username}")
                    else:
                        print(f"❌ {user.name} does NOT have Twitch linked - sending DM...")
                        
                        # Send DM
                        try:
                            embed = discord.Embed(
                                title="🎮 Link Your Twitch Account!",
                                description=f"Hi {user.mention}! You signed up as a backup streamer, but we don't have your Twitch account linked yet.\n\nLinking your Twitch helps us track your attendance during trains and give you credit for participation!",
                                color=0x9146FF
                            )
                            embed.add_field(
                                name="🔗 How to Link Your Twitch",
                                value=f"**Self-Service:** Use `/linktwitch YourTwitchUsername` to link your account instantly!\n\nOr ask an admin for assistance.",
                                inline=False
                            )
                            embed.add_field(
                                name="🎯 Benefits of Linking",
                                value="• Automatic attendance tracking during trains\n• Show up in train rosters with your Twitch name\n• Get credit for participating in raids",
                                inline=False
                            )
                            embed.add_field(
                                name="🔒 Privacy",
                                value="Your Twitch username will only be used for train participation tracking. You can request unlinking at any time.",
                                inline=False
                            )
                            
                            await user.send(embed=embed)
                            print(f"  ✅ DM sent to {user.name}")
                            sent_count += 1
                        except discord.Forbidden:
                            print(f"  ❌ Could not send DM to {user.name} - DMs disabled")
                        except Exception as e:
                            print(f"  ❌ Error sending DM to {user.name}: {e}")
                
                print(f"\n✅ Sent {sent_count} DM(s) to users without Twitch linked")
                
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            await bot.close()
    
    # Run the bot
    token = os.getenv('DISCORD_BOT_TOKEN')
    await bot.start(token)

if __name__ == "__main__":
    asyncio.run(check_and_send_twitch_dms())
