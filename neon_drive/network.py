"""Verified HTTPS with OS trust plus a CA bundle shipped inside the app."""
import ssl
from functools import lru_cache
import certifi


@lru_cache(maxsize=1)
def https_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    # Frozen macOS apps must not depend on a Python/Homebrew certificate path
    # from the build machine. Never disable hostname or certificate checking.
    context.load_verify_locations(cafile=certifi.where())
    return context
