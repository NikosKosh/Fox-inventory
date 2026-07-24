import re
from pathlib import Path
from django.conf import settings
from django.core.exceptions import ValidationError

ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


def validate_document(file):
    ext = Path(file.name).suffix.lower()
    if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise ValidationError("Разрешены только PDF, JPG, JPEG и PNG.")
    limit = settings.MAX_UPLOAD_MB * 1024 * 1024
    if file.size > limit:
        raise ValidationError(f"Файл больше {settings.MAX_UPLOAD_MB} МБ.")


_MAC_PATTERN = re.compile(r"^[0-9A-F]{12}$")
_MAC_ALLOWED_PATTERN = re.compile(r"^[0-9A-Fa-f:.\-\s]+$")


def normalize_mac_address(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if not _MAC_ALLOWED_PATTERN.fullmatch(text):
        raise ValidationError("MAC-адрес содержит недопустимые символы.")
    raw = re.sub(r"[:.\-\s]", "", text).upper()
    if not _MAC_PATTERN.fullmatch(raw):
        raise ValidationError("MAC-адрес должен содержать 12 шестнадцатеричных символов.")
    return ":".join(raw[index:index + 2] for index in range(0, 12, 2))


def validate_mac_address(value):
    normalize_mac_address(value)
