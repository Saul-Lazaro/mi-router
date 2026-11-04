"""Driver TP-Link (y Mercusys).

Se apoya en la librería comunitaria `tplinkrouterc6u`, que ya resuelve el login
cifrado (RSA/AES/ECIES) de las distintas familias de firmware de TP-Link y elige
el cliente adecuado para cada modelo.

Bloqueo de dispositivos: la librería no lo trae, así que se usa el "Control de
acceso" del panel (firmwares con API luci: Archer C6/C6U/AX10/AX20/AX53/AX55…):
  admin/access_control?form=enable        operation=read | write&enable=on
  admin/access_control?form=mode          operation=read  -> {"access_mode": "black"}
  admin/access_control?form=black_devices operation=load | insert&new=<json> | remove&key=<id>&index=<n>
"""
from __future__ import annotations

import inspect
import json
from urllib.parse import quote

from ..base import (AuthError, Band, ClientDevice, NotSupportedError, RouterDriver, RouterError,
                    WifiNetwork, local_ip_for, normalize_mac, validate_wifi_password)

try:  # Import perezoso: la app funciona aunque solo se use el driver Tenda.
    from tplinkrouterc6u import Connection, TplinkRouterProvider
except ImportError:  # pragma: no cover
    Connection = None
    TplinkRouterProvider = None


def _connection_for(band: Band, guest: bool):
    prefix = "GUEST" if guest else "HOST"
    suffix = {Band.B24: "2G", Band.B5: "5G", Band.B6: "6G"}[band]
    return getattr(Connection, f"{prefix}_{suffix}")


