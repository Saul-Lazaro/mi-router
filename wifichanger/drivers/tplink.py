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
                    WifiNetwork, local_ip_for, normalize_mac, validate_ssid,
                    validate_wifi_password)

import requests

# La librería tplinkrouterc6u (y su criptografía) se carga solo cuando hace falta:
# así la app abre más rápido y el WR840N (driver tplink_cgi) no la necesita.
tplinkrouterc6u = None
Connection = None
TplinkRouterProvider = None


def _load_lib() -> None:
    global tplinkrouterc6u, Connection, TplinkRouterProvider
    if tplinkrouterc6u is not None:
        return
    try:
        import tplinkrouterc6u as lib
    except ImportError:  # pragma: no cover
        return
    tplinkrouterc6u = lib
    if Connection is None:
        Connection = lib.Connection
    TplinkRouterProvider = lib.TplinkRouterProvider


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
        text = (homepage_html + " " + " ".join(f"{k} {v}" for k, v in headers.items())).lower()
        return any(s in text for s in ("tp-link", "tplink", "mercusys", "/webpages/", "tplinkwifi.net",
                                       "cgi_gdpr", "cgi/getparm", "userrpm", "loginrpm", "oid_str.js"))

    # --- sesión -------------------------------------------------------------
    def _homepage(self) -> str:
        try:
            r = requests.get(self.host + "/", timeout=8, allow_redirects=True)
        except requests.RequestException as e:
            raise RouterError(f"No se pudo conectar con {self.host}: {e}") from e
        return r.text.lower() + " " + r.headers.get("WWW-Authenticate", "").lower()

    def _gdpr_client(self):
        """WR840N v6+, WR841N v14, WR844N, C50…: login /cgi/login + /cgi_gdpr."""
        for name in ("TPLinkWR841NClient", "TPLinkC50Client"):
            cls = getattr(tplinkrouterc6u, name, None) if tplinkrouterc6u else None
            if cls is None:
                continue
            client = cls(self.host, self.admin_password, self.username, timeout=self.timeout)
            try:
                client.authorize()
                return client
            except Exception:
                continue
        return None

    def login(self) -> None:
        if self._client is not None:
            try:
                self._client.authorize()
            except Exception as e:
                raise AuthError(f"Login rechazado por el router: {e}") from e
            return
        page = self._homepage()
        if "userrpm" in page or "loginrpm" in page or 'basic realm="tp-link' in page:
            raise NotSupportedError(
                "Este TP-Link tiene el firmware antiguo (panel 'userRpm', p. ej. WR840N v1–v5). "
                "La app aún no es compatible con esa versión.")
        _load_lib()
        if TplinkRouterProvider is None:
            raise RouterError("Falta la dependencia: pip install tplinkrouterc6u")
        if "/webpages/" not in page:  # "/webpages/" = firmware luci moderno (Archer C6/AX…)
            self._client = self._gdpr_client()
            if self._client is not None:
                return
        try:
            # el provider ya hace login al elegir el cliente
            self._client = TplinkRouterProvider.get_client(
                self.host, self.admin_password, self.username, timeout=self.timeout)
        except Exception as e:  # el provider lanza si ningún cliente encaja
            raise AuthError("No se pudo iniciar sesión en el TP-Link. Revisa la contraseña de "
                            f"administrador (la misma del panel web). Detalle: {e}") from e

    def logout(self) -> None:
        if self._client is not None:
            self._client.logout()

    # --- familia cgi_gdpr (WR840N v6+, WR841N v14, C50…) ------------------------
    WLAN_STACK = {Band.B24: "1,1,0,0,0,0", Band.B5: "1,2,0,0,0,0"}

    def _is_gdpr(self) -> bool:
        return hasattr(self._client, "req_act") and hasattr(self._client, "ActItem")

    def _gdpr_networks(self) -> list[WifiNetwork]:
        act = self._client.ActItem
        _, values = self._client.req_act([act(act.GL, "LAN_WLAN", attrs=["enable", "SSID", "X_TP_Band", "X_TP_PreSharedKey"])])
        items = values if isinstance(values, list) else [values]
        nets = []
        for i, item in enumerate(i for i in items if isinstance(i, dict)):
            band_txt = str(item.get("X_TP_Band", "")).lower()
            band = Band.B5 if "5" in band_txt else Band.B24 if band_txt else (Band.B24, Band.B5)[min(i, 1)]
            nets.append(WifiNetwork(band, item.get("SSID", "") or "(sin nombre)",
                                    str(item.get("enable", "1")) == "1", "WPA/WPA2",
                                    password=item.get("X_TP_PreSharedKey")))
        return nets

    def _gdpr_set_password(self, bands: list[Band], new_password: str | None, ssid: str | None = None) -> None:
        act = self._client.ActItem
        acts = []
        for band in dict.fromkeys(bands):
            if band not in self.WLAN_STACK:
                raise NotSupportedError(f"Este modelo no tiene banda {band.value}.")
            attrs = ([f"SSID={ssid}"] if ssid is not None else []) + \
                    ([f"X_TP_PreSharedKey={new_password}"] if new_password is not None else [])
            acts.append(act(act.SET, "LAN_WLAN", self.WLAN_STACK[band], attrs=attrs))
        try:
            self._client.req_act(acts)
        except (requests.ConnectionError, requests.Timeout):
            raise
        except Exception as e:
            raise RouterError(f"El router rechazó el cambio de clave: {e}") from e

    # --- lectura ------------------------------------------------------------
    def get_wifi_networks(self) -> list[WifiNetwork]:
        nets: list[WifiNetwork] = []
        if self._is_gdpr():
            try:
                nets = self._gdpr_networks()
            except Exception:
                nets = []
            if nets:
                return nets
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
                                            st.encryption or "", guest, password=getattr(st, "psk_key", None)))
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
        if self._is_gdpr():
            return True
        set_wifi = getattr(self._client, "set_wifi", None)
        return set_wifi is not None and "psk_key" in inspect.signature(set_wifi).parameters

    def set_wifi_password(self, band: Band, new_password: str, guest: bool = False) -> None:
        validate_wifi_password(new_password)
        if not self.supports_password_change():
            raise NotSupportedError(
                f"El cliente {type(self._client).__name__} de este modelo solo permite "
                "activar/desactivar el Wi‑Fi; cambiar la clave requiere un driver propio.")
        if self._is_gdpr():
            if guest:
                raise NotSupportedError("Cambiar la clave de la red de invitados aún no está soportado en este modelo.")
            return self._gdpr_set_password([band], new_password)
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

    def set_wifi_passwords(self, bands: list[Band], new_password: str, guest: bool = False) -> None:
        if self._is_gdpr() and not guest:
            validate_wifi_password(new_password)
            return self._gdpr_set_password(bands, new_password)  # una sola petición
        super().set_wifi_passwords(bands, new_password, guest)

    def apply_wifi_settings(self, bands: list[Band], ssid: str | None = None,
                            password: str | None = None, guest: bool = False) -> None:
        if password is not None:
            validate_wifi_password(password)
        if ssid is not None:
            validate_ssid(ssid)
        if self._is_gdpr():
            if guest:
                raise NotSupportedError("La red de invitados aún no está soportada en este modelo.")
            return self._gdpr_set_password(bands, password, ssid)  # una sola petición
        set_wifi = getattr(self._client, "set_wifi", None)
        params = inspect.signature(set_wifi).parameters if set_wifi else {}
        if (ssid is not None and "ssid" not in params) or (password is not None and "psk_key" not in params):
            raise NotSupportedError(f"El cliente {type(self._client).__name__} de este modelo no permite este cambio.")
        for band in dict.fromkeys(bands):
            kw = {}
            if ssid is not None:
                kw["ssid"] = quote(ssid, safe="")
            if password is not None:
                kw["psk_key"] = quote(password, safe="")
            self._client.set_wifi(_connection_for(band, guest), **kw)

    def _ac(self, form: str, data: str):
        request = getattr(self._client, "request", None)
        if self._is_gdpr():
            raise NotSupportedError(
                "Bloquear dispositivos aún no está disponible para este modelo de TP-Link "
                "(WR840N/WR841N). Sí puedes ver los conectados y cambiar la clave.")
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
