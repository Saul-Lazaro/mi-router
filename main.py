"""Maxs Wifi — app Android (Kivy) para routers TP-Link y Tenda:
cambiar la clave Wi‑Fi, ver quién está conectado y bloquear o desbloquear equipos."""
import threading

from kivy.app import App
from kivy.clock import mainthread
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import BooleanProperty, ListProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.core.clipboard import Clipboard
from kivy.uix.popup import Popup
from kivy.utils import escape_markup

from wifichanger.base import RouterError
from wifichanger.service import (BRAND_KEYS, Connection, change_password, connect, current_passwords,
                                 list_clients, set_blocked, suggest_host)

Window.softinput_mode = "below_target"  # el teclado no tapa el campo activo

BG = (0.08, 0.09, 0.12, 1)
CARD = (0.13, 0.14, 0.18, 1)
BLUE = (0.13, 0.45, 0.85, 1)
RED = (0.78, 0.22, 0.22, 1)
GREEN = (0.2, 0.6, 0.35, 1)
MUTED = (0.6, 0.6, 0.65, 1)

KV = """
#:import dp kivy.metrics.dp

<Field@TextInput>:
    multiline: False
    write_tab: False
    size_hint_y: None
    height: dp(50)
    font_size: "16sp"
    padding: [dp(12), dp(14)]

<Section@Label>:
    size_hint_y: None
    height: dp(30)
    text_size: self.width, None
    halign: "left"
    bold: True
    color: 0.65, 0.8, 1, 1

<BigButton@Button>:
    size_hint_y: None
    height: dp(54)
    font_size: "17sp"
    background_normal: ""
    background_color: (0.13, 0.45, 0.85, 1) if not self.disabled else (0.3, 0.3, 0.35, 1)

<TabButton@ToggleButton>:
    group: "tabs"
    allow_no_selection: False
    background_normal: ""
    background_down: ""
    background_color: (0.13, 0.45, 0.85, 1) if self.state == "down" else (0.18, 0.19, 0.24, 1)
    font_size: "16sp"

<Root>:
    orientation: "vertical"
    canvas.before:
        Color:
            rgba: 0.08, 0.09, 0.12, 1
        Rectangle:
            pos: self.pos
            size: self.size

    # ---------- cabecera ----------
    BoxLayout:
        orientation: "vertical"
        size_hint_y: None
        height: dp(84)
        padding: dp(18), dp(8)
        BoxLayout:
            spacing: dp(10)
            Image:
                source: "icon.png"
                size_hint_x: None
                width: dp(44)
                allow_stretch: True
            Label:
                text: "Maxs Wifi"
                font_size: "24sp"
                bold: True
                text_size: self.size
                halign: "left"
                valign: "middle"
        Label:
            text: (root.brand_name + " · " + root.host_shown) if root.connected else "TP-Link · Tenda"
            color: 0.6, 0.6, 0.65, 1
            font_size: "14sp"

    # ---------- pestañas (solo tras conectar) ----------
    BoxLayout:
        size_hint_y: None
        height: dp(48) if root.connected else 0
        opacity: 1 if root.connected else 0
        disabled: not root.connected
        padding: dp(12), 0
        spacing: dp(6)
        TabButton:
            id: tab_wifi
            text: "Clave Wi‑Fi"
            state: "down"
            on_state: if self.state == "down": root.tab = "wifi"
        TabButton:
            text: "Dispositivos"
            on_state: if self.state == "down": root.show_devices()
        Button:
            text: "Salir"
            size_hint_x: None
            width: dp(76)
            background_normal: ""
            background_color: 0.25, 0.25, 0.3, 1
            on_release: root.disconnect()

    ScrollView:
        do_scroll_x: False
        BoxLayout:
            orientation: "vertical"
            padding: dp(18), dp(14)
            spacing: dp(10)
            size_hint_y: None
            height: self.minimum_height

            # ---------- login ----------
            BoxLayout:
                id: login
                orientation: "vertical"
                spacing: dp(10)
                size_hint_y: None
                height: self.minimum_height if not root.connected else 0
                opacity: 0 if root.connected else 1
                disabled: root.connected
                Section:
                    text: "Conectar al router"
                Field:
                    id: host
                    hint_text: "IP del router (ej. 192.168.0.1)"
                    input_filter: lambda t, undo: t if t in "0123456789.:" else ""
                Spinner:
                    id: brand
                    text: "Detectar marca"
                    values: ["Detectar marca", "TP-Link", "Tenda"]
                    size_hint_y: None
                    height: dp(50)
                Field:
                    id: user
                    text: "admin"
                    hint_text: "Usuario administrador"
                Field:
                    id: admin
                    password: True
                    hint_text: "Contraseña de administrador"
                    on_text_validate: root.do_connect()
                BigButton:
                    text: "Conectando…" if root.busy else "Conectar"
                    disabled: root.busy
                    on_release: root.do_connect()

            # ---------- pestaña Wi‑Fi ----------
            BoxLayout:
                orientation: "vertical"
                spacing: dp(10)
                size_hint_y: None
                height: self.minimum_height if root.connected and root.tab == "wifi" else 0
                opacity: 1 if root.connected and root.tab == "wifi" else 0
                disabled: not (root.connected and root.tab == "wifi")
                Section:
                    text: "Redes"
                BoxLayout:
                    id: nets
                    orientation: "vertical"
                    size_hint_y: None
                    height: self.minimum_height
                    spacing: dp(2)
                Button:
                    text: "Mostrar contraseña actual"
                    size_hint_y: None
                    height: dp(46)
                    background_normal: ""
                    background_color: 0.18, 0.19, 0.24, 1
                    disabled: root.busy
                    on_release: root.show_current_password()
                Section:
                    text: "Nueva clave"
                Field:
                    id: new_pw
                    password: True
                    hint_text: "Nueva clave Wi‑Fi (8 a 63 caracteres)"
                Field:
                    id: confirm
                    password: True
                    hint_text: "Repite la nueva clave"
                BoxLayout:
                    size_hint_y: None
                    height: dp(40)
                    CheckBox:
                        size_hint_x: None
                        width: dp(44)
                        on_active: new_pw.password = confirm.password = not self.active
                    Label:
                        text: "Mostrar clave"
                        text_size: self.size
                        valign: "middle"
                BigButton:
                    text: "Aplicando…" if root.busy else "Cambiar clave"
                    disabled: root.busy
                    on_release: root.do_change()

            # ---------- pestaña Dispositivos ----------
            BoxLayout:
                orientation: "vertical"
                spacing: dp(8)
                size_hint_y: None
                height: self.minimum_height if root.connected and root.tab == "devices" else 0
                opacity: 1 if root.connected and root.tab == "devices" else 0
                disabled: not (root.connected and root.tab == "devices")
                BoxLayout:
                    size_hint_y: None
                    height: dp(44)
                    Section:
                        text: root.devices_title
                        height: dp(44)
                    Button:
                        text: "Actualizar"
                        size_hint_x: None
                        width: dp(110)
                        background_normal: ""
                        background_color: 0.18, 0.19, 0.24, 1
                        disabled: root.busy
                        on_release: root.load_devices()
                BoxLayout:
                    id: devices
                    orientation: "vertical"
                    size_hint_y: None
                    height: self.minimum_height
                    spacing: dp(8)

            Label:
                text: root.status
                color: root.status_color
                text_size: self.width, None
                size_hint_y: None
                height: self.texture_size[1] + dp(16)
                halign: "left"
"""


