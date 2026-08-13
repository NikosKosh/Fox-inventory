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
