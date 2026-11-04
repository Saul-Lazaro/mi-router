"""Detección del gateway y de la marca del router."""
from __future__ import annotations

import os
import platform
import re
import socket
import struct
import subprocess

import requests

from .base import RouterDriver, RouterError
from .drivers.tenda import TendaDriver
from .drivers.tplink import TPLinkDriver

DRIVERS: dict[str, type[RouterDriver]] = {
    "tplink": TPLinkDriver,
    "tenda": TendaDriver,
}


def android_gateway() -> str | None:
    """En Android 10+ /proc/net/route está bloqueado para las apps; se usa WifiManager."""
    try:
        from jnius import autoclass, cast
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        context = autoclass("android.content.Context")
        wm = cast("android.net.wifi.WifiManager",
                  activity.getApplicationContext().getSystemService(context.WIFI_SERVICE))
        gw = wm.getDhcpInfo().gateway  # int de Java, little-endian
        return socket.inet_ntoa(struct.pack("<i", gw)) if gw else None
    except Exception:
        return None


def default_gateway() -> str | None:
    """IP del gateway por defecto (Android, Linux, macOS y Windows)."""
    if "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ:
        return android_gateway()
    system = platform.system()
    try:
        if system == "Linux":
            with open("/proc/net/route") as fh:
                for line in fh.readlines()[1:]:
                    parts = line.split()
                    if parts[1] == "00000000" and int(parts[3], 16) & 2:
                        return socket.inet_ntoa(struct.pack("<L", int(parts[2], 16)))
        elif system == "Darwin":
            out = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True).stdout
            m = re.search(r"gateway:\s*(\S+)", out)
            return m.group(1) if m else None
        elif system == "Windows":
            out = subprocess.run(["ipconfig"], capture_output=True, text=True).stdout
            m = re.search(r"(?:Default Gateway|Puerta de enlace predeterminada)[ .]*:\s*(\d+\.\d+\.\d+\.\d+)", out)
            return m.group(1) if m else None
    except OSError:
        pass
    return None


def detect_brand(host: str, timeout: int = 5) -> str | None:
    """Huella HTTP de la página de inicio del router."""
    url = host if host.startswith("http") else f"http://{host}"
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as e:
        raise RouterError(f"No se pudo conectar con {url}: {e}") from e
    for key, driver in DRIVERS.items():
        if driver.matches(r.text, dict(r.headers)):
            return key
    return None


def get_driver(host: str | None, admin_password: str, brand: str | None = None,
               username: str = "admin") -> RouterDriver:
    host = host or default_gateway()
    if not host:
        raise RouterError("No se encontró el gateway; indica la IP del router.")
    brand = brand or detect_brand(host)
    if brand not in DRIVERS:
        raise RouterError(f"Marca no reconocida en {host}. Usa --marca {'/'.join(DRIVERS)}.")
    if brand == "tplink":
        return _tplink_driver(host, admin_password, username)
    return DRIVERS[brand](host, admin_password, username=username)


def _tplink_driver(host: str, admin_password: str, username: str) -> RouterDriver:
    """TP-Link tiene varios firmwares: elige la implementación según su página de inicio."""
    from .drivers.tplink_cgi import TPLinkCgiDriver
    url = host if host.startswith("http") else f"http://{host}"
    try:
        html = requests.get(url, timeout=8, allow_redirects=True).text
    except requests.RequestException as e:
        raise RouterError(f"No se pudo conectar con {url}: {e}") from e
    cls = TPLinkCgiDriver if TPLinkCgiDriver.looks_like(html) else TPLinkDriver
    return cls(host, admin_password, username=username)
