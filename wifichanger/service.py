"""Operaciones de alto nivel que usa la interfaz (Android o escritorio).
Sin dependencias de Kivy, para poder probarlas en el PC."""
from __future__ import annotations

from dataclasses import dataclass

import requests

from .base import (Band, ClientDevice, NotSupportedError, RouterError, WifiNetwork, validate_ssid,
                   validate_wifi_password)
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


def connect(conn: Connection, progress=None) -> list[WifiNetwork]:
    """Inicia sesión y devuelve las redes. Rellena conn.brand_name.
    `progress(texto)` recibe el paso actual para mostrarlo en pantalla."""
    say = progress or (lambda msg: None)
    if not conn.host.strip():
        raise RouterError("Escribe la IP del router.")
    if not conn.admin_password:
        raise RouterError("Escribe la contraseña de administrador del router.")
    driver = get_driver(conn.host.strip(), conn.admin_password, conn.brand_key,
                        conn.username.strip() or "admin", progress=say)
    conn.brand_name = driver.brand
    conn.brand_key = next(k for k, cls in DRIVERS.items() if isinstance(driver, cls))
    say(f"Iniciando sesión en el {driver.brand}…")
    with driver:
        say("Leyendo las redes Wi‑Fi…")
        nets = driver.get_wifi_networks()
    if not nets:
        raise NotSupportedError("El router no informó ninguna red Wi‑Fi.")
    return nets


def change_wifi(conn: Connection, networks: list[WifiNetwork], new_ssid: str,
                new_password: str, confirm: str) -> dict:
    """Cambia nombre y/o clave de las redes elegidas en una sola operación.

    Devuelve {"message", "ssid", "password", "connect"}: `connect` = (ssid, clave) de la red
    a la que el celular debe conectarse después (o None si no se conoce la clave)."""
    if not networks:
        raise ValueError("Elige al menos una red.")
    new_ssid = (new_ssid or "").strip()
    ssid = new_ssid if new_ssid and any(new_ssid != n.ssid for n in networks) else None
    password = None
    if new_password or confirm:
        if new_password != confirm:
            raise ValueError("Las claves no coinciden.")
        validate_wifi_password(new_password)
        password = new_password
    if ssid is None and password is None:
        raise ValueError("No hay cambios: escribe un nombre nuevo o una clave nueva.")
    if ssid is not None:
        validate_ssid(ssid)

    main = next((n for n in networks if n.band == Band.B24 and not n.guest), networks[0])
    final_ssid = ssid or main.ssid
    final_pw = password if password is not None else main.password
    what = " y ".join(x for x in (("nombre" if ssid else ""), ("clave" if password else "")) if x)
    names = ", ".join(f"{n.ssid} ({n.band.value})" for n in networks)
    result = {"ssid": final_ssid, "password": final_pw,
              "connect": (final_ssid, final_pw) if final_pw else None,
              "message": f"Se cambió {what} de {names}."}

    driver = _driver(conn)
    try:
        driver.login()
        driver.apply_to_networks(networks, ssid=ssid, password=password)
    except (requests.ConnectionError, requests.Timeout):
        # El router suele aplicar el cambio y cortar el Wi‑Fi antes de responder.
        result["message"] = (f"El router aplicó el cambio de {what} y cortó la conexión "
                             "(es normal si este celular estaba en esa red).")
    finally:
        try:
            driver.logout()
        except Exception:
            pass
    return result


def change_password(conn: Connection, networks: list[WifiNetwork], new_password: str, confirm: str) -> str:
    """Compatibilidad: solo la clave."""
    return change_wifi(conn, networks, "", new_password, confirm)["message"]


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
