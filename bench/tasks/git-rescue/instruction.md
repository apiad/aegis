This repository is in a mess and I need it back. I was rebasing my `discounts` branch onto `main`: two commits, "feat: discount codes" and "feat: bulk discount needs ten items". Something went wrong, the commits are no longer on any branch, and I deleted the branch. Then I started merging `refactor-lines` into `main` and stopped halfway through the conflicts.

End with `main` holding all of it: the line refactor merged, and both discount commits on top with their original messages. No conflict markers anywhere, no merge left in progress, and `python3 -m pytest` green on `main` as committed.
