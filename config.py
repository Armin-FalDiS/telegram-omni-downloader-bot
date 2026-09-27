import os
from dataclasses import dataclass

MEGABYTE = 1024 * 1024
DATABASE_PATH = "/data/bot.db"
COOKIES_PATH = "/data/cookies.txt"


@dataclass(frozen=True)
class Config:
    token: str
    admin_id: int
    api_url: str | None

    @property
    def upload_limit(self) -> int:
        return (2000 if self.api_url else 50) * MEGABYTE


def load_config() -> Config:
    return Config(
        token=os.environ["BOT_TOKEN"],
        admin_id=int(os.environ["ADMIN_ID"]),
        api_url=os.environ.get("TELEGRAM_API_URL") or None,
    )
