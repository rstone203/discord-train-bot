"""
Stripe credential and client helper.

Fetches live Stripe credentials from Replit's connector proxy (the Stripe
integration connected via the Integrations tab). Credentials can rotate, so
they are fetched fresh on every call rather than cached at import time.
"""

import os
import logging
import requests
import stripe

logger = logging.getLogger('discord_bot.stripe_client')


class StripeNotConnectedError(RuntimeError):
    """Raised when the Stripe integration has not been connected in this Repl."""
    pass


def _get_replit_token():
    if os.environ.get('REPL_IDENTITY'):
        return "repl " + os.environ.get('REPL_IDENTITY')
    if os.environ.get('WEB_REPL_RENEWAL'):
        return "depl " + os.environ.get('WEB_REPL_RENEWAL')
    return None


def get_stripe_credentials():
    """Fetch (secret_key, webhook_secret) from the Replit Stripe connector.

    Raises StripeNotConnectedError if the connector isn't set up or the
    request fails, so callers can surface a clear error instead of a
    confusing Stripe SDK exception.
    """
    hostname = os.environ.get('REPLIT_CONNECTORS_HOSTNAME')
    token = _get_replit_token()

    if not hostname or not token:
        raise StripeNotConnectedError(
            "Stripe integration is not connected. Connect it via the Integrations tab."
        )

    url = f"https://{hostname}/api/v2/connection?include_secrets=true&connector_names=stripe"
    try:
        resp = requests.get(url, headers={"X-REPLIT-TOKEN": token}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"Failed to fetch Stripe credentials from connector: {e}")
        raise StripeNotConnectedError(f"Could not reach Stripe connector: {e}")

    items = data.get('items', [])
    if not items:
        raise StripeNotConnectedError("Stripe integration is not connected in this Repl.")

    settings = items[0].get('settings', {})
    secret_key = settings.get('secret')
    webhook_secret = settings.get('webhook_secret')

    if not secret_key:
        raise StripeNotConnectedError("Stripe connection is missing a secret key.")

    return secret_key, webhook_secret


def get_stripe_client():
    """Return an authenticated stripe.StripeClient using the live connector key."""
    secret_key, _ = get_stripe_credentials()
    return stripe.StripeClient(secret_key)
