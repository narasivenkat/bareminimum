"""Application-wide constant values."""

# Seconds subtracted from a token's lifetime so it is refreshed slightly
# before the provider actually expires it (avoids edge-of-expiry failures).
TOKEN_EXPIRY_BUFFER: int = 300

__all__ = ["TOKEN_EXPIRY_BUFFER"]
