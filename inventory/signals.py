from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models.signals import post_delete, post_migrate
from django.dispatch import receiver
from .models import Act, Category

DEFAULT_CATEGORIES = [
    ("Ноутбук", "N", "unit"),
    ("Неттоп / рабочая станция", "W", "unit"),
    ("Монитор", "M", "unit"),
    ("Коммутатор", "SW", "unit"),
    ("Маршрутизатор", "R", "unit"),
    ("Точка доступа", "AP", "unit"),
    ("Телефон", "PH", "unit"),
    ("Принтер / МФУ", "PR", "unit"),
    ("Наушники / гарнитура", "HS", "unit"),
    ("Мышь", "MS", "unit"),
    ("Сумка", "BG", "unit"),
    ("Блок питания / зарядное устройство", "PS", "unit"),
    ("Адаптер", "AD", "quantity"),
    ("Другое", "OT", "unit"),
]

@receiver(post_migrate)
def create_default_categories(sender, **kwargs):
    if sender.name != "inventory":
        return
    for name, code, mode in DEFAULT_CATEGORIES:
        Category.objects.get_or_create(name=name, defaults={"code": code, "tracking_mode": mode})


@receiver(post_delete, sender=Act)
def delete_act_document_after_commit(sender, instance, **kwargs):
    document_name = instance.document.name if instance.document else ""
    if not document_name:
        return

    def remove_document():
        if default_storage.exists(document_name):
            default_storage.delete(document_name)

    transaction.on_commit(remove_document)
