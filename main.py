"""Maxs Wifi — app Android (Kivy) para routers TP-Link y Tenda:
cambiar la clave Wi‑Fi, ver quién está conectado y bloquear o desbloquear equipos."""
import threading

from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.checkbox import CheckBox
from kivy.uix.label import Label
from kivy.core.clipboard import Clipboard
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput
from kivy.utils import escape_markup

from wifichanger.base import RouterError
from wifichanger.android_wifi import connect_to_wifi
from wifichanger.service import (BRAND_KEYS, Connection, change_wifi, connect, current_passwords,
                                 list_clients, set_blocked, suggest_host)

Window.softinput_mode = ""  # sin desplazamiento de Kivy: Android ajusta la ventana y los toques llegan al campo correcto

# Paleta tomada del logo de Group S & M (verdes)
BG = (0.945, 0.969, 0.933, 1)       # verde muy claro de fondo
CARD = (1, 1, 1, 1)                 # tarjetas blancas
DARK = (0.106, 0.420, 0.208, 1)     # verde oscuro del logo
GREEN = (0.180, 0.620, 0.310, 1)    # verde principal
LIME = (0.553, 0.776, 0.247, 1)     # verde lima del logo
BLUE = GREEN                        # (compatibilidad: botones principales)
RED = (0.839, 0.271, 0.271, 1)
TEXT = (0.122, 0.176, 0.141, 1)
MUTED = (0.42, 0.48, 0.44, 1)

