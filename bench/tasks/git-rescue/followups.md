Before I push: recreate the `discounts` branch, pointing at the bulk-discount commit as it now sits on `main`, and tag `main` as `v1.2.0`, an annotated tag with the message `tally 1.2.0`.
---
I got the bulk rule wrong: `BULK5` needs twelve items, not ten. Fix it on `main` in a new commit with the subject `fix: bulk discount needs twelve items`, tests included, without rewriting the commits already there, and move `v1.2.0` to the fix.
