import secrets
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from .crypto import decrypt_text, encrypt_text
from .validators import normalize_mac_address, validate_business_document, validate_document, validate_mac_address


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Organization(TimeStampedModel):
    class Kind(models.TextChoices):
        COMPANY = "company", "Организация"
        PERSONAL = "personal", "Личное имущество"
        CLIENT = "client", "Клиентская организация"

    name = models.CharField("Наименование", max_length=255)
    short_name = models.CharField("Краткое наименование", max_length=100, blank=True)
    prefix = models.CharField("Префикс кодов", max_length=12, unique=True, help_text="Например: FOX или SZ")
    kind = models.CharField("Тип", max_length=20, choices=Kind.choices, default=Kind.COMPANY)
    act_organization_name = models.CharField(
        "Наименование организации в актах",
        max_length=255,
        blank=True,
        help_text="Если не заполнено, используется основное наименование организации.",
    )
    act_city = models.CharField(
        "Город в актах",
        max_length=120,
        blank=True,
        help_text="Например: г. Ростов-на-Дону.",
    )
    act_issue_representative_position = models.CharField(
        "Должность представителя при выдаче",
        max_length=255,
        blank=True,
    )
    act_issue_representative_name = models.CharField(
        "ФИО представителя при выдаче",
        max_length=255,
        blank=True,
    )
    act_return_representative_position = models.CharField(
        "Должность представителя при возврате",
        max_length=255,
        blank=True,
    )
    act_return_representative_name = models.CharField(
        "ФИО представителя при возврате",
        max_length=255,
        blank=True,
    )
    archived = models.BooleanField("В архиве", default=False)
    notes = models.TextField("Комментарий", blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "организация/владелец"
        verbose_name_plural = "организации и владельцы"

    def __str__(self):
        return self.short_name or self.name


class Employee(TimeStampedModel):
    full_name = models.CharField("ФИО", max_length=255)
    position = models.CharField("Должность", max_length=255, blank=True)
    department = models.CharField("Подразделение", max_length=255, blank=True)
    workplace = models.CharField("Рабочее место", max_length=500, blank=True)
    workplace_location = models.ForeignKey(
        "Location", verbose_name="Объект / рабочее место", on_delete=models.PROTECT,
        null=True, blank=True, related_name="employees",
    )
    room = models.ForeignKey(
        "Room", verbose_name="Помещение / комната", on_delete=models.PROTECT,
        null=True, blank=True, related_name="employees",
    )
    phone = models.CharField("Телефон", max_length=50, blank=True)
    organization = models.ForeignKey(Organization, verbose_name="Организация", on_delete=models.PROTECT, related_name="employees")
    archived = models.BooleanField("Уволен / в архиве", default=False)
    notes = models.TextField("Комментарий", blank=True)

    class Meta:
        ordering = ["full_name"]
        verbose_name = "сотрудник"
        verbose_name_plural = "сотрудники"

    def __str__(self):
        return self.full_name

    def get_absolute_url(self):
        return reverse("employee_detail", args=[self.pk])

    @property
    def workplace_display(self):
        if self.workplace_location_id:
            return self.workplace_location.label or self.workplace_location.address
        return self.workplace


class Location(TimeStampedModel):
    organization = models.ForeignKey(Organization, verbose_name="Организация", on_delete=models.PROTECT, related_name="locations")
    address = models.CharField("Адрес", max_length=500)
    label = models.CharField("Краткое название", max_length=150, blank=True, help_text="Например: Главный офис")
    archived = models.BooleanField("В архиве", default=False)

    class Meta:
        ordering = ["organization__name", "address"]
        constraints = [models.UniqueConstraint(fields=["organization", "address"], name="uniq_org_address")]
        verbose_name = "адрес"
        verbose_name_plural = "адреса"

    def __str__(self):
        return f"{self.organization}: {self.label or self.address}"

    def get_absolute_url(self):
        return reverse("location_detail", args=[self.pk])


class Room(TimeStampedModel):
    class RoomType(models.TextChoices):
        MEETING = "meeting", "Переговорная"
        OFFICE = "office", "Кабинет / рабочая зона"
        SERVER = "server", "Серверная / техническая"
        STORAGE = "storage", "Склад / кладовая"
        COMMON = "common", "Общее помещение"
        OTHER = "other", "Другое"

    location = models.ForeignKey(Location, verbose_name="Объект", on_delete=models.PROTECT, related_name="rooms")
    name = models.CharField("Название помещения", max_length=180)
    room_type = models.CharField("Тип помещения", max_length=20, choices=RoomType.choices, default=RoomType.OFFICE)
    floor = models.CharField("Этаж / зона", max_length=80, blank=True)
    notes = models.TextField("Описание / комментарий", blank=True)
    archived = models.BooleanField("В архиве", default=False)

    class Meta:
        ordering = ["location__organization__name", "location__address", "name"]
        constraints = [models.UniqueConstraint(fields=["location", "name"], name="uniq_location_room")]
        verbose_name = "помещение"
        verbose_name_plural = "помещения"

    def __str__(self):
        return f"{self.location.label or self.location.address} — {self.name}"

    def get_absolute_url(self):
        return reverse("room_detail", args=[self.pk])


class Cabinet(TimeStampedModel):
    location = models.ForeignKey(Location, verbose_name="Адрес", on_delete=models.PROTECT, related_name="cabinets")
    room = models.ForeignKey(Room, verbose_name="Помещение", on_delete=models.PROTECT, null=True, blank=True, related_name="cabinets")
    name = models.CharField("Название шкафа", max_length=150)
    notes = models.TextField("Комментарий", blank=True)
    archived = models.BooleanField("В архиве", default=False)

    class Meta:
        ordering = ["location__organization__name", "location__address", "name"]
        constraints = [models.UniqueConstraint(fields=["location", "name"], name="uniq_location_cabinet")]
        verbose_name = "коммутационный шкаф"
        verbose_name_plural = "коммутационные шкафы"

    def __str__(self):
        return f"{self.location} — {self.name}"

    def get_absolute_url(self):
        return reverse("cabinet_detail", args=[self.pk])


class Category(TimeStampedModel):
    class TrackingMode(models.TextChoices):
        UNIT = "unit", "Поштучно"
        QUANTITY = "quantity", "Количественно"

    name = models.CharField("Категория", max_length=120, unique=True)
    code = models.CharField("Код категории", max_length=8, unique=True, help_text="Например: N, W, M, SW")
    tracking_mode = models.CharField("Способ учёта", max_length=20, choices=TrackingMode.choices, default=TrackingMode.UNIT)
    archived = models.BooleanField("В архиве", default=False)

    class Meta:
        ordering = ["name"]
        verbose_name = "категория"
        verbose_name_plural = "категории"

    def __str__(self):
        return self.name


class Equipment(TimeStampedModel):
    class AccountingGroup(models.TextChoices):
        EMPLOYEE = "employee", "Для сотрудников"
        TECHNICAL = "technical", "Техническое"

    class UsageStatus(models.TextChoices):
        STOCK = "stock", "На складе"
        EMPLOYEE = "employee", "Выдано сотруднику"
        OBJECT = "object", "Установлено на объекте"
        REPAIR = "repair", "В ремонте"
        RESERVE = "reserve", "В резерве"
        WAITING_DISPOSAL = "waiting_disposal", "Ждёт списания"
        DISPOSED = "disposed", "Списано"
        LOANED = "loaned", "Временно передано другой организации"

    class Condition(models.TextChoices):
        NEW = "new", "Новое"
        USED = "used", "Б/У"
        BROKEN = "broken", "Сломано"

    internal_code = models.CharField("Внутренний номер", max_length=80, unique=True, blank=True)
    accounting_group = models.CharField("Контур учёта", max_length=20, choices=AccountingGroup.choices, default=AccountingGroup.EMPLOYEE, db_index=True)
    category = models.ForeignKey(Category, verbose_name="Категория", on_delete=models.PROTECT, related_name="equipment")
    name = models.CharField("Наименование", max_length=255)
    manufacturer = models.CharField("Производитель", max_length=150, blank=True)
    model = models.CharField("Модель", max_length=180, blank=True)
    serial_number = models.CharField("Серийный номер", max_length=180, blank=True)
    mac_address = models.CharField("MAC-адрес", max_length=17, blank=True, validators=[validate_mac_address], db_index=True)
    hostname = models.CharField("Hostname", max_length=180, blank=True)
    owner = models.ForeignKey(Organization, verbose_name="Владелец", on_delete=models.PROTECT, related_name="owned_equipment")
    responsible_employee = models.ForeignKey(Employee, verbose_name="Ответственный сотрудник", on_delete=models.PROTECT, null=True, blank=True, related_name="equipment")
    location = models.ForeignKey(Location, verbose_name="Адрес установки", on_delete=models.PROTECT, null=True, blank=True, related_name="equipment")
    room = models.ForeignKey(Room, verbose_name="Помещение / комната", on_delete=models.PROTECT, null=True, blank=True, related_name="equipment")
    cabinet = models.ForeignKey(Cabinet, verbose_name="Коммутационный шкаф", on_delete=models.PROTECT, null=True, blank=True, related_name="equipment")
    freeform_location = models.CharField("Место установки (текстом)", max_length=500, blank=True)
    quantity = models.PositiveIntegerField("Количество", default=1, validators=[MinValueValidator(1)])
    usage_status = models.CharField("Статус", max_length=30, choices=UsageStatus.choices, default=UsageStatus.STOCK)
    condition = models.CharField("Состояние", max_length=20, choices=Condition.choices, default=Condition.NEW)
    notes = models.TextField("Описание / комментарий", blank=True)
    network_address = models.CharField("Адрес управления", max_length=255, blank=True)
    network_username = models.CharField("Логин", max_length=255, blank=True)
    network_password_encrypted = models.TextField("Зашифрованный пароль", blank=True, editable=False)
    archived = models.BooleanField("В архиве", default=False)

    class Meta:
        ordering = ["internal_code", "name"]
        verbose_name = "оборудование"
        verbose_name_plural = "оборудование"
        indexes = [
            models.Index(fields=["internal_code"]),
            models.Index(fields=["serial_number"]),
            models.Index(fields=["hostname"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["mac_address"],
                condition=~Q(mac_address=""),
                name="uniq_nonempty_equipment_mac",
            ),
        ]

    def __str__(self):
        return f"{self.internal_code or 'без кода'} — {self.name}"

    def get_absolute_url(self):
        return reverse("equipment_detail", args=[self.pk])

    def set_network_password(self, value):
        self.network_password_encrypted = encrypt_text(value)

    def get_network_password(self):
        return decrypt_text(self.network_password_encrypted)

    @classmethod
    def next_code(cls, owner, category):
        prefix = owner.prefix.upper().strip()
        category_code = category.code.upper().strip()
        base = f"{prefix}-{category_code}-"
        values = cls.objects.filter(internal_code__startswith=base).values_list("internal_code", flat=True)
        max_num = 0
        for value in values:
            try:
                max_num = max(max_num, int(value.rsplit("-", 1)[1]))
            except (ValueError, IndexError):
                continue
        return f"{base}{max_num + 1:03d}"

    def save(self, *args, **kwargs):
        self.mac_address = normalize_mac_address(self.mac_address)
        if not self.internal_code and self.category_id and self.owner_id and self.category.tracking_mode == Category.TrackingMode.UNIT:
            with transaction.atomic():
                self.internal_code = self.next_code(self.owner, self.category)
        if self.responsible_employee_id and self.usage_status == self.UsageStatus.STOCK:
            self.usage_status = self.UsageStatus.EMPLOYEE
        if self.condition == self.Condition.BROKEN and self.usage_status not in {self.UsageStatus.REPAIR, self.UsageStatus.WAITING_DISPOSAL, self.UsageStatus.DISPOSED}:
            pass
        super().save(*args, **kwargs)


class EquipmentMovement(models.Model):
    class MovementType(models.TextChoices):
        CREATED = "created", "Создание карточки"
        EDITED = "edited", "Изменение карточки"
        ASSIGNED = "assigned", "Выдача сотруднику"
        RETURNED = "returned", "Возврат"
        INSTALLED = "installed", "Установка на объекте"
        LOANED = "loaned", "Передача другой организации"
        LOAN_RETURN = "loan_return", "Возврат владельцу"
        REPAIR = "repair", "Передача в ремонт"
        DISPOSED = "disposed", "Списание"
        ACT = "act", "Операция по акту"
        IMPORT = "import", "Импорт"

    equipment = models.ForeignKey(Equipment, verbose_name="Оборудование", on_delete=models.CASCADE, related_name="movements")
    movement_type = models.CharField("Тип операции", max_length=30, choices=MovementType.choices)
    from_employee = models.ForeignKey(Employee, verbose_name="От сотрудника", on_delete=models.PROTECT, null=True, blank=True, related_name="movements_from")
    to_employee = models.ForeignKey(Employee, verbose_name="К сотруднику", on_delete=models.PROTECT, null=True, blank=True, related_name="movements_to")
    from_organization = models.ForeignKey(Organization, verbose_name="От организации", on_delete=models.PROTECT, null=True, blank=True, related_name="movements_from")
    to_organization = models.ForeignKey(Organization, verbose_name="К организации", on_delete=models.PROTECT, null=True, blank=True, related_name="movements_to")
    from_status = models.CharField("Предыдущий статус", max_length=30, blank=True)
    to_status = models.CharField("Новый статус", max_length=30, blank=True)
    notes = models.TextField("Комментарий", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Пользователь", on_delete=models.SET_NULL, null=True, blank=True)
    act = models.ForeignKey(
        "Act",
        verbose_name="Связанный акт",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="movements",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "операция с оборудованием"
        verbose_name_plural = "история оборудования"


class LoginAttempt(models.Model):
    class Result(models.TextChoices):
        SUCCESS = "success", "Успешный вход"
        FAILED = "failed", "Ошибка входа"
        BLOCKED = "blocked", "Вход заблокирован"

    username = models.CharField("Указанный логин", max_length=150, blank=True)
    username_normalized = models.CharField("Нормализованный логин", max_length=150, blank=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Пользователь",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_login_attempts",
    )
    ip_address = models.GenericIPAddressField("IP-адрес", null=True, blank=True, db_index=True)
    user_agent = models.CharField("Браузер / клиент", max_length=512, blank=True)
    result = models.CharField("Результат", max_length=16, choices=Result.choices, db_index=True)
    reason = models.CharField("Описание", max_length=255, blank=True)
    created_at = models.DateTimeField("Дата и время", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "попытка входа"
        verbose_name_plural = "попытки входа"
        indexes = [
            models.Index(fields=["username_normalized", "ip_address", "result", "created_at"], name="login_user_ip_result_time"),
            models.Index(fields=["ip_address", "result", "created_at"], name="login_ip_result_time"),
        ]

    def __str__(self):
        return f"{self.get_result_display()}: {self.username or '—'} ({self.ip_address or 'IP не определён'})"


class EquipmentLoan(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        RETURNED = "returned", "Возвращено"

    equipment = models.ForeignKey(Equipment, verbose_name="Оборудование", on_delete=models.PROTECT, related_name="loans")
    lender = models.ForeignKey(Organization, verbose_name="Владелец", on_delete=models.PROTECT, related_name="loans_given")
    borrower = models.ForeignKey(Organization, verbose_name="Получатель", on_delete=models.PROTECT, related_name="loans_received")
    responsible_employee = models.ForeignKey(Employee, verbose_name="Ответственный", on_delete=models.PROTECT, null=True, blank=True, related_name="organization_loans")
    started_at = models.DateField("Дата передачи", default=timezone.localdate)
    expected_return_at = models.DateField("Плановая дата возврата", null=True, blank=True)
    returned_at = models.DateField("Дата возврата", null=True, blank=True)
    undocumented = models.BooleanField("Передача без документов", default=True)
    document = models.FileField("Документ", upload_to="loans/%Y/%m/", blank=True, validators=[validate_document])
    notes = models.TextField("Комментарий", blank=True)
    status = models.CharField("Статус", max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["-started_at", "-created_at"]
        verbose_name = "временная передача"
        verbose_name_plural = "временные передачи"

    def __str__(self):
        return f"{self.equipment} → {self.borrower}"


class Act(TimeStampedModel):
    class ActType(models.TextChoices):
        ISSUE = "issue", "Выдача оборудования"
        RETURN = "return", "Возврат оборудования"
        TRANSFER = "transfer", "Передача между организациями"
        OTHER = "other", "Другой акт"

    number = models.CharField("Номер акта", max_length=100, blank=True)
    act_type = models.CharField("Тип акта", max_length=20, choices=ActType.choices, default=ActType.ISSUE)
    act_date = models.DateField("Дата акта", default=timezone.localdate)
    employee = models.ForeignKey(Employee, verbose_name="Сотрудник", on_delete=models.PROTECT, null=True, blank=True, related_name="acts")
    from_organization = models.ForeignKey(Organization, verbose_name="Передающая организация", on_delete=models.PROTECT, null=True, blank=True, related_name="acts_from")
    to_organization = models.ForeignKey(Organization, verbose_name="Получающая организация", on_delete=models.PROTECT, null=True, blank=True, related_name="acts_to")
    equipment = models.ManyToManyField(Equipment, verbose_name="Оборудование", related_name="acts", blank=True)
    document = models.FileField("Скан акта", upload_to="acts/%Y/%m/", validators=[validate_document])
    notes = models.TextField("Комментарий", blank=True)
    public_enabled = models.BooleanField("Разрешить просмотр по секретной ссылке", default=False)
    public_token = models.CharField(max_length=64, unique=True, blank=True, editable=False)

    class Meta:
        ordering = ["-act_date", "-created_at"]
        verbose_name = "акт"
        verbose_name_plural = "акты"

    def __str__(self):
        return f"Акт {self.number or self.pk or 'новый'} от {self.act_date:%d.%m.%Y}"

    def save(self, *args, **kwargs):
        if not self.public_token:
            self.public_token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("act_detail", args=[self.pk])


class RepairRecord(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "В ремонте"
        DONE = "done", "Завершён"
        UNREPAIRABLE = "unrepairable", "Не подлежит ремонту"

    equipment = models.ForeignKey(Equipment, verbose_name="Оборудование", on_delete=models.PROTECT, related_name="repairs")
    opened_at = models.DateField("Дата передачи", default=timezone.localdate)
    closed_at = models.DateField("Дата завершения", null=True, blank=True)
    problem = models.TextField("Описание неисправности", blank=True)
    result = models.TextField("Результат", blank=True)
    status = models.CharField("Статус", max_length=20, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-opened_at", "-created_at"]
        verbose_name = "ремонт"
        verbose_name_plural = "ремонты"


def _document_org_segment(instance):
    organization = getattr(instance, "organization", None)
    if organization is None and getattr(instance, "contract_id", None):
        organization = instance.contract.organization
    if organization is None:
        return "unassigned"
    prefix = (organization.prefix or f"org-{organization.pk}").strip().lower()
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in prefix) or f"org-{organization.pk}"


def contract_file_upload_to(instance, filename):
    today = timezone.localdate()
    return f"documents/{_document_org_segment(instance)}/contracts/{today:%Y/%m}/{filename}"


def document_file_upload_to(instance, filename):
    document_date = instance.document_date or timezone.localdate()
    return f"documents/{_document_org_segment(instance)}/records/{document_date:%Y/%m}/{filename}"


class Counterparty(TimeStampedModel):
    name = models.CharField("Наименование", max_length=255)
    short_name = models.CharField("Краткое наименование", max_length=150, blank=True)
    linked_organization = models.OneToOneField(
        Organization, verbose_name="Внутренняя организация", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="counterparty_profile",
    )
    inn = models.CharField("ИНН", max_length=20, blank=True, db_index=True)
    kpp = models.CharField("КПП", max_length=20, blank=True)
    contact_name = models.CharField("Контакт", max_length=255, blank=True)
    phone = models.CharField("Телефон", max_length=80, blank=True)
    email = models.EmailField("Email", blank=True)
    notes = models.TextField("Комментарий", blank=True)
    archived = models.BooleanField("В архиве", default=False)

    class Meta:
        ordering = ["name"]
        verbose_name = "контрагент"
        verbose_name_plural = "контрагенты"
        indexes = [models.Index(fields=["name"], name="inventory_c_name_0cb663_idx")]

    def __str__(self):
        return self.short_name or self.name

    def get_absolute_url(self):
        return reverse("counterparty_detail", args=[self.pk])



class OrganizationCounterpartyLink(TimeStampedModel):
    organization = models.ForeignKey(
        Organization,
        verbose_name="Организация",
        on_delete=models.PROTECT,
        related_name="counterparty_links",
    )
    counterparty = models.ForeignKey(
        Counterparty,
        verbose_name="Вторая сторона",
        on_delete=models.CASCADE,
        related_name="organization_links",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Создал",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_counterparty_links",
    )
    archived = models.BooleanField("В архиве", default=False)

    class Meta:
        ordering = ["counterparty__name", "pk"]
        verbose_name = "связь организации со стороной"
        verbose_name_plural = "связи организаций со сторонами"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "counterparty"],
                name="uniq_org_counterparty_link",
            )
        ]
        indexes = [
            models.Index(
                fields=["organization", "archived"],
                name="inv_party_link_org_idx",
            )
        ]

    def __str__(self):
        return f"{self.organization} ↔ {self.counterparty}"

class Contract(TimeStampedModel):
    class Category(models.TextChoices):
        SERVICES = "services", "Услуги"
        INTERNET = "internet", "Связь / интернет"
        RENT = "rent", "Аренда"
        SOFTWARE = "software", "ПО / лицензии"
        SUPPLY = "supply", "Поставка"
        MAINTENANCE = "maintenance", "Обслуживание"
        OTHER = "other", "Прочее"

    organization = models.ForeignKey(
        Organization, verbose_name="Организация", on_delete=models.PROTECT, related_name="contracts"
    )
    counterparty = models.ForeignKey(
        Counterparty, verbose_name="Контрагент", on_delete=models.PROTECT, related_name="contracts"
    )
    title = models.CharField("Название", max_length=255)
    number = models.CharField("Номер договора", max_length=120, blank=True)
    contract_date = models.DateField("Дата договора", null=True, blank=True)
    category = models.CharField("Категория", max_length=30, choices=Category.choices, default=Category.OTHER)
    starts_at = models.DateField("Начало действия", null=True, blank=True)
    ends_at = models.DateField("Окончание действия", null=True, blank=True)
    indefinite = models.BooleanField("Бессрочный", default=False)
    location = models.ForeignKey(
        Location, verbose_name="Объект", on_delete=models.SET_NULL, null=True, blank=True, related_name="contracts"
    )
    responsible_employee = models.ForeignKey(
        Employee, verbose_name="Ответственный", on_delete=models.SET_NULL, null=True, blank=True, related_name="contracts"
    )
    main_file = models.FileField(
        "Файл договора", upload_to=contract_file_upload_to, blank=True, validators=[validate_business_document]
    )
    main_file_original_name = models.CharField(
        "Исходное имя файла договора", max_length=255, blank=True
    )
    notes = models.TextField("Комментарий", blank=True)
    archived = models.BooleanField("В архиве", default=False, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Создал", on_delete=models.SET_NULL, null=True, blank=True, related_name="created_contracts"
    )

    class Meta:
        ordering = ["archived", "-contract_date", "counterparty__name", "title"]
        verbose_name = "договор"
        verbose_name_plural = "договоры"
        indexes = [
            models.Index(fields=["organization", "archived"], name="inventory_c_organiz_6da538_idx"),
            models.Index(fields=["counterparty", "archived"], name="inventory_c_counter_f94ef6_idx"),
        ]

    def __str__(self):
        if not self.number:
            return self.title
        if self.number.casefold() in self.title.casefold():
            return self.title
        return f"{self.title} №{self.number}"

    def save(self, *args, **kwargs):
        if self.main_file and not self.main_file_original_name:
            self.main_file_original_name = self.main_file.name.rsplit("/", 1)[-1]
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("contract_detail", args=[self.pk])

    @property
    def status_label(self):
        if self.archived:
            return "Архив"
        today = timezone.localdate()
        if self.starts_at and self.starts_at > today:
            return "Ожидает начала"
        if not self.indefinite and self.ends_at and self.ends_at < today:
            return "Срок истёк"
        return "Действует"


class DocumentType(TimeStampedModel):
    name = models.CharField("Название", max_length=120, unique=True)
    code = models.SlugField("Код", max_length=60, unique=True)
    sort_order = models.PositiveIntegerField("Порядок", default=100)
    archived = models.BooleanField("В архиве", default=False)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "тип документа"
        verbose_name_plural = "типы документов"

    def __str__(self):
        return self.name



class DocumentOperation(TimeStampedModel):
    organization = models.ForeignKey(
        Organization, verbose_name="Организация", on_delete=models.PROTECT, related_name="document_operations"
    )
    counterparty = models.ForeignKey(
        Counterparty, verbose_name="Контрагент", on_delete=models.SET_NULL, null=True, blank=True, related_name="document_operations"
    )
    contract = models.ForeignKey(
        Contract, verbose_name="Договор", on_delete=models.SET_NULL, null=True, blank=True, related_name="operations"
    )
    location = models.ForeignKey(
        Location, verbose_name="Объект", on_delete=models.SET_NULL, null=True, blank=True, related_name="document_operations"
    )
    title = models.CharField("Название операции", max_length=255)
    operation_date = models.DateField("Дата операции", null=True, blank=True, db_index=True)
    amount = models.DecimalField("Сумма", max_digits=15, decimal_places=2, null=True, blank=True)
    notes = models.TextField("Комментарий", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Создал", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="created_document_operations",
    )

    class Meta:
        ordering = ["-operation_date", "-created_at", "-pk"]
        verbose_name = "операция документов"
        verbose_name_plural = "операции документов"
        indexes = [
            models.Index(fields=["organization", "operation_date"], name="inv_op_org_date_idx"),
            models.Index(fields=["contract", "operation_date"], name="inv_op_contract_date_idx"),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("operation_detail", args=[self.pk])

    def save(self, *args, **kwargs):
        if self.contract_id:
            self.organization_id = self.contract.organization_id
            if not self.counterparty_id:
                self.counterparty_id = self.contract.counterparty_id
            if not self.location_id and self.contract.location_id:
                self.location_id = self.contract.location_id
        super().save(*args, **kwargs)



class DocumentFileVersion(models.Model):
    document = models.ForeignKey(
        "DocumentRecord",
        verbose_name="Документ",
        on_delete=models.CASCADE,
        related_name="file_versions",
    )
    file = models.FileField(
        "Файл версии",
        upload_to="documents/versions/%Y/%m/",
    )
    original_name = models.CharField(
        "Исходное имя",
        max_length=255,
        blank=True,
    )
    file_sha256 = models.CharField(
        "SHA-256",
        max_length=64,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Сохранил версию",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_document_versions",
    )

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "версия файла документа"
        verbose_name_plural = "версии файлов документов"

    def __str__(self):
        return self.original_name or f"Версия {self.pk}"


class DocumentActivity(models.Model):
    action = models.CharField("Действие", max_length=40)
    message = models.CharField("Описание", max_length=500, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Пользователь",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="document_activity",
    )
    organization = models.ForeignKey(
        Organization,
        verbose_name="Организация",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="document_activity",
    )
    counterparty = models.ForeignKey(
        Counterparty,
        verbose_name="Вторая сторона",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="document_activity",
    )
    contract = models.ForeignKey(
        Contract,
        verbose_name="Договор",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="document_activity",
    )
    operation = models.ForeignKey(
        DocumentOperation,
        verbose_name="Пакет",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity",
    )
    document = models.ForeignKey(
        "DocumentRecord",
        verbose_name="Документ",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "событие документооборота"
        verbose_name_plural = "события документооборота"
        indexes = [
            models.Index(
                fields=["document", "created_at"],
                name="inv_activity_doc_time_idx",
            ),
            models.Index(
                fields=["operation", "created_at"],
                name="inv_activity_op_time_idx",
            ),
        ]

    def __str__(self):
        return self.message or self.action


class DocumentRecord(TimeStampedModel):
    class ClassificationSource(models.TextChoices):
        MANUAL = "manual", "Указано вручную"
        FILENAME = "filename", "Определено по имени файла"
        UNKNOWN = "", "Не определено"

    organization = models.ForeignKey(
        Organization, verbose_name="Организация", on_delete=models.PROTECT, related_name="document_records"
    )
    document_type = models.ForeignKey(
        DocumentType, verbose_name="Тип документа", on_delete=models.PROTECT, null=True, blank=True, related_name="documents"
    )
    counterparty = models.ForeignKey(
        Counterparty, verbose_name="Контрагент", on_delete=models.SET_NULL, null=True, blank=True, related_name="documents"
    )
    contract = models.ForeignKey(
        Contract, verbose_name="Договор", on_delete=models.SET_NULL, null=True, blank=True, related_name="documents"
    )
    operation = models.ForeignKey(
        DocumentOperation, verbose_name="Операция", on_delete=models.SET_NULL, null=True, blank=True, related_name="documents"
    )
    location = models.ForeignKey(
        Location, verbose_name="Объект", on_delete=models.SET_NULL, null=True, blank=True, related_name="document_records"
    )
    equipment = models.ManyToManyField(
        Equipment, verbose_name="Оборудование", blank=True, related_name="document_records"
    )
    title = models.CharField("Название", max_length=255, blank=True)
    number = models.CharField("Номер", max_length=120, blank=True)
    document_date = models.DateField("Дата документа", null=True, blank=True, db_index=True)
    amount = models.DecimalField("Сумма", max_digits=15, decimal_places=2, null=True, blank=True)
    file = models.FileField("Файл", upload_to=document_file_upload_to, validators=[validate_business_document])
    original_name = models.CharField("Исходное имя файла", max_length=255, blank=True)
    classification_source = models.CharField(
        "Источник типа", max_length=20, choices=ClassificationSource.choices,
        default=ClassificationSource.MANUAL,
    )
    file_sha256 = models.CharField("SHA-256 файла", max_length=64, blank=True, db_index=True)
    notes = models.TextField("Комментарий", blank=True)
    trashed_at = models.DateTimeField("В корзине с", null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Загрузил", on_delete=models.SET_NULL, null=True, blank=True, related_name="uploaded_documents"
    )

    class Meta:
        ordering = ["-document_date", "-created_at", "-pk"]
        verbose_name = "документ"
        verbose_name_plural = "документы"
        indexes = [
            models.Index(fields=["organization", "trashed_at"], name="inventory_d_organiz_21a46e_idx"),
            models.Index(fields=["counterparty", "trashed_at"], name="inventory_d_counter_9f6952_idx"),
            models.Index(fields=["contract", "trashed_at"], name="inv_doc_contract_trash_idx"),
            models.Index(fields=["operation", "trashed_at"], name="inv_doc_operation_trash_idx"),
        ]

    def __str__(self):
        return self.display_title

    @property
    def display_title(self):
        if self.title:
            return self.title
        label = self.document_type.name if self.document_type_id else "Документ"
        if self.number:
            label += f" №{self.number}"
        return label

    @property
    def is_unclassified(self):
        return self.document_type_id is None

    def save(self, *args, **kwargs):
        if self.operation_id:
            self.organization_id = self.operation.organization_id
            self.contract_id = self.operation.contract_id
            if not self.counterparty_id:
                self.counterparty_id = self.operation.counterparty_id
            if not self.location_id:
                self.location_id = self.operation.location_id
        if self.file and not self.original_name:
            self.original_name = self.file.name.rsplit("/", 1)[-1]
        if self.contract_id:
            self.organization_id = self.contract.organization_id
            if not self.counterparty_id:
                self.counterparty_id = self.contract.counterparty_id
            if not self.location_id and self.contract.location_id:
                self.location_id = self.contract.location_id
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("document_detail", args=[self.pk])


class Reminder(TimeStampedModel):
    class Recurrence(models.TextChoices):
        ONCE = "once", "Однократно"
        MONTHLY = "monthly", "Ежемесячно"
        YEARLY = "yearly", "Ежегодно"
        INTERVAL = "interval", "Через заданное число дней"

    title = models.CharField("Напоминание", max_length=255)
    organization = models.ForeignKey(
        Organization, verbose_name="Организация", on_delete=models.SET_NULL, null=True, blank=True, related_name="reminders"
    )
    counterparty = models.ForeignKey(
        Counterparty, verbose_name="Контрагент", on_delete=models.SET_NULL, null=True, blank=True, related_name="reminders"
    )
    contract = models.ForeignKey(
        Contract, verbose_name="Договор", on_delete=models.SET_NULL, null=True, blank=True, related_name="reminders"
    )
    location = models.ForeignKey(
        Location, verbose_name="Объект", on_delete=models.SET_NULL, null=True, blank=True, related_name="reminders"
    )
    next_due_date = models.DateField("Дата", default=timezone.localdate, db_index=True)
    remind_days_before = models.PositiveSmallIntegerField("Напомнить заранее, дней", default=0)
    recurrence = models.CharField("Повтор", max_length=20, choices=Recurrence.choices, default=Recurrence.ONCE)
    interval_days = models.PositiveIntegerField("Интервал, дней", null=True, blank=True)
    amount = models.DecimalField("Сумма", max_digits=15, decimal_places=2, null=True, blank=True)
    notes = models.TextField("Комментарий", blank=True)
    snoozed_until = models.DateField("Отложено до", null=True, blank=True)
    active = models.BooleanField("Активно", default=True, db_index=True)
    last_completed_at = models.DateTimeField("Последнее выполнение", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Создал", on_delete=models.SET_NULL, null=True, blank=True, related_name="created_reminders"
    )

    class Meta:
        ordering = ["next_due_date", "pk"]
        verbose_name = "напоминание"
        verbose_name_plural = "напоминания"
        indexes = [models.Index(fields=["active", "next_due_date"], name="inventory_r_active_7541a8_idx")]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("reminder_list")

    @property
    def effective_due_date(self):
        return self.snoozed_until or self.next_due_date