KV = """
#:import dp kivy.metrics.dp

#:set BG (0.945, 0.969, 0.933, 1)
#:set DARK (0.106, 0.420, 0.208, 1)
#:set GREEN (0.180, 0.620, 0.310, 1)
#:set LIME (0.553, 0.776, 0.247, 1)
#:set TEXT (0.122, 0.176, 0.141, 1)
#:set MUTED (0.42, 0.48, 0.44, 1)
#:set GRAY (0.70, 0.74, 0.71, 1)

<Popup>:
    background: ""
    background_color: 1, 1, 1, 1
    title_color: 0.106, 0.420, 0.208, 1
    title_size: "19sp"
    separator_color: 0.553, 0.776, 0.247, 1
    separator_height: dp(3)
    overlay_color: 0, 0.12, 0.05, 0.55

<Field>:
    multiline: False
    write_tab: False
    size_hint_y: None
    height: dp(44)
    font_size: "15sp"
    padding: [dp(12), dp(11)]
    background_normal: ""
    background_active: ""
    background_color: (0.93, 0.95, 0.92, 1) if self.disabled else (1, 1, 1, 1)
    disabled_foreground_color: TEXT
    foreground_color: TEXT
    hint_text_color: MUTED
    cursor_color: GREEN
    halign: "center"
    keyboard_suggestions: False
    input_type: "text"
    canvas.after:
        Color:
            rgba: GREEN if self.focus else (0.80, 0.88, 0.78, 1)
        Line:
            width: dp(1.4) if self.focus else dp(1)
            rounded_rectangle: (self.x, self.y, self.width, self.height, dp(12))

<Section@Label>:
    size_hint_y: None
    height: dp(32)
    text_size: self.width, None
    halign: "center"
    bold: True
    font_size: "17sp"
    color: DARK

<RoundButton>:
    background_normal: ""
    background_down: ""
    background_color: 0, 0, 0, 0
    color: 1, 1, 1, 1
    bold: True
    canvas.before:
        Color:
            rgba: GRAY if self.disabled else ((self.bg[0] * 0.85, self.bg[1] * 0.85, self.bg[2] * 0.85, 1) if self.state == "down" else self.bg)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [self.radius]

<BigButton@RoundButton>:
    size_hint_y: None
    height: dp(50)
    font_size: "17sp"

<SoftButton@RoundButton>:
    bg: 0.86, 0.93, 0.83, 1
    color: DARK
    size_hint_y: None
    height: dp(48)
    font_size: "16sp"

<TabButton@ToggleButton>:
    group: "tabs"
    allow_no_selection: False
    background_normal: ""
    background_down: ""
    background_color: 0, 0, 0, 0
    bold: True
    font_size: "16sp"
    color: (1, 1, 1, 1) if self.state == "down" else DARK
    canvas.before:
        Color:
            rgba: GREEN if self.state == "down" else (0.86, 0.93, 0.83, 1)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(12)]

<FieldLabel@Label>:
    size_hint_y: None
    height: dp(20)
    color: 0.106, 0.420, 0.208, 1
    bold: True
    font_size: "14sp"
    halign: "center"
    valign: "bottom"
    text_size: self.size

<NetToggle>:
    size_hint_y: None
    height: dp(52)
    background_normal: ""
    background_down: ""
    background_color: 0, 0, 0, 0
    halign: "center"
    valign: "middle"
    text_size: self.width - dp(20), None
    font_size: "15sp"
    bold: self.state == "down"
    color: (1, 1, 1, 1) if self.state == "down" else (0.122, 0.176, 0.141, 1)
    canvas.before:
        Color:
            rgba: (0.180, 0.620, 0.310, 1) if self.state == "down" else (0.93, 0.96, 0.92, 1)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(12)]

<BrandButton@TabButton>:
    group: "brand"
    font_size: "15sp"

<Card@BoxLayout>:
    orientation: "vertical"
    size_hint_y: None
    height: self.minimum_height
    padding: dp(16), dp(14)
    spacing: dp(10)
    canvas.before:
        Color:
            rgba: 0, 0.25, 0.1, 0.06
        RoundedRectangle:
            pos: self.x, self.y - dp(2)
            size: self.size
            radius: [dp(18)]
        Color:
            rgba: 1, 1, 1, 1
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(18)]

<Root>:
    orientation: "vertical"
    canvas.before:
        Color:
            rgba: BG
        Rectangle:
            pos: self.pos
            size: self.size

    # ---------- franja verde superior (sin logo ni nombre) ----------
    BoxLayout:
        size_hint_y: None
        height: dp(64) if root.connected else 0
        opacity: 1 if root.connected else 0
        padding: dp(16), dp(10), dp(16), dp(12)
        canvas.before:
            Color:
                rgba: DARK
            Rectangle:
                pos: self.pos
                size: self.size
            Color:
                rgba: LIME
            Rectangle:
                pos: self.x, self.y
                size: self.width, dp(5)
        Label:
            text: ("Conectado a " + root.brand_name + " · " + root.host_shown) if root.connected else ""
            font_size: "14sp"
            bold: True
            color: 0.92, 0.98, 0.88, 1
            halign: "center"
            valign: "bottom"
            text_size: self.size
            shorten: True

    # ---------- pestañas (solo tras conectar) ----------
    BoxLayout:
        id: tabs_bar
        size_hint_y: None
        height: dp(62)
        padding: dp(14), dp(10), dp(14), dp(4)
        spacing: dp(8)
        TabButton:
            id: tab_wifi
            text: "Clave Wi‑Fi"
            state: "down"
            on_state: if self.state == "down": root.tab = "wifi"
        TabButton:
            text: "Dispositivos"
            on_state: if self.state == "down": root.show_devices()
        RoundButton:
            text: "Salir"
            bg: 0.55, 0.60, 0.57, 1
            size_hint_x: None
            width: dp(78)
            on_release: root.disconnect()

    ScrollView:
        id: scroll
        do_scroll_x: False
        BoxLayout:
            id: content
            orientation: "vertical"
            padding: max(dp(16), (self.width - dp(430)) / 2), root.top_pad, max(dp(16), (self.width - dp(430)) / 2), dp(20)
            spacing: dp(12)
            size_hint_y: None
            height: self.minimum_height

            # ---------- inicio de sesión ----------
            Card:
                id: login
                spacing: dp(8)
                Label:
                    text: "Acceder al router"
                    color: DARK
                    bold: True
                    font_size: "20sp"
                    size_hint_y: None
                    height: dp(30)
                    halign: "center"
                    text_size: self.width, None
                Label:
                    text: "Conéctate al Wi‑Fi del router y escribe los datos de su panel."
                    color: MUTED
                    font_size: "13sp"
                    halign: "center"
                    size_hint_y: None
                    text_size: self.width, None
                    height: self.texture_size[1] + dp(6)
                FieldLabel:
                    text: "IP del router"
                Field:
                    id: host
                    halign: "center"
                    hint_text: "ej. 192.168.0.1"
                    input_filter: lambda t, undo: t if t in "0123456789.:" else ""
                    text_validate_unfocus: False
                    on_text_validate: user.focus = True
                FieldLabel:
                    text: "Usuario"
                Field:
                    id: user
                    halign: "center"
                    text: "admin"
                    hint_text: "normalmente admin"
                    text_validate_unfocus: False
                    on_text_validate: admin.focus = True
                FieldLabel:
                    text: "Contraseña"
                PasswordField:
                    id: admin
                    halign: "center"
                    masked: not admin_show.active
                    hint_text: "contraseña del panel del router"
                    on_text_validate: root.do_connect()
                AnchorLayout:
                    size_hint_y: None
                    height: dp(30)
                    BoxLayout:
                        size_hint_x: None
                        width: dp(210)
                        CheckBox:
                            id: admin_show
                            size_hint_x: None
                            width: dp(40)
                            color: DARK
                        Label:
                            text: "Mostrar contraseña"
                            color: TEXT
                            text_size: self.size
                            halign: "left"
                            valign: "middle"
                FieldLabel:
                    text: "Marca del router"
                Button:
                    id: brand_btn
                    text: root.brand_choice
                    size_hint_y: None
                    height: dp(44)
                    font_size: "15sp"
                    bold: True
                    color: DARK
                    background_normal: ""
                    background_down: ""
                    background_color: 0, 0, 0, 0
                    on_release: root.choose_brand()
                    canvas.before:
                        Color:
                            rgba: 1, 1, 1, 1
                        RoundedRectangle:
                            pos: self.pos
                            size: self.size
                            radius: [dp(12)]
                        Color:
                            rgba: 0.80, 0.88, 0.78, 1
                        Line:
                            width: dp(1)
                            rounded_rectangle: (self.x, self.y, self.width, self.height, dp(12))
                    canvas.after:
                        Color:
                            rgba: DARK
                        Triangle:
                            points: [self.right - dp(30), self.center_y + dp(4), self.right - dp(16), self.center_y + dp(4), self.right - dp(23), self.center_y - dp(5)]
                Widget:
                    size_hint_y: None
                    height: dp(2)
                BigButton:
                    text: "Accediendo…" if root.busy else "Conectar"
                    disabled: root.busy
                    on_release: root.do_connect()

            # ---------- pestaña Wi‑Fi ----------
            BoxLayout:
                orientation: "vertical"
                spacing: dp(14)
                size_hint_y: None
                id: wifi_tab
                height: self.minimum_height
                Card:
                    Section:
                        text: "Mis redes Wi‑Fi"
                    Label:
                        text: "Toca una red para elegirla"
                        color: MUTED
                        font_size: "13sp"
                        size_hint_y: None
                        height: dp(18)
                        halign: "center"
                        text_size: self.size
                    BoxLayout:
                        id: nets
                        orientation: "vertical"
                        size_hint_y: None
                        height: self.minimum_height
                        spacing: dp(8)
                    SoftButton:
                        text: "Mostrar contraseña actual"
                        disabled: root.busy
                        on_release: root.show_current_password()
                Card:
                    Section:
                        text: "Nombre y clave"
                    FieldLabel:
                        text: "Nombre de la red (SSID)"
                    BoxLayout:
                        size_hint_y: None
                        height: dp(44)
                        spacing: dp(8)
                        Field:
                            id: new_ssid
                            hint_text: "nombre de la red Wi‑Fi"
                            disabled: not root.ssid_edit
                        RoundButton:
                            id: ssid_btn
                            text: "Cancelar" if root.ssid_edit else "Cambiar"
                            bg: (0.55, 0.60, 0.57, 1) if root.ssid_edit else (0.180, 0.620, 0.310, 1)
                            size_hint_x: None
                            width: dp(100)
                            font_size: "15sp"
                            on_release: root.toggle_ssid_edit()
                    FieldLabel:
                        text: "Nueva clave"
                    PasswordField:
                        id: new_pw
                        hint_text: "vacío = no cambiar la clave"
                    PasswordField:
                        id: confirm
                        hint_text: "repite la nueva clave"
                    AnchorLayout:
                        size_hint_y: None
                        height: dp(32)
                        BoxLayout:
                            size_hint_x: None
                            width: dp(170)
                            CheckBox:
                                size_hint_x: None
                                width: dp(40)
                                color: DARK
                                on_active: new_pw.masked = confirm.masked = not self.active
                            Label:
                                text: "Mostrar clave"
                                color: TEXT
                                text_size: self.size
                                halign: "left"
                                valign: "middle"
                    BigButton:
                        text: "Aplicando…" if root.busy else "Guardar cambios"
                        disabled: root.busy
                        on_release: root.do_change()
                    Label:
                        text: "Al guardar, el celular se conectará solo a la red con el nombre y la clave nuevos."
                        color: MUTED
                        font_size: "12sp"
                        halign: "center"
                        size_hint_y: None
                        text_size: self.width, None
                        height: self.texture_size[1] + dp(4)

            # ---------- pestaña Dispositivos ----------
            BoxLayout:
                orientation: "vertical"
                spacing: dp(10)
                size_hint_y: None
                id: dev_tab
                height: self.minimum_height
                BoxLayout:
                    size_hint_y: None
                    height: dp(46)
                    spacing: dp(8)
                    Section:
                        text: root.devices_title
                        height: dp(46)
                    SoftButton:
                        text: "Actualizar"
                        size_hint_x: None
                        width: dp(118)
                        height: dp(44)
                        disabled: root.busy
                        on_release: root.load_devices()
                BoxLayout:
                    id: devices
                    orientation: "vertical"
                    size_hint_y: None
                    height: self.minimum_height
                    spacing: dp(10)

            Label:
                text: root.status
                color: root.status_color
                font_size: "14sp"
                text_size: self.width, None
                size_hint_y: None
                height: (self.texture_size[1] + dp(8)) if root.status else 0
                halign: "center"

            # ---------- logo grande al pie (inicio de sesión y dentro de la app) ----------
            Card:
                padding: dp(12), dp(12)
                Image:
                    source: "logo.png"
                    size_hint_y: None
                    height: dp(150)
                    allow_stretch: True
                    keep_ratio: True
                Label:
                    text: "Group S & M · Max's Solution E.I.R.L."
                    color: MUTED
                    font_size: "12sp"
                    size_hint_y: None
                    height: dp(18)
"""


