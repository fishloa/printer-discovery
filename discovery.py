"""
Broadcast Bonjour (mDNS/DNS-SD) and WS-Discovery announcements
for a Zebra ZD621 label printer at a fixed IP address.
"""

import json
import pathlib
import uuid
import socket
import struct
import threading
import signal
import sys
import time
import logging
from xml.etree import ElementTree as ET

from zeroconf import Zeroconf, ServiceInfo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("zebra-discovery")

# ---------------------------------------------------------------------------
# Configuration — loaded from config.json
# ---------------------------------------------------------------------------
_cfg_path = pathlib.Path(__file__).parent / "config.json"
with open(_cfg_path) as _f:
    CONFIG = json.load(_f)

PRINTERS = CONFIG["printers"]
HELLO_INTERVAL = CONFIG.get("hello_interval", 120)

# For WS-Discovery we advertise each printer separately
# Generate a stable UUID per printer based on its IP
def _device_uuid(ip: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, ip))

# ---------------------------------------------------------------------------
# Bonjour / mDNS
# ---------------------------------------------------------------------------

def register_mdns(printers: list[dict]) -> Zeroconf:
    """Register mDNS service records for every printer in the config."""
    zc = Zeroconf()

    for p in printers:
        name = p["name"]
        ip = p["ip"]
        location = p.get("location", "")
        addr = socket.inet_aton(ip)
        mdns_host = name.lower().replace(" ", "-") + ".local."

        common_props = {
            "txtvers": "1",
            "product": f"({name})",
            "ty": name,
            "note": location,
            "priority": "50",
            "qtotal": "1",
        }

        raw_info = ServiceInfo(
            "_pdl-datastream._tcp.local.",
            f"{name}._pdl-datastream._tcp.local.",
            addresses=[addr],
            port=9100,
            properties={
                **common_props,
                "pdl": "application/vnd.zebra-zpl,application/octet-stream",
            },
            server=mdns_host,
        )

        ipp_info = ServiceInfo(
            "_ipp._tcp.local.",
            f"{name}._ipp._tcp.local.",
            addresses=[addr],
            port=631,
            properties={
                **common_props,
                "pdl": "application/vnd.zebra-zpl,application/octet-stream",
                "rp": "ipp/print",
            },
            server=mdns_host,
        )

        lpd_info = ServiceInfo(
            "_printer._tcp.local.",
            f"{name}._printer._tcp.local.",
            addresses=[addr],
            port=515,
            properties=common_props,
            server=mdns_host,
        )

        for svc in (raw_info, ipp_info, lpd_info):
            zc.register_service(svc)
            log.info("mDNS registered: %s -> %s", svc.name, ip)

    return zc


# ---------------------------------------------------------------------------
# WS-Discovery
# ---------------------------------------------------------------------------

WSD_MCAST_ADDR = "239.255.255.250"
WSD_MCAST_PORT = 3702

WSD_NS = {
    "soap": "http://www.w3.org/2003/05/soap-envelope",
    "wsa": "http://schemas.xmlsoap.org/ws/2004/08/addressing",
    "wsd": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
    "wsdp": "http://schemas.xmlsoap.org/ws/2006/02/devprof",
    "wprt": "http://schemas.microsoft.com/windows/2006/08/wdp/print",
}

# The <wsd:Types> we advertise (Device + PrintDeviceType)
WSD_TYPES = "wsdp:Device wprt:PrintDeviceType"

_msg_number = 0
_instance_id = str(int(time.time()))


def _next_msg_number() -> str:
    global _msg_number
    _msg_number += 1
    return str(_msg_number)


def _build_hello(device_uuid: str, xaddrs: str) -> bytes:
    """Build a WS-Discovery Hello SOAP envelope."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope
  xmlns:soap="{WSD_NS['soap']}"
  xmlns:wsa="{WSD_NS['wsa']}"
  xmlns:wsd="{WSD_NS['wsd']}"
  xmlns:wsdp="{WSD_NS['wsdp']}"
  xmlns:wprt="{WSD_NS['wprt']}">
  <soap:Header>
    <wsa:Action>{WSD_NS['wsd']}/Hello</wsa:Action>
    <wsa:MessageID>urn:uuid:{uuid.uuid4()}</wsa:MessageID>
    <wsa:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</wsa:To>
    <wsd:AppSequence InstanceId="{_instance_id}" MessageNumber="{_next_msg_number()}"/>
  </soap:Header>
  <soap:Body>
    <wsd:Hello>
      <wsa:EndpointReference>
        <wsa:Address>urn:uuid:{device_uuid}</wsa:Address>
      </wsa:EndpointReference>
      <wsd:Types>{WSD_TYPES}</wsd:Types>
      <wsd:XAddrs>{xaddrs}</wsd:XAddrs>
      <wsd:MetadataVersion>1</wsd:MetadataVersion>
    </wsd:Hello>
  </soap:Body>
</soap:Envelope>""".encode("utf-8")


