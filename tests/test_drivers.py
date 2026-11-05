"""Pruebas contra un Tenda simulado (servidor HTTP real) y un cliente TP-Link falso."""
import hashlib
import json
import threading
from enum import Enum
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from wifichanger.base import AuthError, Band, NotSupportedError, RouterError, validate_wifi_password
from wifichanger.detect import detect_brand
from wifichanger.drivers import tplink as tplink_mod
from wifichanger.drivers.tenda import TendaDriver

ADMIN = "admin123"


# ---------------------------------------------------------------- Tenda falso
class FakeTenda(BaseHTTPRequestHandler):
    state = {}

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        return "password=ok" in self.headers.get("Cookie", "")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._send(200, b"<html><title>Tenda</title><form action='/login/Auth'></form></html>")
        if path.startswith("/goform/") and not self._authed():
            return self._send(302, headers={"Location": "/login.html"})
        if path == "/goform/WifiBasicGet":
            return self._send(200, json.dumps(self.state).encode())
        if path == "/goform/getOnlineList":
            head = {"blackNum": 0, "macFilterType": "black", "localhostIP": "192.168.0.100",
                    "localhostMac": "AA:AA:AA:AA:AA:01"}
            rows = [dict(r, black=1 if r["deviceId"].lower() in FakeTenda.black else 0) for r in FakeTenda.online]
            return self._send(200, json.dumps([head] + rows).encode())
        if path == "/goform/getBlackRuleList":
            return self._send(200, json.dumps([{"deviceId": m, "devName": "Bloqueado"} for m in FakeTenda.black]).encode())
        self._send(200, b"{}")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode(), keep_blank_values=True).items()}
        path = urlparse(self.path).path
        if path == "/login/Auth":
            if form.get("password") == hashlib.md5(ADMIN.encode()).hexdigest():
                return self._send(302, headers={"Location": "/index.html", "Set-Cookie": "password=ok; path=/"})
            return self._send(302, headers={"Location": "/login.html?0"})
        if path in ("/goform/setBlackRule", "/goform/delBlackRule") and self._authed():
            mac = form["mac"].lower()
            if path.endswith("setBlackRule"):
                FakeTenda.black.append(mac)
            elif mac in FakeTenda.black:
                FakeTenda.black.remove(mac)
            return self._send(200, b'{"errCode":0}')
        if path == "/goform/WifiBasicSet" and self._authed():
            FakeTenda.state.update(form)
            FakeTenda.last_post = form
            return self._send(200, b'{"errCode":0}')
        self._send(302, headers={"Location": "/login.html"})


@pytest.fixture
def tenda_host():
    FakeTenda.state = {"doubleBandUnityEnable": "false", "wrlEn": "1", "wrlEn_5g": "1",
                       "ssid": "Casa", "ssid_5g": "Casa_5G", "hideSsid": "0", "hideSsid_5g": "0",
                       "security": "wpawpa2psk", "security_5g": "none",
                       "wrlPwd": "vieja1234", "wrlPwd_5g": ""}
    FakeTenda.online = [
        {"deviceId": "aa:aa:aa:aa:aa:01", "ip": "192.168.0.100", "devName": "MiTelefono", "line": "2",
         "isGuestClient": "false"},
        {"deviceId": "bb:bb:bb:bb:bb:02", "ip": "192.168.0.101", "devName": "Vecino", "line": "1",
         "isGuestClient": "false"},
        {"deviceId": "cc:cc:cc:cc:cc:03", "ip": "192.168.0.102", "devName": "PC", "line": "0",
         "isGuestClient": "false"},
    ]
    FakeTenda.black = ["dd:dd:dd:dd:dd:04"]
    srv = HTTPServer(("127.0.0.1", 0), FakeTenda)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_tenda_detect_and_list(tenda_host):
    assert detect_brand(tenda_host) == "tenda"
    with TendaDriver(tenda_host, ADMIN) as d:
        nets = d.get_wifi_networks()
    assert [(n.band, n.ssid) for n in nets] == [(Band.B24, "Casa"), (Band.B5, "Casa_5G")]


