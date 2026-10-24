---
name: restore-database
description: Restore the Postgres database from a backup, then verify the restore
---
1. Pick the newest backup from before the loss.
2. Restore it into a fresh Postgres database.
3. Verify the row counts, then switch the service over.
