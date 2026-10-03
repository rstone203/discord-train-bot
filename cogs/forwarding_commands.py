"""
Message forwarding commands for cross-server channel following.
"""

import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime
from database import DatabaseSession
from models import ForwardingConfig
import logging
from utils.slash_permissions import owner_or_trusted

class ForwardingCommands(commands.Cog):
    """Commands for managing cross-server message forwarding."""
    
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord_bot.forwarding')
        
    def is_owner_or_trusted():
        """Check if user is the bot owner or a trusted user."""
        async def predicate(ctx):
            return await ctx.bot.is_owner_or_trusted(ctx.author)
        return commands.check(predicate)
    
    @commands.command(name='addforward', aliases=['follow'])
    @commands.guild_only()
    @is_owner_or_trusted()
    async def add_forwarding(self, ctx, source_guild_id: int, source_channel_id: int, target_channel_id: int = 0):
        """Add a channel forwarding configuration (Owner only)."""
        # Use current channel as target if not specified
        if target_channel_id == 0:
            target_channel_id = ctx.channel.id

        # Non-owners may only create forwarding rules where the source guild is
        # their own guild, preventing cross-guild information disclosure.
        is_owner = await ctx.bot.is_owner(ctx.author)
        if not is_owner and source_guild_id != ctx.guild.id:
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="You can only set up forwarding rules where this server is the source.",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return

        # Validate source guild
        source_guild = self.bot.get_guild(source_guild_id)
        if source_guild is None:
            embed = discord.Embed(
                title="❌ Source Server Not Found",
                description=f"Bot is not in server with ID: {source_guild_id}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        # Validate source channel
        source_channel = source_guild.get_channel(source_channel_id)
        if source_channel is None:
            embed = discord.Embed(
                title="❌ Source Channel Not Found",
                description=f"Channel with ID {source_channel_id} not found in {source_guild.name}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        # Validate target channel
        target_channel = self.bot.get_channel(target_channel_id)
        if target_channel is None:
            embed = discord.Embed(
                title="❌ Target Channel Not Found",
                description=f"Channel with ID {target_channel_id} not found",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        # Check bot permissions in source channel
        if not source_channel.permissions_for(source_guild.me).read_messages:
            embed = discord.Embed(
                title="❌ Missing Permissions",
                description=f"Bot lacks read permissions in {source_channel.mention} ({source_guild.name})",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        # Check bot permissions in target channel
        if not target_channel.permissions_for(target_channel.guild.me).send_messages:
            embed = discord.Embed(
                title="❌ Missing Permissions",
                description=f"Bot lacks send permissions in {target_channel.mention}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        try:
            with DatabaseSession() as session:
                # Check if forwarding already exists
                existing = session.query(ForwardingConfig).filter(
                    ForwardingConfig.source_guild_id == str(source_guild_id),
                    ForwardingConfig.source_channel_id == str(source_channel_id),
                    ForwardingConfig.target_channel_id == str(target_channel_id),
                    ForwardingConfig.is_active == True
                ).first()
                
                if existing:
                    embed = discord.Embed(
                        title="⚠️ Forwarding Already Exists",
                        description=f"Messages from {source_channel.mention} ({source_guild.name}) are already being forwarded to {target_channel.mention}",
                        color=0xffa500
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Create new forwarding config
                config = ForwardingConfig(
                    source_guild_id=str(source_guild_id),
                    source_channel_id=str(source_channel_id),
                    target_guild_id=str(target_channel.guild.id),
                    target_channel_id=str(target_channel_id),
                    created_by=str(ctx.author.id),
                    is_active=True,
                    created_at=datetime.utcnow()
                )
                
                session.add(config)
                session.commit()
            
            embed = discord.Embed(
                title="✅ Forwarding Added",
                description="Cross-server message forwarding configured successfully!",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📤 Source",
                value=f"**Server:** {source_guild.name}\n**Channel:** {source_channel.mention}\n**ID:** {source_channel_id}",
                inline=True
            )
            
            embed.add_field(
                name="📥 Target",
                value=f"**Server:** {target_channel.guild.name}\n**Channel:** {target_channel.mention}\n**ID:** {target_channel_id}",
                inline=True
            )
            
            embed.set_footer(text=f"Configured by {ctx.author}")
            
            await ctx.send(embed=embed)
            self.logger.info(f"Forwarding added: {source_guild.name}#{source_channel.name} -> {target_channel.guild.name}#{target_channel.name} by {ctx.author}")
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Database Error",
                description=f"Failed to add forwarding configuration: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            self.logger.error(f"Error adding forwarding config: {e}")
    
    @commands.command(name='removeforward', aliases=['unfollow'])
    @commands.guild_only()
    @is_owner_or_trusted()
    async def remove_forwarding(self, ctx, source_guild_id: int, source_channel_id: int, target_channel_id: int = 0):
        """Remove a channel forwarding configuration (Owner only)."""
        if target_channel_id == 0:
            target_channel_id = ctx.channel.id

        # Non-owners may only remove forwarding rules that involve their own guild
        # as source or target, preventing cross-guild tampering.
        is_owner = await ctx.bot.is_owner(ctx.author)
        target_channel_obj = self.bot.get_channel(target_channel_id)
        target_guild_id = target_channel_obj.guild.id if target_channel_obj else None
        if not is_owner and source_guild_id != ctx.guild.id and target_guild_id != ctx.guild.id:
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="You can only remove forwarding rules that involve this server.",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return

        try:
            with DatabaseSession() as session:
                config = session.query(ForwardingConfig).filter(
                    ForwardingConfig.source_guild_id == str(source_guild_id),
                    ForwardingConfig.source_channel_id == str(source_channel_id),
                    ForwardingConfig.target_channel_id == str(target_channel_id),
                    ForwardingConfig.is_active == True
                ).first()
                
                if not config:
                    embed = discord.Embed(
                        title="❌ Forwarding Not Found",
                        description="No active forwarding configuration found with those parameters.",
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Use SQLAlchemy's update method to properly modify columns
                session.query(ForwardingConfig).filter(ForwardingConfig.id == config.id).update({
                    'is_active': False,
                    'deleted_at': datetime.utcnow()
                })
                session.commit()
            
            # Get channel info for display
            source_guild = self.bot.get_guild(source_guild_id)
            source_channel = source_guild.get_channel(source_channel_id) if source_guild else None
            target_channel = self.bot.get_channel(target_channel_id)
            
            embed = discord.Embed(
                title="✅ Forwarding Removed",
                description="Cross-server message forwarding has been disabled.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            source_info = f"**Server:** {source_guild.name if source_guild else 'Unknown'}\n**Channel:** {source_channel.mention if source_channel else f'ID: {source_channel_id}'}"
            target_info = f"**Server:** {target_channel.guild.name if target_channel else 'Unknown'}\n**Channel:** {target_channel.mention if target_channel else f'ID: {target_channel_id}'}"
            
            embed.add_field(name="📤 Source", value=source_info, inline=True)
            embed.add_field(name="📥 Target", value=target_info, inline=True)
            
            await ctx.send(embed=embed)
            self.logger.info(f"Forwarding removed: {source_guild_id}#{source_channel_id} -> {target_channel_id} by {ctx.author}")
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Database Error",
                description=f"Failed to remove forwarding configuration: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            self.logger.error(f"Error removing forwarding config: {e}")
    
    @commands.command(name='listforwards', aliases=['forwards'])
    @commands.guild_only()
    @is_owner_or_trusted()
    async def list_forwardings(self, ctx):
        """List all active forwarding configurations (Owner only)."""
        is_owner = await ctx.bot.is_owner(ctx.author)
        try:
            with DatabaseSession() as session:
                query = session.query(ForwardingConfig).filter(
                    ForwardingConfig.is_active == True
                )
                # Non-owners only see configurations that involve their own guild.
                if not is_owner:
                    guild_id_str = str(ctx.guild.id)
                    query = query.filter(
                        (ForwardingConfig.source_guild_id == guild_id_str) |
                        (ForwardingConfig.target_guild_id == guild_id_str)
                    )
                configs = query.order_by(ForwardingConfig.created_at.desc()).all()
                
                # Extract data while session is active
                config_data = []
                for config in configs:
                    config_data.append({
                        'source_guild_id': config.source_guild_id,
                        'source_channel_id': config.source_channel_id,
                        'target_channel_id': config.target_channel_id,
                        'created_at': config.created_at,
                        'created_by': config.created_by
                    })
            
            if not config_data:
                embed = discord.Embed(
                    title="📋 Active Forwarding Configurations",
                    description="No active forwarding configurations found.",
                    color=0xffa500
                )
                await ctx.send(embed=embed)
                return
            
            embed = discord.Embed(
                title="📋 Active Forwarding Configurations",
                description=f"Found {len(config_data)} active forwarding configurations:",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            for i, config in enumerate(config_data[:10], 1):  # Show first 10
                source_guild = self.bot.get_guild(int(config['source_guild_id']))
                source_channel = source_guild.get_channel(int(config['source_channel_id'])) if source_guild else None
                target_channel = self.bot.get_channel(int(config['target_channel_id']))
                
                source_name = f"{source_guild.name}#{source_channel.name}" if source_guild and source_channel else f"Unknown ({config['source_guild_id']}#{config['source_channel_id']})"
                target_name = f"{target_channel.guild.name}#{target_channel.name}" if target_channel else f"Unknown ({config['target_channel_id']})"
                
                embed.add_field(
                    name=f"#{i} Forward Configuration",
                    value=f"**From:** {source_name}\n**To:** {target_name}\n**Created:** {config['created_at'].strftime('%Y-%m-%d %H:%M')}",
                    inline=False
                )
            
            if len(config_data) > 10:
                embed.set_footer(text=f"Showing 10 of {len(config_data)} configurations")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Database Error",
                description=f"Failed to retrieve forwarding configurations: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            self.logger.error(f"Error listing forwarding configs: {e}")
    
    # Slash command versions - Essential forwarding commands
    @app_commands.command(name='addforward', description='[Bot Owner or Trusted] Add a channel forwarding configuration')
    @app_commands.guild_only()
    @app_commands.describe(
        source_guild_id='The server ID where messages are posted',
        source_channel_id='The channel ID to forward messages from',
        target_channel_id='The channel ID to forward messages to (optional, defaults to current channel)'
    )
    @owner_or_trusted()
    async def slash_add_forwarding(
        self, 
        interaction: discord.Interaction, 
        source_guild_id: str,
        source_channel_id: str, 
        target_channel_id: str = None
    ):
        """Add a channel forwarding configuration (slash command)."""
        # Defer response to prevent timeout during validation
        await interaction.response.defer(ephemeral=True)
        
        # Use current channel as target if not specified
        if target_channel_id is None:
            target_channel_id = str(interaction.channel.id)
        
        # Convert string IDs to integers
        try:
            source_guild_id_int = int(source_guild_id)
            source_channel_id_int = int(source_channel_id)
            target_channel_id_int = int(target_channel_id)
        except ValueError:
            await interaction.followup.send("❌ Invalid ID format. Please provide numeric IDs.", ephemeral=True)
            return

        # Non-owners may only create forwarding rules where the source guild is
        # their own guild, preventing cross-guild information disclosure.
        is_owner = interaction.user.id == interaction.client.owner_id
        if not is_owner and source_guild_id_int != interaction.guild.id:
            await interaction.followup.send(
                "❌ You can only set up forwarding rules where this server is the source.",
                ephemeral=True
            )
            return

        # Validate source guild
        source_guild = self.bot.get_guild(source_guild_id_int)
        if source_guild is None:
            embed = discord.Embed(
                title="❌ Source Server Not Found",
                description=f"Bot is not in server with ID: {source_guild_id}",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Validate source channel
        source_channel = source_guild.get_channel(source_channel_id_int)
        if source_channel is None:
            embed = discord.Embed(
                title="❌ Source Channel Not Found",
                description=f"Channel with ID {source_channel_id} not found in {source_guild.name}",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Validate target channel
        target_channel = self.bot.get_channel(target_channel_id_int)
        if target_channel is None:
            embed = discord.Embed(
                title="❌ Target Channel Not Found",
                description=f"Channel with ID {target_channel_id} not found",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Check bot permissions in source channel
        if not source_channel.permissions_for(source_guild.me).read_messages:
            embed = discord.Embed(
                title="❌ Missing Permissions",
                description=f"Bot lacks read permissions in {source_channel.mention} ({source_guild.name})",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # Check bot permissions in target channel
        if not target_channel.permissions_for(target_channel.guild.me).send_messages:
            embed = discord.Embed(
                title="❌ Missing Permissions",
                description=f"Bot lacks send permissions in {target_channel.mention}",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        try:
            with DatabaseSession() as session:
                # Check if forwarding already exists
                existing = session.query(ForwardingConfig).filter(
                    ForwardingConfig.source_guild_id == source_guild_id,
                    ForwardingConfig.source_channel_id == source_channel_id,
                    ForwardingConfig.target_channel_id == target_channel_id,
                    ForwardingConfig.is_active == True
                ).first()
                
                if existing:
                    embed = discord.Embed(
                        title="⚠️ Forwarding Already Exists",
                        description=f"Messages from {source_channel.mention} ({source_guild.name}) are already being forwarded to {target_channel.mention}",
                        color=0xffa500
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return
                
                # Create new forwarding config
                config = ForwardingConfig(
                    source_guild_id=source_guild_id,
                    source_channel_id=source_channel_id,
                    target_guild_id=str(target_channel.guild.id),
                    target_channel_id=target_channel_id,
                    created_by=str(interaction.user.id),
                    is_active=True,
                    created_at=datetime.utcnow()
                )
                
                session.add(config)
                session.commit()
            
            embed = discord.Embed(
                title="✅ Forwarding Added",
                description="Cross-server message forwarding configured successfully!",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            embed.add_field(
                name="📤 Source",
                value=f"**Server:** {source_guild.name}\n**Channel:** {source_channel.mention}\n**ID:** {source_channel_id}",
                inline=True
            )
            
            embed.add_field(
                name="📥 Target",
                value=f"**Server:** {target_channel.guild.name}\n**Channel:** {target_channel.mention}\n**ID:** {target_channel_id}",
                inline=True
            )
            
            embed.set_footer(text=f"Configured by {interaction.user}")
            
            await interaction.followup.send(embed=embed)
            self.logger.info(f"Forwarding added via slash: {source_guild.name}#{source_channel.name} -> {target_channel.guild.name}#{target_channel.name} by {interaction.user}")
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Database Error",
                description=f"Failed to add forwarding configuration: {str(e)}",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            self.logger.error(f"Error adding forwarding config via slash: {e}")
    
    @app_commands.command(name='listforwards', description='[Bot Owner or Trusted] List all active forwarding configurations')
    @app_commands.guild_only()
    @owner_or_trusted()
    async def slash_list_forwardings(self, interaction: discord.Interaction):
        """List all active forwarding configurations (slash command)."""
        # Defer response to prevent timeout
        await interaction.response.defer(ephemeral=True)

        is_owner = interaction.user.id == interaction.client.owner_id

        try:
            with DatabaseSession() as session:
                query = session.query(ForwardingConfig).filter(
                    ForwardingConfig.is_active == True
                )
                # Non-owners only see configurations that involve their own guild.
                if not is_owner:
                    guild_id_str = str(interaction.guild.id)
                    query = query.filter(
                        (ForwardingConfig.source_guild_id == guild_id_str) |
                        (ForwardingConfig.target_guild_id == guild_id_str)
                    )
                configs = query.order_by(ForwardingConfig.created_at.desc()).all()
                
                # Extract data while session is active (include ID for removal command)
                config_data = []
                for config in configs:
                    config_data.append({
                        'id': config.id,
                        'source_guild_id': config.source_guild_id,
                        'source_channel_id': config.source_channel_id,
                        'target_channel_id': config.target_channel_id,
                        'created_at': config.created_at,
                        'created_by': config.created_by
                    })
            
            if not config_data:
                embed = discord.Embed(
                    title="📋 Active Forwarding Configurations",
                    description="No active forwarding configurations found.",
                    color=0xffa500
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
            
            embed = discord.Embed(
                title="📋 Active Forwarding Configurations",
                description=f"Found {len(config_data)} active forwarding configurations:",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            for i, config in enumerate(config_data[:10], 1):  # Show first 10
                source_guild = self.bot.get_guild(int(config['source_guild_id']))
                source_channel = source_guild.get_channel(int(config['source_channel_id'])) if source_guild else None
                target_channel = self.bot.get_channel(int(config['target_channel_id']))
                
                source_name = f"{source_guild.name}#{source_channel.name}" if source_guild and source_channel else f"Unknown ({config['source_guild_id']}#{config['source_channel_id']})"
                target_name = f"{target_channel.guild.name}#{target_channel.name}" if target_channel else f"Unknown ({config['target_channel_id']})"
                
                embed.add_field(
                    name=f"#{i} Forward Configuration (ID: {config['id']})",
                    value=f"**From:** {source_name}\n**To:** {target_name}\n**Created:** {config['created_at'].strftime('%Y-%m-%d %H:%M')}\n*Use `/removeforwardbyid {config['id']}` to remove*",
                    inline=False
                )
            
            if len(config_data) > 10:
                embed.set_footer(text=f"Showing 10 of {len(config_data)} configurations")
            
            await interaction.followup.send(embed=embed)
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Database Error",
                description=f"Failed to retrieve forwarding configurations: {str(e)}",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            self.logger.error(f"Error listing forwarding configs via slash: {e}")

    @app_commands.command(name='removeforward', description='[Bot Owner or Trusted] Remove a channel forwarding configuration')
    @app_commands.guild_only()
    @app_commands.describe(
        source_guild_id='The server ID where messages are posted',
        source_channel_id='The channel ID messages are forwarded from',
        target_channel_id='The channel ID messages are forwarded to (optional, defaults to current channel)'
    )
    @owner_or_trusted()
    async def slash_remove_forwarding(
        self, 
        interaction: discord.Interaction, 
        source_guild_id: str,
        source_channel_id: str, 
        target_channel_id: str = None
    ):
        """Remove a channel forwarding configuration (slash command)."""
        await interaction.response.defer(ephemeral=True)
        
        if target_channel_id is None:
            target_channel_id = str(interaction.channel.id)
        
        try:
            source_guild_id_int = int(source_guild_id)
            source_channel_id_int = int(source_channel_id)
            target_channel_id_int = int(target_channel_id)
        except ValueError:
            await interaction.followup.send("❌ Invalid ID format. Please provide numeric IDs.", ephemeral=True)
            return

        # Non-owners may only remove forwarding rules that involve their own guild.
        is_owner = interaction.user.id == interaction.client.owner_id
        target_channel_obj = self.bot.get_channel(target_channel_id_int)
        target_guild_id_int = target_channel_obj.guild.id if target_channel_obj else None
        if not is_owner and source_guild_id_int != interaction.guild.id and target_guild_id_int != interaction.guild.id:
            await interaction.followup.send(
                "❌ You can only remove forwarding rules that involve this server.",
                ephemeral=True
            )
            return

        try:
            with DatabaseSession() as session:
                config = session.query(ForwardingConfig).filter(
                    ForwardingConfig.source_guild_id == source_guild_id,
                    ForwardingConfig.source_channel_id == source_channel_id,
                    ForwardingConfig.target_channel_id == target_channel_id,
                    ForwardingConfig.is_active == True
                ).first()
                
                if not config:
                    embed = discord.Embed(
                        title="❌ Forwarding Not Found",
                        description="No active forwarding configuration found with those parameters.",
                        color=0xff0000
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return
                
                session.query(ForwardingConfig).filter(ForwardingConfig.id == config.id).update({
                    'is_active': False,
                    'deleted_at': datetime.utcnow()
                })
                session.commit()
            
            source_guild = self.bot.get_guild(source_guild_id_int)
            source_channel = source_guild.get_channel(source_channel_id_int) if source_guild else None
            target_channel = self.bot.get_channel(target_channel_id_int)
            
            embed = discord.Embed(
                title="✅ Forwarding Removed",
                description="Cross-server message forwarding has been disabled.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            source_info = f"**Server:** {source_guild.name if source_guild else 'Unknown'}\n**Channel:** {source_channel.mention if source_channel else f'ID: {source_channel_id}'}"
            target_info = f"**Server:** {target_channel.guild.name if target_channel else 'Unknown'}\n**Channel:** {target_channel.mention if target_channel else f'ID: {target_channel_id}'}"
            
            embed.add_field(name="📤 Source", value=source_info, inline=True)
            embed.add_field(name="📥 Target", value=target_info, inline=True)
            
            await interaction.followup.send(embed=embed)
            self.logger.info(f"Forwarding removed via slash: {source_guild_id}#{source_channel_id} -> {target_channel_id} by {interaction.user}")
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Database Error",
                description=f"Failed to remove forwarding configuration: {str(e)}",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            self.logger.error(f"Error removing forwarding config via slash: {e}")

    @app_commands.command(name='removeforwardbyid', description='[Bot Owner or Trusted] Remove a forwarding by its ID from /listforwards')
    @app_commands.guild_only()
    @app_commands.describe(config_id='The ID of the forwarding configuration to remove')
    @owner_or_trusted()
    async def slash_remove_forward_by_id(self, interaction: discord.Interaction, config_id: int):
        """Remove a forwarding configuration by its database ID."""
        await interaction.response.defer(ephemeral=True)

        is_owner = interaction.user.id == interaction.client.owner_id

        try:
            with DatabaseSession() as session:
                config = session.query(ForwardingConfig).filter(
                    ForwardingConfig.id == config_id,
                    ForwardingConfig.is_active == True
                ).first()
                
                if not config:
                    embed = discord.Embed(
                        title="❌ Forwarding Not Found",
                        description=f"No active forwarding configuration found with ID: {config_id}",
                        color=0xff0000
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return

                # Non-owners may only remove forwarding rules that involve their own guild.
                guild_id_str = str(interaction.guild.id)
                if not is_owner and config.source_guild_id != guild_id_str and config.target_guild_id != guild_id_str:
                    await interaction.followup.send(
                        "❌ You can only remove forwarding rules that involve this server.",
                        ephemeral=True
                    )
                    return

                # Get info before disabling
                source_guild_id = config.source_guild_id
                source_channel_id = config.source_channel_id
                target_channel_id = config.target_channel_id
                
                session.query(ForwardingConfig).filter(ForwardingConfig.id == config_id).update({
                    'is_active': False,
                    'deleted_at': datetime.utcnow()
                })
                session.commit()
            
            source_guild = self.bot.get_guild(int(source_guild_id))
            source_channel = source_guild.get_channel(int(source_channel_id)) if source_guild else None
            target_channel = self.bot.get_channel(int(target_channel_id))
            
            embed = discord.Embed(
                title="✅ Forwarding Removed",
                description=f"Forwarding configuration #{config_id} has been disabled.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            source_name = f"{source_guild.name}#{source_channel.name}" if source_guild and source_channel else f"Unknown ({source_guild_id}#{source_channel_id})"
            target_name = f"{target_channel.guild.name}#{target_channel.name}" if target_channel else f"Unknown ({target_channel_id})"
            
            embed.add_field(name="📤 Source", value=source_name, inline=True)
            embed.add_field(name="📥 Target", value=target_name, inline=True)
            
            await interaction.followup.send(embed=embed)
            self.logger.info(f"Forwarding #{config_id} removed by {interaction.user}")
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Database Error",
                description=f"Failed to remove forwarding configuration: {str(e)}",
                color=0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            self.logger.error(f"Error removing forwarding by ID: {e}")

    # Interactive forwarding selection system
    @commands.command(name='selectforward', aliases=['select'])
    @commands.guild_only()
    @is_owner_or_trusted()
    async def select_forwarding(self, ctx):
        """Select which forwarding configurations to activate (Owner only)."""
        try:
            with DatabaseSession() as session:
                configs = session.query(ForwardingConfig).filter(
                    ForwardingConfig.is_active == False
                ).order_by(ForwardingConfig.created_at.desc()).all()
                
                # Extract data while session is active
                config_data = []
                for config in configs:
                    config_data.append({
                        'id': config.id,
                        'source_guild_id': config.source_guild_id,
                        'source_channel_id': config.source_channel_id,
                        'target_channel_id': config.target_channel_id,
                        'created_at': config.created_at,
                        'created_by': config.created_by
                    })
            
            if not config_data:
                embed = discord.Embed(
                    title="📋 No Inactive Forwarding Configurations",
                    description="All forwarding configurations are already active or none exist.",
                    color=0xffa500
                )
                await ctx.send(embed=embed)
                return
            
            embed = discord.Embed(
                title="📋 Select Forwarding Configuration to Activate",
                description=f"Choose from {len(config_data)} inactive configurations:",
                color=0x0099ff,
                timestamp=datetime.utcnow()
            )
            
            selection_text = ""
            for i, config in enumerate(config_data[:10], 1):  # Show first 10
                source_guild = self.bot.get_guild(int(config['source_guild_id']))
                source_channel = source_guild.get_channel(int(config['source_channel_id'])) if source_guild else None
                target_channel = self.bot.get_channel(int(config['target_channel_id']))
                
                source_name = f"{source_guild.name}#{source_channel.name}" if source_guild and source_channel else f"Unknown ({config['source_guild_id']}#{config['source_channel_id']})"
                target_name = f"{target_channel.guild.name}#{target_channel.name}" if target_channel else f"Unknown ({config['target_channel_id']})"
                
                selection_text += f"**{i}.** {source_name} → {target_name}\n"
            
            embed.add_field(
                name="🎯 Available Configurations",
                value=selection_text,
                inline=False
            )
            
            embed.add_field(
                name="💡 How to Activate",
                value="Use `!activateforward <number>` to activate a specific configuration\nExample: `!activateforward 1`",
                inline=False
            )
            
            if len(config_data) > 10:
                embed.set_footer(text=f"Showing 10 of {len(config_data)} configurations")
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Database Error",
                description=f"Failed to retrieve forwarding configurations: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            self.logger.error(f"Error selecting forwarding configs: {e}")

    @commands.command(name='activateforward', aliases=['activate'])
    @commands.guild_only()
    @is_owner_or_trusted()
    async def activate_forwarding(self, ctx, config_number: int):
        """Activate a specific forwarding configuration by number (Owner only)."""
        try:
            with DatabaseSession() as session:
                configs = session.query(ForwardingConfig).filter(
                    ForwardingConfig.is_active == False
                ).order_by(ForwardingConfig.created_at.desc()).all()
                
                if not configs:
                    embed = discord.Embed(
                        title="❌ No Inactive Configurations",
                        description="All forwarding configurations are already active or none exist.",
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                if config_number < 1 or config_number > len(configs):
                    embed = discord.Embed(
                        title="❌ Invalid Selection",
                        description=f"Please choose a number between 1 and {len(configs)}.",
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Get the selected config (1-indexed)
                selected_config = configs[config_number - 1]
                
                # Extract info before activating
                source_guild_id = selected_config.source_guild_id
                source_channel_id = selected_config.source_channel_id
                target_channel_id = selected_config.target_channel_id
                
                # Activate the configuration using SQLAlchemy's update method
                session.query(ForwardingConfig).filter(ForwardingConfig.id == selected_config.id).update({
                    'is_active': True,
                    'deleted_at': None
                })
                session.commit()
            
            # Get channel info for display
            source_guild = self.bot.get_guild(int(source_guild_id))
            source_channel = source_guild.get_channel(int(source_channel_id)) if source_guild else None
            target_channel = self.bot.get_channel(int(target_channel_id))
            
            embed = discord.Embed(
                title="✅ Forwarding Configuration Activated",
                description="The selected forwarding configuration is now active.",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            source_info = f"**Server:** {source_guild.name if source_guild else 'Unknown'}\n**Channel:** {source_channel.mention if source_channel else f'ID: {source_channel_id}'}"
            target_info = f"**Server:** {target_channel.guild.name if target_channel else 'Unknown'}\n**Channel:** {target_channel.mention if target_channel else f'ID: {target_channel_id}'}"
            
            embed.add_field(name="📤 Source", value=source_info, inline=True)
            embed.add_field(name="📥 Target", value=target_info, inline=True)
            embed.add_field(name="👤 Activated By", value=ctx.author.mention, inline=False)
            
            await ctx.send(embed=embed)
            self.logger.info(f"Forwarding activated: {source_guild_id}#{source_channel_id} -> {target_channel_id} by {ctx.author}")
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Database Error",
                description=f"Failed to activate forwarding configuration: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            self.logger.error(f"Error activating forwarding config: {e}")

    @commands.command(name='deactivateforward', aliases=['deactivate'])
    @commands.guild_only()
    @is_owner_or_trusted()
    async def deactivate_forwarding(self, ctx, config_number: int = None):
        """Deactivate forwarding configurations (Owner only)."""
        try:
            with DatabaseSession() as session:
                if config_number is None:
                    # Show active configs for selection
                    configs = session.query(ForwardingConfig).filter(
                        ForwardingConfig.is_active == True
                    ).order_by(ForwardingConfig.created_at.desc()).all()
                    
                    if not configs:
                        embed = discord.Embed(
                            title="📋 No Active Forwarding Configurations",
                            description="No forwarding configurations are currently active.",
                            color=0xffa500
                        )
                        await ctx.send(embed=embed)
                        return
                    
                    embed = discord.Embed(
                        title="📋 Select Forwarding Configuration to Deactivate",
                        description=f"Choose from {len(configs)} active configurations:",
                        color=0xff9900,
                        timestamp=datetime.utcnow()
                    )
                    
                    selection_text = ""
                    for i, config in enumerate(configs[:10], 1):  # Show first 10
                        source_guild = self.bot.get_guild(int(config.source_guild_id))
                        source_channel = source_guild.get_channel(int(config.source_channel_id)) if source_guild else None
                        target_channel = self.bot.get_channel(int(config.target_channel_id))
                        
                        source_name = f"{source_guild.name}#{source_channel.name}" if source_guild and source_channel else f"Unknown ({config.source_guild_id}#{config.source_channel_id})"
                        target_name = f"{target_channel.guild.name}#{target_channel.name}" if target_channel else f"Unknown ({config.target_channel_id})"
                        
                        selection_text += f"**{i}.** {source_name} → {target_name}\n"
                    
                    embed.add_field(
                        name="🎯 Active Configurations",
                        value=selection_text,
                        inline=False
                    )
                    
                    embed.add_field(
                        name="💡 How to Deactivate",
                        value="Use `!deactivateforward <number>` to deactivate a specific configuration\nExample: `!deactivateforward 1`",
                        inline=False
                    )
                    
                    if len(configs) > 10:
                        embed.set_footer(text=f"Showing 10 of {len(configs)} configurations")
                    
                    await ctx.send(embed=embed)
                    return
                
                # Deactivate specific config by number
                configs = session.query(ForwardingConfig).filter(
                    ForwardingConfig.is_active == True
                ).order_by(ForwardingConfig.created_at.desc()).all()
                
                if not configs:
                    embed = discord.Embed(
                        title="❌ No Active Configurations",
                        description="No forwarding configurations are currently active.",
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                if config_number < 1 or config_number > len(configs):
                    embed = discord.Embed(
                        title="❌ Invalid Selection",
                        description=f"Please choose a number between 1 and {len(configs)}.",
                        color=0xff0000
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Get the selected config (1-indexed)
                selected_config = configs[config_number - 1]
                
                # Extract info before deactivating
                source_guild_id = selected_config.source_guild_id
                source_channel_id = selected_config.source_channel_id
                target_channel_id = selected_config.target_channel_id
                
                # Deactivate the configuration using SQLAlchemy's update method
                session.query(ForwardingConfig).filter(ForwardingConfig.id == selected_config.id).update({
                    'is_active': False,
                    'deleted_at': datetime.utcnow()
                })
                session.commit()
            
            # Get channel info for display
            source_guild = self.bot.get_guild(int(source_guild_id))
            source_channel = source_guild.get_channel(int(source_channel_id)) if source_guild else None
            target_channel = self.bot.get_channel(int(target_channel_id))
            
            embed = discord.Embed(
                title="⛔ Forwarding Configuration Deactivated",
                description="The selected forwarding configuration has been deactivated.",
                color=0xff0000,
                timestamp=datetime.utcnow()
            )
            
            source_info = f"**Server:** {source_guild.name if source_guild else 'Unknown'}\n**Channel:** {source_channel.mention if source_channel else f'ID: {source_channel_id}'}"
            target_info = f"**Server:** {target_channel.guild.name if target_channel else 'Unknown'}\n**Channel:** {target_channel.mention if target_channel else f'ID: {target_channel_id}'}"
            
            embed.add_field(name="📤 Source", value=source_info, inline=True)
            embed.add_field(name="📥 Target", value=target_info, inline=True)
            embed.add_field(name="👤 Deactivated By", value=ctx.author.mention, inline=False)
            
            await ctx.send(embed=embed)
            self.logger.info(f"Forwarding deactivated: {source_guild_id}#{source_channel_id} -> {target_channel_id} by {ctx.author}")
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Database Error",
                description=f"Failed to deactivate forwarding configuration: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            self.logger.error(f"Error deactivating forwarding config: {e}")

    # Easy start forwarding commands - for current channel
    @commands.command(name='start', aliases=['starthere'])
    @commands.guild_only()
    @is_owner_or_trusted()
    async def start_forwarding(self, ctx, target_guild_id: int, target_channel_id: int = 0):
        """Start forwarding from current channel to target channel (Owner only)."""
        # Use current channel as target if not specified
        if target_channel_id == 0:
            target_channel_id = ctx.channel.id
        
        source_guild_id = ctx.guild.id
        source_channel_id = ctx.channel.id
        
        # Validate target guild
        target_guild = self.bot.get_guild(target_guild_id)
        if target_guild is None:
            embed = discord.Embed(
                title="❌ Target Server Not Found",
                description=f"Bot is not in server with ID: {target_guild_id}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        # Validate target channel
        target_channel = target_guild.get_channel(target_channel_id)
        if target_channel is None:
            embed = discord.Embed(
                title="❌ Target Channel Not Found", 
                description=f"Channel with ID {target_channel_id} not found in {target_guild.name}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        # Check bot permissions
        source_permissions = ctx.channel.permissions_for(ctx.guild.me)
        if not source_permissions.read_messages:
            embed = discord.Embed(
                title="❌ Missing Source Permissions",
                description=f"Bot lacks read permissions in {ctx.channel.mention}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        target_permissions = target_channel.permissions_for(target_guild.me)
        if not target_permissions.send_messages:
            embed = discord.Embed(
                title="❌ Missing Target Permissions",
                description=f"Bot lacks send permissions in {target_channel.mention}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            return
        
        # Add to database
        try:
            with DatabaseSession() as session:
                # Check if config already exists
                existing = session.query(ForwardingConfig).filter(
                    ForwardingConfig.source_guild_id == str(source_guild_id),
                    ForwardingConfig.source_channel_id == str(source_channel_id),
                    ForwardingConfig.target_channel_id == str(target_channel_id),
                    ForwardingConfig.is_active == True
                ).first()
                
                if existing:
                    embed = discord.Embed(
                        title="⚠️ Already Forwarding",
                        description="This forwarding configuration already exists.",
                        color=0xffa500
                    )
                    await ctx.send(embed=embed)
                    return
                
                # Create new config
                config = ForwardingConfig(
                    source_guild_id=str(source_guild_id),
                    source_channel_id=str(source_channel_id),
                    target_channel_id=str(target_channel_id),
                    created_by=str(ctx.author.id),
                    is_active=True,
                    created_at=datetime.utcnow()
                )
                
                session.add(config)
                session.commit()
            
            embed = discord.Embed(
                title="✅ Forwarding Started",
                description="Messages from this channel will now be forwarded!",
                color=0x00ff00,
                timestamp=datetime.utcnow()
            )
            
            source_info = f"**Server:** {ctx.guild.name}\n**Channel:** {ctx.channel.mention}"
            target_info = f"**Server:** {target_guild.name}\n**Channel:** {target_channel.mention}"
            
            embed.add_field(name="📤 Source (This Channel)", value=source_info, inline=True)
            embed.add_field(name="📥 Target", value=target_info, inline=True)
            embed.add_field(name="👤 Started By", value=ctx.author.mention, inline=False)
            
            await ctx.send(embed=embed)
            self.logger.info(f"Forwarding started: {ctx.guild.name}#{ctx.channel.name} -> {target_guild.name}#{target_channel.name} by {ctx.author}")
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Database Error",
                description=f"Failed to start forwarding: {str(e)}",
                color=0xff0000
            )
            await ctx.send(embed=embed)
            self.logger.error(f"Error starting forwarding: {e}")

    # All orphaned slash command interaction code removed completely
    # All orphaned slash command interaction code removed

async def setup(bot):
    await bot.add_cog(ForwardingCommands(bot))