---
name: database-backup
description: Take, verify and restore backups of the Postgres database
---
Use pg_dump for a backup and pg_restore to restore it into a scratch database first. A backup that was never restored is not verified.
