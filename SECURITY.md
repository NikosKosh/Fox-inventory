# Security

Security reports should be submitted through a private repository channel rather than a public issue.

The following data must remain outside source control:

- `.env`
- database dumps
- uploaded transfer documents
- employee directories
- serial-number inventories
- network equipment credentials
- production logs containing personal data

Internet-facing deployments require HTTPS, restricted administrative access, regular backups, and routine dependency updates.

## Authentication audit

FOX Inventory records successful, failed, and blocked login attempts. The account page exposes the recent audit trail according to the current user's privileges. Configure `LOGIN_TRUST_PROXY_HEADERS` only when requests reach the application through a controlled reverse proxy.
