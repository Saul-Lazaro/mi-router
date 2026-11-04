"""Operaciones de alto nivel que usa la interfaz (Android o escritorio).
Sin dependencias de Kivy, para poder probarlas en el PC."""
from __future__ import annotations

from dataclasses import dataclass

import requests

from .base import ClientDevice, NotSupportedError, RouterError, WifiNetwork, validate_wifi_password
from .detect import DRIVERS, default_gateway, get_driver

BRAND_KEYS = {"Detectar marca": None, "TP-Link": "tplink", "Tenda": "tenda"}


@dataclass
class Connection:
    host: str
    brand_key: str | None
    username: str
    admin_password: str
    brand_name: str = ""


def suggest_host() -> str:
    return default_gateway() or "192.168.0.1"


def connect(conn: Connection) -> list[WifiNetwork]:
    """Inicia sesión y devuelve las redes. Rellena conn.brand_name."""
    if not conn.admin_password:
        raise RouterError("Escribe la contraseña de administrador del router.")
    driver = get_driver(conn.host.strip(), conn.admin_password, conn.brand_key, conn.username.strip() or "admin")
    conn.brand_name = driver.brand
    conn.brand_key = next(k for k, cls in DRIVERS.items() if isinstance(driver, cls))
    with driver:
        nets = driver.get_wifi_networks()
    if not nets:
        raise NotSupportedError("El router no informó ninguna red Wi‑Fi.")
    return nets


def change_password(conn: Connection, networks: list[WifiNetwork], new_password: str, confirm: str) -> str:
    """Cambia la clave en las redes elegidas. Devuelve el mensaje para el usuario."""
    if new_password != confirm:
        raise ValueError("Las claves no coinciden.")
    validate_wifi_password(new_password)
    if not networks:
        raise ValueError("Marca al menos una red.")
    driver = get_driver(conn.host.strip(), conn.admin_password, conn.brand_key, conn.username.strip() or "admin")
    done = "Clave cambiada en " + ", ".join(f"{n.ssid} ({n.band.value})" for n in networks) + "."
    try:
        driver.login()
        for guest in (False, True):
            bands = [n.band for n in networks if n.guest == guest]
            if bands:
                driver.set_wifi_passwords(bands, new_password, guest=guest)
    except (requests.ConnectionError, requests.Timeout):
        # El router suele aplicar el cambio y cortar el Wi‑Fi antes de responder.
        return ("El router cortó la conexión al aplicar el cambio (es normal si este teléfono "
                "estaba en esa red). Reconéctate con la nueva clave y vuelve a pulsar Conectar "
                "para comprobarlo.")
    finally:
        try:
            driver.logout()
        except Exception:
            pass
    return done + " Los equipos conectados deberán reconectarse con la nueva clave."


def current_passwords(conn: Connection) -> list[WifiNetwork]:
    """Redes con su clave actual (password=None si el router no la entrega)."""
    with _driver(conn) as d:
        return d.get_wifi_networks()


def _driver(conn: Connection):
    return get_driver(conn.host.strip(), conn.admin_password, conn.brand_key, conn.username.strip() or "admin")


def list_clients(conn: Connection) -> list[ClientDevice]:
    """Conectados primero (este teléfono arriba), luego los bloqueados."""
    with _driver(conn) as d:
        devices = d.get_clients()
    return sorted(devices, key=lambda x: (x.blocked, not x.is_self, x.label.lower()))


def set_blocked(conn: Connection, device: ClientDevice, block: bool) -> str:
    if block and device.is_self:
        raise RouterError("No puedes bloquear el dispositivo desde el que usas la app.")
    with _driver(conn) as d:
        (d.block_client if block else d.unblock_client)(device)
    return (f"{device.label} bloqueado: ya no tiene acceso a la red." if block
            else f"{device.label} desbloqueado: puede volver a conectarse.")
