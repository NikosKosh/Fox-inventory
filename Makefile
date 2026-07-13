.PHONY: check test migrate run compose-up compose-down logs backup

check:
	DB_ENGINE=sqlite DJANGO_SECRET_KEY=test-only python manage.py check
	DB_ENGINE=sqlite DJANGO_SECRET_KEY=test-only python manage.py makemigrations --check --dry-run

test:
	DB_ENGINE=sqlite DJANGO_SECRET_KEY=test-only python manage.py test

migrate:
	python manage.py migrate

run:
	python manage.py runserver

compose-up:
	docker compose up -d --build

compose-down:
	docker compose down

logs:
	docker compose logs -f --tail=200 web nginx

backup:
	bash scripts/backup.sh
