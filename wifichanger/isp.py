"""Verificación del proveedor: la app solo funciona si el equipo del proveedor (10.99.0.1)
responde. Un técnico puede desbloquearla con contraseña para usarla sin esa verificación."""
from __future__ import annotations

import hashlib
import platform
import socket
import subprocess

ISP_HOST = "10.99.0.1"
ISP_PHONES = ("996770477", "982877033")
ISP_MESSAGE = ("Comuníquese con su Proveedor de Internet:\n"
               "996 770 477  ·  982 877 033")

# Solo se guarda el hash de la contraseña de desbloqueo (no el texto). Ojo: dentro de un APK
# esto evita leerla a simple vista, pero no es una protección fuerte.
_UNLOCK_SALT = b"maxswifi:"
_UNLOCK_HASH = "d9e6ace1e6df09c877c513e26eff81734d4f430c69a48450a58c9d8e797e418c"


def check_unlock_password(password: str) -> bool:
    return hashlib.sha256(_UNLOCK_SALT + (password or "").encode("utf-8")).hexdigest() == _UNLOCK_HASH


def _ping(host: str, timeout: int) -> bool | None:
    """True/False según el ping del sistema; None si no se pudo ejecutar ping."""
    if platform.system() == "Windows":
        cmds = [["ping", "-n", "1", "-w", str(timeout * 1000), host]]
    else:  # Android y Linux/macOS
        cmds = [["/system/bin/ping", "-c", "1", "-W", str(timeout), host],
                ["ping", "-c", "1", "-W", str(timeout), host]]
    for cmd in cmds:
        try:
            r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=timeout + 2)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return True
        if r.returncode == 1:      # ping se ejecutó y no hubo respuesta
            return False
        # otros códigos (p. ej. 2: sin permiso para ICMP) -> probar el siguiente método
    return None


def _tcp_probe(host: str, timeout: float) -> bool:
    """Si el equipo responde aunque sea rechazando la conexión, está alcanzable."""
    for port in (80, 443, 53, 22, 8291):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except ConnectionRefusedError:
            return True
        except OSError:
            continue
    return False


def isp_reachable(host: str = ISP_HOST, timeout: int = 2) -> bool:
    ping = _ping(host, timeout)
    if ping is not None:
        return ping            # el ping se pudo hacer: su resultado manda
    # Solo si el teléfono no permite hacer ping: comprobación alternativa por TCP
    return _tcp_probe(host, timeout / 2)
