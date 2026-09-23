"""Small helpers shared by the test modules."""

from __future__ import annotations

from typing import Any

from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)


def respond_in_sequence(
    aioclient_mock: AiohttpClientMocker,
    method: str,
    url: str,
    responses: list[tuple[int, Any]],
) -> None:
    """Register ``url`` so that consecutive calls get consecutive responses.

    Each item is ``(status, json_body_or_text)``; the last one repeats.
    """
    remaining = list(responses)

    async def _side_effect(req_method, req_url, data):
        status, body = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(body, str):
            return AiohttpClientMockResponse(req_method, req_url, status, text=body)
        return AiohttpClientMockResponse(req_method, req_url, status, json=body)

    aioclient_mock.request(method, url, side_effect=_side_effect)


def calls_to(aioclient_mock: AiohttpClientMocker, method: str, path: str) -> list:
    """Return recorded calls for ``method`` whose URL path ends with ``path``."""
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[0].upper() == method.upper() and call[1].path.endswith(path)
    ]
