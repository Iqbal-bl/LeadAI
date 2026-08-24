import phonenumbers
from fastapi import HTTPException


def validate_phone_number(number: str) -> str:
    """Validate and return a phone number in international E.164 format.

    A leading ``+`` is preferred. For backward compatibility, an all-digit
    value is treated as an international number and prefixed with ``+``.
    National-format numbers cannot be interpreted safely without a country.
    """
    raw = (number or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Phone number is required")

    # Allow harmless visual separators while rejecting letters/extensions.
    compact = raw.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    elif compact.isdigit():
        compact = "+" + compact

    if not compact.startswith("+") or not compact[1:].isdigit():
        raise HTTPException(
            status_code=400,
            detail="Invalid phone number. Use international format, for example +14155552671",
        )

    try:
        parsed = phonenumbers.parse(compact, None)
        if not phonenumbers.is_possible_number(parsed) or not phonenumbers.is_valid_number(parsed):
            raise ValueError("number is not possible or valid")
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except (phonenumbers.NumberParseException, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Invalid phone number. Include the country code, for example +14155552671",
        )
