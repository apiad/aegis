import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""

    def json(self):
        return json.loads(self.body or b"null")

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400
