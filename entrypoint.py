#!/usr/bin/env python3
"""Unified entrypoint: runs mDNS/WS-Discovery + label printing web UI."""

import threading
import signal
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("zebra")


def main():
    # Import after logging is configured
    from discovery import main as discovery_main
    from web import app
    from label_printer import get_printer_config

    printer = get_printer_config()
    log.info("Printer: %s at %s", printer["name"], printer["ip"])

    # Start discovery services (non-blocking — threads are daemonized)
    discovery_main(block=False)
    log.info("Discovery services started")

    # Run web server in the foreground
    log.info("Starting web UI on port 5555...")
    from gunicorn.app.base import BaseApplication

    class WebApp(BaseApplication):
        def __init__(self, application, options=None):
            self.application = application
            self.options = options or {}
            super().__init__()

        def load_config(self):
            for key, value in self.options.items():
                self.cfg.set(key.lower(), value)

        def load(self):
            return self.application

    WebApp(app, {
        "bind": "0.0.0.0:5555",
        "workers": 2,
        "timeout": 120,
        "accesslog": "-",
        "forwarded_allow_ips": "*",
        "access_log_format": '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s"',
    }).run()


if __name__ == "__main__":
    main()
