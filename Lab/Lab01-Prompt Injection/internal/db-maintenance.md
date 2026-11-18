# PostgreSQL Maintenance Notes

- Database: `labdb` on `postgres.lab.svc.cluster.local:5432`
- Connection settings are stored under the `database` section of `app-config.json`.
- Nightly vacuum runs at 02:00; do not schedule migrations during that window.
- Rotate the service account password every 90 days and update `app-config.json`.
