# """A2A JSON-RPC client: one JSON payload in, JSON dict out (from artifact text)."""
# from __future__ import annotations

# import json
# from typing import Any

# import httpx
# from a2a.client import A2ACardResolver, ClientConfig, create_client
# from a2a.helpers import get_stream_response_text, new_text_message
# from a2a.types.a2a_pb2 import Role, SendMessageRequest


# async def send_json_message(base_url: str, payload: dict[str, Any], *, timeout_s: float = 600.0) -> dict[str, Any]:
#     text = json.dumps(payload, ensure_ascii=False)
#     base = base_url.rstrip("/")
#     async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as httpx_client:
#         resolver = A2ACardResolver(httpx_client=httpx_client, base_url=base)
#         public_card = await resolver.get_agent_card()
#         cfg = ClientConfig(streaming=False)
#         client = await create_client(agent=public_card, client_config=cfg)
#         message = new_text_message(text, role=Role.ROLE_USER)
#         request = SendMessageRequest(message=message)
#         parts: list[str] = []
#         try:
#             async for resp in client.send_message(request):
#                 chunk = get_stream_response_text(resp).strip()
#                 if chunk:
#                     parts.append(chunk)
#         finally:
#             await client.close()

#     combined = "\n".join(parts).strip()
#     if not combined:
#         raise RuntimeError("A2A 响应为空")
#     return json.loads(combined)

"""Plain HTTP JSON client (POST /invoke). Kept module name for imports; no a2a-sdk."""
from __future__ import annotations

from typing import Any

from app.a2a.json_http_routes import post_json_async, post_json_sync


async def send_json_message(base_url: str, payload: dict[str, Any], *, timeout_s: float = 600.0) -> dict[str, Any]:
    return await post_json_async(base_url.rstrip("/"), payload, timeout_s=timeout_s)


def send_json_message_sync(base_url: str, payload: dict[str, Any], *, timeout_s: float = 600.0) -> dict[str, Any]:
    return post_json_sync(base_url.rstrip("/"), payload, timeout_s=timeout_s)