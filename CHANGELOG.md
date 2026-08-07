# Changelog

## 1.2.0 — 2026-08-07

- Добавлен встроенный раздел «Документы» для хранения и классификации файлов по организациям.
- Добавлены контрагенты, долгосрочные договоры, настраиваемые типы документов и неразобранные документы.
- Документы можно связывать с организацией, контрагентом, договором, объектом и оборудованием; необязательные связи не мешают быстрой загрузке.
- Добавлена загрузка нескольких файлов и drag-and-drop, а также корзина с восстановлением и отдельным окончательным удалением.
- Добавлены однократные, ежемесячные, ежегодные и интервальные напоминания с действиями «Готово» и «Отложить».
- Напоминания выводятся на главной странице только по созданным пользователем задачам; система не требует закрывающие документы и не ведёт задолженность.
- Документы и договоры интегрированы с организациями, объектами, оборудованием и глобальным поиском.
- Добавлены тесты разделения документов по организациям, неразобранной загрузки, корзины и повторяющихся напоминаний.

## 1.1.6 — 2026-08-03

- Добавлено безопасное удаление одного акта и массовое удаление выбранных актов.
- Удаление акта не меняет текущее закрепление оборудования и не удаляет историю операций.
- Файл удалённого акта и его публичная ссылка удаляются вместе с записью.
- Для каждой организации добавлены редактируемые реквизиты автозаполнения актов: юридическое наименование, город, представители при выдаче и возврате.
- Формы выдачи, возврата и последующего оформления акта используют реквизиты организации с резервным переходом к значениям из `.env`.

## 1.1.5

- Added an account page with an authenticated password-change workflow.
- Added a persistent login audit with username, result, IP address, timestamp, and user agent.
- Added configurable brute-force protection for username/client-IP pairs and a separate source-IP limit.
- Added reverse-proxy-aware client IP detection for Cloudflare and standard forwarding headers.
- Added deduplication of repeated blocked events to prevent audit-log flooding.
- Added a read-only login-attempt section in Django administration.
- Changed the update script to invoke the backup script through Bash, avoiding executable-bit failures after Git checkout.
- Added regression tests for logging, lockout, proxy IP handling, password changes, and log visibility.

## 1.1.4

- Added a normalized MAC-address field to equipment cards.
- Added MAC-address validation and uniqueness protection for non-empty values.
- Added MAC addresses to equipment forms, cards, previews, search, import and XLSX export.
- Added regression tests for MAC normalization, validation, search and export.

## 1.1.3

- Replaced browser-native sorting tooltips with accessible labels.
- Reworked table sorting indicators to use compact SVG chevrons without colored blocks.
- Unified sorting indicators in table headers and compact sort controls.
- Added keyboard focus styling and regression tests for sorting controls.

## 1.1.1

- Prepared a complete repository snapshot suitable for Git-based development.
- Removed historical patch installers and one-off data correction commands.
- Removed organization-specific names, addresses, serial numbers, and operational datasets from source control.
- Replaced hard-coded transfer document representatives and city values with environment configuration.
- Added repository documentation, CI configuration, development commands, and security guidance.
- Normalized interface guidance and source comments to impersonal technical language.

## 1.1.0

- Added rooms within facilities.
- Added room assignment for employees, equipment, and network cabinets.
- Added room cards, room equipment views, and bulk placement.
- Added room support to global search, Excel import, and Excel export.

## 1.0.2

- Refreshed the visual design, navigation, tables, icons, and authentication screen.

## 1.0.1

- Added server-side sorting for major lists and favicon support.

## 1.0.0

- Introduced facility-centered navigation, global search, quick equipment preview, pagination, and data quality control.
