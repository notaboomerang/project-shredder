"""
Launch Project Shredder for your PHONE (second-screen draft mode).

Two ways to reach the app from your phone:

  LAN mode (default) — phone + laptop on the SAME normal Wi-Fi:
      py -3.13 run_phone.py
    Opens http://<laptop-ip>:8666 ; prints the exact URL.

  TUNNEL mode — works on ANY network, including corporate/guest Wi-Fi
  (e.g. AMZ_internet) that blocks devices from seeing each other
  ("client isolation"), or when phone + laptop are on different networks:
      py -3.13 run_phone.py --tunnel
    Spins up a free Cloudflare quick-tunnel and prints a public HTTPS URL
    (https://something.trycloudflare.com) you can open from the phone
    anywhere. No account, no login. Auto-downloads cloudflared if missing.

Tip: in Chrome on the phone, tap ⋮ -> "Add to Home screen" to pin the app.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request

PORT = 8666
_HERE = os.path.dirname(os.path.abspath(__file__))
_CFD = os.path.join(_HERE, "cloudflared.exe")   # local copy if we download it
_CFD_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/"
            "download/cloudflared-windows-amd64.exe")


# --------------------------------------------------------------------------- LAN IP
def _default_route_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        return "127.0.0.1"
    finally:
        s.close()


_VPN_HINTS = ("anyconnect", "cisco", "vpn", "openvpn", "tap-windows", "tap",
              "wireguard", "zerotier", "tailscale", "wsl", "vethernet",
              "hyper-v", "virtual", "loopback", "bluetooth")
_WIFI_HINTS = ("wi-fi", "wifi", "wireless", "wlan")
_ETH_HINTS = ("ethernet", "lan")


def _ip_to_adapter() -> dict:
    out = {}
    try:
        txt = subprocess.run(["ipconfig"], capture_output=True, text=True,
                             timeout=8).stdout
    except Exception:  # noqa: BLE001
        return out
    cur = ""
    for line in txt.splitlines():
        if line and not line.startswith(" "):
            cur = line.strip().rstrip(":")
        m = re.search(r"IPv4 Address[.\s]*:\s*([\d.]+)", line)
        if m:
            out[m.group(1)] = cur
    return out


def _candidate_ips() -> list[str]:
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       family=socket.AF_INET):
            ips.add(info[4][0])
    except Exception:  # noqa: BLE001
        pass
    ips.add(_default_route_ip())
    ips = {i for i in ips if i and not i.startswith("127.")}
    adapters = _ip_to_adapter()

    def score(ip):
        name = (adapters.get(ip, "") or "").lower()
        s = 0
        if any(h in name for h in _WIFI_HINTS):
            s += 100
        elif any(h in name for h in _ETH_HINTS):
            s += 80
        if any(h in name for h in _VPN_HINTS):
            s -= 200
        if ip.startswith("169.254."):
            s -= 100
        if ip.startswith(("192.168.0.", "192.168.1.")):
            s += 10
        if ip.startswith("192.168.128.") or ip.startswith("172."):
            s -= 20
        return s

    return sorted(ips, key=score, reverse=True)


# --------------------------------------------------------------------------- server
def _start_streamlit():
    cmd = [
        sys.executable, "-m", "streamlit", "run", "app.py",
        "--server.address", "0.0.0.0", "--server.port", str(PORT),
        "--server.headless", "true", "--server.enableCORS", "false",
        "--server.enableXsrfProtection", "false",
        "--browser.gatherUsageStats", "false",
    ]
    return subprocess.Popen(cmd, cwd=_HERE)


def _wait_until_up(timeout=40) -> bool:
    """Block until the local server answers, so the tunnel points at a live app."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/_stcore/health",
                                   timeout=2)
            return True
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return False


# --------------------------------------------------------------------------- cloudflared
def _cloudflared_path() -> str | None:
    """Find cloudflared: PATH first, then a local copy we downloaded before."""
    from shutil import which
    p = which("cloudflared")
    if p:
        return p
    if os.path.exists(_CFD):
        return _CFD
    return None


def _download_cloudflared() -> str | None:
    """Fetch the standalone cloudflared.exe into the project (no admin/winget)."""
    print("  Downloading cloudflared (one-time, ~30 MB)…")
    try:
        req = urllib.request.Request(_CFD_URL, headers={"User-Agent": "Shredder"})
        with urllib.request.urlopen(req, timeout=120) as r, open(_CFD, "wb") as f:
            f.write(r.read())
        print("  cloudflared ready.")
        return _CFD
    except Exception as ex:  # noqa: BLE001
        print(f"  Could not download cloudflared automatically: {ex}")
        print("  Manual: download the Windows amd64 exe from")
        print("    https://github.com/cloudflare/cloudflared/releases/latest")
        print(f"  and save it as: {_CFD}")
        return None


