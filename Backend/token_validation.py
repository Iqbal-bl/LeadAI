import os
import json
import time
import asyncio
import logging
import httpx
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Network tuning (overridable via env). Generous read timeout so a momentary
# slow response from the identity server doesn't masquerade as a bad token.
_CONNECT_TIMEOUT = float(os.getenv("IDP_CONNECT_TIMEOUT", "5"))
_READ_TIMEOUT = float(os.getenv("IDP_READ_TIMEOUT", "10"))
_FETCH_RETRIES = int(os.getenv("IDP_FETCH_RETRIES", "1"))  # extra attempts on network error

# Clock skew tolerance (seconds). Allows tokens with iat slightly in the future
# (client clock ahead of server). Default 30s handles typical NTP drift.
_LEEWAY = int(os.getenv("IDP_LEEWAY", "30"))

# In-process JWKS cache: the signing keys rarely change, so we fetch the
# discovery doc + JWKS once and reuse them. This removes two HTTP round-trips
# from every API request (the source of the per-call slowness) and keeps auth
# working through brief identity-server blips. TTL-bounded so key rotation is
# eventually picked up; a cache miss on an unknown `kid` forces a refresh.
_jwks_cache = {"jwks": None, "jwks_uri": None, "fetched_at": 0.0}
_JWKS_TTL = float(os.getenv("IDP_JWKS_TTL", "3600"))  # seconds
_cache_lock = asyncio.Lock()


def _timeout():
    return httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)
 

async def _get_json(client: httpx.AsyncClient, url: str):
    """GET a URL, retrying once on a transient network error (connect/read
    timeout, connection drop). Returns parsed JSON or raises."""
    last_exc = None
    for attempt in range(_FETCH_RETRIES + 1):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
                httpx.RemoteProtocolError) as e:
            last_exc = e
            logger.warning(f"[IDP] fetch {url} failed (attempt {attempt + 1}): {type(e).__name__}")
            if attempt < _FETCH_RETRIES:
                await asyncio.sleep(0.3)
    raise last_exc


async def _load_jwks(identity_server_url: str, force: bool = False):
    """Return JWKS, using the cache unless expired or `force`."""
    now = time.time()
    if (not force and _jwks_cache["jwks"] is not None
            and (now - _jwks_cache["fetched_at"]) < _JWKS_TTL):
        return _jwks_cache["jwks"]

    async with _cache_lock:
        # Re-check after acquiring the lock (another request may have refreshed).
        now = time.time()
        if (not force and _jwks_cache["jwks"] is not None
                and (now - _jwks_cache["fetched_at"]) < _JWKS_TTL):
            return _jwks_cache["jwks"]

        async with httpx.AsyncClient(verify=False, timeout=_timeout()) as client:
            discovery = await _get_json(
                client, f"{identity_server_url}/.well-known/openid-configuration")
            jwks_uri = discovery.get("jwks_uri")
            if not jwks_uri:
                raise ValueError("JWKS URI not found in OpenID configuration")
            jwks = await _get_json(client, jwks_uri)

        _jwks_cache.update({"jwks": jwks, "jwks_uri": jwks_uri, "fetched_at": time.time()})
        logger.info(f"[IDP] JWKS cached ({len(jwks.get('keys', []))} keys) from {jwks_uri}")
        return jwks


def _find_key(jwks: dict, kid: str):
    return next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)


async def validate_token_async(token: str):
    """Validate a Bearer token against the local identity server using OpenID
    discovery + JWKS (RS256). Only LOCAL_IDENTITY_SERVER is read from env.

    Returns the decoded JWT claims dict on success, or None on any failure.
    Network/identity-server problems are logged distinctly from bad tokens."""
    identity_server_url = os.getenv("LOCAL_IDENTITY_SERVER")
    if not identity_server_url:
        logger.error("[IDP] LOCAL_IDENTITY_SERVER is not set")
        return None

    # Read the token header up front so we know which key id we need.
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except Exception as e:
        logger.error(f"❌ Malformed token header: {e}")
        return None
    if not kid:
        logger.error("No 'kid' in token header")
        return None

    # Fetch keys (cached). On a network failure, log it as an IDP/connectivity
    # problem — NOT a token problem — and return None.
    try:
        jwks = await _load_jwks(identity_server_url)
    except Exception as e:
        logger.error(f"[IDP] identity server unreachable / discovery failed: "
                     f"{type(e).__name__}: {e}")
        return None

    # If the kid isn't in the cached set, the keys may have rotated — force one
    # refresh before giving up.
    key_data = _find_key(jwks, kid)
    if not key_data:
        logger.info("[IDP] kid not in cached JWKS; forcing refresh")
        try:
            jwks = await _load_jwks(identity_server_url, force=True)
            key_data = _find_key(jwks, kid)
        except Exception as e:
            logger.error(f"[IDP] JWKS refresh failed: {type(e).__name__}: {e}")
            return None
    if not key_data:
        logger.error("No matching key found in JWKS")
        return None

    # Verify signature + claims. The "iss" claim in issued tokens is the
    # identity server's externally-reachable URL, which can differ from
    # LOCAL_IDENTITY_SERVER (used for in-cluster discovery/JWKS). TOKEN_ISSUER
    # lets the two be configured independently; defaults to LOCAL_IDENTITY_SERVER.
    issuer = os.getenv("TOKEN_ISSUER", identity_server_url)
    try:
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
        decoded = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=issuer,
            leeway=_LEEWAY,
        )
        logger.info("✅ Token successfully validated")
        return decoded
    except ExpiredSignatureError:
        logger.error("❌ Token has expired")
        return None
    except InvalidTokenError as e:
        logger.error(f"❌ Invalid token: {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error during token decode: {e}")
        return None
