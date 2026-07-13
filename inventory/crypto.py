from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    key = settings.FIELD_ENCRYPTION_KEY
    if not key:
        return None
    return Fernet(key.encode())


def encrypt_text(value: str) -> str:
    if not value:
        return ""
    f = _fernet()
    if not f:
        return value
    return f.encrypt(value.encode()).decode()


def decrypt_text(value: str) -> str:
    if not value:
        return ""
    f = _fernet()
    if not f:
        return value
    try:
        return f.decrypt(value.encode()).decode()
    except InvalidToken:
        return "[ошибка расшифровки]"
