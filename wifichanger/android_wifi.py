"""Conectar el celular a una red Wi‑Fi (Android), para no quedarse sin acceso tras
cambiar el nombre o la clave de la red.

- Android 11+ (API 30): ventana del sistema "¿Guardar esta red?" (ACTION_WIFI_ADD_NETWORKS).
  El usuario toca "Guardar" y Android se conecta solo cuando la red vuelve a estar disponible.
- Android 10 (API 29): sugerencia de red (WifiNetworkSuggestion); Android la usa automáticamente.
- Android 9 o anterior: WifiManager.addNetwork + enableNetwork (conexión directa).
"""
from __future__ import annotations

import os


def on_android() -> bool:
    return "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ


def connect_to_wifi(ssid: str, password: str) -> str | None:
    """Devuelve "dialog", "suggestion", "direct" o None (no es Android / falló)."""
    if not on_android() or not ssid or not password:
        return None
    from jnius import autoclass, cast  # solo existe en Android

    sdk = autoclass("android.os.Build$VERSION").SDK_INT
    activity = autoclass("org.kivy.android.PythonActivity").mActivity
    Context = autoclass("android.content.Context")

    def suggestion_list():
        builder = autoclass("android.net.wifi.WifiNetworkSuggestion$Builder")()
        suggestion = builder.setSsid(ssid).setWpa2Passphrase(password).build()
        lst = autoclass("java.util.ArrayList")()
        lst.add(suggestion)
        return lst

    if sdk >= 30:
        from android.runnable import run_on_ui_thread
        Intent = autoclass("android.content.Intent")
        Settings = autoclass("android.provider.Settings")
        intent = Intent(Settings.ACTION_WIFI_ADD_NETWORKS)
        intent.putParcelableArrayListExtra(Settings.EXTRA_WIFI_NETWORK_LIST, suggestion_list())
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        run_on_ui_thread(lambda: activity.startActivity(intent))()
        return "dialog"

    wm = cast("android.net.wifi.WifiManager",
              activity.getApplicationContext().getSystemService(Context.WIFI_SERVICE))
    if sdk == 29:
        wm.addNetworkSuggestions(suggestion_list())
        return "suggestion"

    conf = autoclass("android.net.wifi.WifiConfiguration")()
    conf.SSID = f'"{ssid}"'
    conf.preSharedKey = f'"{password}"'
    net_id = wm.addNetwork(conf)
    if net_id == -1:
        return None
    wm.disconnect()
    wm.enableNetwork(net_id, True)
    wm.reconnect()
    return "direct"
