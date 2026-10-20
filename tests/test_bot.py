import asyncio
import tempfile
import unittest
from pathlib import Path

from bot import AnonymousBot, BUTTON_TEXT, OWNER_DISABLED
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


def private_message(user_id, message_id, text=None, *, chat_id=None, reply_to=None, **content):
    chat_id = chat_id if chat_id is not None else user_id
    message = {
        "message_id": message_id,
        "from": {"id": user_id, "first_name": "آزمایشی", "last_name": "کاربر", "username": "sample"},
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

    def test_start_displays_persistent_single_button(self):
        self.run_async(self.bot.handle_update(private_message(23, 1, "/start")))
        chat, text, kwargs, _ = self.api.sent[-1]
        self.assertEqual(chat, 23)
        self.assertIn("خوش آمدید", text)
        markup = kwargs["reply_markup"]
        self.assertEqual(markup["keyboard"], [[{"text": BUTTON_TEXT}]])
        self.assertFalse(markup["one_time_keyboard"])

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

    def test_reply_routes_only_to_original_sender_and_survives_restart(self):
        async def sequence():
            await self.bot.handle_update(private_message(23, 1, BUTTON_TEXT))
            await self.bot.handle_update(private_message(23, 2, "original"))
            # Reopen SQLite as if the process restarted.
            self.storage.close()
            self.storage = Storage(self.db_path)
            self.bot = AnonymousBot(self.api, self.storage, owner_id=9001)
            owner_copy_id = self.api.copied[0][3]
            await self.bot.handle_update(private_message(9001, 90, "جواب", reply_to=owner_copy_id))
        self.run_async(sequence())
        self.assertEqual(self.api.copied[-1][:3], (23, 9001, 90))
        self.assertNotEqual(self.api.copied[-1][0], 24)

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
        self.assertIn("غیرفعال", self.api.sent[-1][1])
        self.run_async(bot.handle_update(private_message(23, 2, BUTTON_TEXT)))
        self.assertEqual(self.api.sent[-1][1], OWNER_DISABLED)
        self.assertEqual(self.api.copied, [])

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
            self.api.fail_send = True  # Fail sending the owner's metadata once.
            await self.bot.handle_update(private_message(23, 2, "content"))
        self.run_async(sequence())
        self.assertEqual(self.api.copied, [])
        self.assertTrue(any("ناموفق" in item[1] for item in self.api.sent if item[0] == 23))


if __name__ == "__main__":
    unittest.main()
