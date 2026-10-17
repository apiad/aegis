from collections.abc import Iterator

from .client import Client


def pages(client: Client, path: str, per_page: int = 50) -> Iterator[list]:
    """Yield each page's items until the server returns a page without `next`."""
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        data = client.get(f"{path}{sep}page={page}&per_page={per_page}").json()
        yield data["items"]
        if not data.get("next"):
            return
        page += 1


def all_items(client: Client, path: str, per_page: int = 50) -> list:
    return [item for page in pages(client, path, per_page) for item in page]
