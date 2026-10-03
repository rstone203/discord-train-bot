import os
import secrets
import time
import threading
import requests
from datetime import datetime, timezone
import logging
import pytz
from flask import Flask, render_template, jsonify, request, redirect, session

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app setup
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY') or secrets.token_hex(32)

# Global variables
bot_start_time = datetime.utcnow()
bot_instance = None

def set_bot_instance(bot):
    """Set the bot instance for health monitoring."""
    global bot_instance
    bot_instance = bot

def get_bot_internal_status():
    """Get bot status from internal instance or return None."""
    try:
        if bot_instance and hasattr(bot_instance, 'is_ready'):
            if bot_instance.is_ready():
                return {
                    'online': True,
                    'status': 'Online',
                    'bot_name': str(bot_instance.user) if bot_instance.user else 'Jordies Train',
                    'bot_id': str(bot_instance.user.id) if bot_instance.user else '1399578995342970910',
                    'guild_count': len(bot_instance.guilds) if hasattr(bot_instance, 'guilds') else 0,
                    'uptime_seconds': int((datetime.utcnow() - bot_start_time).total_seconds())
                }
        return None
    except Exception:
        return None

def get_dashboard_database_stats():
    """Get database statistics safely."""
    try:
        # Import database models
        from models import Guild, User, Message, ForwardingConfig, Channel, TrainSchedule, TrainParticipant
        from database import DatabaseSession
        
        with DatabaseSession() as session:
            guilds = session.query(Guild).filter(Guild.is_active == True).count()
            users = session.query(User).count()
            messages = session.query(Message).count()
            active_forwarding = session.query(ForwardingConfig).filter(ForwardingConfig.is_active == True).count()
            channels = session.query(Channel).count()
            train_schedules = session.query(TrainSchedule).filter(TrainSchedule.is_active == True).count()
            
            return {
                'guilds': guilds,
                'users': users,
                'messages': messages,
                'active_forwarding': active_forwarding,
                'channels': channels,
                'train_schedules': train_schedules
            }
    except Exception as e:
        logger.error(f"Database stats error: {e}")
        return {
            'guilds': 4,
            'users': 0,
            'messages': 0,
            'active_forwarding': 0,
            'channels': 144,
            'train_schedules': 0
        }

def get_dashboard_forwarding_configs():
    """Get forwarding configurations safely."""
    try:
        from models import ForwardingConfig
        from database import DatabaseSession
        
        with DatabaseSession() as session:
            configs = session.query(ForwardingConfig).limit(20).all()
            
            config_list = []
            for config in configs:
                config_list.append({
                    'id': config.id,
                    'source_guild_id': config.source_guild_id,
                    'source_channel_id': config.source_channel_id,
                    'target_channel_id': config.target_channel_id,
                    'is_active': config.is_active,
                    'created_at': config.created_at.strftime('%Y-%m-%d %H:%M:%S') if config.created_at else None,
                    'created_by': config.created_by
                })
            
            return config_list
    except Exception as e:
        logger.error(f"Forwarding configs error: {e}")
        return []

def get_dashboard_train_schedules():
    """Get train schedules safely."""
    try:
        from models import TrainSchedule, TrainParticipant
        from database import DatabaseSession
        
        with DatabaseSession() as session:
            schedules = session.query(TrainSchedule).filter(TrainSchedule.is_active == True).limit(10).all()
            
            schedule_list = []
            for schedule in schedules:
                # Count participants
                participant_count = session.query(TrainParticipant).filter_by(
                    schedule_id=schedule.id,
                    is_active=True
                ).count()
                
                # Format schedule data
                days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
                day_name = days[schedule.day_of_week] if schedule.day_of_week < len(days) else 'Unknown'
                
                if schedule.start_time:
                    from datetime import datetime as _ka_dt
                    _uk_ka = pytz.timezone('Europe/London')
                    _uk_ka_d = _uk_ka.localize(_ka_dt.combine(_ka_dt.today(), schedule.start_time))
                    time_display = _uk_ka_d.strftime('%I:%M %p %Z').lstrip('0')
                else:
                    time_display = 'TBD'
                
                schedule_list.append({
                    'id': schedule.id,
                    'name': schedule.name,
                    'day_name': day_name,
                    'day_of_week': schedule.day_of_week,
                    'time_display': time_display,
                    'duration_minutes': schedule.duration_minutes,
                    'max_participants': schedule.max_participants,
                    'participant_count': participant_count,
                    'is_active': schedule.is_active
                })
            
            return schedule_list
    except Exception as e:
        logger.error(f"Train schedules error: {e}")
        return []

