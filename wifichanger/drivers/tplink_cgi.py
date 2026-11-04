"""Driver TP-Link para el firmware "/cgi?" sin cifrado (TR-098), p. ej. TL-WR840N
(panel con /js/oid_str.js y /js/lib.js, sin tpEncrypt). Verificado contra el panel
de un TL-WR840N real:

  Login   : cookie  Authorization=Basic base64(usuario:clave)   + Referer del router
  Petición: POST /cgi?<tipo>&<tipo>...   cuerpo  [OID#stack#pstack]i,n\\r\\nattr=valor\\r\\n...
            tipos: 1 GET, 2 SET, 3 ADD, 4 DEL, 5 GL (lista), 6 GS (lista hija)
  Respuesta: líneas "[stack]i" + "attr=valor"; termina en "[error]0" si todo fue bien.

  Clave Wi-Fi   : SET LAN_WLAN <stack> X_TP_PreSharedKey=...            (wlSec.htm)
  Conectados    : GL LAN_HOST_ENTRY (DHCP) + GS LAN_WLAN_ASSOC_DEV     (dhcpClient/wlStats)
  Bloqueo Wi-Fi : filtrado MAC inalámbrico en modo "deny"              (wlAcl/wlAclAdv)
                  LAN_WLAN: X_TP_MACAddressControlRule, MACAddressControlEnabled
                  LAN_WLAN_MACTABLEENTRY: Enabled, MACAddress, Description, HostName
"""
from __future__ import annotations

import base64
import re

import requests

from ..base import (AuthError, Band, ClientDevice, NotSupportedError, RouterError,
                    WifiNetwork, local_ip_for, normalize_mac, validate_wifi_password)
from .tplink import TPLinkDriver

GET, SET, ADD, DEL, GL, GS = 1, 2, 3, 4, 5, 6
PSK_BEACONS = {"wpa", "11i", "wpaand11i"}


def _g(d: dict, key: str, default=""):
    """Lectura sin distinguir mayúsculas (el router devuelve 'status' aunque se pida 'Status')."""
    if key in d:
        return d[key]
    low = key.lower()
    for k, v in d.items():
        if k.lower() == low:
            return v
    return default


def _encode_attrs(attrs) -> tuple[str, int]:
    if not attrs:
        return "", 0
    if isinstance(attrs, dict):
        attrs = [f"{k}={v}" for k, v in attrs.items()]
    return "".join(f"{a}\r\n" for a in attrs), len(attrs)


def _band_of(item: dict) -> Band:
    txt = str(_g(item, "X_TP_Band")).lower()
    if "5" in txt:
        return Band.B5
    if txt:
        return Band.B24
    return Band.B5 if item.get("__stack", "").startswith("1,2") else Band.B24


