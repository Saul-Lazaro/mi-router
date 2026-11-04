"""Driver Tenda — familia de firmware "goform" (AC6, AC7, AC8, AC10, AC15, AC1200,
F6, N300 v2 y similares).

Flujo del panel web:
  POST /login/Auth          username=admin&password=<md5(clave)>   -> cookie de sesión
  GET  /goform/WifiBasicGet                                         -> JSON con la config Wi‑Fi
  POST /goform/WifiBasicSet  (mismos campos)                        -> {"errCode":0}
  GET  /goform/getOnlineList                                        -> [cabecera, dispositivo, ...]
  GET  /goform/getBlackRuleList                                     -> lista negra rápida
  POST /goform/setBlackRule | delBlackRule   mac=aa:bb:...          -> bloquear / desbloquear

Para no tocar nada más que la clave, el driver lee la configuración actual y la
reenvía completa cambiando solo los campos de contraseña.
"""
from __future__ import annotations

import base64
import hashlib
import random

import requests

from ..base import (AuthError, Band, ClientDevice, NotSupportedError, RouterDriver, RouterError,
                    WifiNetwork, local_ip_for, normalize_mac, validate_wifi_password)

# Campos por banda en WifiBasicGet/Set
FIELDS = {
    Band.B24: {"enable": "wrlEn", "ssid": "ssid", "security": "security", "pwd": "wrlPwd", "hide": "hideSsid"},
    Band.B5: {"enable": "wrlEn_5g", "ssid": "ssid_5g", "security": "security_5g", "pwd": "wrlPwd_5g", "hide": "hideSsid_5g"},
}
SET_KEYS = ("wrlEn", "wrlEn_5g", "security", "security_5g", "ssid", "ssid_5g",
            "hideSsid", "hideSsid_5g", "wrlPwd", "wrlPwd_5g", "doubleBandUnityEnable")
DEFAULT_SECURITY = "wpawpa2psk"