def _wrap_label(text, color=(1, 1, 1, 1), font_size="15sp", bold=False, markup=False):
    lbl = Label(text=text, color=color, font_size=font_size, bold=bold, markup=markup,
                halign="left", valign="middle", size_hint_y=None)
    lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
             texture_size=lambda w, v: setattr(w, "height", v[1] + dp(2)))
    return lbl


class DeviceCard(BoxLayout):
    """Tarjeta de un dispositivo con su botón Bloquear/Desbloquear."""

    def __init__(self, device, on_action, **kw):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(78),
                         padding=(dp(12), dp(8)), spacing=dp(8), **kw)
        from kivy.graphics import Color, RoundedRectangle
        with self.canvas.before:
            Color(*CARD)
            self._bg = RoundedRectangle(radius=[dp(10)])
        self.bind(pos=lambda *_: setattr(self._bg, "pos", self.pos),
                  size=lambda *_: setattr(self._bg, "size", self.size))

        info = BoxLayout(orientation="vertical", spacing=dp(2))
        title = escape_markup(device.label) + ("  [color=8fc3ff](este teléfono)[/color]" if device.is_self else "")
        info.add_widget(_wrap_label(title, bold=True, markup=True))
        detail = " · ".join(x for x in (device.ip, device.connection, device.mac.upper()) if x)
        info.add_widget(_wrap_label(detail, color=MUTED, font_size="12sp"))
        state = "Bloqueado" if device.blocked else "Conectado"
        info.add_widget(_wrap_label(state, color=(RED if device.blocked else GREEN), font_size="12sp"))
        self.add_widget(info)

        btn = Button(text="Desbloquear" if device.blocked else "Bloquear",
                     size_hint=(None, None), size=(dp(118), dp(44)), pos_hint={"center_y": .5},
                     background_normal="", background_color=(GREEN if device.blocked else RED))
        if device.is_self and not device.blocked:
            btn.disabled = True
            btn.text = "Tú"
            btn.background_color = (0.3, 0.3, 0.35, 1)
        btn.bind(on_release=lambda *_: on_action(device))
        self.add_widget(btn)