class TPLinkCgiDriver(TPLinkDriver):
    """Mismo "TP-Link" para la app; distinta implementación por debajo."""

    def __init__(self, *args, **kwargs):
        kwargs.pop("client", None)
        super().__init__(*args, **kwargs)
        self.session = requests.Session()
        token = base64.b64encode(f"{self.username}:{self.admin_password}".encode()).decode()
        self.session.headers.update({
            "Referer": f"{self.host}/",
            "Cookie": f"Authorization=Basic {token}",
            "Content-Type": "text/plain",
        })

    @staticmethod
    def looks_like(homepage_html: str) -> bool:
        html = homepage_html.lower()
        return "oid_str.js" in html and not re.search(r"encrypt|rsa\.js|cgi_gdpr", html)

    # --- protocolo ----------------------------------------------------------
    def _req(self, acts: list) -> list:
        """acts: [(tipo, oid, stack, pstack, attrs)] -> un resultado por act."""
        types, body = [], ""
        for i, (typ, oid, stack, pstack, attrs) in enumerate(acts):
            text, n = _encode_attrs(attrs)
            types.append(str(typ))
            body += f"[{oid}#{stack or '0,0,0,0,0,0'}#{pstack or '0,0,0,0,0,0'}]{i},{n}\r\n{text}"
        try:
            r = self.session.post(f"{self.host}/cgi?{'&'.join(types)}", data=body.encode(),
                                  timeout=self.timeout, allow_redirects=False)
        except (requests.ConnectionError, requests.Timeout):
            raise
        if r.status_code in (401, 403):
            raise AuthError("El router rechazó la contraseña de administrador.")
        if r.status_code != 200:
            raise RouterError(f"El router respondió HTTP {r.status_code}.")

        results = [[] if a[0] in (GL, GS) else {} for a in acts]
        current, error = None, None
        for line in r.text.splitlines():
            if not line:
                continue
            if line.startswith("["):
                m = re.match(r"\[([^\]]*)\](\d+)", line)
                if not m:
                    continue
                stack, idx = m.group(1), int(m.group(2))
                if stack == "error":
                    error = idx
                    break
                if idx >= len(results):
                    current = None
                    continue
                if isinstance(results[idx], list):
                    current = {"__stack": stack}
                    results[idx].append(current)
                else:
                    current = results[idx]
                    current["__stack"] = stack
            elif current is not None and "=" in line:
                k, v = line.split("=", 1)
                current[k] = v
        if error is None:
            if "<html" in r.text.lower():
                raise AuthError("Sesión no válida: revisa la contraseña de administrador.")
            raise RouterError("Respuesta del router incompleta.")
        if error != 0:
            raise RouterError(f"El router devolvió el código de error {error}.")
        return results

    # --- sesión -------------------------------------------------------------
    def login(self) -> None:
        self._wlans()  # lectura inocua: valida la contraseña

    def logout(self) -> None:
        self.session.close()

    # --- Wi-Fi --------------------------------------------------------------
    def _wlans(self) -> list[dict]:
        (items,) = self._req([(GL, "LAN_WLAN", None, None,
                               ["name", "SSID", "Enable", "Status", "X_TP_Band", "BeaconType",
                                "X_TP_MACAddressControlRule", "MACAddressControlEnabled"])])
        return items

    def get_wifi_networks(self) -> list[WifiNetwork]:
        nets = []
        for w in self._wlans():
            status = str(_g(w, "Status")).lower()
            enabled = status == "up" or str(_g(w, "Enable")) == "1"
            if status == "disabled" and not _g(w, "SSID"):
                continue
            beacon = str(_g(w, "BeaconType"))
            sec = "Abierta" if beacon.lower() in ("basic", "none", "") else "WPA/WPA2"
            nets.append(WifiNetwork(_band_of(w), _g(w, "SSID") or "(sin nombre)", enabled, sec))
        return nets

    def supports_password_change(self) -> bool:
        return True

    def set_wifi_password(self, band: Band, new_password: str, guest: bool = False) -> None:
        self.set_wifi_passwords([band], new_password, guest)

    def set_wifi_passwords(self, bands: list[Band], new_password: str, guest: bool = False) -> None:
        validate_wifi_password(new_password)
        if guest:
            raise NotSupportedError("Cambiar la clave de la red de invitados aún no está soportado en este modelo.")
        wlans = {_band_of(w): w for w in self._wlans()}
        acts = []
        for band in dict.fromkeys(bands):
            w = wlans.get(band)
            if w is None:
                raise NotSupportedError(f"Este router no tiene red de {band.value}.")
            if str(_g(w, "BeaconType")).lower() in PSK_BEACONS:
                attrs = [f"X_TP_PreSharedKey={new_password}"]
            else:  # red abierta o WEP -> WPA/WPA2-PSK, igual que el panel web
                attrs = ["BeaconType=WPAand11i", "WPAEncryptionModes=AESEncryption",
                         "WPAAuthenticationMode=PSKAuthentication",
                         "IEEE11iAuthenticationMode=PSKAuthentication",
                         "IEEE11iEncryptionModes=AESEncryption",
                         f"X_TP_PreSharedKey={new_password}", "X_TP_GroupKeyUpdateInterval=0"]
            acts.append((SET, "LAN_WLAN", w["__stack"], None, attrs))
        self._req(acts)  # una sola petición para todas las bandas

    # --- dispositivos -------------------------------------------------------
    def _snapshot(self):
        hosts, wlans = self._req([
            (GL, "LAN_HOST_ENTRY", None, None, ["leaseTimeRemaining", "MACAddress", "hostName", "IPAddress"]),
            (GL, "LAN_WLAN", None, None, ["name", "SSID", "Status", "X_TP_Band",
                                          "X_TP_MACAddressControlRule", "MACAddressControlEnabled"]),
        ])
        up = [w for w in wlans if str(_g(w, "Status")).lower() == "up"]
        acts = []
        for w in up:
            acts.append((GS, "LAN_WLAN_ASSOC_DEV", None, w["__stack"],
                         ["AssociatedDeviceMACAddress", "X_TP_HostName"]))
            acts.append((GS, "LAN_WLAN_MACTABLEENTRY", None, w["__stack"],
                         ["Enabled", "MACAddress", "Description", "HostName"]))
        res = self._req(acts) if acts else []
        per_wlan = [(w, res[2 * i], res[2 * i + 1]) for i, w in enumerate(up)]
        return hosts, per_wlan

    @staticmethod
    def _deny_active(w: dict) -> bool:
        return (str(_g(w, "X_TP_MACAddressControlRule")).lower() == "deny"
                and str(_g(w, "MACAddressControlEnabled")) == "1")

    def get_clients(self) -> list[ClientDevice]:
        hosts, per_wlan = self._snapshot()
        my_ip = local_ip_for(self.host)
        devices: dict[str, ClientDevice] = {}
        for h in hosts:
            mac = normalize_mac(_g(h, "MACAddress"))
            if not mac:
                continue
            d = ClientDevice(mac, _g(h, "IPAddress"), _g(h, "hostName"), "Cable/inactivo")
            d.is_self = bool(my_ip) and d.ip == my_ip
            devices[mac] = d
        for w, assoc, table in per_wlan:
            band = _band_of(w).value
            for a in assoc:
                mac = normalize_mac(_g(a, "AssociatedDeviceMACAddress"))
                if not mac:
                    continue
                d = devices.setdefault(mac, ClientDevice(mac, "", _g(a, "X_TP_HostName"), band))
                d.connection = band
                if not d.name:
                    d.name = _g(a, "X_TP_HostName")
            if self._deny_active(w):
                for e in table:
                    mac = normalize_mac(_g(e, "MACAddress"))
                    if mac and str(_g(e, "Enabled")) == "1":
                        d = devices.setdefault(mac, ClientDevice(mac, "", _g(e, "Description").strip(), ""))
                        d.blocked = True
        return list(devices.values())

    def block_client(self, device: ClientDevice) -> None:
        if device.is_self:
            raise RouterError("No puedes bloquear el dispositivo desde el que usas la app.")
        _, per_wlan = self._snapshot()
        if not per_wlan:
            raise RouterError("El Wi‑Fi del router está apagado: no se puede usar el filtrado MAC.")
        mac = device.mac.upper()
        desc = re.sub(r"[^A-Za-z0-9_-]", "", device.name or "")[:20] or "MiRouter"
        for w, _assoc, table in per_wlan:
            rule = str(_g(w, "X_TP_MACAddressControlRule")).lower()
            enabled = str(_g(w, "MACAddressControlEnabled")) == "1"
            acts = []
            if rule == "allow":
                if enabled or table:
                    raise NotSupportedError(
                        "El filtrado MAC del router está en modo 'Permitir' (lista blanca). Cámbialo a "
                        "'Denegar' en el panel web (Inalámbrico > Filtrado MAC) para bloquear desde la app.")
                acts.append((SET, "LAN_WLAN", w["__stack"], None, ["X_TP_MACAddressControlRule=deny"]))
            existing = next((e for e in table if normalize_mac(_g(e, "MACAddress")) == device.mac), None)
            if existing is not None:
                if str(_g(existing, "Enabled")) != "1":
                    acts.append((SET, "LAN_WLAN_MACTABLEENTRY", existing["__stack"], None, ["Enabled=1"]))
            else:
                acts.append((ADD, "LAN_WLAN_MACTABLEENTRY", None, w["__stack"],
                             {"Enabled": 1, "Description": desc, "MACAddress": mac,
                              "HostName": _g(w, "name") or "wlan0"}))
            if not enabled:
                acts.append((SET, "LAN_WLAN", w["__stack"], None, ["MACAddressControlEnabled=1"]))
            if acts:
                self._req(acts)

    def unblock_client(self, device: ClientDevice) -> None:
        _, per_wlan = self._snapshot()
        acts = [(DEL, "LAN_WLAN_MACTABLEENTRY", e["__stack"], None, None)
                for _w, _a, table in per_wlan for e in table
                if normalize_mac(_g(e, "MACAddress")) == device.mac]
        if acts:
            self._req(acts)
