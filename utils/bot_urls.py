"""
Centralized URL helpers so every cog uses the same base URL for OAuth links.
"""
import os


def get_oauth_base_url() -> str:
    """Return the correct base URL for OAuth callbacks and authorization links.

    REPLIT_DOMAINS contains the deployment's public domain(s) when deployed
    (e.g. rstone203.replit.app).  In dev it equals REPLIT_DEV_DOMAIN (a
    .replit.dev tunnel).

    Strategy: prefer any domain that is NOT a .replit.dev tunnel, because
    those are login-protected and inaccessible to regular users.  Fall back
    to the dev tunnel only when no public domain exists (i.e. pure local dev).
    """
    replit_domains = os.getenv('REPLIT_DOMAINS', '').strip()
    if replit_domains:
        candidates = [d.strip() for d in replit_domains.split(',') if d.strip()]
        public = [d for d in candidates if not d.endswith('.replit.dev')]
        if public:
            return f"https://{public[0]}"
        if candidates:
            return f"https://{candidates[0]}"

    dev_domain = os.getenv('REPLIT_DEV_DOMAIN', '').strip()
    if dev_domain:
        return f"https://{dev_domain}"

    return 'https://rstone203.replit.app'
