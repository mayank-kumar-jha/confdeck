"""Bounded, read-only local-network discovery and relay-only camera probing."""

import asyncio
import ipaddress
import socket
import uuid
from urllib.parse import urlsplit
from xml.etree import ElementTree

import psutil

from confdeck.models import Camera, private_subnet

PATHS = [
    "/rtpstream/config1",
    "/rtpstream/config2",
    "/rtpstream/config3",
    "/Streaming/Channels/101",
    "/cam/realmonitor?channel=1&subtype=0",
    "/live/0",
    "/stream1",
    "/live",
]


def networks() -> list[dict]:
    """Infer local private /24s from interfaces without contacting a DNS server."""
    found = []
    for name, addresses in psutil.net_if_addrs().items():
        for address in addresses:
            if address.family != socket.AF_INET:
                continue
            try:
                subnet = private_subnet(address.address + "/24")
                found.append({"interface": name, "ip": address.address, "subnet": str(subnet)})
            except ValueError:
                pass
    return found


async def tcp_open(ip: str, port: int) -> bool:
    writer = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), 0.35)
        return True
    except (OSError, asyncio.TimeoutError):
        return False
    finally:
        if writer:
            writer.close()
            await writer.wait_closed()


async def onvif(subnet) -> set[str]:
    """WS-Discovery only; no SOAP writes or PTZ changes."""
    found = set()

    class Receiver(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            if len(data) > 32768 or ipaddress.ip_address(addr[0]) not in subnet:
                return
            try:
                tree = ElementTree.fromstring(data)
                for node in tree.iter():
                    if node.tag.endswith("XAddrs") and node.text:
                        for value in node.text.split():
                            host = urlsplit(value).hostname
                            if host and ipaddress.ip_address(host) in subnet:
                                found.add(host)
            except (ElementTree.ParseError, ValueError):
                pass

    transport = None
    try:
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            Receiver, local_addr=("0.0.0.0", 0)
        )
        probe = f'<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" xmlns:n="http://www.onvif.org/ver10/network/wsdl"><e:Header><w:MessageID>uuid:{uuid.uuid4()}</w:MessageID><w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To><w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header><e:Body><d:Probe><d:Types>n:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>'
        transport.sendto(probe.encode(), ("239.255.255.250", 3702))
        await asyncio.sleep(1.5)
    except OSError:
        pass
    finally:
        if transport:
            transport.close()
    return found


class Scanner:
    def __init__(self, media, settings):
        self.media = media
        self.settings = settings
        self.state = "Idle"
        self.progress = 0
        self.results = []
        self.credentials = {}
        self.task = None

    async def start(self, subnet: str, username: str, password: str):
        network = private_subnet(subnet)
        if self.task and not self.task.done():
            return
        self.credentials = {"username": username, "password": password}
        self.results = []
        self.progress = 0
        self.state = "Scanning"
        self.task = asyncio.create_task(self.run(network))

    async def run(self, network):
        semaphore = asyncio.Semaphore(24)
        candidates = {}
        known = {c.ip for c in self.settings.cameras}
        own = {n["ip"] for n in networks()}

        async def check(ip):
            async with semaphore:
                if ip not in own and ip not in known:
                    ports = await asyncio.gather(*(tcp_open(ip, p) for p in (80, 554, 52381)))
                    if any(ports):
                        candidates[ip] = [p for p, opened in zip((80, 554, 52381), ports) if opened]
                self.progress += 1

        try:
            ws_task = asyncio.create_task(onvif(network))
            await asyncio.gather(*(check(str(ip)) for ip in network.hosts()))
            for ip in await ws_task:
                if ip not in known:
                    candidates.setdefault(ip, [])
            self.state = "Checking streams"
            semaphore = asyncio.Semaphore(3)

            async def verify(ip, ports):
                async with semaphore:
                    identity = "scan-" + ip.replace(".", "-")
                    camera = Camera(id=identity, ip=ip, name=f"Camera {ip}")
                    result = {"state": "Needs stream path", "issue": None}
                    try:
                        for path in PATHS:
                            camera.path = path
                            result = await self.media.probe(camera, self.credentials)
                            if result.get("codec") or (result.get("issue") or {}).get("code") in (
                                "password",
                                "limit",
                                "media",
                                "unreachable",
                            ):
                                break
                        self.results.append(
                            {**camera.model_dump(), "ports": ports, "status": result}
                        )
                    finally:
                        await self.media.remove(identity)

            await asyncio.gather(
                *(verify(ip, ports) for ip, ports in list(candidates.items())[:32])
            )
            self.state = "Finished"
        except asyncio.CancelledError:
            self.state = "Cancelled"
            raise
        except Exception:
            self.state = "Could not finish scan. Check the network and retry."

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.credentials = {}