def _run_tunnel():
    """Start a Cloudflare quick-tunnel to the local app and print the public URL."""
    cfd = _cloudflared_path() or _download_cloudflared()
    if not cfd:
        print("\n  Tunnel unavailable. Falling back to LAN URL below.\n")
        return None
    proc = subprocess.Popen(
        [cfd, "tunnel", "--url", f"http://localhost:{PORT}", "--no-autoupdate"],
        cwd=_HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1)
    public = {"url": None, "error": None}

    def _reader():
        # the real quick-tunnel URL is a RANDOM multi-word subdomain, e.g.
        # https://calm-river-oak-tree.trycloudflare.com — NOT the generic
        # "api.trycloudflare.com" that cloudflared also logs. Require a hyphenated
        # (multi-word) subdomain and explicitly skip the api/dash hosts.
        rx = re.compile(r"https://([a-z0-9-]+)\.trycloudflare\.com")
        for line in proc.stdout:
            low = line.lower()
            if ("no such host" in low or "failed to request quick tunnel" in low
                    or "dial tcp" in low):
                public["error"] = "network blocks the tunnel (DNS/outbound denied)"
            for m in rx.finditer(line):
                sub = m.group(1)
                if sub in ("api", "dash", "www"):
                    continue
                if "-" not in sub:          # quick-tunnel names are always hyphenated
                    continue
                public["url"] = m.group(0)
                return
    threading.Thread(target=_reader, daemon=True).start()

    # wait up to ~25s for the public URL to appear (or a clear failure)
    for _ in range(50):
        if public["url"] or public["error"]:
            break
        time.sleep(0.5)
    return proc, public["url"], public["error"]


def _print_hotspot_help(lan_url):
    """The bulletproof fallback that works on ANY locked-down network: put both
    devices on the PHONE'S hotspot (a private network with no isolation)."""
    print("\n  >>> GUARANTEED FIX — phone hotspot (works anywhere): <<<")
    print("    1. On your phone, turn ON the personal hotspot.")
    print("    2. Connect this LAPTOP to that hotspot's Wi-Fi.")
    print("    3. Re-run:  py -3.13 run_phone.py")
    print("    4. Open the printed http://... URL in the phone's browser.")
    print("    (Both devices are now on the phone's private net — no isolation,")
    print("     no firewall-from-router, no corporate DNS. Live ESPN sync still")
    print("     works since the laptop gets internet through the phone.)")
    print(f"\n  If you're already on a NORMAL shared Wi-Fi, this may work now:")
    print(f"      {lan_url}")


# --------------------------------------------------------------------------- main
def main():
    tunnel = "--tunnel" in sys.argv or "-t" in sys.argv
    cands = _candidate_ips()
    ip = cands[0] if cands else "127.0.0.1"
    lan_url = f"http://{ip}:{PORT}"
    bar = "=" * 62

    print("\n" + bar)
    print("  PROJECT SHREDDER — phone mode" + ("  (TUNNEL)" if tunnel else ""))
    print(bar)

    srv = _start_streamlit()
    try:
        if not _wait_until_up():
            print("  ! The app server didn't come up in time. Check for errors above.")
        if tunnel:
            print("  Starting a public tunnel…")
            res = _run_tunnel()
            pub = res[1] if res else None
            err = res[2] if res and len(res) > 2 else None
            if pub:
                print("\n  On your phone (ANY network — even mobile data), open:")
                print(f"\n      {pub}\n")
                print("  Chrome on the phone: ⋮ -> 'Add to Home screen' to pin it.")
            else:
                print("\n  Public tunnel could NOT start"
                      + (f" — {err}." if err else "."))
                print("  (Corporate/locked networks often block tunnels AND")
                print("   device-to-device Wi-Fi. Best guaranteed fix below.)")
                _print_hotspot_help(lan_url)
        else:
            print("  On your phone (SAME normal Wi-Fi as this laptop), open:")
            print(f"\n      {lan_url}\n")
            for alt in cands[1:]:
                print(f"      (or)  http://{alt}:{PORT}")
            print("\n  On CORPORATE/GUEST Wi-Fi (e.g. AMZ_internet) the phone")
            print("  usually CAN'T reach the laptop (client isolation).")
            print("  Try a public tunnel:  py -3.13 run_phone.py --tunnel")
            print("  …and if that's blocked too, the phone-hotspot method always")
            print("  works (turn on phone hotspot, connect laptop to it, re-run).")
        print(bar)
        print("  Keep this window open during the draft. Ctrl+C to stop.\n")
        srv.wait()
    except KeyboardInterrupt:
        print("\nStopped. See you at the next pick. \U0001f918")
    finally:
        try:
            srv.terminate()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
