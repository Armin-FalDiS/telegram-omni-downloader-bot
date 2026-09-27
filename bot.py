import asyncio
import contextlib
import logging
import secrets
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, MessageEntity, Update
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from access import AccessList, Member
from config import COOKIES_PATH, DATABASE_PATH, MEGABYTE, Config, load_config
from links import LinkKind, classify, normalize
from media import Downloader, MediaFile, VideoOption

MIN_CHOICE_QUALITY = 1080
UPLOAD_TIMEOUT = 3600
UPLOAD_ATTEMPTS = 3
UPLOAD_RETRY_DELAY = 10

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Request:
    link: Message
    status: Message
    url: str


@dataclass(frozen=True)
class PendingChoice:
    request: Request
    options: list[VideoOption]


class OmniBot:
    def __init__(self, config: Config):
        self._config = config
        self._access = AccessList(DATABASE_PATH, config.admin_id)
        self._downloader = Downloader(COOKIES_PATH)
        self._pending: dict[str, PendingChoice] = {}

    def build(self) -> Application:
        builder = Application.builder().token(self._config.token).concurrent_updates(True)
        if self._config.api_url:
            builder = (
                builder.base_url(f"{self._config.api_url}/bot")
                .base_file_url(f"{self._config.api_url}/file/bot")
                .local_mode(True)
            )
        application = builder.build()
        admin_only = filters.User(user_id=self._config.admin_id)
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("waitlist", self.show_waitlist, filters=admin_only))
        application.add_handler(CallbackQueryHandler(self.approve, pattern=r"^approve:"))
        application.add_handler(CallbackQueryHandler(self.choose_video, pattern=r"^video:"))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_link))
        return application

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if self._access.is_approved(user.id):
            await update.message.reply_text("Send me a link.")
            return
        self._access.add_to_waitlist(Member(user.id, user.full_name, user.username))

    async def show_waitlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text, keyboard = self._waitlist_view()
        await update.message.reply_text(text, reply_markup=keyboard)

    async def approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not self._access.is_admin(query.from_user.id):
            await query.answer()
            return
        user_id = int(query.data.removeprefix("approve:"))
        if self._access.approve(user_id):
            await context.bot.send_message(user_id, "You now have access. Send me a link.")
        await query.answer("Approved")
        text, keyboard = self._waitlist_view()
        await query.edit_message_text(text, reply_markup=keyboard)

    async def handle_link(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        if not self._access.is_approved(message.from_user.id):
            return
        url = find_link(message)
        if url is None:
            return
        kind = classify(url)
        if kind is None:
            await message.reply_text("Unsupported link.")
            return
        request = Request(message, await message.reply_text("Working on it…"), url)
        if kind is LinkKind.AUDIO:
            await self._send_audio(request)
        else:
            await self._offer_video(request)

    async def choose_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        _, token, index = query.data.split(":")
        pending = self._pending.get(token)
        if pending is None or pending.request.link.from_user.id != query.from_user.id:
            await query.answer("This choice has expired.")
            return
        del self._pending[token]
        option = pending.options[int(index)]
        await query.answer()
        await query.edit_message_text(f"Downloading {option.label}…")
        await self._send_video(pending.request, option)

    async def _offer_video(self, request: Request) -> None:
        status = request.status
        try:
            options = await self._downloader.video_options(request.url)
        except Exception:
            logger.exception("Failed to read formats for %s", request.url)
            await status.edit_text("Couldn't read this link.")
            return
        fitting = [option for option in options if option.size is None or option.size <= self._config.upload_limit]
        if options and not fitting:
            await status.edit_text(f"Too large to send (at least {format_size(min(option.size for option in options))}).")
            return
        choices = [option for option in fitting if option.quality >= MIN_CHOICE_QUALITY]
        if not choices and any(option.quality >= MIN_CHOICE_QUALITY for option in options):
            await status.edit_text(f"Too large in {MIN_CHOICE_QUALITY}p and above, downloading {fitting[-1].label}…")
        choices = choices or fitting[-1:]
        if len(choices) <= 1:
            await self._send_video(request, choices[0] if choices else None)
            return
        token = secrets.token_urlsafe(6)
        self._pending[token] = PendingChoice(request, choices)
        buttons = [
            [InlineKeyboardButton(button_label(option), callback_data=f"video:{token}:{index}")]
            for index, option in enumerate(choices)
        ]
        await status.edit_text("Choose quality:", reply_markup=InlineKeyboardMarkup(buttons))

    async def _send_video(self, request: Request, option: VideoOption | None) -> None:
        async def send(media: MediaFile) -> None:
            await request.link.chat.send_video(
                media.path,
                caption=media.title,
                duration=media.duration,
                width=media.width,
                height=media.height,
                supports_streaming=True,
                read_timeout=UPLOAD_TIMEOUT,
                write_timeout=UPLOAD_TIMEOUT,
            )

        await self._deliver(request, lambda directory: self._downloader.video(request.url, option, directory), send)

    async def _send_audio(self, request: Request) -> None:
        async def send(media: MediaFile) -> None:
            await request.link.chat.send_audio(
                media.path,
                title=media.title,
                performer=media.artist,
                duration=media.duration,
                read_timeout=UPLOAD_TIMEOUT,
                write_timeout=UPLOAD_TIMEOUT,
            )

        await self._deliver(request, lambda directory: self._downloader.audio(request.url, directory), send)

    async def _deliver(self, request: Request, download, send) -> None:
        status = request.status
        with tempfile.TemporaryDirectory() as directory:
            try:
                media = await download(Path(directory))
            except Exception:
                logger.exception("Download failed")
                await status.edit_text("Download failed.")
                return
            size = media.path.stat().st_size
            if size > self._config.upload_limit:
                await status.edit_text(f"Too large to send ({format_size(size)}).")
                return
            await status.edit_text("Uploading…")
            try:
                await send_with_retries(send, media)
            except Exception:
                logger.exception("Upload failed")
                await status.edit_text("Upload failed.")
                return
        for message in (request.status, request.link):
            with contextlib.suppress(BadRequest):
                await message.delete()

    def _waitlist_view(self) -> tuple[str, InlineKeyboardMarkup | None]:
        members = self._access.waitlist()
        if not members:
            return "Waitlist is empty.", None
        buttons = [[InlineKeyboardButton(member.label, callback_data=f"approve:{member.id}")] for member in members]
        return "Tap to approve:", InlineKeyboardMarkup(buttons)


async def send_with_retries(send, media: MediaFile) -> None:
    for attempt in range(1, UPLOAD_ATTEMPTS + 1):
        try:
            await send(media)
            return
        except (BadRequest, TimedOut):
            raise
        except (NetworkError, RetryAfter) as error:
            if attempt == UPLOAD_ATTEMPTS:
                raise
            logger.warning("Upload attempt %d failed: %s", attempt, error)
            await asyncio.sleep(retry_delay(error))


def retry_delay(error: Exception) -> float:
    if not isinstance(error, RetryAfter):
        return UPLOAD_RETRY_DELAY
    delay = error.retry_after
    return delay.total_seconds() if isinstance(delay, timedelta) else delay


def button_label(option: VideoOption) -> str:
    return f"{option.label} · {format_size(option.size)}" if option.size else option.label


def format_size(size: int) -> str:
    if size >= 1000 * MEGABYTE:
        return f"{size / (1024 * MEGABYTE):.1f} GB"
    return f"{size // MEGABYTE} MB"


def find_link(message: Message) -> str | None:
    entities = message.parse_entities([MessageEntity.URL, MessageEntity.TEXT_LINK])
    for entity, text in entities.items():
        return normalize(entity.url or text)
    return None


def main() -> None:
    load_dotenv()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    OmniBot(load_config()).build().run_polling()


if __name__ == "__main__":
    main()
