import base64
import hashlib
import hmac
import json
import os
import time


_TOKEN_TTL_SECONDS = 600
_SECRET_ENV_KEY = "FLASK_SECRET_KEY"


def _get_secret() -> bytes:
    secret = os.getenv(_SECRET_ENV_KEY, "")
    if not secret:
        raise RuntimeError(
            f"Environment variable {_SECRET_ENV_KEY} is not set; "
            "cannot generate or verify OAuth binding tokens."
        )
    return secret.encode()


def create_oauth_binding_token(discord_user_id: int | str, guild_id: int | str | None = None) -> str:
    """Return a short-lived, HMAC-signed token encoding *discord_user_id* and optional *guild_id*.

    The token is safe to embed in a public URL because it cannot be forged or
    modified without knowing the server secret, and it expires after
    ``_TOKEN_TTL_SECONDS`` seconds.
    """
    payload_dict = {"uid": str(discord_user_id), "exp": int(time.time()) + _TOKEN_TTL_SECONDS}
    if guild_id is not None:
        payload_dict["gid"] = str(guild_id)
    payload = json.dumps(payload_dict, separators=(",", ":")).encode()
    encoded_payload = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    sig = hmac.new(_get_secret(), encoded_payload.encode(), hashlib.sha256).hexdigest()
    return f"{encoded_payload}.{sig}"


def verify_oauth_binding_token(token: str) -> tuple[int, int | None] | None:
    """Verify *token* and return ``(discord_user_id, guild_id)`` it encodes.

    Returns ``None`` if the token is missing, malformed, expired, or has an
    invalid signature.  *guild_id* is ``None`` when not present in the token.
    """
    if not token or "." not in token:
        return None
    try:
        encoded_payload, sig = token.rsplit(".", 1)
        expected_sig = hmac.new(
            _get_secret(), encoded_payload.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        padding = 4 - len(encoded_payload) % 4
        padded = encoded_payload + ("=" * (padding % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if int(time.time()) > payload["exp"]:
            return None
        guild_id = int(payload["gid"]) if payload.get("gid") else None
        return int(payload["uid"]), guild_id
    except Exception:
        return None
