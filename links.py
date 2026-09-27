from enum import Enum
from urllib.parse import urlparse


class LinkKind(Enum):
    AUDIO = "audio"
    VIDEO = "video"


KIND_BY_HOST = {
    "music.youtube.com": LinkKind.AUDIO,
    "youtube.com": LinkKind.VIDEO,
    "www.youtube.com": LinkKind.VIDEO,
    "m.youtube.com": LinkKind.VIDEO,
    "youtu.be": LinkKind.VIDEO,
}


def normalize(link: str) -> str:
    return link if "://" in link else f"https://{link}"


def classify(url: str) -> LinkKind | None:
    host = (urlparse(url).hostname or "").lower()
    return KIND_BY_HOST.get(host)
