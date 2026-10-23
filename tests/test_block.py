"""Tests for the owner-facing blocklist («مسدود @username» / «مسدود <chat_id>»)."""
import asyncio
import tempfile
import unittest
from pathlib import Path

from bot import AnonymousBot, BLOCKED_USER_NOTICE, BUTTON_TEXT
from storage import Storage

from test_bot import FakeAPI, private_message

OWNER = 9001
USER = 23


class BlockTests(unittest.TestCase):
    username = "osinw"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.username = "osinw"
        self.db_path = Path(self.temp.name) / "test.sqlite3"
        self.storage = Storage(self.db_path)
        self.api = FakeAPI()
        self.bot = AnonymousBot(self.api, self.storage, owner_id=OWNER)

    def tearDown(self):
        self.storage.close()
        self.temp.cleanup()

    # -- helpers -------------------------------------------------------
    def send(self, update):
        return asyncio.run(self.bot.handle_update(update))

    def owner_says(self, text, message_id=500, **kwargs):
        return self.send(private_message(OWNER, message_id, text, **kwargs))

    def user_says(self, text, message_id=1, **kwargs):
        kwargs.setdefault("username", self.username)
        return self.send(private_message(USER, message_id, text, **kwargs))

    def owner_messages(self):
        return [item[1] for item in self.api.sent if item[0] == OWNER]

    def user_messages(self):
        return [item[1] for item in self.api.sent if item[0] == USER]

    def relayed_count(self):
        return len([item for item in self.api.sent if item[0] == OWNER and "پیام ناشناس جدید" in item[1]])

    def make_user_known(self, username=None):
        """Simulate a first contact so the bot knows this username <-> id pair."""
        self.username = username or self.username
        self.user_says("/start", message_id=1)
        self.api.sent.clear()

    # -- blocking by username -----------------------------------------
    def test_block_known_username_stops_relay(self):
        self.make_user_known()
        self.owner_says("مسدود @osinw", message_id=501)
        confirmation = self.owner_messages()[-1]
        self.assertIn("مسدود شد", confirmation)
        self.assertIn("osinw", confirmation)
        self.assertIn(str(USER), confirmation)

        self.user_says(BUTTON_TEXT, message_id=2)
        self.assertIn(BLOCKED_USER_NOTICE, self.user_messages()[-1])
        self.user_says("پیام محرمانه", message_id=3)
        self.assertEqual(self.relayed_count(), 0)
        self.assertEqual(self.api.copied, [])
        self.assertIn(BLOCKED_USER_NOTICE, self.user_messages()[-1])

    def test_block_username_without_at_sign_and_uppercase(self):
        self.make_user_known("OsInW")
        self.username = "osinw"
        self.owner_says("مسدود osinw", message_id=501)
        self.assertTrue(self.storage.is_blocked(USER))
        self.user_says(BUTTON_TEXT, message_id=2)
        self.assertIn(BLOCKED_USER_NOTICE, self.user_messages()[-1])

    def test_block_by_reply_to_forwarded_message(self):
        self.user_says(BUTTON_TEXT, message_id=1)
        self.user_says("سلام", message_id=2)
        # Metadata message the owner received for that relay.
        meta = [item for item in self.api.sent if item[0] == OWNER][-1][3]["message_id"]
        self.api.sent.clear()
        self.owner_says("مسدود", message_id=502, reply_to=meta)
        self.assertTrue(self.storage.is_blocked(USER))
        self.assertIn("مسدود شد", self.owner_messages()[-1])

    # -- blocking by numeric chat id -----------------------------------
    def test_block_by_chat_id_for_user_who_never_messaged(self):
        self.owner_says("مسدود 555444", message_id=501)
        self.assertTrue(self.storage.is_blocked(555444))
        self.assertIn("555444", self.owner_messages()[-1])

        # That user now shows up for the first time.
        self.send(private_message(555444, 1, BUTTON_TEXT, username="someone"))
        self.assertIn(BLOCKED_USER_NOTICE, self.api.sent[-1][1])
        self.send(private_message(555444, 2, "متن", username="someone"))
        self.assertEqual(self.relayed_count(), 0)

    def test_block_while_user_is_composing_cancels_pending_message(self):
        self.make_user_known()
        self.user_says(BUTTON_TEXT, message_id=2)
        self.owner_says("مسدود @osinw", message_id=501)
        self.user_says("متنی که نباید برسد", message_id=3)
        self.assertEqual(self.relayed_count(), 0)
        self.assertIn(BLOCKED_USER_NOTICE, self.user_messages()[-1])

    def test_blocked_user_does_not_enter_composing_state(self):
        self.make_user_known()
        self.owner_says("مسدود @osinw", message_id=501)
        self.user_says(BUTTON_TEXT, message_id=2)
        self.assertIsNone(self.storage.get_awaiting_chat(USER))

    # -- rename evasion / username-only rules --------------------------
    def test_rename_after_block_does_not_bypass_id_rule(self):
        self.make_user_known()
        self.owner_says("مسدود @osinw", message_id=501)
        self.username = "brand_new_name"
        self.user_says(BUTTON_TEXT, message_id=2)
        self.assertIn(BLOCKED_USER_NOTICE, self.user_messages()[-1])

    def test_username_only_rule_catches_user_on_first_message(self):
        self.owner_says("مسدود @ghost", message_id=501)
        self.assertIn("مسدود شد", self.owner_messages()[-1])
        self.send(private_message(777, 1, BUTTON_TEXT, username="ghost"))
        self.assertIn(BLOCKED_USER_NOTICE, self.api.sent[-1][1])
        self.send(private_message(777, 2, "پیام", username="ghost"))
        self.assertEqual(self.relayed_count(), 0)
        # The numeric id is learned so the rule is now permanent.
        self.assertTrue(self.storage.is_blocked(777))

    # -- unblocking ----------------------------------------------------
    def test_unblock_by_username_restores_sending(self):
        self.make_user_known()
        self.owner_says("مسدود @osinw", message_id=501)
        self.owner_says("رفع‌مسدود @osinw", message_id=502)
        self.assertFalse(self.storage.is_blocked(USER))
        self.user_says(BUTTON_TEXT, message_id=2)
        self.user_says("الان می‌رسد", message_id=3)
        self.assertEqual(self.relayed_count(), 1)

    def test_unblock_by_numeric_id(self):
        self.make_user_known()
        self.owner_says("مسدود @osinw", message_id=501)
        self.owner_says(f"رفع‌مسدود {USER}", message_id=502)
        self.assertFalse(self.storage.is_blocked(USER))

    def test_unblock_unknown_target_is_reported(self):
        self.owner_says("رفع‌مسدود @nobody", message_id=501)
        self.assertIn("نبود", self.owner_messages()[-1])

    # -- list / validation / security ----------------------------------
    def test_blocklist_shows_entries(self):
        self.make_user_known()
        self.owner_says("مسدود @osinw", message_id=501)
        self.owner_says("مسدود 4242", message_id=502)
        self.owner_says("لیست‌مسدود", message_id=503)
        listing = self.owner_messages()[-1]
        self.assertIn("osinw", listing)
        self.assertIn("4242", listing)

    def test_block_without_target_shows_usage(self):
        self.owner_says("مسدود", message_id=501)
        self.assertIn("مسدود @username", self.owner_messages()[-1])
        self.assertEqual(self.storage.list_blocked(), [])

    def test_block_with_invalid_target_is_rejected(self):
        self.owner_says("مسدود -100123", message_id=501)
        self.assertIn("معتبر نیست", self.owner_messages()[-1])
        self.assertEqual(self.storage.list_blocked(), [])

    def test_owner_cannot_block_self(self):
        self.owner_says(f"مسدود {OWNER}", message_id=501)
        self.assertIn("خودتان", self.owner_messages()[-1])
        self.assertFalse(self.storage.is_blocked(OWNER))

    def test_non_owner_cannot_block_anyone(self):
        self.make_user_known()
        self.send(private_message(24, 10, "مسدود @osinw", username="attacker"))
        self.assertFalse(self.storage.is_blocked(USER))
        self.assertEqual(self.storage.list_blocked(), [])

    def test_blocklist_persists_across_restart(self):
        self.make_user_known()
        self.owner_says("مسدود @osinw", message_id=501)
        self.storage.close()
        self.storage = Storage(self.db_path)
        self.bot = AnonymousBot(self.api, self.storage, owner_id=OWNER)
        self.assertTrue(self.storage.is_blocked(USER))
        self.user_says(BUTTON_TEXT, message_id=2)
        self.assertIn(BLOCKED_USER_NOTICE, self.user_messages()[-1])

    def test_blocked_user_can_still_read_help(self):
        self.make_user_known()
        self.owner_says("مسدود @osinw", message_id=501)
        self.user_says("/start", message_id=2)
        self.assertIn("خوش آمدید", self.user_messages()[-1])

    def test_owner_start_shows_moderation_commands(self):
        self.owner_says("/start", message_id=501)
        self.assertIn("مسدود", self.owner_messages()[-1])

    def test_command_glued_to_username_works(self):
        self.make_user_known()
        self.owner_says("مسدود@osinw", message_id=501)
        self.assertTrue(self.storage.is_blocked(USER))

    def test_block_by_id_from_reply_mapping(self):
        self.make_user_known()
        self.user_says(BUTTON_TEXT, message_id=1)
        self.user_says("متن", message_id=2)
        meta = [item for item in self.api.sent if item[0] == OWNER][-1][3]["message_id"]
        self.api.sent.clear()
        self.owner_says("مسدود", message_id=502, reply_to=meta)
        listing = self.storage.list_blocked()
        self.assertEqual(listing[0]["user_id"], USER)
        self.assertEqual(listing[0]["username"], "osinw")


if __name__ == "__main__":
    unittest.main()
