from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0007_equipment_mac_address"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LoginAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("username", models.CharField(blank=True, max_length=150, verbose_name="Указанный логин")),
                ("username_normalized", models.CharField(blank=True, db_index=True, max_length=150, verbose_name="Нормализованный логин")),
                ("ip_address", models.GenericIPAddressField(blank=True, db_index=True, null=True, verbose_name="IP-адрес")),
                ("user_agent", models.CharField(blank=True, max_length=512, verbose_name="Браузер / клиент")),
                ("result", models.CharField(choices=[("success", "Успешный вход"), ("failed", "Ошибка входа"), ("blocked", "Вход заблокирован")], db_index=True, max_length=16, verbose_name="Результат")),
                ("reason", models.CharField(blank=True, max_length=255, verbose_name="Описание")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="Дата и время")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="inventory_login_attempts", to=settings.AUTH_USER_MODEL, verbose_name="Пользователь")),
            ],
            options={
                "verbose_name": "попытка входа",
                "verbose_name_plural": "попытки входа",
                "ordering": ["-created_at", "-pk"],
                "indexes": [
                    models.Index(fields=["username_normalized", "ip_address", "result", "created_at"], name="login_user_ip_result_time"),
                    models.Index(fields=["ip_address", "result", "created_at"], name="login_ip_result_time"),
                ],
            },
        ),
    ]
