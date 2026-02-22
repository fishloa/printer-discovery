"""
Broadcast Bonjour (mDNS/DNS-SD) and WS-Discovery announcements
for a Zebra ZD621 label printer at a fixed IP address.
"""

import os
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
# Configuration
# ---------------------------------------------------------------------------
PRINTER_IP = os.environ.get("PRINTER_IP", "192.168.30.4")
PRINTER_NAME = os.environ.get("PRINTER_NAME", "Zebra ZD621")
HELLO_INTERVAL = int(os.environ.get("HELLO_INTERVAL", "120"))

# Deterministic UUID so the printer keeps the same identity across restarts
DEVICE_UUID = uuid.uuid5(uuid.NAMESPACE_DNS, f"zebra-printer-{PRINTER_IP}")

# mDNS hostname for the printer (no spaces, lowercase)
MDNS_HOST = PRINTER_NAME.lower().replace(" ", "-") + ".local."

# ---------------------------------------------------------------------------
# Bonjour / mDNS
# ---------------------------------------------------------------------------

def register_mdns() -> Zeroconf:
    """Register mDNS service records advertising the printer."""
    zc = Zeroconf()
    addr = socket.inet_aton(PRINTER_IP)

    common_props = {
        "txtvers": "1",
        "product": f"({PRINTER_NAME})",
        "ty": PRINTER_NAME,
        "note": "",
        "priority": "50",
        "qtotal": "1",
    }

    # Raw TCP / ZPL printing on port 9100
    raw_info = ServiceInfo(
        "_pdl-datastream._tcp.local.",
        f"{PRINTER_NAME}._pdl-datastream._tcp.local.",
        addresses=[addr],
        port=9100,
        properties={
            **common_props,
            "pdl": "application/vnd.zebra-zpl,application/octet-stream",
        },
        server=MDNS_HOST,
    )

    # IPP on port 631
    ipp_info = ServiceInfo(
        "_ipp._tcp.local.",
        f"{PRINTER_NAME}._ipp._tcp.local.",
        addresses=[addr],
        port=631,
        properties={
            **common_props,
            "pdl": "application/vnd.zebra-zpl,application/octet-stream",
            "rp": "ipp/print",
        },
        server=MDNS_HOST,
    )

    # LPD on port 515
    lpd_info = ServiceInfo(
        "_printer._tcp.local.",
        f"{PRINTER_NAME}._printer._tcp.local.",
        addresses=[addr],
        port=515,
        properties=common_props,
        server=MDNS_HOST,
    )

    for svc in (raw_info, ipp_info, lpd_info):
        zc.register_service(svc)
        log.info("mDNS registered: %s", svc.name)

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

# XAddrs — where clients will connect for metadata exchange
XADDRS = f"http://{PRINTER_IP}/"

# The <wsd:Types> we advertise (Device + PrintDeviceType)
WSD_TYPES = "wsdp:Device wprt:PrintDeviceType"

_msg_number = 0
_instance_id = str(int(time.time()))


def _next_msg_number() -> str:
    global _msg_number
    _msg_number += 1
    return str(_msg_number)


def _build_hello() -> bytes:
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
        <wsa:Address>urn:uuid:{DEVICE_UUID}</wsa:Address>
      </wsa:EndpointReference>
      <wsd:Types>{WSD_TYPES}</wsd:Types>
      <wsd:XAddrs>{XADDRS}</wsd:XAddrs>
      <wsd:MetadataVersion>1</wsd:MetadataVersion>
    </wsd:Hello>
  </soap:Body>
</soap:Envelope>""".encode("utf-8")


def _build_probe_match(relates_to: str) -> bytes:
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
          <wsa:Address>urn:uuid:{DEVICE_UUID}</wsa:Address>
        </wsa:EndpointReference>
        <wsd:Types>{WSD_TYPES}</wsd:Types>
        <wsd:XAddrs>{XADDRS}</wsd:XAddrs>
        <wsd:MetadataVersion>1</wsd:MetadataVersion>
      </wsd:ProbeMatch>
    </wsd:ProbeMatches>
  </soap:Body>
</soap:Envelope>""".encode("utf-8")


def _build_resolve_match(relates_to: str) -> bytes:
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
          <wsa:Address>urn:uuid:{DEVICE_UUID}</wsa:Address>
        </wsa:EndpointReference>
        <wsd:Types>{WSD_TYPES}</wsd:Types>
        <wsd:XAddrs>{XADDRS}</wsd:XAddrs>
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


def _send_hello(sock: socket.socket) -> None:
    data = _build_hello()
    sock.sendto(data, (WSD_MCAST_ADDR, WSD_MCAST_PORT))
    log.info("WS-Discovery Hello sent (device %s)", DEVICE_UUID)


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


def _is_resolve_for_us(data: bytes) -> bool:
    """Check if a Resolve message targets our device UUID."""
    try:
        root = ET.fromstring(data)
        addr = root.find(
            f".//{{{WSD_NS['wsd']}}}Resolve"
            f"/{{{WSD_NS['wsa']}}}EndpointReference"
            f"/{{{WSD_NS['wsa']}}}Address"
        )
        return addr is not None and str(DEVICE_UUID) in (addr.text or "")
    except ET.ParseError:
        return False


def wsd_listener(sock: socket.socket, stop_event: threading.Event) -> None:
    """Listen for WS-Discovery Probe/Resolve and respond."""
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
                resp = _build_probe_match(msg_id)
                # Unicast back to the sender
                sock.sendto(resp, addr)
                log.info("WS-Discovery ProbeMatch -> %s", addr)

        elif action.endswith("/Resolve"):
            if _is_resolve_for_us(data):
                msg_id = _extract_message_id(data)
                if msg_id:
                    resp = _build_resolve_match(msg_id)
                    sock.sendto(resp, addr)
                    log.info("WS-Discovery ResolveMatch -> %s", addr)


def wsd_hello_loop(sock: socket.socket, stop_event: threading.Event) -> None:
    """Periodically send WS-Discovery Hello messages."""
    while not stop_event.is_set():
        _send_hello(sock)
        stop_event.wait(HELLO_INTERVAL)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("Starting discovery for %s at %s", PRINTER_NAME, PRINTER_IP)
    log.info("Device UUID: %s", DEVICE_UUID)

    stop = threading.Event()

    def shutdown(signum, frame):
        log.info("Shutting down...")
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Bonjour / mDNS
    zc = register_mdns()

    # WS-Discovery
    wsd_sock = _make_wsd_socket()
    listener_thread = threading.Thread(
        target=wsd_listener, args=(wsd_sock, stop), daemon=True
    )
    hello_thread = threading.Thread(
        target=wsd_hello_loop, args=(wsd_sock, stop), daemon=True
    )
    listener_thread.start()
    hello_thread.start()

    log.info("All discovery services running. Press Ctrl+C to stop.")

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
