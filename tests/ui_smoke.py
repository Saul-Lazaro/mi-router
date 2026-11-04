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


def poke(dt):
    """Abre las ventanas emergentes y cambia de pestaña para ejercitar todo el KV."""
    try:
        app = main.App.get_running_app()
        root = app.root
        # pantalla de acceso: escribir la contraseña y elegir marca
        root.ids.admin.focus = True
        root.ids.admin.insert_text("clave123")
        assert root.ids.admin.text == "clave123", root.ids.admin.text
        root.ids.admin_show.active = True
        assert root.ids.admin.password is False
        root.brand_choice = "TP-Link"
        root.ids.admin.focus = False
        root._show_wait("Accediendo al equipo…")
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
