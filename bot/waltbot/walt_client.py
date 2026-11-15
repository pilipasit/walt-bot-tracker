"""Connection to Walt's MCP server for the live trader.

Sign-in is the owner's: the first connection opens Walt's authorization page in the browser
and waits for the owner to approve it there and in Telegram. The resulting token is stored
in the owner's home folder (never in the repository) and refreshed automatically.

Walt's access tokens last 15 minutes; `connect` refreshes them from the stored refresh token.
"""
from __future__ import annotations

import asyncio
import json
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

URL = "https://walletbot.me/bot-relay/public-api/mcp"
TOKEN_URL = "https://walletbot.me/bot-relay/public-api/oauth/v1/token"
STORE = Path.home() / ".waltbot" / "walt_oauth.json"
PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}/callback"


class WaltError(RuntimeError):
    """A Walt tool call returned an error. The text carries Walt's error code."""


class _FileStorage:
    def _read(self) -> dict:
        return json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else {}

    def _write(self, key: str, value: str) -> None:
        data = self._read()
        data[key] = json.loads(value)
        if key == "tokens":
            data["tokens_saved_at"] = time.time()
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(data), encoding="utf-8")

    def expires_at(self, expires_in: float) -> float:
        """Absolute expiry of the stored access token (falls back to the file's age)."""
        saved = self._read().get("tokens_saved_at") or STORE.stat().st_mtime
        return saved + expires_in

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken

        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens) -> None:
        self._write("tokens", tokens.model_dump_json())

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull

        raw = self._read().get("client")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info) -> None:
        self._write("client", client_info.model_dump_json())


async def _open_browser(url: str) -> None:
    print("\nApprove the connection in your browser, then confirm in Walt (Telegram).")
    print(f"If the browser does not open, paste this address into it:\n{url}\n", flush=True)
    webbrowser.open(url)


async def _wait_for_callback():
    """Serve one request on 127.0.0.1 and return the authorization code from its query string."""
    from mcp.client.auth import AuthorizationCodeResult

    done: asyncio.Future = asyncio.get_running_loop().create_future()

    async def handle(reader, writer):
        line = (await reader.readline()).decode("latin-1")
        query = parse_qs(urlparse(line.split(" ")[1]).query) if " " in line else {}
        ok = "code" in query
        body = "Connected. You can close this tab." if ok else "No authorization code received."
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n\r\n{body}".encode())
        await writer.drain()
        writer.close()
        if ok and not done.done():
            done.set_result(query)

    server = await asyncio.start_server(handle, "127.0.0.1", PORT)
    try:
        query = await asyncio.wait_for(done, timeout=600)
    finally:
        server.close()
    return AuthorizationCodeResult(code=query["code"][0], state=query.get("state", [None])[0],
                                   iss=query.get("iss", [None])[0])


class WaltClient:
    def __init__(self, session):
        self._session = session

    async def call(self, name: str, args: dict | None = None) -> dict:
        result = await self._session.call_tool(name, args or {}, read_timeout_seconds=60)
        text = "".join(getattr(c, "text", "") for c in result.content)
        if result.is_error:
            raise WaltError(text)
        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError:
            return {"text": text}


@asynccontextmanager
async def connect(interactive: bool = False):
    """Open one authenticated session. With interactive=False a missing sign-in is an error
    instead of a browser window, so an unattended loop never blocks on a login page."""
    from mcp import ClientSession
    from mcp.client.auth import OAuthClientProvider
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
    from mcp.shared.auth import OAuthClientMetadata

    if not interactive and not STORE.exists():
        raise WaltError("Not signed in. Run: python -m waltbot live-login")

    async def no_browser(url: str) -> None:
        raise WaltError("Walt sign-in has expired. Run: python -m waltbot live-login")

    storage = _FileStorage()

    class Provider(OAuthClientProvider):
        """The SDK does not remember when a stored token expires, so after a restart it would
        send a dead token and then demand a full browser sign-in instead of refreshing. Walt's
        tokens last 15 minutes, so tell it the real expiry and let it refresh."""

        async def _initialize(self) -> None:
            await super()._initialize()
            tokens, client = self.context.current_tokens, self.context.client_info
            if not (tokens and tokens.expires_in):
                return
            expires = storage.expires_at(tokens.expires_in) - 60
            if time.time() > expires and tokens.refresh_token and client:
                # Refresh here, against Walt's real token endpoint. Left to itself the SDK posts
                # the refresh to a default URL (https://walletbot.me/token) and gets a 405.
                import httpx2
                from mcp.shared.auth import OAuthToken

                async with httpx2.AsyncClient(timeout=30) as http:
                    r = await http.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": tokens.refresh_token,
                                                         "client_id": client.client_id, "resource": URL})
                if r.status_code == 200:
                    fresh = OAuthToken.model_validate(r.json())
                    if not fresh.refresh_token:
                        fresh.refresh_token = tokens.refresh_token
                    await storage.set_tokens(fresh)
                    self.context.current_tokens = fresh
                    expires = time.time() + (fresh.expires_in or 900) - 60
                else:
                    print(f"Walt token refresh failed: HTTP {r.status_code}", flush=True)
            self.context.token_expiry_time = expires

    provider = Provider(
        server_url=URL,
        client_metadata=OAuthClientMetadata(
            client_name="waltbot (owner-run experiment bot)",
            redirect_uris=[REDIRECT],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
            scope="perps.trade",
        ),
        storage=storage,
        redirect_handler=_open_browser if interactive else no_browser,
        callback_handler=_wait_for_callback,
    )
    async with create_mcp_http_client(auth=provider) as http:
        async with streamable_http_client(URL, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield WaltClient(session)