class Field(TextInput):
    """Campo de texto con el estilo de la app (ver regla <Field> en el KV)."""


class PasswordField(Field):
    """Contraseña con puntos dibujados por la app.

    En varios teclados de Android el modo `password` nativo de Kivy no deja escribir;
    aquí el campo es de texto normal y la clave real se guarda en `secret`."""
    secret = StringProperty("")
    masked = BooleanProperty(True)
    MASK = "\u2022"

    def __init__(self, **kw):
        self._sync = False
        super().__init__(**kw)
        self.bind(text=self._on_text_changed, masked=lambda *_: self._refresh())

    def _refresh(self):
        self._sync = True
        self.text = (self.MASK * len(self.secret)) if self.masked else self.secret
        self.cursor = (len(self.text), 0)
        self._sync = False

    def _on_text_changed(self, *_):
        if self._sync:
            return
        if not self.masked:
            self.secret = self.text
            return
        # Edición con máscara: lo que no sea punto es texto nuevo; si se acortó, se borró al final
        new = self.text.replace(self.MASK, "")
        kept = min(self.text.count(self.MASK), len(self.secret))
        self.secret = self.secret[:kept] + new
        self._refresh()

    def clear(self):
        self.secret = ""
        self._refresh()


class NetToggle(ToggleButton):
    """Red Wi‑Fi como botón: verde = elegida."""
    pass


