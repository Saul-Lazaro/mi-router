"""Arranca la app real en una pantalla virtual (xvfb) y la cierra a los pocos segundos.
Falla si Kivy lanza cualquier excepción al construir la interfaz."""
import os
import sys
import traceback

os.environ.setdefault("KIVY_NO_ARGS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kivy.config import Config  # noqa: E402

# Ventana con proporción de celular (alto > ancho), como en el teléfono
Config.set("graphics", "width", "540")
Config.set("graphics", "height", "1400")

from kivy.clock import Clock  # noqa: E402

import main  # noqa: E402

errors = []


def tap(widget):
    """Toque real (down + up) en el centro del widget, como un dedo en la pantalla."""
    from kivy.tests.common import UnitTestTouch
    x, y = widget.to_window(*widget.center)
    t = UnitTestTouch(x, y)
    t.touch_down()
    t.touch_up()


_waits = [0]


def poke(dt):
    app = main.App.get_running_app()
    sc = app.root.ids.scroll if app and app.root else None
    if sc is None or sc.width < 200 or sc.height < 200:   # la ventana aún no se dibuja
        _waits[0] += 1
        if _waits[0] < 30:
            Clock.schedule_once(poke, 1)
            return
    Clock.schedule_once(_poke, 1)                          # un segundo más para que todo se acomode


def _poke(dt):
    """Abre las ventanas emergentes y cambia de pestaña para ejercitar todo el KV."""
    try:
        app = main.App.get_running_app()
        root = app.root
        # toques reales: cada campo debe recibir el foco (= aparece el teclado) y el combo abrir
        from kivy.uix.popup import Popup
        for name in ("host", "user", "admin"):
            w = root.ids[name]
            tap(w)
            sc = root.ids.scroll; ct = root.ids.content
            assert w.focus, (f"el toque no llegó al campo '{name}' win={app.root_window.size} "
                             f"scroll={sc.pos},{sc.size} sy={sc.scroll_y} content={ct.pos},{ct.size} "
                             f"pad={ct.padding} field={w.pos},{w.size} at={w.to_window(*w.center)}")
            w.focus = False
        tap(root.ids.brand_btn)
        opened = [c for c in app.root_window.children if isinstance(c, Popup)]
        assert opened, "el combo de marca no abrió la lista"
        opened[0].dismiss()

        # pantalla de acceso: escribir la contraseña (con puntos), mostrarla y elegir marca
        f = root.ids.admin
        f.focus = True
        for ch in "clave123":
            f.insert_text(ch)
        assert f.secret == "clave123", f.secret
        assert f.text == "\u2022" * 8, f.text
        f.do_backspace()
        assert f.secret == "clave12", f.secret
        root.ids.admin_show.active = True
        assert f.text == "clave12", f.text
        f.insert_text("3")
        assert f.secret == "clave123", f.secret
        f.focus = False
        # desbloqueo de técnico: contraseña incorrecta y luego correcta
        root.ask_unlock()
        Clock.schedule_once(unlock_step, 0.6)   # dejar que la ventana de desbloqueo se prepare
        return
    except Exception:
        errors.append(traceback.format_exc())
    Clock.schedule_once(lambda dt: app.stop(), 1)


def unlock_step(dt):
    app = main.App.get_running_app()
    root = app.root
    try:
        uf = root._unlock_field
        uf.focus = True
        for ch in "mala1234":
            uf.insert_text(ch)
        assert uf.secret == "mala1234", (f"no se escribió en el campo de desbloqueo (secret={uf.secret!r} "
                                         f"text={uf.text!r} disabled={uf.disabled} readonly={uf.readonly} "
                                         f"type={type(uf).__name__} parent={type(uf.parent).__name__} masked={uf.masked})")
        root._unlock_ok.dispatch("on_release")
        assert not root.unlocked, "una contraseña incorrecta no debe desbloquear"
        uf.focus = True
        for ch in "juliolazaro1983":
            uf.insert_text(ch)
        root._unlock_ok.dispatch("on_release")
        assert root.unlocked, f"la contraseña correcta debe desbloquear (secret={uf.secret!r} text={uf.text!r})"
        assert root.ids.unlock_btn.text == "Modo técnico activo"
        root._unlock_popup.dismiss(animation=False) if root._unlock_popup.parent else None
        root.choose_brand()
        root.brand_choice = "TP-Link"
        root._show_wait("Accediendo al equipo…")
        root._progress("Buscando el router…")
        root._wait.dismiss()
        root._alert("Prueba", "Mensaje de prueba")
        root._password_popup("Clave cambiada", "Prueba", [("Red · 2.4GHz", "Clave1234")])
        root._confirm("Bloquear", "¿Seguro?", "Bloquear", main.RED, lambda: None)
        from wifichanger.base import Band, ClientDevice, WifiNetwork
        root._on_connected(type("C", (), {"brand_name": "TP-Link", "host": "192.168.0.1"})(),
                           [WifiNetwork(Band.B24, "Casa", True, "WPA2", password="x")])
        Clock.schedule_once(wifi_step, 0.6)   # dejar que la pestaña Wi-Fi se acomode
        return
    except Exception:
        errors.append(traceback.format_exc())
    Clock.schedule_once(lambda dt: app.stop(), 1)


def wifi_step(dt):
    app = main.App.get_running_app()
    root = app.root
    try:
        from kivy.uix.modalview import ModalView
        from wifichanger.base import ClientDevice
        for w in list(app.root_window.children):     # cerrar las ventanas de prueba anteriores
            if isinstance(w, ModalView):
                w.dismiss(animation=False)
        Clock.schedule_once(wifi_taps, 0.4)
        return
    except Exception:
        errors.append(traceback.format_exc())
    Clock.schedule_once(lambda dt: app.stop(), 1)


def wifi_taps(dt):
    app = main.App.get_running_app()
    root = app.root
    from wifichanger.base import ClientDevice
    try:
        # pestaña Wi-Fi: elegir red con un toque, nombre precargado, ventana final
        net_btn = root.checks[0][0]
        assert root.ids.new_ssid.text == "Casa"
        tap(net_btn)
        assert net_btn.state == "normal"
        tap(net_btn)
        assert net_btn.state == "down" and root.ids.new_ssid.text == "Casa"
        # el nombre de la red está bloqueado hasta tocar "Cambiar"
        tap(root.ids.new_ssid)
        assert not root.ids.new_ssid.focus and root.ids.new_ssid.disabled, "el nombre debería estar bloqueado"
        tap(root.ids.ssid_btn)
        assert root.ssid_edit and not root.ids.new_ssid.disabled, "Cambiar no habilitó el nombre"
        assert root.ids.ssid_btn.text == "Cancelar"
        tap(root.ids.new_ssid)
        assert root.ids.new_ssid.focus, "el toque no llegó al campo del nombre de red"
        root.ids.new_ssid.focus = False
        root.ids.new_ssid.text = "Otro nombre"
        tap(root.ids.ssid_btn)                       # Cancelar: vuelve al nombre actual y bloquea
        assert not root.ssid_edit and root.ids.new_ssid.text == "Casa"
        root._changed({"message": "Se cambió nombre y clave de Casa (2.4GHz).",
                       "ssid": "Casa 2", "password": "Clave1234", "connect": ("Casa 2", "Clave1234")})
        root.tab = "devices"
        root._fill_devices([ClientDevice("aa:bb:cc:dd:ee:ff", "192.168.0.5", "PC", "2.4GHz"),
                            ClientDevice("11:22:33:44:55:66", "", "Bloq", "", blocked=True)])
        root.disconnect()
    except Exception:
        errors.append(traceback.format_exc())
    Clock.schedule_once(lambda dt: app.stop(), 1)


Clock.schedule_once(poke, 3)
try:
    main.MaxsWifiApp().run()
except Exception:
    errors.append(traceback.format_exc())

if errors:
    print("\n".join(errors))
    sys.exit(1)
print("UI OK")
