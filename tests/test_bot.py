import asyncio
import tempfile
import unittest
from pathlib import Path

from bot import (
    AnonymousBot,
    BUTTON_TEXT,
    HELP_BUTTON,
    MAIN_MENU_MARKUP,
    OWNER_DISABLED,
    START_BUTTON,
)
from soroush_api import SoroushAPIError
from storage import Storage


class FakeAPI:
    def __init__(self):
        self.sent = []
        self.copied = []
        self.next_id = 100
        self.fail_copy = False
        self.fail_send = False

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail_send:
            self.fail_send = False
            raise SoroushAPIError("fake send error")
        self.next_id += 1
        result = {"message_id": self.next_id, "chat": {"id": chat_id}, "text": text}
        self.sent.append((chat_id, text, kwargs, result))
        return result

    async def copy_message(self, chat_id, from_chat_id, message_id):
        if self.fail_copy:
            raise SoroushAPIError("fake copy error")
        self.next_id += 1
        self.copied.append((chat_id, from_chat_id, message_id, self.next_id))
        return {"message_id": self.next_id}


def private_message(user_id, message_id, text=None, *, chat_id=None, reply_to=None, username="sample", **content):
    chat_id = chat_id if chat_id is not None else user_id
    message = {
        "message_id": message_id,
        "from": {"id": user_id, "first_name": "آزمایشی", "last_name": "کاربر", "username": username},
        "chat": {"id": chat_id, "type": "private"},
    }
    if text is not None:
        message["text"] = text
    if reply_to is not None:
        message["reply_to_message"] = {"message_id": reply_to}
    message.update(content)
    return {"update_id": message_id, "message": message}


class BotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.sqlite3"
        self.storage = Storage(self.db_path)
        self.api = FakeAPI()
        self.bot = AnonymousBot(self.api, self.storage, owner_id=9001)

    def tearDown(self):
        self.storage.close()
        self.temp.cleanup()

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_start_logs_real_ids_without_message_content(self):
        with self.assertLogs("bot", level="INFO") as captured:
            self.run_async(
                self.bot.handle_update(
                    private_message(23, 1, "/start never-log-this-body", chat_id=456789)
                )
            )
        joined = "\n".join(captured.output)
        self.assertIn("OWNER_DISCOVERY | chat_id=456789 | user_id=23 | username=sample", joined)
        self.assertNotIn("never-log-this-body", joined)
        self.assertNotIn("آزمایشی", joined)

    def test_discovery_log_is_emitted_for_non_private_messages_too(self):
        update = {
            "update_id": 99,
            "message": {
                "message_id": 70,
                "from": {"id": 37, "username": "group_user"},
                "chat": {"id": -7654, "type": "group"},
                "text": "private-content-must-not-be-logged",
            },
        }
        with self.assertLogs("bot", level="INFO") as captured:
            self.run_async(self.bot.handle_update(update))
        self.assertIn("chat_id=-7654 | user_id=37 | username=group_user", captured.output[0])
        self.assertNotIn("private-content-must-not-be-logged", captured.output[0])
        self.assertEqual(self.api.sent, [])

    def test_start_displays_blue_persistent_reply_keyboard(self):
        self.run_async(self.bot.handle_update(private_message(23, 1, "/start")))
        chat, text, kwargs, _ = self.api.sent[-1]
        self.assertEqual(chat, 23)
        self.assertIn("خوش آمدید", text)
        markup = kwargs["reply_markup"]
        self.assertEqual(
            [[button["text"] for button in row] for row in markup["keyboard"]],
            [[START_BUTTON], [BUTTON_TEXT], [HELP_BUTTON]],
        )
        self.assertTrue(all(button["style"] == "primary" for row in markup["keyboard"] for button in row))
        self.assertTrue(markup["resize_keyboard"])
        self.assertFalse(markup["one_time_keyboard"])
        self.assertNotIn("remove_keyboard", markup)

    def test_start_and_help_buttons_work_and_keep_keyboard_attached(self):
        async def sequence():
            await self.bot.handle_update(private_message(23, 1, "/start"))
            await self.bot.handle_update(private_message(23, 2, HELP_BUTTON))
            await self.bot.handle_update(private_message(23, 3, START_BUTTON))
            await self.bot.handle_update(private_message(23, 4, BUTTON_TEXT))
        self.run_async(sequence())
        help_reply = self.api.sent[1]
        start_reply = self.api.sent[2]
        prompt = self.api.sent[3]
        self.assertIn("راهنمای استفاده", help_reply[1])
        self.assertIn("خوش آمدید", start_reply[1])
        for sent in (self.api.sent[0], help_reply, start_reply, prompt):
            self.assertEqual(sent[2]["reply_markup"], MAIN_MENU_MARKUP)
            self.assertFalse(sent[2]["reply_markup"]["one_time_keyboard"])

    def test_unconfigured_owner_start_shows_chat_id_setup_guide_and_menu(self):
        bot = AnonymousBot(self.api, self.storage, owner_id=None)
        with self.assertLogs("bot", level="INFO") as captured:
            self.run_async(bot.handle_update(private_message(23, 1, "/start", chat_id=456789)))
        message = self.api.sent[-1]
        self.assertIn("OWNER_ID", message[1])
        self.assertIn("chat_id", message[1])
        self.assertIn("هیچ کاربری را خودکار مالک نمی‌کند", message[1])
        self.assertIn("456789", captured.output[0])
        self.assertEqual(message[2]["reply_markup"], MAIN_MENU_MARKUP)

    def test_button_then_user_text_reaches_owner_with_private_metadata(self):
        self.run_async(self.bot.handle_update(private_message(23, 1, BUTTON_TEXT)))
        self.assertIn("پیامتان", self.api.sent[-1][1])
        self.run_async(self.bot.handle_update(private_message(23, 2, "سلام، پیام محرمانه")))
        owner_notes = [item for item in self.api.sent if item[0] == 9001]
        self.assertEqual(len(owner_notes), 1)
        self.assertIn("نام نمایشی: آزمایشی کاربر", owner_notes[0][1])
        self.assertIn("@sample", owner_notes[0][1])
        self.assertIn("شناسه کاربر: 23", owner_notes[0][1])
        self.assertNotIn("شناسه کاربر: 23", " ".join(item[1] for item in self.api.sent if item[0] == 23))
        self.assertEqual(self.api.copied[0][:3], (9001, 23, 2))
        self.assertIn("با موفقیت", [item[1] for item in self.api.sent if item[0] == 23][-1])
        user_responses = [item for item in self.api.sent if item[0] == 23]
        self.assertTrue(all(item[2].get("reply_markup") == MAIN_MENU_MARKUP for item in user_responses))

    def test_reply_routes_only_to_original_sender_and_survives_restart(self):
        async def sequence():
            await self.bot.handle_update(private_message(23, 1, BUTTON_TEXT))
            await self.bot.handle_update(private_message(23, 2, "original"))
            self.storage.close()
            self.storage = Storage(self.db_path)
            self.bot = AnonymousBot(self.api, self.storage, owner_id=9001)
            owner_copy_id = self.api.copied[0][3]
            await self.bot.handle_update(private_message(9001, 90, "جواب", reply_to=owner_copy_id))
        self.run_async(sequence())
        self.assertEqual(self.api.copied[-1][:3], (23, 9001, 90))
        self.assertNotEqual(self.api.copied[-1][0], 24)

    def test_owner_authorization_uses_numeric_id_not_username(self):
        self.bot.owner_username = "realowner"
        # A different numeric user cannot gain owner powers by copying the owner's username.
        self.run_async(
            self.bot.handle_update(
                private_message(24, 10, "spoof", reply_to=555, username="realowner")
            )
        )
        self.assertEqual(self.api.copied, [])
        # The configured numeric owner is recognized even if their username has changed.
        self.run_async(
            self.bot.handle_update(
                private_message(9001, 11, "reply", reply_to=555, username="renamed")
            )
        )
        self.assertIn("معتبر", self.api.sent[-1][1])

    def test_owner_reply_to_unmapped_message_fails_safely(self):
        self.run_async(self.bot.handle_update(private_message(9001, 10, "reply", reply_to=55555)))
        self.assertIn("معتبر", self.api.sent[-1][1])
        self.assertEqual(self.api.copied, [])

    def test_non_owner_cannot_route_reply(self):
        self.run_async(self.bot.handle_update(private_message(24, 10, "no", reply_to=100)))
        self.assertEqual(self.api.copied, [])
        self.assertFalse(any(item[0] == 23 for item in self.api.sent))

    def test_unconfigured_owner_disables_sending_without_crashing(self):
        bot = AnonymousBot(self.api, self.storage, owner_id=None)
        self.run_async(bot.handle_update(private_message(23, 1, "/start")))
        self.assertIn("OWNER_ID", self.api.sent[-1][1])
        self.run_async(bot.handle_update(private_message(23, 2, BUTTON_TEXT)))
        self.assertIn("غیرفعال", self.api.sent[-1][1])
        self.assertEqual(self.api.copied, [])
        self.assertEqual(self.api.sent[-1][2]["reply_markup"], MAIN_MENU_MARKUP)

    def test_copy_failure_notifies_and_stores_mapped_fallback(self):
        self.api.fail_copy = True
        async def sequence():
            await self.bot.handle_update(private_message(23, 1, BUTTON_TEXT))
            await self.bot.handle_update(private_message(23, 2, photo=[{"file_id": "x"}], caption="تصویر"))
            self.api.fail_copy = False
            owner_note = [item for item in self.api.sent if item[0] == 9001][-1][3]["message_id"]
            await self.bot.handle_update(private_message(9001, 90, "جواب", reply_to=owner_note))
        self.run_async(sequence())
        self.assertEqual(self.api.copied[-1][:3], (23, 9001, 90))
        self.assertTrue(any("قابل کپی نبود" in item[1] for item in self.api.sent if item[0] == 9001))

    def test_network_error_is_handled(self):
        async def sequence():
            await self.bot.handle_update(private_message(23, 1, BUTTON_TEXT))
            self.api.fail_send = True
            await self.bot.handle_update(private_message(23, 2, "content"))
        self.run_async(sequence())
        self.assertEqual(self.api.copied, [])
        self.assertTrue(any("ناموفق" in item[1] for item in self.api.sent if item[0] == 23))


if __name__ == "__main__":
    unittest.main()