def test_tenda_change_only_password(tenda_host):
    with TendaDriver(tenda_host, ADMIN) as d:
        d.set_wifi_password(Band.B24, "Nu3va&Clave=ok")
    post = FakeTenda.last_post
    assert post["wrlPwd"] == "Nu3va&Clave=ok"       # caracteres especiales intactos
    assert post["ssid"] == "Casa" and post["ssid_5g"] == "Casa_5G"  # nada más cambió
    assert post["wrlPwd_5g"] == ""


def test_tenda_open_network_gets_wpa(tenda_host):
    with TendaDriver(tenda_host, ADMIN) as d:
        d.set_wifi_password(Band.B5, "clave5ghz!")
    assert FakeTenda.last_post["security_5g"] == "wpawpa2psk"


def test_tenda_unified_bands(tenda_host):
    FakeTenda.state["doubleBandUnityEnable"] = "true"
    with TendaDriver(tenda_host, ADMIN) as d:
        d.set_wifi_password(Band.B24, "unificada99")
    assert FakeTenda.last_post["wrlPwd"] == FakeTenda.last_post["wrlPwd_5g"] == "unificada99"


def test_tenda_bad_admin(tenda_host):
    with pytest.raises(AuthError):
        TendaDriver(tenda_host, "mala").login()


# --------------------------------------------------------- TP-Link simulado
class FakeConnection(Enum):
    HOST_2G = "host_2g"; HOST_5G = "host_5g"; HOST_6G = "host_6g"
    GUEST_2G = "guest_2g"; GUEST_5G = "guest_5g"; GUEST_6G = "guest_6g"


class FakeWifi:
    def __init__(self, ssid):
        self.ssid, self.enable, self.encryption = ssid, True, "psk"


class FakeArcher:  # firma igual a TplinkRouter.set_wifi de la librería
    def __init__(self):
        self.calls = []

    def authorize(self): pass
    def logout(self): pass

    def get_wifi(self, conn):
        if conn.name in ("HOST_2G", "HOST_5G"):
            return FakeWifi("Archer_" + conn.name[-2:])
        raise Exception("no existe")

    def set_wifi(self, wifi, enable=None, ssid=None, hidden=None, encryption=None, psk_version=None,
                 psk_cipher=None, psk_key=None, hwmode=None, htmode=None, channel=None, txpower=None,
                 disabled_all=None, portal_password=None):
        self.calls.append((wifi, psk_key))


class FakeOldTPLink(FakeArcher):  # firmwares que solo activan/desactivan
    def set_wifi(self, wifi, enable):
        pass


@pytest.fixture(autouse=True)
def stub_connection(monkeypatch):
    monkeypatch.setattr(tplink_mod, "Connection", FakeConnection)


def test_tplink_list_and_change():
    client = FakeArcher()
    with tplink_mod.TPLinkDriver("192.168.0.1", ADMIN, client=client) as d:
        assert [n.ssid for n in d.get_wifi_networks()] == ["Archer_2G", "Archer_5G"]
        d.set_wifi_password(Band.B5, "a&b=c 12345")
    conn, key = client.calls[0]
    assert conn is FakeConnection.HOST_5G
    assert unquote(key) == "a&b=c 12345" and "&" not in key


def test_tplink_unsupported_model():
    with tplink_mod.TPLinkDriver("192.168.0.1", ADMIN, client=FakeOldTPLink()) as d:
        with pytest.raises(NotSupportedError):
            d.set_wifi_password(Band.B24, "12345678")


def test_tplink_fingerprint():
    assert tplink_mod.TPLinkDriver.matches("<script src='/webpages/js/su.js'>", {})


# ------------------------------------------------------------- validación
@pytest.mark.parametrize("pw", ["corta", "x" * 64, "contraseña1"])
def test_invalid_passwords(pw):
    with pytest.raises(ValueError):
        validate_wifi_password(pw)


