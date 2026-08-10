# Changelog

## 1.4.0 — 2026-08-10

- Replaced the flat everyday “Documents” concept with a grouped “Registry”: one package is one logical record, while the technical “All files” mode remains available.
- Registry counters now distinguish logical records from physical files; grouping a set of files into a package reduces the logical record count without pretending files disappeared.
- Packaged files can no longer be selected for “assemble into package” again.
- Added package lifecycle controls: remove a file from a package, move it between compatible packages, and disband a package without deleting its files.
- Added safe unlinking of unused external parties from an organization. Links with business data and links between internal organizations are protected.
- Quick package upload no longer invents each document date from the package date.
- Filename-based type recognition is now explicitly marked as an assumption and routed to “Needs attention” for confirmation.
- Split “Needs attention” from “outside package”: a file can be correctly grouped but still need type confirmation.
- Added SHA-256 duplicate detection for new uploads and package quick uploads.
- Multi-file upload no longer silently copies one document date/number/amount/title to every selected file; per-document requisites are allowed only for a single file.
- Added document file version retention when a file is replaced.
- Added document/package activity history for upload, edit, grouping, moving, removing, trashing, restoring and disbanding.
- Organization filters in the registry, document dashboard and contract list now use the same symmetric relationship logic as the organization workspace.
- Internal organization counterparty pages now count both sides of internal relationships instead of only the physical counterparty FK.
- Reworked document navigation to “Workspace / Registry / Needs attention / Reminders”; flat dictionaries moved under “Directories”.
- Document edit, trash and restore flows preserve the user’s relationship/package context.
- Trash, attention queue and counterparty file lists are paginated instead of silently cutting off at fixed limits.
- Contract main files now remember the original uploaded filename and use it in the viewer/download flow.
- Added consistency/safety regression tests for grouped registry counts, package lifecycle, duplicate protection, upload metadata safety, symmetric filters, party unlinking and file versioning.

## 1.3.0 — 2026-08-10

- Reworked the document area around a complete user journey instead of separate database entities.
- Added persistent organization-to-counterparty relationships, so a company can be added before any contract or document exists.
- Added in-context “Add side” workflow: attach an existing counterparty or create a new one without leaving the organization workspace.
- Added duplicate protection by INN and normalized company name; quick creation reuses an existing card instead of producing a duplicate.
- Rebuilt the organization workspace: the side picker is now the primary action, large duplicate selectors/stat cards were removed, and totals became secondary context.
- Added an empty-relationship onboarding state with three explicit next steps: contract, package without contract, or standalone document.
- Added a persistent relationship action bar with context-aware Contract / Package / Document actions.
- Contract and package creation now lock the already selected organization/counterparty context and return to the correct viewpoint.
- Added direct drag-and-drop upload inside a document package. Files inherit the package context automatically.
- Added filename-based recognition for common document types: invoice, UPD, service act, waybill, invoice-facture, specification, addendum and contract.
- Reworked package and contract pages to make attached files visually obvious and openable in the 1.2.4 viewer.
- Rebuilt the generic document upload screen with a proper drop zone, selected-file list, context banner and safer navigation back to the source context.
- Added migration 0014 to backfill existing organization/counterparty relationships without duplicating contracts, operations or documents.
- Added UX regression tests for zero-document parties, duplicate handling, context locking and package quick upload.

## 1.2.4 — 2026-08-10

- Added a first-class document viewer for invoices, UPDs, service acts, waybills, specifications and other attached business documents.
- PDF files and images now open inside FOX Inventory instead of forcing users to download the original first.
- Preview files are served through authenticated application routes; the viewer does not rely on exposing raw media URLs.
- Document pages now keep the organization/counterparty viewpoint and show a relationship breadcrumb back through contract and operation context.
- Added previous/next navigation inside one operation package or the contract-document set, including keyboard Left/Right navigation.
- Unsupported Office/archive formats keep their originals intact and show a clear download fallback instead of a broken preview.
- Main contract files now use the same protected viewer and download flow.
- Operation cards show the first attached document names so a package is understandable before opening it.
- Included the 1.2.3 relationship-workspace hotfix: internal organization tests now follow friendly short-name rendering and package documents are visible in the workspace.

## 1.2.3 — 2026-08-10

- Rebuilt document navigation around a clear relationship context: organization → counterparty → contract → operation → documents.
- Organization workspace no longer mixes all counterparties into one flat screen; it opens as a searchable partner picker.
- Added a persistent two-sided context switcher so users always see whose side they are working from and which counterparty is selected.
- Added relationship cards that keep contract-level documents and execution operations inside the same contract hierarchy.
- Added explicit links between Counterparty and internal Organization records, replacing fragile name-only matching for two-sided internal contracts.
- Existing internal organizations receive canonical counterparty profiles during migration without duplicating contracts or documents.
- The same contract remains visible from either internal organization’s perspective.
- Contract names no longer repeat their number when the number is already part of the title.
- Contract, operation and document creation can be prefilled from the selected relationship context.
- Counterparty screens now distinguish external counterparties from internal organizations.

## 1.2.2 — 2026-08-10

- Added document operations as a package layer between contracts and individual files.
- One operation can contain invoices, UPDs, acts, waybills and related documents for one event.
- Contract-level documents such as addenda and general specifications remain outside operations.
- Existing transactional documents are grouped automatically on migration using contract category and date.
- Organization and contract workspaces now prioritize operations instead of flat document lists.
- Added "collect into operation" action for selected documents.
- New documents can be uploaded directly into an operation.

## 1.2.1 — 2026-08-10

- Document Center is now organization-centric: each organization has its own workspace.
- Contracts are visible from either internal party when a counterparty corresponds to an organization.
- Contract cards and details show both parties explicitly.
- Added FOX-IT SHOP as an organization workspace without duplicating existing contracts or documents.
- Organization filters for contracts, documents and reminders include records where the organization is the second party.

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