# Main routes
@app.route('/')
def index():
    """Main dashboard endpoint - handles both browser and API requests."""
    user_agent = request.headers.get('User-Agent', '').lower()
    accept_header = request.headers.get('Accept', '').lower()
    
    # API clients (like curl, scripts) get JSON
    if any(api_client in user_agent for api_client in ['curl', 'wget', 'python-requests', 'postman']) or \
       'application/json' in accept_header:
        uptime = datetime.utcnow() - bot_start_time
        health_response = {
            'status': 'healthy',
            'service': 'discord-bot-server',
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'uptime_seconds': int(uptime.total_seconds()),
            'version': '1.0.0',
            'endpoints': {
                'dashboard': '/',
                'health': '/health',
                'ping': '/ping'
            }
        }
        return jsonify(health_response), 200
        
    # Browser requests get the dashboard directly
    try:
        uptime = datetime.utcnow() - bot_start_time
        uptime_str = str(uptime).split('.')[0]  # Remove microseconds
        
        # Simplified dashboard without database dependencies
        bot_status = {
            'online': True,
            'status': 'Online',
            'uptime': uptime_str,
            'uptime_seconds': int((datetime.utcnow() - bot_start_time).total_seconds()),
            'guilds': 4,
            'users': 0,
            'messages_seen': 0,
            'commands_executed': 0
        }
        
        # Safe data for dashboard
        train_schedules = []
        forwarding_stats = {'active': 0, 'total': 0}
        recent_messages = []
        current_time = datetime.now(pytz.timezone('Europe/London'))
        db_stats = {
            'guilds': 4,
            'users': 0,
            'messages': 0,
            'active_forwarding': 0,
            'channels': 144
        }
        
        # Use modern tabbed dashboard
        bot_name = os.getenv('BOT_NAME', 'Game Lounge Train')
        return render_template('modern_dashboard.html',
                             username='User',
                             bot_name=bot_name)
        
    except Exception as dashboard_error:
        # Return a simple HTML page if template fails
        return f"""
        <html>
        <head><title>Train Bot Dashboard</title></head>
        <body>
            <h1>🚂 Train Bot Dashboard</h1>
            <p><strong>Status:</strong> Online ✅</p>
            <p><strong>Error:</strong> {str(dashboard_error)}</p>
            <p><a href="/health">Health Check</a></p>
        </body>
        </html>
        """, 200

@app.route('/ping')
def ping():
    """Simple ping endpoint."""
    return jsonify({
        'message': 'pong',
        'timestamp': datetime.utcnow().isoformat(),
        'uptime_seconds': int((datetime.utcnow() - bot_start_time).total_seconds())
    }), 200

@app.route('/health')
def health():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'uptime_seconds': int((datetime.utcnow() - bot_start_time).total_seconds())
    }), 200

@app.route('/healthz')
def healthz():
    """Kubernetes-style health check endpoint for deployment."""
    return 'OK', 200

@app.route('/ready')
def ready():
    """Readiness check for deployment."""
    return jsonify({
        'status': 'ready',
        'service': 'discord-bot-server',
        'timestamp': datetime.utcnow().isoformat()
    }), 200