# ------------------------------------------------- capa de servicio (la usa la app)
from wifichanger import service


def test_service_flow(tenda_host):
    conn = service.Connection(tenda_host, None, "admin", ADMIN)
    nets = service.connect(conn)
    assert conn.brand_name == "Tenda" and conn.brand_key == "tenda"
    msg = service.change_password(conn, nets, "Ambas1234", "Ambas1234")
    assert "Casa (2.4GHz)" in msg
    post = FakeTenda.last_post
    assert post["wrlPwd"] == post["wrlPwd_5g"] == "Ambas1234"   # una sola petición


def test_service_mismatch(tenda_host):
    conn = service.Connection(tenda_host, "tenda", "admin", ADMIN)
    with pytest.raises(ValueError, match="no coinciden"):
        service.change_password(conn, service.connect(conn), "abcdefgh", "abcdefgX")


def test_service_router_drops_connection(tenda_host, monkeypatch):
    import requests
    conn = service.Connection(tenda_host, "tenda", "admin", ADMIN)
    nets = service.connect(conn)

    def boom(*a, **k):
        raise requests.ConnectionError("reset")
    monkeypatch.setattr(TendaDriver, "apply_wifi_settings", boom)
    assert "cortó la conexión" in service.change_password(conn, nets, "abcdefgh", "abcdefgh")


# ------------------------------------------------ dispositivos y bloqueo: Tenda
def test_tenda_clients(tenda_host):
    with TendaDriver(tenda_host, ADMIN) as d:
        devs = {x.mac: x for x in d.get_clients()}
    assert devs["aa:aa:aa:aa:aa:01"].is_self and devs["aa:aa:aa:aa:aa:01"].connection == "5GHz"
    assert devs["cc:cc:cc:cc:cc:03"].connection == "Cable"
    assert devs["dd:dd:dd:dd:dd:04"].blocked          # solo está en la lista negra
    assert not devs["bb:bb:bb:bb:bb:02"].blocked


def test_tenda_block_unblock(tenda_host):
    conn = service.Connection(tenda_host, "tenda", "admin", ADMIN)
    vecino = next(x for x in service.list_clients(conn) if x.name == "Vecino")
    assert "bloqueado" in service.set_blocked(conn, vecino, True)
    assert "bb:bb:bb:bb:bb:02" in FakeTenda.black
    assert next(x for x in service.list_clients(conn) if x.mac == vecino.mac).blocked
    service.set_blocked(conn, vecino, False)
    assert "bb:bb:bb:bb:bb:02" not in FakeTenda.black


def test_cannot_block_self(tenda_host):
    conn = service.Connection(tenda_host, "tenda", "admin", ADMIN)
    me = service.list_clients(conn)[0]
    assert me.is_self                                  # el propio teléfono va primero
    with pytest.raises(RouterError, match="No puedes bloquear"):
        service.set_blocked(conn, me, True)
    assert "aa:aa:aa:aa:aa:01" not in FakeTenda.black


# ---------------------------------------------- dispositivos y bloqueo: TP-Link
class FakeDev:
    def __init__(self, conn, mac, ip, name):
        self.type, self.macaddr, self.ipaddr, self.hostname = conn, mac, ip, name


class FakeStatus:
    def __init__(self):
        self.devices = [FakeDev(FakeConnection.HOST_5G, "11-22-33-44-55-66", "192.168.0.50", "Tablet"),
                        FakeDev(FakeConnection.GUEST_2G, "66-55-44-33-22-11", "192.168.0.51", "Visita")]


