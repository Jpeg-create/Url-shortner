# ============================================================
# Base62 Encoder / Decoder
#
# HOW IT WORKS:
# When you insert a URL, the database returns an auto-increment ID
# (e.g. 10043). We convert that integer to Base62, which uses
# characters a-z, A-Z, 0-9 (62 characters total).
#
# ID 1      → "1"
# ID 10043  → "2Bp"
# ID 999999 → "4c91"
#
# Why Base62 and not just the raw ID?
# - Short:  ID 1,000,000 becomes "4c91" (4 chars vs 7)
# - URL-safe: no +, /, or = like Base64 uses
# - Unique: guaranteed because DB IDs are unique — no collision checking needed
# - 7 chars covers 62^7 = 3.5 TRILLION URLs
# ============================================================

ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
BASE = len(ALPHABET)  # 62


def encode(num: int) -> str:
    """Convert an integer (DB id) to a Base62 short code."""
    if num == 0:
        return ALPHABET[0]

    result = []
    while num > 0:
        result.append(ALPHABET[num % BASE])
        num //= BASE

    return "".join(reversed(result))


def decode(code: str) -> int:
    """Convert a Base62 short code back to the original integer."""
    num = 0
    for char in code:
        num = num * BASE + ALPHABET.index(char)
    return num
