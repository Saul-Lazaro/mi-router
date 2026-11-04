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


_PAGE_TIMEOUT = 6
_cache: dict[tuple[str, str | None], type[RouterDriver]] = {}  # (host, marca) -> clase elegida


def _fetch(host: str) -> tuple[str, dict]:
    url = host if host.startswith("http") else f"http://{host}"
    try:
        r = requests.get(url, timeout=_PAGE_TIMEOUT, allow_redirects=True)
    except requests.Timeout as e:
        raise RouterError(f"El router en {host} no respondió a tiempo. ¿Estás conectado a su Wi‑Fi "
                          "y la IP es correcta?") from e
    except requests.RequestException as e:
        raise RouterError(f"No se pudo conectar con {host}. Revisa que estés en el Wi‑Fi del router "
                          "y que la IP sea correcta.") from e
    return r.text, dict(r.headers)


def _brand_from_page(html: str, headers: dict) -> str | None:
    for key, driver in DRIVERS.items():
        if driver.matches(html, headers):
            return key
    return None


def detect_brand(host: str, timeout: int = 5) -> str | None:
    """Huella HTTP de la página de inicio del router."""
    return _brand_from_page(*_fetch(host))


def get_driver(host: str | None, admin_password: str, brand: str | None = None,
               username: str = "admin", progress=None) -> RouterDriver:
    say = progress or (lambda msg: None)
    host = (host or default_gateway() or "").strip()
    if not host:
        raise RouterError("No se encontró el router; escribe su IP.")
    key = (host, brand)
    cls = _cache.get(key)
    if cls is None:
        say(f"Buscando el router en {host}…")
        html, headers = _fetch(host)          # una sola descarga de la página de inicio
        if brand is None:
            say("Detectando la marca…")
            brand = _brand_from_page(html, headers)
        if brand not in DRIVERS:
            raise RouterError(f"No se reconoció la marca del router en {host}. Elige TP-Link o Tenda.")
        cls = DRIVERS[brand]
        if brand == "tplink":
            from .drivers.tplink_cgi import TPLinkCgiDriver
            if TPLinkCgiDriver.looks_like(html):
                cls = TPLinkCgiDriver
        _cache[key] = cls
    return cls(host, admin_password, username=username)
