"""User-operated USB provisioning. Secrets use no argv/env/file/log/echo.

Requires pyserial. Hold the board's physical Stop/BOOT input while sending.
Every boot requires a physical trusted time anchor before certificate validation.
"""
import argparse
import getpass
import json
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--first-enrollment", action="store_true")
    parser.add_argument("--ca", type=Path, help="Public CA PEM only; omit for the built-in CA bundle")
    args = parser.parse_args()
    import serial
    payload = {"trustedUnixMs": int(time.time() * 1000)}
    if args.first_enrollment:
        payload.update(ssid=getpass.getpass("Wi-Fi SSID (hidden): "), password=getpass.getpass("Wi-Fi password: "),
                       origin=input("Trusted HTTPS Engine origin: ").strip(), authorityId=input("Engine authority ID: ").strip(),
                       ticket=getpass.getpass("Single-use executor enrollment ticket: "))
        if args.ca:
            payload["ca"] = args.ca.read_text(encoding="ascii")
    input("Hold the board's Stop/BOOT button, then press Enter to send locally: ")
    encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
    if len(encoded) > 8192:
        parser.error("Provisioning frame is too large")
    with serial.Serial(args.port, 115200, timeout=1, write_timeout=3) as device:
        device.write(encoded)
        device.flush()
    print("Configuration sent over USB. No physical action or enrollment success has been inferred; inspect the Engine device directory.")


if __name__ == "__main__":
    main()
