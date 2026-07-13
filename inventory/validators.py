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
