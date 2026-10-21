#!/usr/bin/env python3
"""Serve public TLS test fixtures on loopback. Never installs certificates or runs Wine."""

import argparse
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import ssl
import subprocess
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "tests/fixtures/tls"
CASES = ("valid", "untrusted", "expired", "hostname", "expired-untrusted",
         "hostname-untrusted", "expired-hostname", "expired-hostname-untrusted")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.send_header("X-Test-Connection", str(self.connection.test_id))
        self.end_headers()

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    daemon_threads = True
    connections = 0

    def get_request(self):
        connection, address = super().get_request()
        # Bound both abandoned TLS handshakes and idle HTTP clients.
        connection.settimeout(15)
        try:
            secured = self.context.wrap_socket(connection, server_side=True)
            self.connections += 1
            secured.test_id = self.connections
            return secured, address
        except (OSError, ValueError):
            connection.close()
            raise


@contextmanager
def serve(fixtures=FIXTURES):
    """Yield case URLs only after all servers are listening; close on any exit."""
    with ExitStack() as cleanup:
        urls = {}
        for name in CASES:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(fixtures / (name + ".pem")), str(fixtures / "server.key"))
            server = Server(("127.0.0.1", 0), Handler)
            cleanup.callback(server.server_close)
            server.context = context
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            thread.start()
            cleanup.callback(thread.join)
            cleanup.callback(server.shutdown)
            urls[name] = "https://127.0.0.1:%d/" % server.server_port
        yield urls


def generate_fixtures(destination):
    """Generate PUBLIC TEST KEYS with openssl; discard CA signing keys afterward."""
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mactmnf-test-ca-") as directory:
        work = Path(directory)

        def openssl(*args):
            subprocess.run(["openssl", *map(str, args)], cwd=work, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        (work / "index").touch()
        (work / "serial").write_text("01\n", encoding="ascii")
        (work / "ca.cnf").write_text("""[ca]
default_ca = test_ca
[test_ca]
database = index
serial = serial
new_certs_dir = .
certificate = ca.pem
private_key = ca.key
default_md = sha256
policy = names
unique_subject = no
[names]
commonName = supplied
[root]
basicConstraints = critical,CA:true,pathlen:0
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
[server]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = IP:127.0.0.1,DNS:127.0.0.1
authorityKeyIdentifier = keyid
[hostname]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:wrong-host.invalid
authorityKeyIdentifier = keyid
""", encoding="ascii")
        openssl("genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", "server.key")
        openssl("req", "-new", "-key", "server.key", "-subj", "/CN=127.0.0.1", "-out", "server.csr")
        openssl("req", "-new", "-key", "server.key", "-subj", "/CN=wrong-host.invalid", "-out", "hostname.csr")
        for ca in ("ca", "untrusted-ca"):
            openssl("req", "-new", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
                    "-days", "7300", "-subj", "/CN=MacTMNF PUBLIC TEST ONLY " + ca,
                    "-addext", "basicConstraints=critical,CA:true,pathlen:0",
                    "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                    "-keyout", ca + ".key", "-out", ca + ".pem")
            openssl("req", "-new", "-key", ca + ".key", "-subj",
                    "/CN=MacTMNF PUBLIC TEST ONLY " + ca, "-out", ca + ".csr")
            # Cover the expired leaf's entire lifetime too, avoiding time-nesting errors.
            openssl("ca", "-selfsign", "-batch", "-notext", "-config", "ca.cnf",
                    "-cert", ca + ".pem", "-keyfile", ca + ".key", "-in", ca + ".csr",
                    "-out", ca + "-fixed.pem", "-extensions", "root",
                    "-startdate", "20190101000000Z", "-enddate", "20410101000000Z")
            (work / (ca + "-fixed.pem")).replace(work / (ca + ".pem"))
        for name in CASES:
            ca = "untrusted-ca" if "untrusted" in name else "ca"
            openssl("ca", "-batch", "-notext", "-config", "ca.cnf", "-cert", ca + ".pem",
                    "-keyfile", ca + ".key", "-in", "hostname.csr" if "hostname" in name else "server.csr",
                    "-out", name + ".pem",
                    "-extensions", "hostname" if "hostname" in name else "server",
                    "-startdate", "20200101000000Z", "-enddate",
                    "20210101000000Z" if "expired" in name else "20400101000000Z")
        for name in ("ca.pem", "untrusted-ca.pem", "server.key", *(case + ".pem" for case in CASES)):
            (destination / name).write_bytes((work / name).read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=FIXTURES)
    parser.add_argument("--generate-fixtures", type=Path, metavar="DIRECTORY",
                        help="write public test material using openssl, then exit; replaces existing fixtures")
    args = parser.parse_args()
    if args.generate_fixtures is not None:
        generate_fixtures(args.generate_fixtures.resolve())
        return 0
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda signum, frame: stop.set())
                for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        with serve(args.fixtures) as urls:
            print(json.dumps(urls, sort_keys=True), flush=True)
            stop.wait()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
