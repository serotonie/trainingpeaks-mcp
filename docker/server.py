import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from mcp.server import Server, ServerRequestContext
from mcp.server.streamable_http_manager import (
    StreamableHTTPASGIApp,
    StreamableHTTPSessionManager,
)
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from tp_mcp import server as upstream

logger = logging.getLogger("trainingpeaks-http")


def live_enabled() -> bool:
    value = os.environ.get("TP_LIVE_ENABLED", "false")
    if value not in ("true", "false"):
        raise ValueError("TP_LIVE_ENABLED must be true or false")
    return value == "true"


def read_only() -> bool:
    value = os.environ.get("TP_READ_ONLY", "true")
    if value not in ("true", "false"):
        raise ValueError("TP_READ_ONLY must be true or false")
    return value == "true"


def allowed_tool(tool) -> bool:
    return not read_only() or (
        tool.annotations is not None and tool.annotations.read_only_hint is True
    )


async def list_tools(ctx, params) -> ListToolsResult:
    return ListToolsResult(
        tools=[tool for tool in await upstream.list_tools() if allowed_tool(tool)]
    )


def tool_error(code: str, message: str) -> CallToolResult:
    return CallToolResult(
        is_error=True,
        content=[
            TextContent(
                type="text",
                text=json.dumps({"isError": True, "error_code": code, "message": message}),
            )
        ],
    )


async def call_tool(
    ctx: ServerRequestContext, params: CallToolRequestParams
) -> CallToolResult:
    if not live_enabled():
        logger.warning("Blocked tool %s: TrainingPeaks live access disabled", params.name)
        return tool_error(
            "LIVE_ACCESS_DISABLED",
            "TrainingPeaks live access is disabled. No request was sent.",
        )
    tool = next((tool for tool in upstream.TOOLS if tool.name == params.name), None)
    if tool is None:
        logger.warning("Blocked unknown tool %s", params.name)
        return tool_error("UNKNOWN_TOOL", "Unknown TrainingPeaks tool.")
    if not allowed_tool(tool):
        logger.warning("Blocked tool %s: read-only mode", params.name)
        return tool_error("READ_ONLY", "TrainingPeaks is in read-only mode. No request was sent.")
    contents = await upstream.call_tool(params.name, params.arguments)
    is_error = any(
        json.loads(content.text).get("isError", False) for content in contents
    )
    return CallToolResult(content=contents, is_error=is_error)


server = Server(
    "trainingpeaks-mcp",
    version=upstream.__version__,
    on_list_tools=list_tools,
    on_call_tool=call_tool,
    on_list_resources=upstream._on_list_resources,
    on_read_resource=upstream._on_read_resource,
)
server.extensions.update(upstream.server.extensions)

manager = StreamableHTTPSessionManager(
    server,
    json_response=True,
    stateless=True,
    security_settings=TransportSecuritySettings(
        allowed_hosts=["trainingpeaks:3000", "localhost:3000", "127.0.0.1:3000"],
        allowed_origins=["http://trainingpeaks:3000", "http://localhost:3000"],
    ),
)


@asynccontextmanager
async def lifespan(app: Starlette):
    enabled = live_enabled()
    read_only()
    cookie_file = os.environ.get("TP_AUTH_COOKIE_FILE")
    if enabled and cookie_file:
        cookie = Path(cookie_file).read_text().strip()
        if not cookie:
            raise RuntimeError("TP_AUTH_COOKIE_FILE is empty")
        os.environ["TP_AUTH_COOKIE"] = cookie
    if enabled and not os.environ.get("TP_AUTH_COOKIE"):
        raise RuntimeError("TP_AUTH_COOKIE is required when TP_LIVE_ENABLED=true")
    logger.info("Starting HTTP MCP; live access enabled: %s", enabled)
    async with manager.run():
        yield


async def health(request: Request) -> JSONResponse:
    usage = os.statvfs("/tmp")
    return JSONResponse(
        {
            "status": "ok",
            "transport": "streamableHttp",
            "liveEnabled": live_enabled(),
            "readOnly": read_only(),
            "tmpUsedBytes": (usage.f_blocks - usage.f_bfree) * usage.f_frsize,
            "tmpCapacityBytes": usage.f_blocks * usage.f_frsize,
        }
    )


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/healthz", health),
        Route("/mcp", StreamableHTTPASGIApp(manager), methods=["GET", "POST", "DELETE"]),
    ],
)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3000)
