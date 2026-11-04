"""Núcleo común: modelos, errores e interfaz que implementa cada driver de marca."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class Band(str, Enum):
    B24 = "2.4GHz"
    B5 = "5GHz"
    B6 = "6GHz"


@dataclass
class WifiNetwork:
    band: Band
    ssid: str
    enabled: bool
    security: str = ""
    guest: bool = False
    password: str | None = None   # clave actual, si el router la entrega


def normalize_mac(mac: str) -> str:
    """'AA-BB-CC-DD-EE-FF' / 'aabb.ccdd.eeff' -> 'aa:bb:cc:dd:ee:ff'."""
    hexchars = "".join(c for c in (mac or "").lower() if c in "0123456789abcdef")
    if len(hexchars) != 12:
        return (mac or "").lower()
    return ":".join(hexchars[i:i + 2] for i in range(0, 12, 2))


@dataclass
class ClientDevice:
    mac: str
    ip: str = ""
    name: str = ""
    connection: str = ""      # "Cable", "2.4GHz", "5GHz", "Invitados"...
    blocked: bool = False
    is_self: bool = False     # el dispositivo desde el que se usa la app

    def __post_init__(self):
        self.mac = normalize_mac(self.mac)

    @property
    def label(self) -> str:
        return self.name or "Desconocido"


class RouterError(Exception):
    """Error genérico del router."""


class AuthError(RouterError):
    """Usuario o contraseña de administrador incorrectos."""


class NotSupportedError(RouterError):
    """El modelo o firmware no permite esta operación por API."""


def validate_wifi_password(password: str) -> None:
    """Reglas WPA/WPA2/WPA3-Personal: 8-63 caracteres ASCII imprimibles
    (o exactamente 64 dígitos hexadecimales)."""
    if len(password) == 64 and all(c in "0123456789abcdefABCDEF" for c in password):
        return
    if not 8 <= len(password) <= 63:
        raise ValueError("La clave Wi‑Fi debe tener entre 8 y 63 caracteres.")
    if not all(32 <= ord(c) <= 126 for c in password):
        raise ValueError("La clave Wi‑Fi solo admite caracteres ASCII imprimibles (sin tildes ni ñ).")


class RouterDriver(ABC):
    """Contrato que implementa cada marca. Siempre usa las credenciales
    legítimas de administrador del equipo."""

    brand: str = "generic"

    def __init__(self, host: str, admin_password: str, username: str = "admin", timeout: int = 15):
        self.host = host if host.startswith("http") else f"http://{host}"
        self.admin_password = admin_password
        self.username = username
        self.timeout = timeout

    @classmethod
    @abstractmethod
    def matches(cls, homepage_html: str, headers: dict) -> bool:
        """True si la página de inicio del router parece de esta marca."""

    @abstractmethod
    def login(self) -> None: ...

    @abstractmethod
    def get_wifi_networks(self) -> list[WifiNetwork]: ...

    @abstractmethod
    def set_wifi_password(self, band: Band, new_password: str, guest: bool = False) -> None: ...

    def set_wifi_passwords(self, bands: list[Band], new_password: str, guest: bool = False) -> None:
        """Cambia varias bandas. Los drivers que puedan hacerlo en una sola petición
        lo sobrescriben: así el teléfono no pierde la conexión a mitad del cambio."""
        for band in dict.fromkeys(bands):
            self.set_wifi_password(band, new_password, guest)

    # --- dispositivos conectados y bloqueo -------------------------------------
    def get_clients(self) -> list[ClientDevice]:
        """Dispositivos conectados + los que están en la lista negra."""
        raise NotSupportedError(f"{self.brand}: listar dispositivos no implementado.")

    def block_client(self, device: ClientDevice) -> None:
        raise NotSupportedError(f"{self.brand}: bloquear dispositivos no implementado.")

    def unblock_client(self, device: ClientDevice) -> None:
        raise NotSupportedError(f"{self.brand}: desbloquear dispositivos no implementado.")

    def logout(self) -> None:
        pass

    def __enter__(self):
        self.login()
        return self

    def __exit__(self, *exc):
        try:
            self.logout()
        except Exception:
            pass


def local_ip_for(host: str) -> str:
    """IP de este dispositivo en la red del router (sirve para no bloquearse a sí mismo)."""
    import socket
    from urllib.parse import urlparse
    target = urlparse(host if "://" in host else f"http://{host}").hostname or host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((target, 80))  # UDP: no envía nada, solo elige la ruta
            return s.getsockname()[0]
    except OSError:
        return ""