class FakeArcherAC(FakeArcher):
    """Simula los endpoints admin/access_control del firmware luci."""
    def __init__(self, mode="black", enabled="off"):
        super().__init__()
        self.mode, self.enabled, self.black, self.requests = mode, enabled, [], []

    def get_status(self):
        return FakeStatus()

    def request(self, path, data):
        self.requests.append((path, data))
        q = parse_qs(data)
        op = q["operation"][0]
        form = path.split("form=")[1]
        if form == "mode":
            return {"access_mode": self.mode}
        if form == "enable":
            if op == "write":
                self.enabled = q["enable"][0]
            return {"enable": self.enabled}
        if form == "black_devices":
            if op == "load":
                return list(self.black)
            if op == "insert":
                entry = json.loads(q["new"][0])
                entry[".name"] = f"cfg{len(self.black):04d}"
                self.black.append(entry)
                return entry
            if op == "remove":
                idx = int(q["index"][0])
                assert self.black[idx][".name"] == q["key"][0]
                self.black.pop(idx)
                return {}
        raise Exception("endpoint desconocido")


def test_tplink_clients_and_block():
    client = FakeArcherAC()
    with tplink_mod.TPLinkDriver("192.168.0.1", ADMIN, client=client) as d:
        devs = {x.mac: x for x in d.get_clients()}
        tablet = devs["11:22:33:44:55:66"]
        assert tablet.connection == "5GHz" and devs["66:55:44:33:22:11"].connection == "Invitados 2.4GHz"
        d.block_client(tablet)
        assert client.enabled == "on"                       # activó el control de acceso
        assert client.black[0]["mac"] == "11-22-33-44-55-66"
        assert {x.mac: x for x in d.get_clients()}["11:22:33:44:55:66"].blocked
        d.block_client(tablet)                              # idempotente
        assert len(client.black) == 1
        d.unblock_client(tablet)
        assert client.black == []


def test_tplink_whitelist_mode_refused():
    client = FakeArcherAC(mode="white")
    with tplink_mod.TPLinkDriver("192.168.0.1", ADMIN, client=client) as d:
        tablet = d.get_clients()[0]
        with pytest.raises(NotSupportedError, match="lista blanca"):
            d.block_client(tablet)
    assert client.black == [] and client.enabled == "off"


def test_tplink_old_model_lists_but_cannot_block():
    class Old(FakeArcher):
        def get_status(self):
            return FakeStatus()
    with tplink_mod.TPLinkDriver("192.168.0.1", ADMIN, client=Old()) as d:
        devs = d.get_clients()
        assert len(devs) == 2
        with pytest.raises(NotSupportedError):
            d.block_client(devs[0])


def test_normalize_mac():
    from wifichanger.base import normalize_mac
    assert normalize_mac("AA-BB-CC-DD-EE-FF") == normalize_mac("aabb.ccdd.eeff") == "aa:bb:cc:dd:ee:ff"


# ------------------------------------- TP-Link familia cgi_gdpr (WR840N v6+, WR841N)
class FakeGdpr:
    """Imita TPLinkWR841NClient: req_act + ActItem, sin get_wifi ni request."""
    class ActItem:
        GET, SET, ADD, DEL, GL, GS, OP, CGI = 1, 2, 3, 4, 5, 6, 7, 8

        def __init__(self, type, oid, stack="0,0,0,0,0,0", pstack="0,0,0,0,0,0", attrs=None):
            self.type, self.oid, self.stack, self.attrs = type, oid, stack, attrs or []

    def __init__(self):
        self.sets = []

    def authorize(self): pass
    def logout(self): pass

    def get_status(self):
        return FakeStatus()

    def req_act(self, acts):
        out = []
        for a in acts:
            if a.type == self.ActItem.GL and a.oid == "LAN_WLAN":
                out.append({"enable": "1", "SSID": "TP-Link_840", "X_TP_Band": "2.4GHz"})
            elif a.type == self.ActItem.SET:
                self.sets.append((a.oid, a.stack, a.attrs))
        return "", (out[0] if len(out) == 1 else out)

    def set_wifi(self, wifi, enable):
        pass


