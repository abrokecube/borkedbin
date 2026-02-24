import random
import string
import os

def generate_short_id(length=8):
    """Generate a random alphanumeric short ID."""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))

def ensure_uploads_dir():
    """Ensure that the uploads directory exists."""
    if not os.path.exists("uploads"):
        os.makedirs("uploads")
