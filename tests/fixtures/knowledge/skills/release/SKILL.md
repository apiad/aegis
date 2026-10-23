---
name: release
description: Use when bumping the version and cutting a release; covers the lock-file gate and the changelog
---
1. Bump the version in pyproject.
2. Refresh the lock file and check it is committed.
3. Collate the changelog fragments.
4. Tag the commit and push the tag; CI publishes the package.
