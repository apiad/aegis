# Changelog

## 1.4.0

- `store check` reports books with more loans out than copies.
- `fetch_rows` accepts `order_by="-column"` for descending order.

## 1.3.0

- `search_books` falls back to matching the author.
- New `store export TABLE` command.

## 1.2.0

- `fetch_rows` takes `(operator, value)` pairs in `where`, and `None` means SQL NULL.
