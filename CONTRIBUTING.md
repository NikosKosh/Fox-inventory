# Contributing

## Branch workflow

- `main` contains production-ready code.
- Feature work is developed in short-lived branches.
- Database schema changes require Django migrations.
- Pull requests must pass checks and tests before merge.

## Development checks

```bash
export DB_ENGINE=sqlite
export DJANGO_SECRET_KEY=test-only
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

## Code conventions

- Business logic belongs in `inventory/services.py` or dedicated service modules.
- Views should remain focused on request handling.
- User-visible strings are maintained in Russian.
- Tests must use synthetic organizations, people, addresses, serial numbers, and documents.
- Credentials, production exports, signed documents, and database dumps must not be committed.
