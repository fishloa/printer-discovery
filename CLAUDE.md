# Zebra Discovery Service

Docker-containerised service that broadcasts **Bonjour (mDNS/DNS-SD)** and **WS-Discovery** announcements for Zebra label printers so network clients auto-discover them.

## Key files

| File | Purpose |
|---|---|
| `discovery.py` | Main service — mDNS registration (zeroconf) + WS-Discovery Hello/Probe/Resolve |
| `config.json` | Printer list and settings (name, IP, location, hello interval) |
| `docker-compose.yml` | Stack definition (host network) |
| `Dockerfile` | Python 3.12-slim, installs zeroconf |
| `.github/workflows/deploy.yml` | CI — build/push to GHCR, trigger Portainer redeploy |

## Configuration

Edit `config.json` to add/remove printers:
```json
{
  "printers": [
    { "name": "Zebra ZD621", "ip": "192.168.30.4", "location": "Alex Office Cupboard" }
  ],
  "hello_interval": 120
}
```

## Deployment

- **Portainer stack ID:** `179` (zebra-discovery)
- **Webhook UUID:** `8b8037ad-ddbe-43cb-a1c4-34ef4c83c694`
- **Image:** `ghcr.io/fishloa/zebra-discovery:latest`
- **CI:** Push to `main` → GitHub Actions builds image → pushes to GHCR → triggers Portainer webhook
- **GitHub secret:** `PORTAINER_WEBHOOK_URL` stores the full webhook URL

The container uses `network_mode: host` for multicast access on zelkova (endpoint ID 2).

## Protocols advertised

- **mDNS**: `_pdl-datastream._tcp` (9100), `_ipp._tcp` (631), `_printer._tcp` (515)
- **WS-Discovery**: `wsdp:Device wprt:PrintDeviceType` on multicast 239.255.255.250:3702
