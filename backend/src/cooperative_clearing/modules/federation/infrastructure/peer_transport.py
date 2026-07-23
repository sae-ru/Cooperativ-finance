"""Bounded HTTPS transport for authenticated peer protocol messages."""

import asyncio
import json
import ssl
from http.client import HTTPMessage
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from cooperative_clearing.modules.federation.domain.types import federation_error
from cooperative_clearing.shared.core.config import Environment, Settings

PEER_MESSAGE_PATH = "/api/v1/federation/peer/messages"


class PeerTransport(Protocol):
    async def post(self, endpoint: str, body: dict[str, object]) -> dict[str, object]: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        return None


class UrllibPeerTransport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def post(self, endpoint: str, body: dict[str, object]) -> dict[str, object]:
        url = peer_message_url(endpoint, self.settings.environment)
        return await asyncio.to_thread(self._post_sync, url, body)

    def _post_sync(self, url: str, body: dict[str, object]) -> dict[str, object]:
        encoded = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        request = Request(
            url,
            data=encoded,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": f"cooperative-clearing/{self.settings.release}",
            },
        )
        context = ssl.create_default_context(
            cafile=str(self.settings.peer_tls_ca_file)
            if self.settings.peer_tls_ca_file is not None
            else None
        )
        if self.settings.peer_tls_client_cert_file is not None:
            context.load_cert_chain(
                certfile=str(self.settings.peer_tls_client_cert_file),
                keyfile=str(self.settings.peer_tls_client_key_file),
            )
        opener = build_opener(_NoRedirect(), HTTPSHandler(context=context))
        try:
            with opener.open(
                request, timeout=self.settings.peer_connect_timeout_seconds
            ) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > self.settings.peer_response_max_bytes:
                    raise federation_error("PEER_RESPONSE_TOO_LARGE", 502)
                raw = response.read(self.settings.peer_response_max_bytes + 1)
        except HTTPError as exc:
            raise federation_error("PEER_HTTP_ERROR", 502) from exc
        except (URLError, TimeoutError, OSError, ssl.SSLError) as exc:
            raise federation_error("PEER_UNAVAILABLE", 503) from exc
        if len(raw) > self.settings.peer_response_max_bytes:
            raise federation_error("PEER_RESPONSE_TOO_LARGE", 502)
        try:
            parsed = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise federation_error("PEER_RESPONSE_INVALID", 502) from exc
        if not isinstance(parsed, dict):
            raise federation_error("PEER_RESPONSE_INVALID", 502)
        return parsed


def peer_message_url(endpoint: str, environment: Environment) -> str:
    candidate = endpoint.strip().rstrip("/")
    parsed = urlsplit(candidate)
    allowed_schemes = {"https"}
    if environment in {Environment.DEV, Environment.TEST}:
        allowed_schemes.add("http")
    if (
        parsed.scheme.lower() not in allowed_schemes
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise federation_error("PEER_ENDPOINT_INVALID", 422)
    return f"{candidate}{PEER_MESSAGE_PATH}"