def _build_probe_match(relates_to: str, device_uuid: str, xaddrs: str) -> bytes:
    """Build a WS-Discovery ProbeMatches response."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope
  xmlns:soap="{WSD_NS['soap']}"
  xmlns:wsa="{WSD_NS['wsa']}"
  xmlns:wsd="{WSD_NS['wsd']}"
  xmlns:wsdp="{WSD_NS['wsdp']}"
  xmlns:wprt="{WSD_NS['wprt']}">
  <soap:Header>
    <wsa:Action>{WSD_NS['wsd']}/ProbeMatches</wsa:Action>
    <wsa:MessageID>urn:uuid:{uuid.uuid4()}</wsa:MessageID>
    <wsa:RelatesTo>{relates_to}</wsa:RelatesTo>
    <wsa:To>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</wsa:To>
    <wsd:AppSequence InstanceId="{_instance_id}" MessageNumber="{_next_msg_number()}"/>
  </soap:Header>
  <soap:Body>
    <wsd:ProbeMatches>
      <wsd:ProbeMatch>
        <wsa:EndpointReference>
          <wsa:Address>urn:uuid:{device_uuid}</wsa:Address>
        </wsa:EndpointReference>
        <wsd:Types>{WSD_TYPES}</wsd:Types>
        <wsd:XAddrs>{xaddrs}</wsd:XAddrs>
        <wsd:MetadataVersion>1</wsd:MetadataVersion>
      </wsd:ProbeMatch>
    </wsd:ProbeMatches>
  </soap:Body>
</soap:Envelope>""".encode("utf-8")


def _build_resolve_match(relates_to: str, device_uuid: str, xaddrs: str) -> bytes:
    """Build a WS-Discovery ResolveMatches response."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope
  xmlns:soap="{WSD_NS['soap']}"
  xmlns:wsa="{WSD_NS['wsa']}"
  xmlns:wsd="{WSD_NS['wsd']}"
  xmlns:wsdp="{WSD_NS['wsdp']}"
  xmlns:wprt="{WSD_NS['wprt']}">
  <soap:Header>
    <wsa:Action>{WSD_NS['wsd']}/ResolveMatches</wsa:Action>
    <wsa:MessageID>urn:uuid:{uuid.uuid4()}</wsa:MessageID>
    <wsa:RelatesTo>{relates_to}</wsa:RelatesTo>
    <wsa:To>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</wsa:To>
    <wsd:AppSequence InstanceId="{_instance_id}" MessageNumber="{_next_msg_number()}"/>
  </soap:Header>
  <soap:Body>
    <wsd:ResolveMatches>
      <wsd:ResolveMatch>
        <wsa:EndpointReference>
          <wsa:Address>urn:uuid:{device_uuid}</wsa:Address>
        </wsa:EndpointReference>
        <wsd:Types>{WSD_TYPES}</wsd:Types>
        <wsd:XAddrs>{xaddrs}</wsd:XAddrs>
        <wsd:MetadataVersion>1</wsd:MetadataVersion>
      </wsd:ResolveMatch>
    </wsd:ResolveMatches>
  </soap:Body>
</soap:Envelope>""".encode("utf-8")


