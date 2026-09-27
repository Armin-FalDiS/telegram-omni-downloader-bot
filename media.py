import asyncio
from dataclasses import dataclass, replace
from pathlib import Path

from yt_dlp import YoutubeDL

FALLBACK_VIDEO_SELECTOR = "bestvideo*+bestaudio/best"
CODEC_RANK = {"av01": 1, "vp9": 2, "vp09": 2, "avc1": 3}


@dataclass(frozen=True)
class VideoOption:
    quality: int
    fps: int
    selector: str
    size: int | None

    @property
    def label(self) -> str:
        return f"{self.quality}p{self.fps}" if self.fps > 30 else f"{self.quality}p"


@dataclass(frozen=True)
class MediaFile:
    path: Path
    title: str
    artist: str | None
    duration: int | None
    width: int | None
    height: int | None


class Downloader:
    def __init__(self, cookies_file: str | None):
        self._params = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True}
        if cookies_file:
            self._params["cookiefile"] = cookies_file

    async def video_options(self, url: str) -> list[VideoOption]:
        info = await asyncio.to_thread(self._extract, url)
        return _best_option_per_quality(info.get("formats") or [], info.get("duration"))

    async def video(self, url: str, option: VideoOption | None, directory: Path) -> MediaFile:
        params = {
            "format": option.selector if option else FALLBACK_VIDEO_SELECTOR,
            "merge_output_format": "mp4",
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        }
        return await asyncio.to_thread(self._download, url, directory, params)

    async def audio(self, url: str, directory: Path) -> MediaFile:
        params = {
            "format": "bestaudio/best",
            "writethumbnail": True,
            "postprocessors": [
                {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
                {"key": "FFmpegMetadata"},
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg"},
                {"key": "EmbedThumbnail"},
            ],
        }
        media = await asyncio.to_thread(self._download, url, directory, params)
        return replace(media, path=media.path.rename(media.path.with_suffix(".m4a")))

    def _extract(self, url: str) -> dict:
        with YoutubeDL(self._params) as ydl:
            return ydl.extract_info(url, download=False)

    def _download(self, url: str, directory: Path, params: dict) -> MediaFile:
        params = {**self._params, **params, "outtmpl": str(directory / "%(id)s.%(ext)s")}
        with YoutubeDL(params) as ydl:
            info = ydl.extract_info(url, download=True)
        return MediaFile(
            path=Path(info["requested_downloads"][0]["filepath"]),
            title=info.get("track") or info.get("title") or "",
            artist=", ".join(info.get("artists") or []) or info.get("uploader"),
            duration=int(info["duration"]) if info.get("duration") else None,
            width=info.get("width"),
            height=info.get("height"),
        )


def _best_option_per_quality(formats: list[dict], duration: float | None) -> list[VideoOption]:
    audio_size = _audio_size(formats, duration)
    best: dict[int, dict] = {}
    for fmt in formats:
        if fmt.get("vcodec") == "none" or not fmt.get("height"):
            continue
        quality = _quality(fmt)
        if quality not in best or _rank(fmt) > _rank(best[quality]):
            best[quality] = fmt
    return [_option(quality, best[quality], duration, audio_size) for quality in sorted(best)]


def _quality(fmt: dict) -> int:
    return min(fmt["height"], fmt.get("width") or fmt["height"])


def _rank(fmt: dict) -> tuple:
    codec = (fmt.get("vcodec") or "").split(".")[0]
    codec_rank = CODEC_RANK.get(codec, 0)
    is_direct = not (fmt.get("protocol") or "").startswith("m3u8")
    return fmt.get("fps") or 0, codec_rank, is_direct, fmt.get("tbr") or 0


def _option(quality: int, fmt: dict, duration: float | None, audio_size: int | None) -> VideoOption:
    format_id = fmt["format_id"]
    has_audio = fmt.get("acodec") not in (None, "none")
    selector = format_id if has_audio else f"{format_id}+bestaudio[ext=m4a]/{format_id}+bestaudio"
    size = _size(fmt, duration)
    if size is not None and not has_audio:
        size += audio_size or 0
    return VideoOption(quality, round(fmt.get("fps") or 0), selector, size)


def _audio_size(formats: list[dict], duration: float | None) -> int | None:
    audio = [fmt for fmt in formats if fmt.get("vcodec") == "none" and fmt.get("acodec") not in (None, "none")]
    preferred = [fmt for fmt in audio if fmt.get("ext") == "m4a"] or audio
    return max((size for fmt in preferred if (size := _size(fmt, duration))), default=None)


def _size(fmt: dict, duration: float | None) -> int | None:
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if size:
        return int(size)
    if fmt.get("tbr") and duration:
        return int(fmt["tbr"] * 1000 / 8 * duration)
    return None
