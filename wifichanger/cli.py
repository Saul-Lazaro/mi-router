"""CLI de prueba:  python -m wifichanger [listar|cambiar] ..."""
from __future__ import annotations

import argparse
import getpass
import sys

from .base import Band, ClientDevice, RouterError, normalize_mac, validate_wifi_password
from .detect import DRIVERS, get_driver

BANDS = {"2.4": Band.B24, "5": Band.B5, "6": Band.B6}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="wifichanger", description="Clave Wi‑Fi, dispositivos conectados y bloqueo en routers TP-Link y Tenda.")
    p.add_argument("--host", help="IP del router (por defecto, el gateway)")
    p.add_argument("--marca", choices=list(DRIVERS), help="Fuerza el driver en vez de autodetectar")
    p.add_argument("--usuario", default="admin")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("listar", help="Muestra las redes Wi‑Fi")
    c = sub.add_parser("cambiar", help="Cambia la clave de una banda")
    c.add_argument("--banda", choices=list(BANDS), default="2.4")
    c.add_argument("--todas", action="store_true", help="Aplica a todas las bandas activas")
    c.add_argument("--invitados", action="store_true")
    sub.add_parser("dispositivos", help="Lista los equipos conectados y bloqueados")
    b = sub.add_parser("bloquear", help="Bloquea un equipo por su MAC")
    b.add_argument("mac")
    u = sub.add_parser("desbloquear", help="Desbloquea un equipo por su MAC")
    u.add_argument("mac")
    args = p.parse_args(argv)

    admin = getpass.getpass("Contraseña de administrador del router: ")
    try:
        driver = get_driver(args.host, admin, args.marca, args.usuario)
        print(f"Router: {driver.brand} en {driver.host}")
        with driver:
            if args.cmd == "listar":
                for n in driver.get_wifi_networks():
                    tipo = "invitados" if n.guest else "principal"
                    print(f"  [{n.band.value:6}] {n.ssid!r:28} {'ON ' if n.enabled else 'OFF'} {n.security} ({tipo})")
                return 0

            if args.cmd == "dispositivos":
                for d in driver.get_clients():
                    estado = "BLOQUEADO" if d.blocked else "conectado"
                    yo = "  <- este equipo" if d.is_self else ""
                    print(f"  {d.mac.upper()}  {d.ip:15} {d.connection:12} {estado:10} {d.label}{yo}")
                return 0

            if args.cmd in ("bloquear", "desbloquear"):
                mac = normalize_mac(args.mac)
                known = {d.mac: d for d in driver.get_clients()}
                device = known.get(mac, ClientDevice(mac))
                if args.cmd == "bloquear":
                    driver.block_client(device)
                else:
                    driver.unblock_client(device)
                print(f"  ✔ {device.label} ({mac.upper()}) {args.cmd.replace('ar', 'ado')}")
                return 0

            nueva = getpass.getpass("Nueva clave Wi‑Fi: ")
            if nueva != getpass.getpass("Repite la clave: "):
                print("Las claves no coinciden.", file=sys.stderr)
                return 1
            validate_wifi_password(nueva)
            bands = ([n.band for n in driver.get_wifi_networks() if n.enabled and n.guest == args.invitados]
                     if args.todas else [BANDS[args.banda]])
            driver.set_wifi_passwords(bands, nueva, guest=args.invitados)
            print(f"  ✔ Clave actualizada en {', '.join(b.value for b in dict.fromkeys(bands))}")
            print("Los dispositivos conectados se desconectarán; reconéctalos con la nueva clave.")
            return 0
    except (RouterError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