def _make_wsd_socket() -> socket.socket:
    """Create a UDP socket joined to the WS-Discovery multicast group."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
    sock.bind(("", WSD_MCAST_PORT))
    # Join multicast group on all interfaces
    mreq = struct.pack("4sL", socket.inet_aton(WSD_MCAST_ADDR), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    # Set multicast TTL
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
    return sock


def _send_hello(sock: socket.socket, device_uuid: str, xaddrs: str) -> None:
    data = _build_hello(device_uuid, xaddrs)
    sock.sendto(data, (WSD_MCAST_ADDR, WSD_MCAST_PORT))
    log.info("WS-Discovery Hello sent (device %s)", device_uuid)


def _extract_message_id(data: bytes) -> str | None:
    """Extract wsa:MessageID from a SOAP envelope."""
    try:
        root = ET.fromstring(data)
        mid = root.find(f".//{{{WSD_NS['wsa']}}}MessageID")
        return mid.text if mid is not None else None
    except ET.ParseError:
        return None


def _extract_action(data: bytes) -> str | None:
    """Extract wsa:Action from a SOAP envelope."""
    try:
        root = ET.fromstring(data)
        act = root.find(f".//{{{WSD_NS['wsa']}}}Action")
        return act.text if act is not None else None
    except ET.ParseError:
        return None


def _resolve_target_uuid(data: bytes) -> str | None:
    """Return the target UUID from a Resolve message, or None."""
    try:
        root = ET.fromstring(data)
        addr = root.find(
            f".//{{{WSD_NS['wsd']}}}Resolve"
            f"/{{{WSD_NS['wsa']}}}EndpointReference"
            f"/{{{WSD_NS['wsa']}}}Address"
        )
        return addr.text if addr is not None else None
    except ET.ParseError:
        return None


def wsd_listener(sock: socket.socket, stop_event: threading.Event, printer_map: dict[str, str]) -> None:
    """Listen for WS-Discovery Probe/Resolve and respond.

    printer_map: {device_uuid: xaddrs} for all printers.
    """
    sock.settimeout(1.0)
    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break

        action = _extract_action(data)
        if action is None:
            continue

        if action.endswith("/Probe"):
            msg_id = _extract_message_id(data)
            if msg_id:
                for duuid, xaddrs in printer_map.items():
                    resp = _build_probe_match(msg_id, duuid, xaddrs)
                    sock.sendto(resp, addr)
                log.info("WS-Discovery ProbeMatch -> %s (%d printers)", addr, len(printer_map))

        elif action.endswith("/Resolve"):
            target = _resolve_target_uuid(data)
            if target:
                for duuid, xaddrs in printer_map.items():
                    if duuid in target:
                        msg_id = _extract_message_id(data)
                        if msg_id:
                            resp = _build_resolve_match(msg_id, duuid, xaddrs)
                            sock.sendto(resp, addr)
                            log.info("WS-Discovery ResolveMatch -> %s", addr)


def wsd_hello_loop(sock: socket.socket, stop_event: threading.Event, printer_map: dict[str, str]) -> None:
    """Periodically send WS-Discovery Hello messages."""
    while not stop_event.is_set():
        for duuid, xaddrs in printer_map.items():
            _send_hello(sock, duuid, xaddrs)
        stop_event.wait(HELLO_INTERVAL)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(block: bool = True) -> None:
    """Start all discovery services.

    If block=True (default, standalone mode), blocks until SIGTERM/SIGINT.
    If block=False (embedded mode), starts threads and returns immediately.
    """
    # Build printer map for WS-Discovery: {uuid: xaddrs}
    printer_map: dict[str, str] = {}
    for p in PRINTERS:
        duuid = _device_uuid(p["ip"])
        xaddrs = f"http://{p['ip']}/"
        printer_map[duuid] = xaddrs
        log.info("Printer %s at %s  UUID=%s", p["name"], p["ip"], duuid)

    stop = threading.Event()

    def shutdown(signum, frame):
        log.info("Shutting down...")
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Bonjour / mDNS
    zc = register_mdns(PRINTERS)

    # WS-Discovery
    wsd_sock = _make_wsd_socket()
    listener_thread = threading.Thread(
        target=wsd_listener, args=(wsd_sock, stop, printer_map), daemon=True
    )
    hello_thread = threading.Thread(
        target=wsd_hello_loop, args=(wsd_sock, stop, printer_map), daemon=True
    )
    listener_thread.start()
    hello_thread.start()

    log.info("All discovery services running.")

    if not block:
        return

    # Block until signalled
    stop.wait()

    # Cleanup
    log.info("Unregistering mDNS services...")
    zc.unregister_all_services()
    zc.close()
    wsd_sock.close()
    log.info("Done.")


if __name__ == "__main__":
    main()
