# import hashlib
# import secrets


# def hash_password(password: str) -> str:
#     """Hash passwords without storing plaintext (replace with bcrypt in production)."""
#     salt = secrets.token_hex(16)
#     digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310_000)
#     return f"{salt}${digest.hex()}"


# def verify_password(password: str, stored_hash: str) -> bool:
#     salt, expected = stored_hash.split("$", 1)
#     actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310_000).hex()
#     return secrets.compare_digest(actual, expected)
