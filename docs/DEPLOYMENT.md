# Ubuntu Server deployment

## Requirements

- Ubuntu Server 22.04 or newer
- 2 vCPU
- 4 GB RAM
- 40 GB or more of storage
- A user account with `sudo` privileges

## Installation from Git

```bash
cd "$HOME"
git clone <repository-url> fox-inventory
cd fox-inventory
```

Docker installation:

```bash
bash scripts/install-docker-ubuntu.sh
```

A new login session is required after the account is added to the `docker` group.

Application installation:

```bash
cd "$HOME/fox-inventory"
bash scripts/install-app.sh
```

The installer requests the application port, administrator login, and administrator password. Generated secrets and runtime configuration are stored in `.env` with mode `0600`.

## Manual configuration

A manually maintained configuration can be created from the example:

```bash
cp .env.example .env
chmod 600 .env
```

For access through an IP address and port:

```dotenv
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.50
CSRF_TRUSTED_ORIGINS=http://192.168.1.50:8088
```

For access through HTTPS:

```dotenv
DJANGO_ALLOWED_HOSTS=inventory.example.org
CSRF_TRUSTED_ORIGINS=https://inventory.example.org
```

## Service commands

```bash
cd "$HOME/fox-inventory"
docker compose ps
docker compose logs -f --tail=200 web nginx
bash scripts/diagnostics.sh
```

## Update

```bash
cd "$HOME/fox-inventory"
git pull --ff-only
bash scripts/update.sh
```

The update script creates a backup before rebuilding the containers.

## Backup

```bash
cd "$HOME/fox-inventory"
bash scripts/backup.sh
```

Backup structure:

```text
backups/YYYYMMDD_HHMMSS/
├── database.dump
├── media.tar.gz
└── env.backup
```

## Restore

```bash
cd "$HOME/fox-inventory"
bash scripts/restore.sh backups/YYYYMMDD_HHMMSS
```

## Firewall

Example for the default port:

```bash
sudo ufw allow 8088/tcp
sudo ufw status
```

For an internet-facing installation, direct port publication should be replaced or protected by an HTTPS reverse proxy.
