import json
import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mcp.types import CallToolRequestParams, TextContent
from starlette.testclient import TestClient

import server


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_blocks_all_tool_calls_even_with_cookie(self):
        with (
            patch.dict(os.environ, {"TP_LIVE_ENABLED": "false", "TP_AUTH_COOKIE": "unused"}),
            patch.object(server.upstream, "call_tool", new_callable=AsyncMock) as call,
        ):
            for name in ("tp_auth_status", "tp_get_profile", "tp_create_workout"):
                result = await server.call_tool(None, CallToolRequestParams(name=name))
                self.assertTrue(result.is_error)
                self.assertEqual(
                    json.loads(result.content[0].text)["error_code"],
                    "LIVE_ACCESS_DISABLED",
                )
            call.assert_not_awaited()

    async def test_offline_still_lists_tools(self):
        tools = await server.upstream.list_tools()
        self.assertIn("tp_get_profile", [tool.name for tool in tools])
        self.assertGreater(len(tools), 0)

    async def test_live_preserves_upstream_error_flag(self):
        with (
            patch.dict(os.environ, {"TP_LIVE_ENABLED": "true"}),
            patch.object(
                server.upstream,
                "call_tool",
                new_callable=AsyncMock,
                return_value=[TextContent(type="text", text='{"isError": true}')],
            ),
        ):
            result = await server.call_tool(None, CallToolRequestParams(name="tp_get_profile"))
            self.assertTrue(result.is_error)

    async def test_live_readonly_blocks_mutations_even_when_called_directly(self):
        with (
            patch.dict(os.environ, {"TP_LIVE_ENABLED": "true", "TP_READ_ONLY": "true"}),
            patch.object(server.upstream, "call_tool", new_callable=AsyncMock) as call,
        ):
            for name in ("tp_create_workout", "tp_delete_workout", "tp_update_ftp"):
                result = await server.call_tool(None, CallToolRequestParams(name=name))
                self.assertTrue(result.is_error)
                self.assertEqual(json.loads(result.content[0].text)["error_code"], "READ_ONLY")
            call.assert_not_awaited()

    async def test_readonly_discovery_contains_only_read_tools(self):
        with patch.dict(os.environ, {"TP_READ_ONLY": "true"}):
            tools = (await server.list_tools(None, None)).tools
            self.assertTrue(all(tool.annotations.read_only_hint for tool in tools))
            self.assertNotIn("tp_create_workout", [tool.name for tool in tools])
            self.assertIn("tp_get_workouts", [tool.name for tool in tools])

    async def test_cookie_file_and_empty_cookie(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cookie"
            with patch.dict(
                os.environ,
                {"TP_LIVE_ENABLED": "true", "TP_AUTH_COOKIE_FILE": str(path)},
            ):
                path.write_text("")
                with self.assertRaisesRegex(RuntimeError, "empty"):
                    async with server.lifespan(server.app):
                        pass
                path.write_text("test-only-cookie\n")
                with patch.object(server.manager, "run") as run:
                    from contextlib import asynccontextmanager

                    @asynccontextmanager
                    async def mock_run():
                        yield

                    run.return_value = mock_run()
                    async with server.lifespan(server.app):
                        self.assertEqual(os.environ["TP_AUTH_COOKIE"], "test-only-cookie")

    def test_invalid_mode_is_rejected(self):
        with patch.dict(os.environ, {"TP_LIVE_ENABLED": "yes"}):
            with self.assertRaises(ValueError):
                server.live_enabled()
        with patch.dict(os.environ, {"TP_READ_ONLY": "yes"}):
            with self.assertRaises(ValueError):
                server.read_only()


class HTTPTests(unittest.TestCase):
    def test_discovery_and_calls_over_http_without_live_access(self):
        headers = {
            "Accept": "application/json, text/event-stream",
            "Host": "trainingpeaks:3000",
        }
        with (
            patch.dict(os.environ, {"TP_LIVE_ENABLED": "false"}),
            patch.object(server.upstream, "call_tool", new_callable=AsyncMock) as call,
            patch.object(server.upstream, "validate_auth", new_callable=AsyncMock) as auth,
            TestClient(server.app, base_url="http://trainingpeaks:3000") as client,
        ):
            health = client.get("/healthz")
            self.assertEqual(health.status_code, 200)
            self.assertFalse(health.json()["liveEnabled"])

            def rpc(method, params, request_id):
                response = client.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
                )
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()["result"]

            result = rpc(
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "offline-http-test", "version": "1"},
                },
                1,
            )
            self.assertEqual(result["serverInfo"]["name"], "trainingpeaks-mcp")
            tools = rpc("tools/list", {}, 2)["tools"]
            self.assertIn("tp_get_profile", [tool["name"] for tool in tools])
            blocked = rpc("tools/call", {"name": "tp_get_profile", "arguments": {}}, 3)
            self.assertTrue(blocked["isError"])
            self.assertEqual(
                json.loads(blocked["content"][0]["text"])["error_code"],
                "LIVE_ACCESS_DISABLED",
            )
            call.assert_not_awaited()
            auth.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
