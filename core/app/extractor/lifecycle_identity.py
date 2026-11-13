"""Resolve an explicit Telegram identity from a lifecycle message."""
import re


_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_EXPLICIT_USERNAME = re.compile(
    r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_]{4,31})(?![A-Za-z0-9_])"
    r"|(?:TG|Telegram|电报|账号|联系(?:人)?)[\s:：=]*@?"
    r"([A-Za-z][A-Za-z0-9_]{4,31})(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def explicit_lifecycle_username(text: str, extracted_username: str | None) -> str | None:
    """Return a username only when the lifecycle text explicitly names it."""
    username = (extracted_username or "").strip().lstrip("@").lower()
    if not username or not _USERNAME.fullmatch(username):
        return None

    identities = {
        (match.group(1) or match.group(2)).lower()
        for match in _EXPLICIT_USERNAME.finditer(text or "")
    }
    if identities == {username}:
        return username
    return None
