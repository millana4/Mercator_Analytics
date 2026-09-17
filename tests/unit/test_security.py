import bcrypt
import jwt
import pytest

from app.users.security import create_access_token, decode_access_token, verify_password


def test_verify_password_correct():
    hashed = bcrypt.hashpw(b"1234", bcrypt.gensalt()).decode()
    assert verify_password("1234", hashed) is True


def test_verify_password_wrong():
    hashed = bcrypt.hashpw(b"1234", bcrypt.gensalt()).decode()
    assert verify_password("wrong", hashed) is False


def test_token_roundtrip():
    token = create_access_token("someuser")
    assert decode_access_token(token) == "someuser"


def test_token_garbage_raises():
    with pytest.raises(jwt.PyJWTError):
        decode_access_token("not-a-real-token")
        