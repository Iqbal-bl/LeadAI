import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict

from fastapi import HTTPException, Request, status, WebSocket, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import bcrypt
from twilio.request_validator import RequestValidator

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", os.getenv("SESSION_SECRET", "demo-secret-key"))
if JWT_SECRET_KEY == "demo-secret-key":
    import warnings
    warnings.warn("JWT_SECRET_KEY is using a default value 'demo-secret-key'. This is insecure.")

JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRES_MINUTES = int(os.getenv("JWT_EXPIRES_MINUTES", "120"))
JWT_LEEWAY = int(os.getenv("JWT_LEEWAY", "30"))
JWT_COOKIE_NAME = os.getenv("JWT_COOKIE_NAME", "access_token")
JWT_REFRESH_COOKIE_NAME = os.getenv("JWT_REFRESH_COOKIE_NAME", "refresh_token")
SERVER_URL = os.getenv("SERVER_URL", "")
# Secure cookies only if explicitly requested via env var (safer for dev/prod mix)
JWT_COOKIE_SECURE = os.getenv("JWT_COOKIE_SECURE", "false").lower() in ("1", "true", "yes")

# Security scheme for Swagger UI (Simple Bearer Token)
security_scheme = HTTPBearer(auto_error=False)

# Twilio validator setup
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
twilio_validator = RequestValidator(TWILIO_AUTH_TOKEN) if TWILIO_AUTH_TOKEN else None


def get_password_hash(password: str) -> str:
    """Hash a plain password using bcrypt directly"""
    if not password:
        raise ValueError("Password cannot be empty")
    
    pw_bytes = password.encode('utf-8')
    if len(pw_bytes) > 72:
        raise ValueError("Password is too long (max 72 bytes for bcrypt)")
        
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pw_bytes, salt)
    return hashed.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hash using bcrypt directly"""
    if not plain_password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )
    except Exception:
        return False


def create_stream_token(call_sid: str) -> str:
    """Create a short-lived token for a Twilio media stream"""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=5)
    payload = {
        "sub": call_sid,
        "iat": now,
        "exp": expire,
        "type": "stream",
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_access_token(subject: str, expires_minutes: Optional[int] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expires_minutes if expires_minutes is not None else JWT_EXPIRES_MINUTES)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": expire,
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(subject: str, expires_minutes: Optional[int] = None) -> str:
    now = datetime.now(timezone.utc)
    refresh_expire_minutes = int(os.getenv("JWT_REFRESH_EXPIRES_MINUTES", "43200"))
    expire = now + timedelta(minutes=expires_minutes if expires_minutes is not None else refresh_expire_minutes)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": expire,
        "type": "refresh",
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


async def validate_twilio_request(request: Request):
    """Validate that the request came from Twilio"""
    if not twilio_validator:
        if os.getenv("ENVIRONMENT") == "production":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Twilio validation required in production")
        return
    
    url = f"{SERVER_URL.rstrip('/')}{request.url.path}"
    if request.url.query:
        url += f"?{request.url.query}"
    # if "SERVER_URL" in os.environ:
    #     import urllib.parse
    #     parsed = urllib.parse.urlparse(url)
    #     parsed_server = urllib.parse.urlparse(os.environ["SERVER_URL"])
    #     url = parsed._replace(scheme=parsed_server.scheme, netloc=parsed_server.netloc).geturl()

    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing Twilio signature")
    
    form = await request.form()
    params = dict(form)
    
    if not twilio_validator.validate(url, params, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


def _get_token_from_header(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header:
        return None
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return None


def _decode_token(token: str, expected_type: str) -> Dict[str, str]:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM], leeway=JWT_LEEWAY)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if not payload or payload.get("sub") is None or payload.get("type") != expected_type:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")
    return payload


def get_token_from_request(request: Request, auth: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme)) -> Optional[str]:
    """FastAPI dependency to extract token from Header (Bearer), Cookies, or Swagger Authorize"""
    # 1. First priority: Swagger Authorize or Header
    if auth:
        return auth.credentials
    
    # 2. Second priority: Cookies (for browser clients)
    return request.cookies.get(JWT_COOKIE_NAME)


def _get_user_from_token_optional(token: Optional[str]) -> Optional[str]:
    """Internal helper to get user from token without raising FastAPI exceptions directly"""
    if not token:
        return None
    try:
        payload = _decode_token(token, "access")
        return payload.get("sub")
    except Exception:
        return None


def _user_from_identity(request: Request) -> Optional[str]:
    """Return the user identified by the identity-server token that the
    TokenValidationMiddleware already validated for this request. The middleware
    stashes the decoded claims on request.state.identity. We prefer the
    human-readable email, then username, then subject id."""
    identity = getattr(getattr(request, "state", None), "identity", None)
    if not identity:
        return None
    return (identity.get("user_email")
            or identity.get("user_name")
            or identity.get("email")
            or identity.get("sub"))


def get_current_user(request: Request) -> str:
    """Current user from the identity-server token validated upstream by the
    TokenValidationMiddleware (request.state.identity). Identity server is the
    only auth source."""
    user = _user_from_identity(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_user_optional(request: Request) -> Optional[str]:
    """Optional auth: identity-server claims if present, else None."""
    return _user_from_identity(request)


def get_token_from_websocket(ws: WebSocket) -> Optional[str]:
    """Extract token from WS headers, query params, or cookies"""
    token = _get_token_from_header(ws.headers.get("authorization"))
    if token:
        return token
    token = ws.query_params.get("token")
    if token:
        return token
    return ws.cookies.get(JWT_COOKIE_NAME)


async def get_current_user_websocket(ws: WebSocket) -> str:
    """Verify a WebSocket connection against the identity server (the only auth
    source). WS routes bypass the HTTP middleware, so validate here directly."""
    from token_validation import validate_token_async  # local import avoids cycle
    token = get_token_from_websocket(ws)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authentication token")

    identity = await validate_token_async(token)
    if not identity:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return (identity.get("user_email")
            or identity.get("user_name")
            or identity.get("email")
            or identity.get("sub"))


def verify_stream_token(ws: WebSocket) -> str:
    token = get_token_from_websocket(ws)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing stream token")
    payload = _decode_token(token, "stream")
    return payload["sub"]


def get_current_refresh_token(request: Request) -> str:
    token = _get_token_from_header(request.headers.get("Authorization"))
    if not token:
        token = request.cookies.get(JWT_REFRESH_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")
    return token


def decode_refresh_token(token: str) -> Dict[str, str]:
    return _decode_token(token, "refresh")


def is_authenticated_request(request: Request) -> bool:
    """Utility to check if a request is authenticated via JWT (manual check)"""
    # Manually extract token since we're outside FastAPI DI
    token = request.cookies.get(JWT_COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("Authorization")
        token = _get_token_from_header(auth_header)
        
    return _get_user_from_token_optional(token) is not None
