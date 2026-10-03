"""
Standalone Flask OAuth server for Twitch token setup.

Run this via the "Twitch OAuth Setup" workflow when you need to link a
Twitch account.  It starts ONLY the web server (no Discord bot) so the
production bot keeps running without any double-response conflicts.

Usage:
  1. Start the "Twitch OAuth Setup" workflow.
  2. Run /twitchoauth in Discord — the bot will reply with a link.
  3. Click the link, authorize on Twitch, and you're done.
  4. Stop the "Twitch OAuth Setup" workflow when finished.
"""
import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('oauth_server')

# Import the Flask app — this registers all OAuth routes without starting Discord
from consolidated_train_bot import app

if __name__ == '__main__':
    port = int(os.getenv('PORT', os.getenv('REPLIT_PORT', 5000)))

    from utils.bot_urls import get_oauth_base_url
    base_url = get_oauth_base_url()

    logger.info("=" * 60)
    logger.info("Twitch OAuth Setup Server")
    logger.info(f"Listening on port {port}")
    logger.info(f"OAuth base URL: {base_url}")
    logger.info("Run /twitchoauth in Discord, then click the link.")
    logger.info("=" * 60)

    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False, threaded=True)
