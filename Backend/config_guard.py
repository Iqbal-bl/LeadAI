"""
Fail-fast configuration validation.

WHY THIS EXISTS AS A SEPARATE FILE
The insecure defaults are spread across three places:

    auth.py:11                JWT_SECRET_KEY -> "demo-secret-key"
    multiligual_call.py:169   SESSION_SECRET -> "demo-secret-key"
    LeadAI/config.py:79       both of the above, again

Fixing them in place would mean editing multiligual_call.py, which the project's
integration doctrine keeps byte-identical to what runs in production. So instead
this module validates the environment ONCE at startup and refuses to boot a
production process that is running on a signing key published in a public repo.

That is not a theoretical risk. "demo-secret-key" is in the source. Anyone who
has read the repo can mint a valid session cookie and a valid JWT for any user
if the real value is missing from .env — and a missing .env value is silent,
because `os.getenv(k, default)` cannot tell you it fell through.

BEHAVIOUR
    ENVIRONMENT=production (or staging)  ->  hard failure on any critical issue
    anything else                        ->  loud warnings, boot continues

Development stays frictionless; production cannot start misconfigured.

Wire it up as the FIRST import in main.py, before the app is constructed.
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

# Values that mean "nobody set this". Extend rather than replace.
PLACEHOLDERS = {
    "",
    "demo-secret-key",
    "change-this-to-a-long-random-string",
    "change-this-too",
    "changeme",
    "your_key_here",
    "placeholder",
    "todo",
    "xxx",
}

MIN_SECRET_LENGTH = 32


def _is_placeholder(value: str | None) -> bool:
    return (value or "").strip().lower() in PLACEHOLDERS


def _check_secret(name: str, *, min_length: int = MIN_SECRET_LENGTH) -> str | None:
    raw = os.getenv(name)
    if _is_placeholder(raw):
        return f"{name} is unset or still a placeholder — sessions and tokens would be forgeable."
    if len(raw or "") < min_length:
        return (
            f"{name} is only {len(raw or '')} characters. Use at least {min_length}: "
            f"`python -c \"import secrets;print(secrets.token_urlsafe(48))\"`"
        )
    return None


def _check_required(name: str, why: str) -> str | None:
    if _is_placeholder(os.getenv(name)):
        return f"{name} is unset or a placeholder — {why}"
    return None


def collect_issues() -> tuple[list[str], list[str]]:
    """Return (critical, warnings). Neither raises; the caller decides."""
    critical: list[str] = []
    warnings: list[str] = []

    # --- signing keys -----------------------------------------------------
    for key in ("JWT_SECRET_KEY", "SESSION_SECRET"):
        issue = _check_secret(key)
        if issue:
            critical.append(issue)

    if os.getenv("JWT_SECRET_KEY") and os.getenv("JWT_SECRET_KEY") == os.getenv("SESSION_SECRET"):
        warnings.append(
            "JWT_SECRET_KEY and SESSION_SECRET are identical. Separate them so rotating "
            "one does not invalidate the other."
        )

    # --- database ---------------------------------------------------------
    for key, why in (
        ("MYSQL_HOST", "the app cannot connect to its database."),
        ("MYSQL_USER", "the app cannot connect to its database."),
        ("MYSQL_DATABASE", "the app cannot connect to its database."),
    ):
        issue = _check_required(key, why)
        if issue:
            critical.append(issue)

    if _is_placeholder(os.getenv("MYSQL_PASSWORD")):
        critical.append("MYSQL_PASSWORD is unset or a placeholder.")

    # --- LeadAI PII -------------------------------------------------------
    # Without LEADAI_PII_KEY, security._fernet() derives a key from the JWT
    # secret. That works, but it silently couples PII decryptability to the JWT
    # secret: rotate the JWT secret and every stored phone number becomes
    # permanently unreadable. In production that must be an explicit choice.
    if _is_placeholder(os.getenv("LEADAI_PII_KEY")):
        critical.append(
            "LEADAI_PII_KEY is unset. PII encryption will fall back to a key derived from "
            "JWT_SECRET_KEY, so rotating that secret would irreversibly destroy every stored "
            "phone number and email. Generate one: "
            "`python -c \"from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())\"`"
        )

    if _is_placeholder(os.getenv("LEADAI_BOOTSTRAP_ADMINS")):
        warnings.append(
            "LEADAI_BOOTSTRAP_ADMINS is unset — nobody can obtain the first platform-admin "
            "grant, so /companies and /knowledge will 403 for every caller."
        )

    # --- cookies ----------------------------------------------------------
    if (os.getenv("JWT_COOKIE_SECURE", "true") or "").lower() in ("0", "false", "no"):
        critical.append("JWT_COOKIE_SECURE is disabled — auth cookies would be sent over plain HTTP.")

    # --- AI / telephony (degraded, not fatal) -----------------------------
    if _is_placeholder(os.getenv("OPENAI_API_KEY")):
        warnings.append(
            "OPENAI_API_KEY is unset. LeadAI falls back to offline embeddings and extractive "
            "answering — functional, but noticeably worse. Demo quality will suffer."
        )

    if _is_placeholder(os.getenv("TWILIO_ACCOUNT_SID")) and _is_placeholder(os.getenv("EXOTEL_SID")):
        warnings.append("No telephony provider configured — outbound calling is disabled.")

    if _is_placeholder(os.getenv("SERVER_URL")) and _is_placeholder(os.getenv("NGROKURL")):
        warnings.append(
            "Neither SERVER_URL nor NGROKURL is set. Carrier webhooks (/outbound-twiml, "
            "/media-stream) have nowhere to call back to, so calls will connect and go silent."
        )

    return critical, warnings


def validate(strict: bool | None = None) -> None:
    """Validate the environment. Exits non-zero on a critical issue in production.

    `strict` defaults to True when ENVIRONMENT is production or staging.
    """
    env = (os.getenv("ENVIRONMENT") or "development").strip().lower()
    if strict is None:
        strict = env in ("production", "staging", "prod")

    critical, warnings = collect_issues()

    for w in warnings:
        logger.warning("[config] %s", w)

    if not critical:
        if warnings:
            logger.info("[config] %d warning(s), no critical issues (env=%s)", len(warnings), env)
        else:
            logger.info("[config] validated clean (env=%s)", env)
        return

    banner = "\n".join(f"  ✗ {c}" for c in critical)

    if not strict:
        logger.warning(
            "[config] %d CRITICAL issue(s) — tolerated because ENVIRONMENT=%s.\n%s\n"
            "  These WILL prevent startup once ENVIRONMENT=production.",
            len(critical),
            env,
            banner,
        )
        return

    sys.stderr.write(
        "\n"
        "═══════════════════════════════════════════════════════════════════\n"
        f" REFUSING TO START — {len(critical)} critical configuration issue(s)\n"
        f" ENVIRONMENT={env}\n"
        "═══════════════════════════════════════════════════════════════════\n"
        f"{banner}\n"
        "═══════════════════════════════════════════════════════════════════\n"
        " Fix these in .env, or set ENVIRONMENT=development to boot anyway.\n\n"
    )
    raise SystemExit(78)  # EX_CONFIG


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from dotenv import load_dotenv

    load_dotenv()
    validate(strict=False)
    print("\nDry run complete — nothing above means the environment is clean.")
