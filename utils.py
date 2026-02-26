import random
import string
import os
from urllib.parse import urlparse


def is_valid_url(url: str) -> bool:
    """Perform a conservative syntactic check that the URL is valid.

    Requirements:
    - scheme must be http or https
    - netloc (host) must be present
    - reasonable length (protect against extremely long values)
    """
    if not url or not isinstance(url, str):
        return False
    if len(url) > 2000:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    if not parsed.netloc:
        return False
    return True

def generate_short_id(length=8):
    """Generate a random alphanumeric short ID."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))

def ensure_uploads_dir():
    """Ensure that the uploads directory exists."""
    if not os.path.exists("uploads"):
        os.makedirs("uploads")