def test_gdpr_wifi_and_password():
    client = FakeGdpr()
    with tplink_mod.TPLinkDriver("192.168.0.1", ADMIN, client=client) as d:
        nets = d.get_wifi_networks()
        assert [(n.ssid, n.band) for n in nets] == [("TP-Link_840", Band.B24)]
        d.set_wifi_passwords([Band.B24], "Nueva=Clave&1")
    assert client.sets == [("LAN_WLAN", "1,1,0,0,0,0", ["X_TP_PreSharedKey=Nueva=Clave&1"])]


def test_gdpr_clients_ok_block_not_supported():
    with tplink_mod.TPLinkDriver("192.168.0.1", ADMIN, client=FakeGdpr()) as d:
        devs = d.get_clients()
        assert len(devs) == 2
        with pytest.raises(NotSupportedError, match="WR840N"):
            d.block_client(devs[0])


def test_old_firmware_detected():
    class Old(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_GET(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="TP-LINK Wireless N Router WR840N"')
            self.end_headers()
    srv = HTTPServer(("127.0.0.1", 0), Old)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host = f"http://127.0.0.1:{srv.server_port}"
    try:
        assert detect_brand(host) == "tplink"
        with pytest.raises(NotSupportedError, match="firmware antiguo"):
            tplink_mod.TPLinkDriver(host, ADMIN).login()
    finally:
        srv.shutdown()


# ------------------- TP-Link firmware "/cgi?" sin cifrado (TL-WR840N real, TR-098)
import base64 as _b64
import re as _re

from wifichanger import detect as _detect
from wifichanger.drivers.tplink_cgi import TPLinkCgiDriver


class FakeWR840N(BaseHTTPRequestHandler):
    """Imita las respuestas observadas en el panel de un TL-WR840N."""
    def log_message(self, *a): pass

    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b'<html><script src="/js/oid_str.js"></script><script src="/js/lib.js"></script>'
                         b'<script>var modelName="TL-WR840N";</script></html>')

    def do_POST(self):
        st = FakeWR840N.st
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        good = "Basic " + _b64.b64encode(f"admin:{ADMIN}".encode()).decode()
        if self.headers.get("Cookie") != f"Authorization={good}" or not self.headers.get("Referer"):
            self.send_response(403); self.end_headers(); return
        types = urlparse(self.path).query.split("&")
        blocks = _re.split(r"(?=\[[A-Z_0-9]+#)", body)
        blocks = [b for b in blocks if b.strip()]
        out = []
        for t, blk in zip(types, blocks):
            head, *lines = blk.split("\r\n")
            m = _re.match(r"\[([A-Z_0-9]+)#([\d,]+)#([\d,]+)\](\d+),(\d+)", head)
            oid, stack, pstack, idx = m.group(1), m.group(2), m.group(3), m.group(4)
            attrs = dict(l.split("=", 1) for l in lines if "=" in l)
            st["log"].append((t, oid, stack, pstack, attrs))
            if t == "5" and oid == "LAN_WLAN":
                out.append(f"[1,1,0,0,0,0]{idx}\nname=wlan0\nSSID=Casa840\nstatus=Up\nX_TP_Band=2.4GHz\n"
                           f"BeaconType={st['beacon']}\nX_TP_PreSharedKey={st['psk']}\nX_TP_MACAddressControlRule={st['rule']}\n"
                           f"MACAddressControlEnabled={st['acl_en']}")
                out.append(f"[1,2,0,0,0,0]{idx}\nname=wlan5\nSSID=\nstatus=Disabled")
            elif t == "5" and oid == "LAN_HOST_ENTRY":
                out.append(f"[1,1,1,0,0,0]{idx}\nMACAddress=AA:AA:AA:AA:AA:01\nhostName=Telefono\nIPAddress=192.168.60.10")
                out.append(f"[1,1,2,0,0,0]{idx}\nMACAddress=BB:BB:BB:BB:BB:02\nhostName=Vecino\nIPAddress=192.168.60.11")
            elif t == "6" and oid == "LAN_WLAN_ASSOC_DEV":
                out.append(f"[1,1,0,1,0,0]{idx}\nAssociatedDeviceMACAddress=BB:BB:BB:BB:BB:02\nX_TP_HostName=Vecino")
            elif t == "6" and oid == "LAN_WLAN_MACTABLEENTRY":
                for n, e in st["table"].items():
                    out.append(f"[1,1,0,{n},0,0]{idx}\nEnabled={e['Enabled']}\nMACAddress={e['MACAddress']}\n"
                               f"Description={e['Description']}\nHostName={e['HostName']}")
            elif t == "2" and oid == "LAN_WLAN":
                for k, v in attrs.items():
                    if k == "X_TP_PreSharedKey": st["psk"] = v
                    if k == "BeaconType": st["beacon"] = v
                    if k == "X_TP_MACAddressControlRule": st["rule"] = v
                    if k == "MACAddressControlEnabled": st["acl_en"] = v
            elif t == "3" and oid == "LAN_WLAN_MACTABLEENTRY":
                n = max(st["table"], default=0) + 1
                st["table"][n] = dict(attrs)
                out.append(f"[1,1,0,{n},0,0]{idx}")
            elif t == "4" and oid == "LAN_WLAN_MACTABLEENTRY":
                st["table"].pop(int(stack.split(",")[3]), None)
        self.send_response(200); self.end_headers()
        self.wfile.write(("\n".join(out) + "\n[error]0").encode())


@pytest.fixture
def wr840n():
    FakeWR840N.st = {"beacon": "WPAand11i", "rule": "deny", "acl_en": "0", "psk": "vieja1234",
                     "table": {}, "log": []}
    srv = HTTPServer(("127.0.0.1", 0), FakeWR840N)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_wr840n_detected_and_wifi(wr840n):
    d = _detect.get_driver(wr840n, ADMIN)
    assert isinstance(d, TPLinkCgiDriver) and d.brand == "TP-Link"
    with d:
        nets = d.get_wifi_networks()
        assert [(n.ssid, n.band, n.enabled) for n in nets] == [("Casa840", Band.B24, True)]
        d.set_wifi_passwords([Band.B24], "Nueva&Clave=99")
    assert FakeWR840N.st["psk"] == "Nueva&Clave=99"
    sets = [l for l in FakeWR840N.st["log"] if l[0] == "2"]
    assert sets[-1][2] == "1,1,0,0,0,0" and list(sets[-1][4]) == ["X_TP_PreSharedKey"]


def test_wr840n_bad_password(wr840n):
    with pytest.raises(AuthError):
        _detect.get_driver(wr840n, "mala").login()


def test_wr840n_clients_block_unblock(wr840n):
    conn = service.Connection(wr840n, None, "admin", ADMIN)
    service.connect(conn)
    assert conn.brand_key == "tplink"
    devs = {x.mac: x for x in service.list_clients(conn)}
    vecino = devs["bb:bb:bb:bb:bb:02"]
    assert vecino.connection == "2.4GHz" and not vecino.blocked
    assert devs["aa:aa:aa:aa:aa:01"].connection == "Cable/inactivo"

    service.set_blocked(conn, vecino, True)
    st = FakeWR840N.st
    assert st["acl_en"] == "1" and st["rule"] == "deny"
    assert [e["MACAddress"] for e in st["table"].values()] == ["BB:BB:BB:BB:BB:02"]
    assert next(x for x in service.list_clients(conn) if x.mac == vecino.mac).blocked

    service.set_blocked(conn, vecino, False)
    assert st["table"] == {}
    assert not next(x for x in service.list_clients(conn) if x.mac == vecino.mac).blocked


def test_wr840n_whitelist_refused(wr840n):
    FakeWR840N.st.update(rule="allow", acl_en="1")
    conn = service.Connection(wr840n, "tplink", "admin", ADMIN)
    vecino = next(x for x in service.list_clients(conn) if x.name == "Vecino")
    with pytest.raises(NotSupportedError, match="Permitir"):
        service.set_blocked(conn, vecino, True)
    assert FakeWR840N.st["table"] == {}


def test_current_passwords(tenda_host, wr840n):
    t = service.current_passwords(service.Connection(tenda_host, "tenda", "admin", ADMIN))
    assert t[0].password == "vieja1234"
    FakeWR840N.st["psk"] = "clave840"
    w = service.current_passwords(service.Connection(wr840n, "tplink", "admin", ADMIN))
    assert w[0].password == "clave840"



# ------------------------------------------------ cambiar nombre (SSID) + clave
def test_tenda_rename_and_password(tenda_host):
    conn = service.Connection(tenda_host, "tenda", "admin", ADMIN)
    nets = service.connect(conn)
    r = service.change_wifi(conn, [nets[0]], "MiCasa Nueva", "Clave1234", "Clave1234")
    post = FakeTenda.last_post
    assert post["ssid"] == "MiCasa Nueva" and post["wrlPwd"] == "Clave1234"
    assert post["ssid_5g"] == "Casa_5G"                     # la otra banda no se tocó
    assert r["connect"] == ("MiCasa Nueva", "Clave1234")


def test_rename_only_keeps_password_for_autoconnect(tenda_host):
    conn = service.Connection(tenda_host, "tenda", "admin", ADMIN)
    nets = service.connect(conn)
    r = service.change_wifi(conn, [nets[0]], "SoloNombre", "", "")
    assert FakeTenda.last_post["wrlPwd"] == "vieja1234"
    assert r["connect"] == ("SoloNombre", "vieja1234")


def test_no_changes_rejected(tenda_host):
    conn = service.Connection(tenda_host, "tenda", "admin", ADMIN)
    nets = service.connect(conn)
    with pytest.raises(ValueError, match="No hay cambios"):
        service.change_wifi(conn, [nets[0]], nets[0].ssid, "", "")


def test_wr840n_rename(wr840n):
    conn = service.Connection(wr840n, "tplink", "admin", ADMIN)
    nets = service.connect(conn)
    service.change_wifi(conn, nets, "Casa 3", "Nueva12345", "Nueva12345")
    sets = [l for l in FakeWR840N.st["log"] if l[0] == "2" and l[1] == "LAN_WLAN"]
    assert sets[-1][4] == {"SSID": "Casa 3", "X_TP_PreSharedKey": "Nueva12345"}


def test_ssid_validation():
    from wifichanger.base import validate_ssid
    with pytest.raises(ValueError):
        validate_ssid("x" * 33)
    with pytest.raises(ValueError):
        validate_ssid("   ")
    validate_ssid("Casa de Saúl")


# ------------------------------------------------ verificación del proveedor y desbloqueo
from wifichanger import isp


def test_unlock_password():
    assert isp.check_unlock_password("juliolazaro1983")
    assert not isp.check_unlock_password("juliolazaro1984")
    assert not isp.check_unlock_password("")


def test_isp_reachable_local():
    assert isp.isp_reachable("127.0.0.1", timeout=1)


def test_isp_unreachable_when_ping_fails(monkeypatch):
    monkeypatch.setattr(isp, "_ping", lambda host, timeout: False)
    assert not isp.isp_reachable("10.99.0.1", timeout=1)      # ping sin respuesta = sin proveedor


def test_isp_ok_when_ping_answers(monkeypatch):
    monkeypatch.setattr(isp, "_ping", lambda host, timeout: True)
    assert isp.isp_reachable("10.99.0.1", timeout=1)


def test_isp_tcp_fallback_only_without_ping(monkeypatch):
    monkeypatch.setattr(isp, "_ping", lambda host, timeout: None)
    monkeypatch.setattr(isp, "_tcp_probe", lambda host, timeout: False)
    assert not isp.isp_reachable("10.99.0.1", timeout=1)