class RoundButton(Button):
    """Botón redondeado; `bg` y `radius` son propiedades reales para que el KV
    pueda usarlas en el canvas desde el primer dibujo."""
    bg = ListProperty([0.180, 0.620, 0.310, 1])
    radius = NumericProperty(dp(14))


def _wrap_label(text, color=TEXT, font_size="15sp", bold=False, markup=False):
    lbl = Label(text=text, color=color, font_size=font_size, bold=bold, markup=markup,
                halign="left", valign="middle", size_hint_y=None)
    lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
             texture_size=lambda w, v: setattr(w, "height", v[1] + dp(2)))
    return lbl


class DeviceCard(BoxLayout):
    """Tarjeta de un dispositivo con su botón Bloquear/Desbloquear."""

    def __init__(self, device, on_action, **kw):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(84),
                         padding=(dp(12), dp(8)), spacing=dp(8), **kw)
        from kivy.graphics import Color, RoundedRectangle
        with self.canvas.before:
            Color(*CARD)
            self._bg = RoundedRectangle(radius=[dp(16)])
        self.bind(pos=lambda *_: setattr(self._bg, "pos", self.pos),
                  size=lambda *_: setattr(self._bg, "size", self.size))

        info = BoxLayout(orientation="vertical", spacing=dp(2))
        title = escape_markup(device.label) + ("  [color=2e9e4f](este teléfono)[/color]" if device.is_self else "")
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
    brand_choice = StringProperty("Detectar marca")
    top_pad = NumericProperty(dp(28))
    ssid_edit = BooleanProperty(False)   # el nombre de la red solo se edita tras tocar "Cambiar"
    status_color = ListProperty(list(MUTED))

    def __init__(self, **kw):
        super().__init__(**kw)
        self.conn = None
        self.checks = []  # (CheckBox, WifiNetwork)
        self.ids.host.text = suggest_host()
        # Las secciones que no se ven se QUITAN de la pantalla (no solo se ocultan):
        # ocultas pero presentes, se quedaban con los toques de la pantalla de acceso.
        self._sections = {k: self.ids[k].__self__ for k in ("login", "wifi_tab", "dev_tab", "tabs_bar")}
        self.bind(connected=self._layout, tab=self._layout)
        self._layout()
        # Centrado vertical: margen superior = espacio libre / 2 (sin contenedores extra,
        # que interferían con los toques dentro del ScrollView)
        content = self.ids.content
        content.bind(minimum_height=self._center, children=self._center)
        self.ids.scroll.bind(height=self._center)

    def _center(self, *_):
        content = self.ids.content
        kids = content.children
        used = sum(c.height for c in kids) + content.spacing * max(len(kids) - 1, 0) + dp(20)
        base = dp(12) if self.connected else dp(28)
        self.top_pad = max(base, (self.ids.scroll.height - used) / 2)

    def _layout(self, *_):
        content = self.ids.content
        login, wifi, dev, tabs = (self._sections[k] for k in ("login", "wifi_tab", "dev_tab", "tabs_bar"))
        wanted = ([wifi] if self.tab == "wifi" else [dev]) if self.connected else [login]
        for w in (login, wifi, dev):
            if w.parent is not None and w not in wanted:
                w.parent.remove_widget(w)
        for w in wanted:
            if w.parent is None:
                content.add_widget(w, index=len(content.children))  # arriba del todo
        if self.connected and tabs.parent is None:
            self.add_widget(tabs, index=1)        # entre la cabecera y el contenido
        elif not self.connected and tabs.parent is not None:
            tabs.parent.remove_widget(tabs)

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
        box.add_widget(Label(text=text, font_size="19sp", bold=True, color=DARK))
        self._wait_step = Label(text="Por favor espera…", color=MUTED, font_size="14sp",
                                halign="center", valign="middle")
        self._wait_step.bind(size=lambda w, v: setattr(w, "text_size", v))
        box.add_widget(self._wait_step)
        self._wait = Popup(title="", separator_height=0, content=box, size_hint=(0.85, None),
                           height=dp(170), auto_dismiss=False)
        self._wait.open()

    @mainthread
    def _progress(self, text):
        """Paso actual de una operación larga (se llama desde el hilo de trabajo)."""
        self._msg(text, ok=None)
        if getattr(self, "_wait", None) is not None:
            self._wait_step.text = text

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
                                  color=DARK, halign="left", valign="middle",
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
                       background_normal="", background_color=(0.55, 0.60, 0.57, 1))
        box.add_widget(close)
        height = dp(150) + len(rows) * dp(90) + (dp(60) if intro else 0)
        popup = Popup(title=title, content=box, size_hint=(0.92, None), height=min(height, dp(560)))
        close.bind(on_release=lambda *_: popup.dismiss())
        popup.open()

    def _msg(self, text, ok=True):
        self.status_color = {True: list(DARK), False: list(RED), None: list(MUTED)}[ok]
        self.status = text

    def _confirm(self, title, text, yes_text, yes_color, on_yes):
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(12))
        box.add_widget(_wrap_label(text))
        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))
        popup = Popup(title=title, content=box, size_hint=(0.9, None), height=dp(260), auto_dismiss=True)
        cancel = Button(text="Cancelar", background_normal="", background_color=(0.55, 0.60, 0.57, 1))
        yes = Button(text=yes_text, background_normal="", background_color=yes_color)
        cancel.bind(on_release=lambda *_: popup.dismiss())
        yes.bind(on_release=lambda *_: (popup.dismiss(), on_yes()))
        row.add_widget(cancel)
        row.add_widget(yes)
        box.add_widget(row)
        popup.open()

    # --- conexión ----------------------------------------------------------
    def choose_brand(self):
        """Combo desplegable de marca (lista en ventana emergente: funciona con cualquier toque)."""
        box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        popup = Popup(title="Marca del router", content=box, size_hint=(0.8, None), height=dp(290))
        for option in BRAND_KEYS:
            btn = RoundButton(text=option, size_hint_y=None, height=dp(48), font_size="16sp",
                              bg=list(GREEN) if option == self.brand_choice else [0.86, 0.93, 0.83, 1],
                              color=(1, 1, 1, 1) if option == self.brand_choice else DARK)

            def pick(_b, value=option):
                self.brand_choice = value
                popup.dismiss()
            btn.bind(on_release=pick)
            box.add_widget(btn)
        popup.open()

    def do_connect(self):
        conn = Connection(self.ids.host.text, BRAND_KEYS[self.brand_choice],
                          self.ids.user.text, self.ids.admin.secret)
        self._run(lambda: connect(conn, self._progress), lambda nets: self._on_connected(conn, nets),
                  "Accediendo al equipo…")

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
        self.ids.admin.clear()
        self.ids.nets.clear_widgets()
        self.ids.devices.clear_widgets()
        self.checks = []
        self._msg("Sesión cerrada.", ok=None)

    # --- pestaña Wi‑Fi -----------------------------------------------------
    def _fill_networks(self, nets):
        self.ids.nets.clear_widgets()
        self.checks = []
        first = True
        for n in nets:
            label = f"{n.ssid}  ·  {n.band.value}" + ("  ·  invitados" if n.guest else "")
            if not n.enabled:
                label += "  (apagada)"
            choose = n.enabled and not n.guest and first
            btn = NetToggle(text=label, state="down" if choose else "normal")
            first = first and not choose
            btn.bind(state=lambda *_: self._prefill_ssid())
            self.ids.nets.add_widget(btn)
            self.checks.append((btn, n))
        self._prefill_ssid()

    def _selected_networks(self):
        return [n for b, n in self.checks if b.state == "down"]

    def _prefill_ssid(self):
        sel = self._selected_networks()
        self.ssid_edit = False                      # al cambiar de red, el nombre vuelve a bloquearse
        self.ids.new_ssid.text = sel[0].ssid if sel else ""

    def toggle_ssid_edit(self):
        if self.ssid_edit:                          # Cancelar: vuelve al nombre actual y bloquea
            self._prefill_ssid()
            return
        if not self._selected_networks():
            self._alert("Elige una red", "Toca primero la red Wi‑Fi cuyo nombre quieres cambiar.")
            return
        self.ssid_edit = True
        field = self.ids.new_ssid
        Clock.schedule_once(lambda dt: setattr(field, "focus", True), 0.1)

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
        selected = self._selected_networks()
        new_ssid = self.ids.new_ssid.text if self.ssid_edit else ""
        new_pw, confirm = self.ids.new_pw.secret, self.ids.confirm.secret
        conn = self.conn
        names = ", ".join(n.ssid for n in selected) or "—"

        def go():
            self._run(lambda: change_wifi(conn, selected, new_ssid, new_pw, confirm),
                      self._changed, "Guardando cambios…")

        if not selected:
            self._alert("Elige una red", "Toca la red Wi‑Fi que quieres cambiar.")
            return
        changes = []
        if new_ssid.strip() and any(new_ssid.strip() != n.ssid for n in selected):
            changes.append(f"nombre: {new_ssid.strip()}")
        if new_pw or confirm:
            changes.append("clave nueva")
        if not changes:
            go()  # muestra "No hay cambios"
            return
        self._confirm("Guardar cambios", f"Red: {names}\nCambios: {', '.join(changes)}.\n\n"
                      "Los equipos conectados se desconectarán. Este celular se volverá a "
                      "conectar solo con los datos nuevos.", "Guardar", GREEN, go)

    def _changed(self, result):
        self.ids.new_pw.clear()
        self.ids.confirm.clear()
        self.ssid_edit = False
        self._msg(result["message"], ok=True)
        how = None
        if result.get("connect"):
            try:
                how = connect_to_wifi(*result["connect"])
            except Exception as e:  # nunca romper la app por esto
                how = None
                self._msg(f"{result['message']} (no se pudo conectar automáticamente: {e})", ok=False)
        note = {
            "dialog": "Toca «Guardar» en la ventana de Android: el celular se conectará solo a la red nueva.",
            "suggestion": "Android conectará el celular a la red nueva automáticamente en unos segundos.",
            "direct": "El celular se está conectando a la red nueva…",
        }.get(how, "Conecta el celular a la red con estos datos:")
        rows = [("Nombre de la red", result["ssid"])]
        if result.get("password"):
            rows.append(("Clave", result["password"]))
        self._password_popup("Cambios guardados", result["message"] + "\n\n" + note, rows)

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
