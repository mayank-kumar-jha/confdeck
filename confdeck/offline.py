"""Defense in depth: block public socket destinations in the Python runtime."""

import ipaddress
import sys

from confdeck.models import PRIVATE


def allowed_address(host: str) -> bool:
    if host in ("localhost", "::1", "239.255.255.250", "224.0.0.251"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.version == 4 and any(ip in net for net in PRIVATE)
    except ValueError:
        return False


def network_audit(event, args):
    if event in ("socket.connect", "socket.sendto"):
        address = args[1]
        if isinstance(address, tuple) and not allowed_address(address[0]):
            raise PermissionError("ConfDeck only connects to private local addresses.")
    if event == "socket.getaddrinfo" and args[0] and not allowed_address(args[0]):
        raise PermissionError("Internet DNS lookups are disabled in ConfDeck.")


def install_guard():
    """Install an irreversible runtime policy after startup/bundle installation."""
    sys.addaudithook(network_audit)
