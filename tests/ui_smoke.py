"""Arranca la app real en una pantalla virtual (xvfb) y la cierra a los pocos segundos.
Falla si Kivy lanza cualquier excepción al construir la interfaz."""
import os
import sys
import traceback

os.environ.setdefault("KIVY_NO_ARGS", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def poke(dt):
    """Abre las ventanas emergentes y cambia de pestaña para ejercitar todo el KV."""
    try:
        app = main.App.get_running_app()
        root = app.root
        # toques reales: cada campo debe recibir el foco (= aparece el teclado) y el combo abrir
        from kivy.uix.popup import Popup
        for name in ("host", "user", "admin"):
            w = root.ids[name]
            tap(w)
            assert w.focus, f"el toque no llegó al campo '{name}'"
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
