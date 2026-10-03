# Discord Bot - 24/7 Replit Hosting

A complete Discord bot setup designed for 24/7 hosting on Replit with comprehensive monitoring and debugging capabilities.

## Features

- ✅ 24/7 uptime with keep-alive mechanism
- ✅ Comprehensive logging and debugging
- ✅ Web-based status monitoring
- ✅ Basic Discord commands (ping, info, help, uptime)
- ✅ Error handling and auto-reconnection
- ✅ Environment variable management
- ✅ Health check endpoints
- ✅ Modern web interface for monitoring

## Quick Setup

### 1. Environment Variables

Set these environment variables in your Replit Secrets:

**Required:**
- `DISCORD_BOT_TOKEN` - Your Discord bot token

**Optional:**
- `COMMAND_PREFIX` - Bot command prefix (default: `!`)
- `BOT_NAME` - Display name for your bot (default: `My Discord Bot`)
- `LOG_LEVEL` - Logging level (default: `INFO`)

### 2. Getting Your Bot Token

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application
3. Go to "Bot" section
4. Create a bot and copy the token
5. Add the token to your Replit Secrets as `DISCORD_BOT_TOKEN`

### 3. Running the Bot

1. Click the "Run" button in Replit
2. The bot will start automatically
3. Visit the web interface to monitor status
4. Use external monitoring services to ping your Replit URL

## File Structure