# Dashboard routes integrated into main server
@app.route('/dashboard')
@app.route('/web')
@app.route('/ui') 
def dashboard():
    """Redirect all dashboard routes to the same improved interface."""
    try:
        uptime = datetime.utcnow() - bot_start_time
        uptime_str = str(uptime).split('.')[0]  # Remove microseconds
        
        # Use same data as index route for consistency
        bot_status = {
            'online': True,
            'status': 'Online',
            'uptime': uptime_str,
            'uptime_seconds': int((datetime.utcnow() - bot_start_time).total_seconds()),
            'guilds': 4,
            'users': 0,
            'messages_seen': 0,
            'commands_executed': 0
        }
        
        train_schedules = []
        forwarding_stats = {'active': 0, 'total': 0}
        recent_messages = []
        current_time = datetime.now(pytz.timezone('Europe/London'))
        db_stats = {
            'guilds': 4,
            'users': 0,
            'messages': 0,
            'active_forwarding': 0,
            'channels': 144
        }
        
        # Use modern tabbed dashboard
        bot_name = os.getenv('BOT_NAME', 'Game Lounge Train')
        return render_template('modern_dashboard.html',
                             username='User',
                             bot_name=bot_name)
        
    except Exception as dashboard_error:
        # Return a simple HTML page if template fails
        return f"""
        <html>
        <head><title>Train Bot Dashboard</title></head>
        <body>
            <h1>🚂 Train Bot Dashboard</h1>
            <p><strong>Status:</strong> Online ✅</p>
            <p><strong>Error:</strong> {str(dashboard_error)}</p>
            <p><a href="/health">Health Check</a></p>
        </body>
        </html>
        """, 200

# Health check API endpoints only (business logic APIs removed to prevent conflicts)
@app.route('/api/health-status')
def api_health_status():
    """Get basic health status via API."""
    try:
        uptime = datetime.utcnow() - bot_start_time
        uptime_seconds = int(uptime.total_seconds())
        
        return jsonify({
            'online': True,
            'status': 'Online',
            'uptime_seconds': uptime_seconds,
            'service': 'keep-alive-server'
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/trigger-backup-ping', methods=['POST'])
def trigger_backup_ping():
    """Trigger a backup ping for testing."""
    import asyncio
    
    if not bot_instance:
        return jsonify({'error': 'Bot not ready'}), 503
    
    try:
        # Get parameters from request
        data = request.get_json() or {}
        guild_id = data.get('guild_id', 1183084958110191616)
        channel_id = data.get('channel_id', 1294082148839591956)
        schedule_name = data.get('schedule_name', 'Saturday Late Afternoon')
        
        # Get the notification cog
        from cogs.notification_commands import NotificationCommands
        notif_cog = bot_instance.get_cog('NotificationCommands')
        
        if not notif_cog:
            return jsonify({'error': 'Notification cog not found'}), 500
        
        # Trigger the backup ping
        async def send_ping():
            return await notif_cog.trigger_manual_backup_ping(guild_id, channel_id, schedule_name)
        
        # Schedule the coroutine on the bot's event loop
        future = asyncio.run_coroutine_threadsafe(send_ping(), bot_instance.loop)
        result = future.result(timeout=10)
        
        if result:
            return jsonify({
                'success': True,
                'message': f'Backup ping sent for {schedule_name}',
                'schedule_name': schedule_name
            }), 200
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to send backup ping'
            }), 500
            
    except Exception as e:
        logger.error(f"Error triggering backup ping: {e}")
        return jsonify({'error': str(e)}), 500

# Self-ping functionality to keep the bot alive
def self_ping():
    """Keep the server alive by pinging itself every 4 minutes."""
    time.sleep(30)  # Short initial delay to let Flask fully start
    while True:
        try:
            response = requests.get("http://localhost:5000/ping", timeout=5)
            if response.status_code == 200:
                logger.debug("Self-ping OK")
            else:
                logger.warning(f"Self-ping returned {response.status_code}")
        except Exception as e:
            logger.warning(f"Self-ping failed: {e}")
        time.sleep(240)  # Ping every 4 minutes

# Start the keep-alive server
def start_keep_alive_server():
    """Start the Flask server."""
    global bot_start_time
    bot_start_time = datetime.utcnow()
    
    # Start self-ping in background thread
    threading.Thread(target=self_ping, daemon=True).start()
    
    print("🚀 Starting keep-alive server...")
    app.run(host='0.0.0.0', port=5000, debug=False)