class TendaDriver(RouterDriver):
    brand = "Tenda"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = requests.Session()
        self.session.headers["Referer"] = f"{self.host}/index.html"

    @classmethod
    def matches(cls, homepage_html: str, headers: dict) -> bool:
        html = homepage_html.lower()
        return "tenda" in html or "/login/auth" in html or "goform" in html

    # --- sesión -------------------------------------------------------------
    def _encodings(self):
        pw = self.admin_password.encode()
        yield hashlib.md5(pw).hexdigest()           # la mayoría de firmwares AC
        yield base64.b64encode(pw).decode()          # algunos firmwares antiguos

    def login(self) -> None:
        for encoded in self._encodings():
            self.session.cookies.clear()
            r = self.session.post(f"{self.host}/login/Auth",
                                  data={"username": self.username, "password": encoded},
                                  allow_redirects=False, timeout=self.timeout)
            location = r.headers.get("Location", "").lower()
            wrong = r.text.strip() == "1"  # algunos firmwares responden "1" si la clave es mala
            if r.status_code in (200, 302) and self.session.cookies and "login" not in location and not wrong:
                return
        raise AuthError("Tenda rechazó la contraseña de administrador.")

    def logout(self) -> None:
        try:
            self.session.get(f"{self.host}/goform/logout", timeout=self.timeout)
        finally:
            self.session.close()

    # --- lectura ------------------------------------------------------------
    def _get_config(self) -> dict:
        r = self.session.get(f"{self.host}/goform/WifiBasicGet",
                             params={"random": random.random()},
                             allow_redirects=False, timeout=self.timeout)
        if r.status_code in (301, 302) or "login" in r.headers.get("Location", "").lower():
            raise AuthError("Sesión expirada o no iniciada.")
        try:
            data = r.json()
        except ValueError as e:
            raise NotSupportedError(
                "Este Tenda no expone /goform/WifiBasicGet (firmware de otra familia).") from e
        if "wrlPwd" not in data and "ssid" not in data:
            raise NotSupportedError("Respuesta de WifiBasicGet con formato desconocido.")
        return data

    def get_wifi_networks(self) -> list[WifiNetwork]:
        cfg = self._get_config()
        nets = []
        for band, f in FIELDS.items():
            if f["ssid"] in cfg:
                nets.append(WifiNetwork(band, cfg[f["ssid"]], str(cfg.get(f["enable"], "1")) == "1",
                                        cfg.get(f["security"], ""), password=cfg.get(f["pwd"], "")))
        return nets

    # --- escritura ----------------------------------------------------------
    def set_wifi_password(self, band: Band, new_password: str, guest: bool = False) -> None:
        self.set_wifi_passwords([band], new_password, guest)

    def set_wifi_passwords(self, bands: list[Band], new_password: str, guest: bool = False) -> None:
        """Todas las bandas en una sola petición WifiBasicSet."""
        validate_wifi_password(new_password)
        if guest:
            raise NotSupportedError("La red de invitados de Tenda usa otro endpoint (GuestSet); aún no implementado.")
        for band in bands:
            if band not in FIELDS:
                raise NotSupportedError(f"Tenda no tiene banda {band.value}.")

        cfg = self._get_config()
        payload = {k: cfg[k] for k in SET_KEYS if k in cfg}

        bands = list(dict.fromkeys(bands))
        # Con "unificar bandas" activo el router usa un solo SSID/clave para ambas.
        if str(cfg.get("doubleBandUnityEnable", "false")).lower() in ("true", "1"):
            bands = [b for b in FIELDS if FIELDS[b]["pwd"] in cfg or FIELDS[b]["ssid"] in cfg]

        for b in bands:
            f = FIELDS[b]
            payload[f["pwd"]] = new_password
            if payload.get(f["security"], "none") in ("none", ""):
                payload[f["security"]] = DEFAULT_SECURITY  # red abierta -> pasa a WPA/WPA2

        self._post("WifiBasicSet", payload)

    # --- dispositivos conectados y lista negra -------------------------------
    def _get_json(self, form: str):
        r = self.session.get(f"{self.host}/goform/{form}?{random.random()}",
                             allow_redirects=False, timeout=self.timeout)
        if r.status_code in (301, 302) or "<!doctype" in r.text[:200].lower():
            raise AuthError("Sesión expirada o no iniciada.")
        try:
            return r.json()
        except ValueError as e:
            raise NotSupportedError(f"Este Tenda no expone /goform/{form}.") from e

    def _post(self, form: str, data: dict) -> int:
        r = self.session.post(f"{self.host}/goform/{form}", data=data,
                              allow_redirects=False, timeout=self.timeout)
        if r.status_code in (301, 302):
            raise AuthError("Sesión expirada o no iniciada.")
        try:
            err = r.json().get("errCode")
        except (ValueError, AttributeError):
            err = None
        if r.status_code == 200 and str(err) == "0":
            return 0
        if form == "setBlackRule" and str(err) == "1":
            raise RouterError("La lista negra del Tenda está llena (máximo 30 equipos).")
        raise RouterError(f"Tenda devolvió error en {form} (HTTP {r.status_code}, errCode={err}).")

    LINE = {"0": "Cable", "1": "2.4GHz", "2": "5GHz"}

    def get_clients(self) -> list[ClientDevice]:
        online = self._get_json("getOnlineList")
        if not isinstance(online, list):
            raise NotSupportedError("Respuesta de getOnlineList con formato desconocido.")
        head = online[0] if online and isinstance(online[0], dict) and "deviceId" not in online[0] else {}
        rows = online[1:] if head else online
        self_mac = normalize_mac(head.get("localhostMac", ""))
        my_ip = head.get("localhostIP") or local_ip_for(self.host)

        devices: dict[str, ClientDevice] = {}
        for item in rows:
            if not isinstance(item, dict) or not item.get("deviceId"):
                continue
            conn = "Invitados" if str(item.get("isGuestClient")) == "true" else self.LINE.get(str(item.get("line")), "")
            d = ClientDevice(item["deviceId"], item.get("ip", ""), item.get("devName", ""), conn,
                             blocked=str(item.get("black", "0")) == "1")
            d.is_self = d.mac == self_mac or (bool(my_ip) and d.ip == my_ip)
            devices[d.mac] = d

        try:
            black = self._get_json("getBlackRuleList")
        except NotSupportedError:
            black = []
        for item in black if isinstance(black, list) else []:
            mac = normalize_mac(item.get("deviceId", ""))
            if not mac:
                continue
            if mac in devices:
                devices[mac].blocked = True
            else:
                devices[mac] = ClientDevice(mac, "", item.get("devName", ""), "", blocked=True)
        return list(devices.values())

    def block_client(self, device: ClientDevice) -> None:
        if device.is_self:
            raise RouterError("No puedes bloquear el dispositivo desde el que usas la app.")
        self._post("setBlackRule", {"mac": device.mac})

    def unblock_client(self, device: ClientDevice) -> None:
        self._post("delBlackRule", {"mac": device.mac})
