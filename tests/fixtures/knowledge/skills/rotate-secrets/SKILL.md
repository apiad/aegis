---
name: rotate-secrets
description: Rotate API tokens and passwords and update every service that reads them
---
Issue the new credential, update each service's env file, restart the services, then revoke the old token. A leaked token is rotated at once.
