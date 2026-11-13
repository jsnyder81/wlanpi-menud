"""HTTPS fake wlanpi-core for idle-CPU benchmarking (keep-alive, real TLS)."""
import json
import random
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

START = time.time()
COUNT = {"n": 0, "conns": 0}
LOCK = threading.Lock()
from collections import Counter

PATHS = Counter()
IFACES = {f"if{i}": [{"ifname": f"if{i}", "operstate": "UP", "address": f"02:00:00:00:00:{i:02x}",
                      "addr_info": [{"family": "inet", "local": f"192.0.2.{i}", "prefixlen": 24}]}] for i in range(12)}
IFACES["eth0"] = [{"ifname": "eth0", "operstate": "UP", "address": "dc:a6:32:00:00:01", "addr_info": []}]

OPENAPI: dict = {}

def routes(path):
    p = path.split("?")[0].removeprefix("/api/v1")
    up = int(time.time() - START) // 60
    if p == "/system/device/info":
        return {"model": "M4+", "name": "wlanpi-9be", "hostname": "wlanpi-9be", "software_version": "3.4.0",
                "mode": "classic", "wlan_management": "auto"}
    if p == "/system/device/model":
        return {"model": "M4+"}
    if p == "/system/device/stats":
        return {"ip": "192.0.2.10", "cpu": f"{random.randint(1, 9)}%", "ram": "400/4000MB", "disk": "3/32GB",
                "cpu_temp": f"{random.uniform(45, 52):.1f}C", "uptime": f"{up}m"}
    if p == "/system/battery":
        return {"present": False}
    if p == "/bluetooth/status":
        return {"name": "wlanpi", "alias": "wlanpi", "addr": "00:00:00:00:00:01", "power": "On", "paired_devices": []}
    if p == "/profiler/status":
        return {"running": False}
    if p == "/network/config/status":
        return {"root": {"wlan0": {"type": "managed", "addr": "02:00:00:00:00:00", "wiphy": "0"},
                         "wlanpi0": {"type": "monitor", "addr": "02:00:00:00:00:01", "wiphy": "0"}}}
    if p == "/network/interfaces":
        return IFACES
    if p.startswith("/network/interfaces/") and p.endswith("/wlan-link"):
        return {"connected": True, "ssid": "HomeNet", "bssid": "00:11:22:33:44:55",
                "freq_mhz": 5180, "signal_dbm": -52, "links": []}
    if p == "/utils/reachability":
        return {"ping_google": "OK", "browse_google": "OK", "dns_google": "OK"}
    if p == "/openapi.json":
        return OPENAPI
    return None

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a):
        pass
    def setup(self):
        super().setup()
        with LOCK:
            COUNT["conns"] += 1
    def do_GET(self):
        with LOCK:
            COUNT["n"] += 1
            PATHS[self.path.split("?")[0]] += 1
        body = routes(self.path)
        code = 200 if body is not None else 404
        data = json.dumps(body if body is not None else {"detail": "Not Found"}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

class S(ThreadingHTTPServer):
    daemon_threads = True

if __name__ == "__main__":
    port, cert, key, countfile, openapi = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
    OPENAPI.update(json.loads(Path(openapi).read_text()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    srv = S(("127.0.0.1", port), H)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    def dump():
        while True:
            time.sleep(1)
            with open(countfile, "w") as f:
                f.write(f"{COUNT['n']} {COUNT['conns']}")
            with open(countfile + ".paths", "w") as f:
                f.write(json.dumps(PATHS))
    threading.Thread(target=dump, daemon=True).start()
    srv.serve_forever()
