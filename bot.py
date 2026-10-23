"""Anonymous message relay bot for the official Soroush Plus Bot API."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from storage import Storage
from soroush_api import SoroushAPI, SoroushAPIError

START_BUTTON = "🚀 استارت"
BUTTON_TEXT = "✉️ پیام ناشناس به مالک"
HELP_BUTTON = "📖 راهنمای استفاده"

# Owner moderation commands. They accept the target as "@username" or a numeric chat id.
BLOCK_COMMAND = "مسدود"
UNBLOCK_COMMAND = "رفع‌مسدود"
BLOCKLIST_COMMAND = "لیست‌مسدود"
BLOCK_COMMAND_ALIASES = {
    BLOCK_COMMAND, "مسدودکن", "بلاک", "بلاککن", "block", "ban",
}
UNBLOCK_COMMAND_ALIASES = {
    UNBLOCK_COMMAND, "رفع", "رفعمسدودیت", "آنبلاک", "آنمسدود", "آزادکن", "unblock", "unban",
}
BLOCKLIST_COMMAND_ALIASES = {
    BLOCKLIST_COMMAND, "لیستمسدودها", "مسدودها", "بلاکلیست", "blocklist",
}
BLOCK_USAGE = (
    "🚫 برای مسدود کردن کاربر، دستور را با یوزرنیم یا چت‌ایدی بفرستید:\n"
    "مسدود @username\n"
    "مسدود 123456789\n"
    "همچنین می‌توانید روی پیام همان کاربر ریپلای کنید و فقط بنویسید: مسدود"
)
UNBLOCK_USAGE = (
    "♻️ برای رفع مسدودیت:\n"
    "رفع‌مسدود @username\n"
    "رفع‌مسدود 123456789"
)
BLOCKED_USER_NOTICE = (
    "⛔️ امکان ارسال پیام ناشناس برای شما در حال حاضر وجود ندارد."
)
OWNER_COMMANDS_HELP = (
    "\n\n🛡 دستورهای مدیریتی مالک:\n"
    "• مسدود @username — مسدود کردن با یوزرنیم\n"
    "• مسدود 123456789 — مسدود کردن با چت‌ایدی عددی\n"
    "• «مسدود» به‌تنهایی در Reply روی پیام کاربر\n"
    "• رفع‌مسدود @username یا رفع‌مسدود 123456789\n"
    "• لیست‌مسدود — نمایش فهرست مسدودها"
)
WELCOME = (
    "سلام! به صندوق پیام ناشناس خوش آمدید.\n"
    "از منوی پایین، ارسال پیام یا راهنمای استفاده را انتخاب کنید."
)
HELP_TEXT = (
    "📖 راهنمای استفاده\n"
    "۱) «✉️ پیام ناشناس به مالک» را بزنید.\n"
    "۲) در پیام بعدی متن، عکس یا پیام پشتیبانی‌شده را بفرستید.\n"
    "۳) مالک می‌تواند روی پیام دریافتی ریپلای کند تا پاسخ برای شما ارسال شود.\n"
    "اطلاعات شناسایی شما فقط در پیام خصوصی مالک نمایش داده می‌شود."
)
OWNER_SETUP_GUIDE = (
    "\n\n⚙️ مالک هنوز تنظیم نشده است. مالک باید مقدار OWNER_ID در فایل .env را برابر "
    "chat_id عددی خودش در خط OWNER_DISCOVERY لاگ قرار دهد و ربات را دوباره اجرا کند.\n"
    "در گفت‌وگوی خصوصی، chat_id معمولاً همان شناسه عددی حساب است. ربات هیچ کاربری را خودکار مالک نمی‌کند."
)
OWNER_DISABLED = (
    "⛔️ ارسال پیام فعلاً غیرفعال است؛ OWNER_ID هنوز تنظیم نشده.\n"
    "اگر مالک ربات هستید، chat_id عددی خود را از لاگ OWNER_DISCOVERY بردارید، "
    "در .env مقابل OWNER_ID قرار دهید و ربات را دوباره اجرا کنید."
)
MAIN_MENU_MARKUP: dict[str, Any] = {
    "keyboard": [
        [{"text": START_BUTTON, "style": "primary"}],
        [{"text": BUTTON_TEXT, "style": "primary"}],
        [{"text": HELP_BUTTON, "style": "primary"}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
}


def _int_env(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} باید یک شناسه عددی باشد.") from exc
    if number <= 0:
        raise ValueError(f"{name} باید یک شناسه عددی مثبت باشد.")
    return number


def load_settings() -> tuple[str, int | None, str]:
    load_dotenv()
    token = os.getenv("SPLUS_BOT_TOKEN", "").strip()
    if not token or token.lower().startswith("put_"):
        raise ValueError("توکن تنظیم نشده است؛ فایل .env را از روی .env.example بسازید و SPLUS_BOT_TOKEN را وارد کنید.")
    owner_id = _int_env("OWNER_ID")
    owner_username = os.getenv("OWNER_USERNAME", "").strip().lstrip("@")
    return token, owner_id, owner_username


def _valid_id(value: Any) -> int | None:
    # bool is an int subclass in Python, but it is never a valid platform ID.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _safe_username(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "unknown"
    # Prevent control characters / log forging; usernames are informational only.
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "", value.lstrip("@"))[:64]
    return cleaned or "unknown"


def _log_owner_discovery(chat_id: int | None, user_id: int | None, username: Any) -> None:
    """Log identifiers only. Never include message text, tokens, or profile names."""
    logging.getLogger(__name__).info(
        "OWNER_DISCOVERY | chat_id=%s | user_id=%s | username=%s",
        chat_id if chat_id is not None else "unknown",
        user_id if user_id is not None else "unknown",
        _safe_username(username),
    )


def _normalize_command_token(text: str) -> str:
    """Normalize a command word: drop ZWNJ/RTL marks, slashes, and case differences."""
    cleaned = re.sub(r"[\u200c\u200d\u200e\u200f\ufeff]", "", text).strip().lower()
    return cleaned.lstrip("/")


_USERNAME_RE = re.compile(r"^[\w.]{2,64}$", re.UNICODE)
_ID_TEXT_RE = re.compile(r"^[+-]?\d{1,20}$")


def normalize_username(value: Any) -> str | None:
    """Return a bare, validated username (no '@') or None."""
    if not isinstance(value, str):
        return None
    name = value.strip().lstrip("@").strip()
    if not name or not _USERNAME_RE.fullmatch(name):
        return None
    return name.lower()


def _parse_block_target(raw: str) -> tuple[str, Any]:
    """Parse the argument of a block/unblock command.

    Returns one of:
      ("username", "<name>")  for "@name" or "name"
      ("id", <int>)           for a numeric chat id
      ("invalid", None)       for something unusable (e.g. a group id)
      ("empty", None)         when no argument was given
    """
    arg = raw.strip().strip(":،,").strip()
    if not arg:
        return "empty", None
    if arg.startswith("@"):
        name = normalize_username(arg)
        return ("username", name) if name else ("invalid", None)
    if _ID_TEXT_RE.fullmatch(arg):
        number = int(arg)
        # Private user chat ids are positive; negative ids mean groups/channels.
        return ("id", number) if number > 0 else ("invalid", None)
    name = normalize_username(arg)
    return ("username", name) if name else ("invalid", None)


def _fold_alias(value: str) -> str:
    """Fold a command word so ZWNJ, spaces, underscores, slashes and case do not matter."""
    return re.sub(r"[\u200c\u200d\u200e\u200f\ufeff\s_/]+", "", value).lower()


_BLOCK_ALIAS_FOLDED = {_fold_alias(alias) for alias in BLOCK_COMMAND_ALIASES}
_UNBLOCK_ALIAS_FOLDED = {_fold_alias(alias) for alias in UNBLOCK_COMMAND_ALIASES}
_BLOCKLIST_ALIAS_FOLDED = {_fold_alias(alias) for alias in BLOCKLIST_COMMAND_ALIASES}


def _block_command_kind(command: str) -> str | None:
    """Map a command token to 'block' | 'unblock' | 'blocklist'."""
    folded = _fold_alias(command)
    if not folded:
        return None
    if folded in _BLOCK_ALIAS_FOLDED:
        return "block"
    if folded in _UNBLOCK_ALIAS_FOLDED:
        return "unblock"
    if folded in _BLOCKLIST_ALIAS_FOLDED:
        return "blocklist"
    return None


def _display_name(user: dict[str, Any]) -> str:
    name = " ".join(
        str(user.get(key, "")).strip()
        for key in ("first_name", "last_name")
        if user.get(key)
    )
    return name or "نام ثبت نشده"


def _content_description(message: dict[str, Any]) -> str:
    for key in (
        "text", "photo", "video", "audio", "document", "voice", "video_note",
        "animation", "sticker", "contact", "location", "venue", "dice", "poll",
    ):
        if key in message:
            return "متن" if key == "text" else key.replace("_", " ")
    return "نوع پیام نامشخص/پشتیبانی‌نشده"


class AnonymousBot:
    def __init__(self, api: Any, storage: Storage, owner_id: int | None,
                 owner_username: str = "") -> None:
        self.api = api
        self.storage = storage
        self.owner_id = owner_id
        # Kept only as optional configuration metadata; it never grants owner access.
        self.owner_username = owner_username

    async def _say(self, chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
        return await self.api.send_message(chat_id, text, **kwargs)

    async def _user_say(self, chat_id: int, text: str, **kwargs: Any) -> dict[str, Any]:
        """Attach the main reply keyboard to every normal user-facing bot response."""
        kwargs.setdefault("reply_markup", deepcopy(MAIN_MENU_MARKUP))
        return await self._say(chat_id, text, **kwargs)

    def _is_owner(self, user_id: int, chat_id: int) -> bool:
        # Both server-provided numeric IDs and a private chat are required.
        # Username / display name are deliberately not used for authorization.
        return (
            self.owner_id is not None
            and user_id == self.owner_id
            and chat_id == self.owner_id
        )

    async def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message")
        if not isinstance(message, dict):
            return
        sender = message.get("from") or {}
        chat = message.get("chat") or {}
        if not isinstance(sender, dict):
            sender = {}
        if not isinstance(chat, dict):
            chat = {}
        user_id = _valid_id(sender.get("id"))
        chat_id = _valid_id(chat.get("id"))

        # Log discovery identifiers for every received message before normal routing.
        # Message bodies, names, and tokens are intentionally excluded.
        _log_owner_discovery(chat_id, user_id, sender.get("username"))
        if user_id is None or chat_id is None:
            return
        if chat.get("type") != "private":
            return

        text = message.get("text")
        text = text if isinstance(text, str) else ""
        command = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""
        is_owner = self._is_owner(user_id, chat_id)

        # Remember the username so a later "مسدود @name" (or remeasure after a rename)
        # can still be matched to a numeric ID even if the user changes it.
        sender_username = normalize_username(sender.get("username"))
        if sender_username:
            self.storage.upsert_known_user(user_id, chat_id, sender_username)

        # A real owner reply takes precedence over menu labels that might be the reply text.
        if is_owner and isinstance(message.get("reply_to_message"), dict):
            if self.storage.get_awaiting_chat(user_id) is None:
                handled = await self._handle_owner_command(message, user_id, chat_id)
                if handled:
                    return
            await self._handle_owner_message(message)
            return

        # Owner moderation commands (block / unblock / list) never apply to other users.
        if is_owner and self.storage.get_awaiting_chat(user_id) is None:
            if await self._handle_owner_command(message, user_id, chat_id):
                return

        # Both /start and the persistent start button show the same main menu.
        if command == "/start" or text == START_BUTTON:
            welcome = WELCOME + (OWNER_SETUP_GUIDE if self.owner_id is None else "")
            if is_owner:
                welcome += OWNER_COMMANDS_HELP
            await self._user_say(chat_id, welcome)
            return

        if text == HELP_BUTTON:
            help_text = HELP_TEXT + (OWNER_SETUP_GUIDE if self.owner_id is None else "")
            await self._user_say(chat_id, help_text)
            return

        # Blocked users cannot start composing an anonymous message.
        if self.storage.is_blocked(user_id, sender_username):
            if sender_username:
                self.storage.remember_username(user_id, sender_username, chat_id)
            self.storage.clear_awaiting(user_id)
            await self._user_say(chat_id, BLOCKED_USER_NOTICE)
            return

        # Retain the old button label for users who still have an older keyboard cached.
        if text == BUTTON_TEXT:
            if self.owner_id is None:
                await self._user_say(chat_id, OWNER_DISABLED)
                return
            self.storage.set_awaiting(user_id, chat_id)
            await self._user_say(
                chat_id,
                "پیامتان را در پیام بعدی بفرستید؛ متن، عکس و انواع پیام پشتیبانی‌شده قابل ارسال‌اند.",
            )
            return

        # Owner replies are routed only above; other owner messages do not enter user states.
        if is_owner and self.storage.get_awaiting_chat(user_id) is None:
            return

        awaiting_chat_id = self.storage.get_awaiting_chat(user_id)
        if awaiting_chat_id is not None:
            await self._relay_to_owner(message, user_id, awaiting_chat_id)
            self.storage.clear_awaiting(user_id)
            return

        if self.owner_id is None:
            await self._user_say(chat_id, OWNER_DISABLED)
        else:
            await self._user_say(
                chat_id,
                "برای ادامه از منوی پایین «✉️ پیام ناشناس به مالک» را بزنید؛ "
                "برای راهنما، «📖 راهنمای استفاده» را انتخاب کنید.",
            )

    async def _relay_to_owner(self, message: dict[str, Any], user_id: int, user_chat_id: int) -> None:
        if self.owner_id is None:
            self.storage.clear_awaiting(user_id)
            await self._user_say(user_chat_id, OWNER_DISABLED)
            return
        sender = message.get("from") or {}
        username = sender.get("username")
        username_text = f"@{_safe_username(username)}" if _safe_username(username) != "unknown" else "ندارد"
        msg_id = _valid_id(message.get("message_id"))
        if msg_id is None:
            await self._user_say(user_chat_id, "❌ پیام شناسه معتبر ندارد و ارسال نشد. لطفاً دوباره تلاش کنید.")
            return

        metadata = (
            "📩 پیام ناشناس جدید\n"
            f"👤 نام نمایشی: {_display_name(sender)}\n"
            f"🔗 یوزرنیم: {username_text}\n"
            f"🆔 شناسه کاربر: {user_id}\n"
            f"📦 نوع پیام: {_content_description(message)}\n\n"
            "برای پاسخ، روی این پیام یا پیام محتوای آن ریپلای کنید."
        )
        try:
            owner_note = await self._say(self.owner_id, metadata)
        except SoroushAPIError as exc:
            logging.warning("Could not deliver sender metadata (%s)", type(exc).__name__)
            await self._user_say(user_chat_id, "❌ ارسال به مالک ناموفق بود. لطفاً کمی بعد دوباره تلاش کنید.")
            return

        note_id = _extract_message_id(owner_note)
        if note_id is not None:
            self.storage.add_reply_target(self.owner_id, note_id, user_id, user_chat_id)

        try:
            copied = await self.api.copy_message(self.owner_id, user_chat_id, msg_id)
            copied_id = _extract_message_id(copied)
            if copied_id is not None:
                self.storage.add_reply_target(self.owner_id, copied_id, user_id, user_chat_id)
            await self._user_say(user_chat_id, "✅ پیام شما با موفقیت و به‌صورت ناشناس برای مالک ارسال شد.")
        except SoroushAPIError as exc:
            logging.info("Message copy unavailable (%s)", type(exc).__name__)
            fallback = await self._say(
                self.owner_id,
                "⚠️ محتوای پیام به‌دلیل محدودیت API قابل کپی نبود. "
                f"نوع گزارش‌شده: {_content_description(message)}. "
                "برای پاسخ به کاربر، به همین پیام یا پیام مشخصات فرستنده ریپلای کنید.",
            )
            fallback_id = _extract_message_id(fallback)
            if fallback_id is not None:
                self.storage.add_reply_target(self.owner_id, fallback_id, user_id, user_chat_id)
            await self._user_say(
                user_chat_id,
                "⚠️ مشخصات پیام به مالک ارسال شد، اما خود محتوای این نوع پیام توسط API قابل کپی نبود.",
            )

    # ------------------------------------------------------------------
    # Owner moderation: block / unblock / list blocked users
    # ------------------------------------------------------------------
    async def _handle_owner_command(self, message: dict[str, Any], user_id: int,
                                    chat_id: int) -> bool:
        """Handle «مسدود …» style commands. Returns True when the message was consumed."""
        text = message.get("text")
        if not isinstance(text, str):
            return False
        parts = text.strip().split(maxsplit=1)
        if not parts:
            return False
        first = parts[0]
        argument = parts[1] if len(parts) > 1 else ""
        kind = _block_command_kind(_normalize_command_token(first))
        if kind is None and "@" in first:
            # Tolerate the command glued to the username: «مسدود@osinw».
            head, _, tail = first.partition("@")
            kind = _block_command_kind(_normalize_command_token(head))
            if kind is not None:
                argument = ("@" + tail + (" " + argument if argument else "")).strip()
        if kind is None:
            return False

        if kind == "blocklist":
            await self._send_blocklist(chat_id)
            return True
        if kind == "block":
            await self._block_target(message, chat_id, argument)
            return True
        await self._unblock_target(message, chat_id, argument)
        return True

    def _resolve_target(self, argument: str, message: dict[str, Any]) -> tuple[str, Any]:
        """Work out who the command refers to: explicit argument first, then Reply target."""
        kind, value = _parse_block_target(argument)
        if kind != "empty":
            return kind, value
        reply = message.get("reply_to_message")
        owner_chat_id = _valid_id((message.get("chat") or {}).get("id"))
        reply_id = _valid_id(reply.get("message_id")) if isinstance(reply, dict) else None
        if reply_id is not None and owner_chat_id is not None:
            target = self.storage.get_reply_target(owner_chat_id, reply_id)
            if target is not None:
                return "id", target[0]
        return "empty", None

    async def _block_target(self, message: dict[str, Any], chat_id: int, argument: str) -> None:
        kind, value = self._resolve_target(argument, message)
        if kind == "empty":
            await self._say(chat_id, "⚠️ مخاطب مشخص نشد.\n" + BLOCK_USAGE)
            return
        if kind == "invalid":
            await self._say(chat_id, "⚠️ یوزرنیم یا چت‌ایدی معتبر نیست.\n" + BLOCK_USAGE)
            return

        if kind == "id":
            target_id: int = value
            username = None
            row = self.storage.get_known_user(target_id)
            if row is not None:
                username = row.get("username")
            if target_id == self.owner_id:
                await self._say(chat_id, "⚠️ نمی‌توانید خودتان را مسدود کنید.")
                return
        else:
            username = value
            known = self.storage.find_user_id_by_username(username)
            target_id = known if known is not None else None
            if target_id is None:
                # Not seen yet: keep a username-only rule that matches future messages.
                self.storage.block_username_only(username)
                await self._say(
                    chat_id,
                    f"🚫 یوزرنیم @{username} مسدود شد.\n"
                    "این کاربر هنوز با ربات صحبت نکرده است؛ به‌محض ارسال پیام بعدی‌اش، "
                    "پیام‌هایش برای شما ارسال نخواهد شد. برای مسدودسازی قطعی، چت‌ایدی عددی را هم مسدود کنید.",
                )
                return
            if target_id == self.owner_id:
                await self._say(chat_id, "⚠️ نمی‌توانید خودتان را مسدود کنید.")
                return
            row = self.storage.get_known_user(target_id)
            username = row.get("username") if row else username

        known_row = self.storage.get_known_user(target_id)
        known_chat_id = known_row.get("chat_id") if known_row else None
        self.storage.block_user(target_id, known_chat_id, username)
        self.storage.clear_awaiting(target_id)
        label = f"@{username}" if username else "بدون یوزرنیم"
        await self._say(
            chat_id,
            "✅ کاربر مسدود شد.\n"
            f"🆔 شناسه: {target_id}\n"
            f"🔗 یوزرنیم: {label}\n"
            "از این پس پیام‌های او برای شما ارسال نمی‌شود.",
        )

    async def _unblock_target(self, message: dict[str, Any], chat_id: int, argument: str) -> None:
        kind, value = self._resolve_target(argument, message)
        if kind == "empty":
            await self._say(chat_id, "⚠️ مخاطب مشخص نشد.\n" + UNBLOCK_USAGE)
            return
        if kind == "invalid":
            await self._say(chat_id, "⚠️ یوزرنیم یا چت‌ایدی معتبر نیست.\n" + UNBLOCK_USAGE)
            return

        removed = False
        if kind == "id":
            removed = self.storage.unblock_user(value)
        else:
            found = self.storage.find_blocked_by_username(value)
            if found is not None and found[0] is not None:
                removed = self.storage.unblock_user(found[0])
            removed = self.storage.unblock_username(value) or removed

        if removed:
            await self._say(chat_id, "♻️ مسدودیت برداشته شد؛ این کاربر دوباره می‌تواند پیام ناشناس بفرستد.")
        else:
            await self._say(chat_id, "ℹ️ این کاربر در فهرست مسدودها نبود.")

    async def _send_blocklist(self, chat_id: int) -> None:
        rows = self.storage.list_blocked()
        if not rows:
            await self._say(chat_id, "📋 فهرست مسدودها خالی است.")
            return
        lines = [f"📋 کاربران مسدود ({len(rows)} مورد):"]
        for row in rows[:50]:
            username = f"@{row['username']}" if row["username"] else "بدون یوزرنیم"
            identifier = row["user_id"] if row["user_id"] is not None else "شناسه ناشناخته"
            lines.append(f"• {identifier} — {username}")
        if len(rows) > 50:
            lines.append(f"… و {len(rows) - 50} مورد دیگر")
        lines.append("\nبرای رفع مسدودیت: رفع‌مسدود <یوزرنیم یا چت‌ایدی>")
        await self._say(chat_id, "\n".join(lines))

    async def _handle_owner_message(self, message: dict[str, Any]) -> None:
        reply = message.get("reply_to_message")
        if not isinstance(reply, dict) or _valid_id(reply.get("message_id")) is None:
            return
        owner_chat_id = _valid_id((message.get("chat") or {}).get("id"))
        if owner_chat_id is None:
            return
        target = self.storage.get_reply_target(owner_chat_id, reply["message_id"])
        if target is None:
            await self._say(owner_chat_id, "⚠️ این پیام به پیام دریافتی معتبری از ربات مربوط نیست؛ پاسخی ارسال نشد.")
            return
        _user_id, user_chat_id = target
        msg_id = _valid_id(message.get("message_id"))
        if msg_id is None:
            await self._say(owner_chat_id, "⚠️ شناسه پیام پاسخ معتبر نیست؛ پاسخی ارسال نشد.")
            return
        try:
            await self.api.copy_message(user_chat_id, owner_chat_id, msg_id)
            await self._say(owner_chat_id, "✅ پاسخ شما به فرستنده اصلی ارسال شد.")
        except SoroushAPIError as exc:
            logging.warning("Could not copy owner reply (%s)", type(exc).__name__)
            text = message.get("text")
            if isinstance(text, str) and text:
                try:
                    await self._user_say(user_chat_id, text)
                    await self._say(owner_chat_id, "✅ پاسخ متنی شما به فرستنده اصلی ارسال شد.")
                    return
                except SoroushAPIError:
                    pass
            await self._say(owner_chat_id, "❌ ارسال پاسخ ناموفق بود؛ نوع پیام ممکن است توسط API پشتیبانی نشود.")


def _extract_message_id(result: Any) -> int | None:
    if not isinstance(result, dict):
        return None
    return _valid_id(result.get("message_id"))


async def run() -> None:
    token, owner_id, owner_username = load_settings()
    database_path = Path(os.getenv("DATABASE_PATH", "bot.sqlite3"))
    database_path.parent.mkdir(parents=True, exist_ok=True)
    storage = Storage(database_path)
    api = SoroushAPI(token)
    bot = AnonymousBot(api, storage, owner_id, owner_username)
    if owner_id is None:
        logging.warning("OWNER_ID تنظیم نشده؛ ربات اجرا می‌شود اما ارسال پیام ناشناس غیرفعال است.")
    else:
        logging.info("Configured owner numeric ID: %s", owner_id)

    offset = storage.get_cursor() + 1
    delay = 2
    try:
        while True:
            try:
                updates = await api.get_updates(offset=offset, timeout=25)
                delay = 2
                for update in updates:
                    update_id = _valid_id(update.get("update_id"))
                    if update_id is None or update_id < offset:
                        continue
                    try:
                        await bot.handle_update(update)
                    except SoroushAPIError as exc:
                        logging.warning("Update handling API failure (%s); will retry update", type(exc).__name__)
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 60)
                        break
                    except Exception:
                        logging.exception("Unexpected update handler failure; will retry update")
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 60)
                        break
                    storage.save_cursor(update_id)
                    offset = update_id + 1
            except SoroushAPIError as exc:
                logging.warning("Polling failed (%s); retrying in %ss", type(exc).__name__, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
    except asyncio.CancelledError:
        raise
    finally:
        await api.close()
        storage.close()


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(run())
    except ValueError as exc:
        raise SystemExit(f"تنظیمات نامعتبر: {exc}") from exc
    except KeyboardInterrupt:
        print("ربات متوقف شد.")


if __name__ == "__main__":
    main()
