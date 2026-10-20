import asyncio
import json
import unittest

import httpx

from soroush_api import SoroushAPI, SoroushAPIError


class SoroushAPITests(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_uses_official_host_and_posts_poll_parameters_as_json(self):
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"ok": True, "result": []})

        async def run():
            api = SoroushAPI("TEST_TOKEN")
            await api._client.aclose()
            api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                result = await api.get_updates(8, timeout=25)
                self.assertEqual(result, [])
            finally:
                await api.close()

        self.run_async(run())
        self.assertTrue(captured["url"].startswith("https://api.splus.ir/botTEST_TOKEN/getUpdates"))
        self.assertEqual(captured["body"]["offset"], 8)
        self.assertEqual(captured["body"]["timeout"], 25)
        self.assertEqual(captured["body"]["allowed_updates"], ["message"])

    def test_non_ok_response_becomes_api_error(self):
        def handler(request):
            return httpx.Response(200, json={"ok": False, "description": "bad request"})

        async def run():
            api = SoroushAPI("TEST_TOKEN")
            await api._client.aclose()
            api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                with self.assertRaises(SoroushAPIError) as error:
                    await api.call("sendMessage", chat_id=1, text="hi")
                self.assertIn("bad request", str(error.exception))
            finally:
                await api.close()

        self.run_async(run())

    def test_network_exception_does_not_expose_tokenized_url(self):
        def handler(request):
            raise httpx.ConnectError("offline", request=request)

        async def run():
            secret = "TEST_TOKEN_SHOULD_NOT_LEAK"
            api = SoroushAPI(secret)
            await api._client.aclose()
            api._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                with self.assertRaises(SoroushAPIError) as error:
                    await api.call("getMe")
                self.assertNotIn(secret, str(error.exception))
            finally:
                await api.close()

        self.run_async(run())


if __name__ == "__main__":
    unittest.main()
