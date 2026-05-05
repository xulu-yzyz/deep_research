"""Shared Starlette routes: POST /invoke (JSON body -> JSON), GET /health."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def mount_json_invoke_routes(handler: Callable[[dict[str, Any]], dict[str, Any]]) -> list[Route]:
    async def invoke(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "JSON body must be an object"}, status_code=400)
        try:
            out = await asyncio.to_thread(handler, body)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return JSONResponse(out)

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    return [
        Route("/invoke", endpoint=invoke, methods=["POST"]),
        Route("/health", endpoint=health, methods=["GET"]),
    ]


def post_json_sync(base_url: str, payload: dict[str, Any], *, timeout_s: float = 600.0) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/invoke"
    with httpx.Client(timeout=httpx.Timeout(timeout_s)) as client:
        r = client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("remote /invoke returned non-object JSON")
    return data


async def post_json_async(base_url: str, payload: dict[str, Any], *, timeout_s: float = 600.0) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/invoke"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("remote /invoke returned non-object JSON")
    return data