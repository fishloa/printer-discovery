# Zebra Discovery Service

Docker-containerised service that broadcasts **Bonjour (mDNS/DNS-SD)** and **WS-Discovery** announcements for a Zebra ZD621 label printer so network clients auto-discover it.

## Key files

| File | Purpose |
|---|---|
| `discovery.py` | Main service — mDNS registration (zeroconf) + WS-Discovery Hello/Probe/Resolve |
| `docker-compose.yml` | Stack definition (host network, env vars) |
| `Dockerfile` | Python 3.12-slim, installs zeroconf |

## Configuration (env vars)

- `PRINTER_IP` — printer's fixed IP (default `192.168.30.4`)
- `PRINTER_NAME` — display name (default `Zebra ZD621`)
- `HELLO_INTERVAL` — seconds between WS-Discovery Hello broadcasts (default `120`)

## Deployment

Deployed via Portainer on **zelkova** (endpoint ID 2). The container uses `network_mode: host` for multicast access.

## Protocols advertised

- **mDNS**: `_pdl-datastream._tcp` (9100), `_ipp._tcp` (631), `_printer._tcp` (515)
- **WS-Discovery**: `wsdp:Device wprt:PrintDeviceType` on multicast 239.255.255.250:3702
