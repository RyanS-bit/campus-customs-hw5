"""Small synchronous wrappers around FastMCP's in-memory client (no network, no model)."""

import asyncio

from fastmcp import Client


def call(server, tool, args=None, meta=None):
    async def go():
        async with Client(server) as c:
            return await c.call_tool(tool, args or {}, raise_on_error=False, meta=meta)
    return asyncio.run(go())


def tool_names(server) -> set[str]:
    async def go():
        async with Client(server) as c:
            return {t.name for t in await c.list_tools()}
    return asyncio.run(go())


def tool_schemas(server) -> dict:
    async def go():
        async with Client(server) as c:
            return {t.name: t.input_schema for t in await c.list_tools()}
    return asyncio.run(go())


def text(result) -> str:
    return result.content[0].text if result.content else ""