class TPLinkDriver(RouterDriver):
    brand = "TP-Link"

    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._client = client  # inyectable para pruebas

    @classmethod
    def matches(cls, homepage_html: str, headers: dict) -> bool:
        html = homepage_html.lower()
        return any(s in html for s in ("tp-link", "tplink", "mercusys", "/webpages/", "tplinkwifi.net"))

    # --- sesión -------------------------------------------------------------
    def login(self) -> None:
        if self._client is None:
            if TplinkRouterProvider is None:
                raise RouterError("Falta la dependencia: pip install tplinkrouterc6u")
            try:
                self._client = TplinkRouterProvider.get_client(
                    self.host, self.admin_password, self.username, timeout=self.timeout)
            except Exception as e:  # el provider lanza si ningún cliente encaja
                raise AuthError(f"No se pudo iniciar sesión en el TP-Link: {e}") from e
        try:
            self._client.authorize()
        except Exception as e:
            raise AuthError(f"Login rechazado por el router: {e}") from e

    def logout(self) -> None:
        if self._client is not None:
            self._client.logout()

    # --- lectura ------------------------------------------------------------
    def get_wifi_networks(self) -> list[WifiNetwork]:
        nets: list[WifiNetwork] = []
        if hasattr(self._client, "get_wifi"):
            for guest in (False, True):
                for band in Band:
                    try:
                        st = self._client.get_wifi(_connection_for(band, guest))
                    except Exception:
                        continue  # banda inexistente en este modelo
                    if st is None or st.ssid is None:
                        continue
                    nets.append(WifiNetwork(band, st.ssid, bool(st.enable),
                                            st.encryption or "", guest))
            if nets:
                return nets
        # Modelos sin get_wifi: solo sabemos si cada banda está activa.
        status = self._client.get_status()
        for band, attr in ((Band.B24, "wifi_2g_enable"), (Band.B5, "wifi_5g_enable"),
                           (Band.B6, "wifi_6g_enable")):
            val = getattr(status, attr, None)
            if val is not None:
                nets.append(WifiNetwork(band, "(desconocido)", bool(val)))
        return nets

    # --- escritura ----------------------------------------------------------
    def supports_password_change(self) -> bool:
        set_wifi = getattr(self._client, "set_wifi", None)
        return set_wifi is not None and "psk_key" in inspect.signature(set_wifi).parameters

    def set_wifi_password(self, band: Band, new_password: str, guest: bool = False) -> None:
        validate_wifi_password(new_password)
        if not self.supports_password_change():
            raise NotSupportedError(
                f"El cliente {type(self._client).__name__} de este modelo solo permite "
                "activar/desactivar el Wi‑Fi; cambiar la clave requiere un driver propio.")
        # La librería arma el cuerpo x-www-form-urlencoded sin escapar: una clave con
        # '&' o '=' rompería la petición, así que la codificamos aquí.
        self._client.set_wifi(_connection_for(band, guest), psk_key=quote(new_password, safe=""))

    # --- dispositivos conectados y bloqueo ------------------------------------
    AC = "admin/access_control?form="

    @staticmethod
    def _conn_label(conn) -> str:
        name = getattr(conn, "name", str(conn))
        if "WIRED" in name:
            return "Cable"
        band = {"2G": "2.4GHz", "5G": "5GHz", "6G": "6GHz"}.get(name[-2:], "Wi‑Fi")
        return f"Invitados {band}" if name.startswith("GUEST") else band

    def _ac(self, form: str, data: str):
        request = getattr(self._client, "request", None)
        if request is None:
            raise NotSupportedError(
                f"El modelo ({type(self._client).__name__}) no expone el Control de acceso por API.")
        try:
            return request(self.AC + form, data)
        except NotSupportedError:
            raise
        except Exception as e:
            raise RouterError(f"El router rechazó la operación de Control de acceso ({form}): {e}") from e

    def _blacklist(self) -> list[dict]:
        last_error = None
        for form in ("black_devices", "black_list"):
            try:
                data = self._ac(form, "operation=load")
            except RouterError as e:
                last_error = e
                continue
            if isinstance(data, list):
                self._black_form = form
                return data
            if data in ("", None):
                self._black_form = form
                return []
        raise NotSupportedError(f"No se pudo leer la lista de bloqueados: {last_error}")

    def get_clients(self) -> list[ClientDevice]:
        my_ip = local_ip_for(self.host)
        devices: dict[str, ClientDevice] = {}
        status = self._client.get_status()
        for dev in getattr(status, "devices", []) or []:
            d = ClientDevice(dev.macaddr, dev.ipaddr, dev.hostname or "", self._conn_label(dev.type))
            d.is_self = bool(my_ip) and d.ip == my_ip
            devices[d.mac] = d
        try:
            black = self._blacklist()
        except NotSupportedError:
            black = []  # se puede ver la lista aunque el modelo no permita bloquear
        for item in black:
            mac = normalize_mac(item.get("mac", ""))
            if not mac:
                continue
            if mac in devices:
                devices[mac].blocked = True
            else:
                devices[mac] = ClientDevice(mac, item.get("ipaddr", ""), item.get("name", ""), "", blocked=True)
        return list(devices.values())

    def _ensure_blacklist_mode(self) -> None:
        mode = self._ac("mode", "operation=read") or {}
        if mode.get("access_mode", "black") != "black":
            raise NotSupportedError(
                "El Control de acceso del router está en modo lista blanca. Cámbialo a "
                "\"lista negra\" en el panel web (Seguridad > Control de acceso) para bloquear desde la app.")
        enable = self._ac("enable", "operation=read") or {}
        if enable.get("enable") != "on":
            self._ac("enable", "operation=write&enable=on")

    def block_client(self, device: ClientDevice) -> None:
        if device.is_self:
            raise RouterError("No puedes bloquear el dispositivo desde el que usas la app.")
        black = self._blacklist()
        if any(normalize_mac(i.get("mac", "")) == device.mac for i in black):
            return
        self._ensure_blacklist_mode()
        entry = {"name": device.name or device.mac, "mac": device.mac.upper().replace(":", "-"),
                 "deviceType": "other", "host": "NON_HOST"}
        res = self._ac(self._black_form, "operation=insert&new=" + quote(json.dumps(entry), safe=""))
        if isinstance(res, dict) and res.get("success") is False:
            raise RouterError("El router no aceptó el bloqueo.")

    def unblock_client(self, device: ClientDevice) -> None:
        for index, item in enumerate(self._blacklist()):
            if normalize_mac(item.get("mac", "")) == device.mac:
                key = item.get(".name") or item.get("key")
                if not key:
                    raise RouterError("El router no devolvió el identificador de la regla.")
                self._ac(self._black_form, f"operation=remove&key={quote(key)}&index={index}")
                return
        # no estaba en la lista: nada que hacer
