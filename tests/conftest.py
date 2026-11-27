"""Tests must remain usable with all public networking disabled."""

import socket

import httpx
import pytest
import pytest_asyncio

from confdeck.app import create_app
from confdeck.offline import allowed_address
from confdeck.runtime import Runtime


@pytest.fixture(autouse=True)
def no_internet(monkeypatch):
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    sendto = socket.socket.sendto
    resolve = socket.getaddrinfo

    def checked_connect(sock, address):
        if isinstance(address, tuple):
            assert allowed_address(address[0]), f"Forbidden outbound request to {address}"
        return connect(sock, address)

    def checked_connect_ex(sock, address):
        if isinstance(address, tuple):
            assert allowed_address(address[0]), f"Forbidden outbound request to {address}"
        return connect_ex(sock, address)

    def checked_sendto(sock, data, *args):
        assert allowed_address(args[-1][0]), "Forbidden outbound datagram"
        return sendto(sock, data, *args)

    def checked_resolve(host, *args, **kwargs):
        if host:
            assert allowed_address(host), f"Forbidden public DNS lookup: {host}"
        return resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", checked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", checked_connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", checked_sendto)
    monkeypatch.setattr(socket, "getaddrinfo", checked_resolve)


@pytest_asyncio.fixture
async def runtime(tmp_path):
    rt = Runtime(tmp_path / "private")
    yield rt
    await rt.close()


@pytest_asyncio.fixture
async def client(runtime):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(runtime), raise_app_exceptions=False),
        base_url="http://127.0.0.1",
        headers={"x-confdeck": "1"},
    ) as client:
        yield client
