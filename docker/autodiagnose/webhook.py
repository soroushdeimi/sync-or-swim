#!/usr/bin/env python3
"""HTTP webhook service for Alertmanager: executes diagnose.py for critical alerts."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import subprocess
import sys

PORT = int(os.environ.get("WEBHOOK_PORT", 9095))
SCRIPT_PATH = os.environ.get("DIAGNOSE_SCRIPT", "/scripts/diagnose.py")


class WebhookHandler(BaseHTTPRequestHandler):
    """Receives Alertmanager webhooks and triggers diagnose.py."""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK\n")

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length)
        try:
            payload = json.loads(post_data.decode("utf-8"))
        except Exception as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"Bad JSON: {e}\n".encode("utf-8"))
            return

        alerts = payload.get("alerts", [])
        for alert in alerts:
            status = alert.get("status") or payload.get("status", "")
            labels = alert.get("labels", {})
            severity = labels.get("severity", "")

            if status == "firing" and severity == "critical":
                alertname = labels.get("alertname", "unknown")
                instance = labels.get("instance", "mysql-1")
                node = instance if instance in ["mysql-1", "mysql-2"] else "mysql-1"

                cmd = [sys.executable, SCRIPT_PATH, "--node", node, "--json"]
                try:
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    verdict = ""
                    if res.stdout:
                        try:
                            diag = json.loads(res.stdout)
                            verdict = diag.get("verdict", "")
                        except Exception:
                            verdict = res.stdout.strip()
                    if not verdict and res.stderr:
                        verdict = res.stderr.strip()
                except Exception as e:
                    verdict = f"failed to run diagnose: {e}"

                verdict_line = " ".join(verdict.split())
                print(
                    f"autodiagnose alert={alertname} instance={instance} verdict={verdict_line}",
                    flush=True,
                )

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}\n')


def main():
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
