from threading import Lock
from collections import defaultdict

PARSE_IP = "10.100.3.110"
PARSE_PORTS = ["9087", "9089", "9091", "9092", "9093"]
PARSE_ROUTE = "/file_parse"

_port_running = defaultdict(int)
_port_lock = Lock()

def acquire_port():
    with _port_lock:
        port = min(PARSE_PORTS, key=lambda p: _port_running[p])
        _port_running[port] += 1
        return port

def release_port(port):
    with _port_lock:
        _port_running[port] -= 1
        if _port_running[port] <= 0:
            del _port_running[port]

def get_parse_url():
    port = acquire_port()
    url = f"http://{PARSE_IP}:{port}{PARSE_ROUTE}"
    return url, port
