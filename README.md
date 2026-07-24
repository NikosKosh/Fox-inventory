# FOX Inventory

FOX Inventory is a self-hosted equipment accounting system for organizations, employees, warehouses, facilities, rooms, network cabinets, transfer documents, and movement history.

The application is designed for deployment on Ubuntu Server with Docker Compose. The interface and generated documents are localized in Russian.

## Features

- Multiple organizations and personal ownership records.
- Employee directory with departments, positions, phones, facilities, and rooms.
- Separate accounting groups for employee equipment and technical infrastructure.
- Warehouse view for available equipment.
- Facilities with rooms, employees, equipment, and network cabinets.
- Individual equipment cards with serial numbers, MAC addresses, internal codes, status, condition, location, credentials, and notes.
- Equipment issue and return workflows with or without a transfer document.
- DOCX transfer document generation and signed PDF attachment storage.
- Complete movement history linked to employees, equipment, and documents.
- Temporary inter-organization transfers.
- Excel and CSV import, Excel export.
- Global search, filters, server-side sorting, pagination, and quick equipment preview.
- Data quality control for missing documents, duplicate serial numbers, incomplete workstation sets, and unlinked locations.
- Authenticated document access with optional secret public links.
- PostgreSQL and file backup scripts.

## Technology stack

- Python 3.12+
- Django 5.2 LTS
- PostgreSQL 17
- Gunicorn
- Nginx
- Docker Compose
- OpenPyXL and python-docx

## Repository layout

```text
config/                 Django project configuration
inventory/              Domain models, forms, services, views, tests, migrations
templates/              Server-rendered HTML templates
static/                 Styles and application icons
deploy/                  Nginx configuration
scripts/                 Installation, update, backup, restore, and diagnostics
compose.yaml             Production container topology
```

## Production deployment

Detailed deployment instructions are available in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Minimal deployment sequence:

```bash
git clone <repository-url> fox-inventory
cd fox-inventory
bash scripts/install-docker-ubuntu.sh
# Start a new login session after Docker installation.
bash scripts/install-app.sh
```

The installation script creates `.env`, generates cryptographic secrets, starts PostgreSQL, Django, and Nginx, applies migrations, creates the administrator account, and performs a health check.

## Configuration

Configuration is loaded from `.env`. The complete variable list is provided in `.env.example`.

Important variables:

| Variable | Purpose |
|---|---|
| `APP_PORT` | Published HTTP port |
| `DJANGO_SECRET_KEY` | Django signing secret |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated host names or IP addresses |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated trusted origins including scheme and port |
| `POSTGRES_*` | PostgreSQL credentials |
| `FIELD_ENCRYPTION_KEY` | Fernet key for encrypted network passwords |
| `ADMIN_USERNAME` | Initial administrator login |
| `ADMIN_PASSWORD` | Initial administrator password |
| `ACT_*` | Optional defaults for generated transfer documents |

Secrets and uploaded documents must not be committed to Git. `.env`, `data/`, `media/`, and `backups/` are excluded by `.gitignore`.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
export DB_ENGINE=sqlite
export DJANGO_SECRET_KEY=development-only
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

## Tests

```bash
export DB_ENGINE=sqlite
export DJANGO_SECRET_KEY=test-only
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

The GitHub Actions workflow runs the same validation for each push and pull request.

## Operations

```bash
# Status and logs
bash scripts/diagnostics.sh

# Backup PostgreSQL and uploaded files
bash scripts/backup.sh

# Restore a backup
bash scripts/restore.sh backups/YYYYMMDD_HHMMSS

# Build and deploy the current checkout
bash scripts/update.sh
```

## Security notes

- Production mode uses `DJANGO_DEBUG=0`.
- Network equipment passwords are encrypted with `FIELD_ENCRYPTION_KEY`.
- Transfer document files require authentication unless a secret public link is explicitly enabled.
- Internet-facing deployments require HTTPS and a correctly configured `CSRF_TRUSTED_ORIGINS` value.
- Backup archives contain confidential data and must be protected accordingly.
