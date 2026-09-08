import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from dotenv import load_dotenv

from app.db.database import get_db
from app.db.models.restaurant import User

load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET", "super_secret_jwt_key_987654321")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_SECONDS = 86400  # 24 hours

security = HTTPBearer()


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256 with a random salt."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310_000)
    return f"{salt}${digest.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a stored hash."""
    if not stored_hash_valid(hashed_password):
        return False
    salt, expected = hashed_password.split("$", 1)
    actual = hashlib.pbkdf2_hmac("sha256", plain_password.encode(), salt.encode(), 310_000).hex()
    return secrets.compare_digest(actual, expected)


def stored_hash_valid(stored_hash: str) -> bool:
    return bool(stored_hash and "$" in stored_hash)


# Custom JWT implementation without external PyJWT dependency for 100% portability
def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('utf-8').replace('=', '')


def base64url_decode(data: str) -> bytes:
    padding = '=' * (4 - len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_access_token(data: dict, expires_delta: Optional[int] = None) -> str:
    header = {"alg": ALGORITHM, "typ": "JWT"}
    payload = data.copy()

    expire = int(time.time()) + (expires_delta or ACCESS_TOKEN_EXPIRE_SECONDS)
    payload["exp"] = expire

    header_b64 = base64url_encode(json.dumps(header).encode('utf-8'))
    payload_b64 = base64url_encode(json.dumps(payload).encode('utf-8'))

    signing_input = f"{header_b64}.{payload_b64}".encode('utf-8')
    signature = hmac.new(SECRET_KEY.encode('utf-8'), signing_input, hashlib.sha256).digest()
    signature_b64 = base64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{signature_b64}"


def decode_access_token(token: str) -> Optional[dict]:
    print("called----------decode_access_token")
    try:
        parts = token.split('.')
        print("[DEBUG] Token parts:", parts)
        if len(parts) != 3:
            print("[DEBUG] Invalid token structure")
            return None
        header_b64, payload_b64, signature_b64 = parts

        # Decode header and payload for logging
        try:
            header_json = json.loads(base64url_decode(header_b64).decode('utf-8'))
            print("[DEBUG] Decoded header:", header_json)
        except Exception as e:
            header_json = None
            print("[DEBUG] Header decode error:", e)
        try:
            payload_json = json.loads(base64url_decode(payload_b64).decode('utf-8'))
            print("[DEBUG] Decoded payload (pre‑validation):", payload_json)
        except Exception as e:
            payload_json = None
            print("[DEBUG] Payload decode error:", e)

        # Verify signature
        signing_input = f"{header_b64}.{payload_b64}".encode('utf-8')
        expected_sig = hmac.new(SECRET_KEY.encode('utf-8'), signing_input, hashlib.sha256).digest()
        expected_sig_b64 = base64url_encode(expected_sig)
        signature_valid = hmac.compare_digest(signature_b64, expected_sig_b64)
        print("[DEBUG] Signature valid:", signature_valid)
        if not signature_valid:
            return None

        # At this point payload_json should be a dict
        payload = payload_json
        # Check expiration
        exp = payload.get("exp", 0)
        now = time.time()
        print(f"[DEBUG] Expiration check – exp: {exp}, now: {now}, valid: {exp > now}")
        if exp < now:
            return None

        print("[DEBUG] Token successfully validated and decoded")
        return payload
    except Exception as e:
        print("[DEBUG] Unexpected error during decode_access_token:", e)
        return None


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: AsyncSession = Depends(get_db)) -> User:
    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("user_id")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload is missing user identifiers",
        )

    try:
        if isinstance(user_id, str):
            user_id_val = uuid.UUID(user_id)
        else:
            user_id_val = user_id
    except ValueError:
        user_id_val = user_id

    result = await db.execute(
        select(User).options(selectinload(User.customer)).where(User.id == user_id_val)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user no longer exists",
        )
    return user