class Root(BoxLayout):
    busy = BooleanProperty(False)
    connected = BooleanProperty(False)
    tab = StringProperty("wifi")
    brand_name = StringProperty("")
    host_shown = StringProperty("")
    devices_title = StringProperty("Dispositivos")
    status = StringProperty("")
    status_color = ListProperty([0.8, 0.8, 0.85, 1])

    def __init__(self, **kw):
        super().__init__(**kw)
        self.conn = None
        self.checks = []  # (CheckBox, WifiNetwork)
        self.ids.host.text = suggest_host()

    # --- utilidades --------------------------------------------------------
    def _run(self, work, on_ok, working="Trabajando…"):
        """Ejecuta `work` en segundo plano para no congelar la interfaz."""
        if self.busy:
            return
        self.busy = True
        self._msg(working, ok=None)
        self._show_wait(working)

        def target():
            try:
                result = work()
            except (RouterError, ValueError) as e:
                self._done(None, str(e))
            except Exception as e:  # red caída, timeout, respuesta inesperada
                self._done(None, f"No se pudo comunicar con el router: {e}")
            else:
                self._done(lambda: on_ok(result), None)

        threading.Thread(target=target, daemon=True).start()

    def _show_wait(self, text):
        box = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(10))
        box.add_widget(Label(text=text, font_size="18sp", bold=True))
        box.add_widget(Label(text="Por favor espera…", color=MUTED, font_size="14sp"))
        self._wait = Popup(title="", separator_height=0, content=box, size_hint=(0.85, None),
                           height=dp(170), auto_dismiss=False)
        self._wait.open()

    @mainthread
    def _done(self, callback, error):
        self.busy = False
        if getattr(self, "_wait", None) is not None:
            self._wait.dismiss()
            self._wait = None
        if error:
            self._msg(error, ok=False)
            self._alert("No se pudo completar", error)
        else:
            callback()

    def _alert(self, title, text):
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(12))
        box.add_widget(_wrap_label(text))
        ok = Button(text="Entendido", size_hint_y=None, height=dp(48),
                    background_normal="", background_color=BLUE)
        box.add_widget(ok)
        popup = Popup(title=title, content=box, size_hint=(0.9, None), height=dp(320))
        ok.bind(on_release=lambda *_: popup.dismiss())
        popup.open()

    def _password_popup(self, title, intro, rows):
        """rows: [(nombre_red, clave)]. Muestra cada clave con su botón Copiar."""
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        if intro:
            box.add_widget(_wrap_label(intro, color=MUTED, font_size="14sp"))
        for name, pw in rows:
            box.add_widget(_wrap_label(name, bold=True))
            line = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
            line.add_widget(Label(text=pw or "(sin clave)", font_size="20sp", bold=True,
                                  color=(1, 1, 1, 1), halign="left", valign="middle",
                                  text_size=(None, None)))
            copy = Button(text="Copiar", size_hint_x=None, width=dp(96),
                          background_normal="", background_color=BLUE, disabled=not pw)

            def do_copy(btn, value=pw):
                Clipboard.copy(value)
                btn.text = "Copiada"
            copy.bind(on_release=do_copy)
            line.add_widget(copy)
            box.add_widget(line)
        close = Button(text="Cerrar", size_hint_y=None, height=dp(48),
                       background_normal="", background_color=(0.25, 0.25, 0.3, 1))
        box.add_widget(close)
        height = dp(150) + len(rows) * dp(90) + (dp(60) if intro else 0)
        popup = Popup(title=title, content=box, size_hint=(0.92, None), height=min(height, dp(560)))
        close.bind(on_release=lambda *_: popup.dismiss())
        popup.open()

    def _msg(self, text, ok=True):
        self.status_color = {True: [0.45, 0.85, 0.5, 1], False: [1, 0.45, 0.4, 1],
                             None: [0.8, 0.8, 0.85, 1]}[ok]
        self.status = text

    def _confirm(self, title, text, yes_text, yes_color, on_yes):
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(12))
        box.add_widget(_wrap_label(text))
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))
        popup = Popup(title=title, content=box, size_hint=(0.9, None), height=dp(260), auto_dismiss=True)
        cancel = Button(text="Cancelar", background_normal="", background_color=(0.25, 0.25, 0.3, 1))
        yes = Button(text=yes_text, background_normal="", background_color=yes_color)
        cancel.bind(on_release=lambda *_: popup.dismiss())
        yes.bind(on_release=lambda *_: (popup.dismiss(), on_yes()))
        row.add_widget(cancel)
        row.add_widget(yes)
        box.add_widget(row)
        popup.open()

    # --- conexión ----------------------------------------------------------
    def do_connect(self):
        conn = Connection(self.ids.host.text, BRAND_KEYS[self.ids.brand.text],
                          self.ids.user.text, self.ids.admin.text)
        self._run(lambda: connect(conn), lambda nets: self._on_connected(conn, nets), "Accediendo al equipo…")

    def _on_connected(self, conn, nets):
        self.conn = conn
        self.brand_name = conn.brand_name
        self.host_shown = conn.host.strip()
        self._fill_networks(nets)
        self.ids.devices.clear_widgets()
        self.devices_title = "Dispositivos"
        self.ids.tab_wifi.state = "down"
        self.tab = "wifi"
        self.connected = True
        self._msg(f"Conectado a {conn.brand_name}.")

    def disconnect(self):
        self.connected = False
        self.conn = None
        self.ids.admin.text = ""
        self.ids.nets.clear_widgets()
        self.ids.devices.clear_widgets()
        self.checks = []
        self._msg("Sesión cerrada.", ok=None)

    # --- pestaña Wi‑Fi -----------------------------------------------------
    def _fill_networks(self, nets):
        self.ids.nets.clear_widgets()
        self.checks = []
        for n in nets:
            row = BoxLayout(size_hint_y=None, height=dp(44))
            cb = CheckBox(size_hint_x=None, width=dp(44), active=n.enabled and not n.guest)
            label = f"{n.ssid}  ·  {n.band.value}" + ("  ·  invitados" if n.guest else "")
            if not n.enabled:
                label += "  (apagada)"
            row.add_widget(cb)
            row.add_widget(_wrap_label(label))
            self.ids.nets.add_widget(row)
            self.checks.append((cb, n))

    def show_current_password(self):
        conn = self.conn

        def ok(nets):
            rows = [(f"{n.ssid}  ·  {n.band.value}", n.password) for n in nets if n.password is not None]
            if not rows:
                self._alert("Contraseña actual", "Este router no permite leer la clave actual desde la app.")
                return
            self._password_popup("Contraseña actual", None, rows)
            self._msg("Clave actual leída del router.", ok=None)

        self._run(lambda: current_passwords(conn), ok, "Leyendo la clave…")

    def do_change(self):
        selected = [n for cb, n in self.checks if cb.active]
        new_pw, confirm = self.ids.new_pw.text, self.ids.confirm.text
        conn = self.conn
        names = ", ".join(n.ssid for n in selected) or "—"

        def go():
            self._run(lambda: change_password(conn, selected, new_pw, confirm),
                      lambda msg: self._changed(msg, selected, new_pw), "Cambiando la clave…")

        if new_pw and new_pw == confirm and selected:
            self._confirm("Cambiar clave", f"Se cambiará la clave de: {names}.\n\nTodos los equipos "
                          "conectados (incluido este teléfono, si está en esa red) se desconectarán.",
                          "Cambiar", BLUE, go)
        else:
            go()  # deja que la validación muestre el error

    def _changed(self, message, networks, new_pw):
        self.ids.new_pw.text = self.ids.confirm.text = ""
        self._msg(message, ok=True)
        names = ", ".join(f"{n.ssid} ({n.band.value})" for n in networks)
        self._password_popup("Clave cambiada", message, [(f"Nueva clave de {names}", new_pw)])

    # --- pestaña Dispositivos ----------------------------------------------
    def show_devices(self):
        self.tab = "devices"
        if not self.ids.devices.children:
            self.load_devices()

    def load_devices(self):
        conn = self.conn
        self._run(lambda: list_clients(conn), self._fill_devices, "Buscando dispositivos…")

    def _fill_devices(self, devices):
        box = self.ids.devices
        box.clear_widgets()
        online = [d for d in devices if not d.blocked]
        blocked = [d for d in devices if d.blocked]
        self.devices_title = f"Conectados: {len(online)}" + (f"  ·  Bloqueados: {len(blocked)}" if blocked else "")
        if not devices:
            box.add_widget(_wrap_label("El router no informó dispositivos.", color=MUTED))
        for d in online + blocked:
            box.add_widget(DeviceCard(d, self.ask_toggle))
        self._msg("Lista actualizada.", ok=None)

    def ask_toggle(self, device):
        if device.blocked:
            self._confirm("Desbloquear", f"¿Permitir de nuevo el acceso a {device.label}?",
                          "Desbloquear", GREEN, lambda: self._toggle(device, False))
        else:
            self._confirm("Bloquear", f"¿Bloquear a {device.label} ({device.mac.upper()})?\n\n"
                          "Se desconectará de inmediato y no podrá volver a entrar hasta que lo desbloquees.",
                          "Bloquear", RED, lambda: self._toggle(device, True))

    def _toggle(self, device, block):
        conn = self.conn

        def work():
            msg = set_blocked(conn, device, block)
            return msg, list_clients(conn)

        def ok(result):
            msg, devices = result
            self._fill_devices(devices)
            self._msg(msg, ok=True)

        self._run(work, ok, "Bloqueando…" if block else "Desbloqueando…")


class MaxsWifiApp(App):
    title = "Maxs Wifi"

    def build(self):
        Window.clearcolor = BG
        Builder.load_string(KV)
        return Root()


if __name__ == "__main__":
    MaxsWifiApp().run()
