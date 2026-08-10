import hashlib

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from inventory.models import DocumentRecord


class Command(BaseCommand):
    help = "Backfill SHA-256 hashes for existing active document files."

    def handle(self, *args, **options):
        qs = DocumentRecord.objects.filter(
            file_sha256="",
            trashed_at__isnull=True,
        ).only("pk", "file")
        total = qs.count()
        updated = 0
        missing = 0

        for document in qs.iterator(chunk_size=100):
            if not document.file or not document.file.name:
                missing += 1
                continue
            if not default_storage.exists(document.file.name):
                missing += 1
                continue

            digest = hashlib.sha256()
            with default_storage.open(document.file.name, "rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)

            DocumentRecord.objects.filter(pk=document.pk).update(
                file_sha256=digest.hexdigest()
            )
            updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Scanned: {total}; hashes written: {updated}; missing files: {missing}"
            )
        )
