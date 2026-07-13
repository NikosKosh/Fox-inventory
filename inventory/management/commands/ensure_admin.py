import os
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

class Command(BaseCommand):
    help = "Создаёт или обновляет администратора из переменных окружения"

    def handle(self, *args, **options):
        username = os.getenv("ADMIN_USERNAME", "administrator")
        password = os.getenv("ADMIN_PASSWORD", "")
        email = os.getenv("ADMIN_EMAIL", "")
        if not password:
            self.stdout.write(self.style.WARNING("ADMIN_PASSWORD не задан, администратор не создан."))
            return
        User = get_user_model()
        user, created = User.objects.get_or_create(username=username, defaults={"email": email, "is_staff": True, "is_superuser": True})
        user.email = email
        user.is_staff = True
        user.is_superuser = True
        if created or not user.has_usable_password():
            user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(f"Администратор {username} готов."))
