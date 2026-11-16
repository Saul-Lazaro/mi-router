"""Actualización de la app desde el panel Red Maxs Solution (igual que APP Max Fibra).

API pública (no requiere iniciar sesión):
  GET https://redmax.maxsolution.pe/api/v1/app-wifi/version?version=<instalada>
      -> {"disponible": bool, "version": "x.y.z", "hay_nueva": bool, "obligatoria": bool,
          "notas": str|null, "tamano": bytes, "sha256": hex, "fecha": iso, "url": ".../descargar"}

Android: la descarga la hace el DownloadManager del sistema (progreso consultable, sin
permisos de almacenamiento) en la carpeta privada de la app; se verifica el SHA-256 y se abre
el instalador de Android con un content:// URI. Escritorio: descarga con requests.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading

import requests

from .version import __version__

API_URL = "https://redmax.maxsolution.pe/api/v1/app-wifi/version"
APK_MIME = "application/vnd.android.package-archive"


def parse_version(v: str) -> tuple:
    parts = []
    for p in str(v or "0").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def check_for_update(current: str = __version__, timeout: int = 8) -> dict | None:
    """Datos de la versión nueva, o None si no hay (o no se pudo consultar)."""
    try:
        r = requests.get(API_URL, params={"version": current}, timeout=timeout)
        info = r.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(info, dict) or not info.get("disponible") or not info.get("url"):
        return None
    if parse_version(info.get("version")) <= parse_version(current):
        return None  # nunca "actualizar" a una versión igual o más vieja
    return info


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def apk_name(info: dict) -> str:
    return f"MaxsWifi-{info.get('version', 'nueva')}.apk"


# ---------------------------------------------------------------- escritorio / pruebas
def download_desktop(info: dict, on_progress, on_done, on_error, folder: str | None = None):
    """Descarga en un hilo con requests (para escritorio y pruebas)."""
    def work():
        path = os.path.join(folder or tempfile.gettempdir(), apk_name(info))
        try:
            with requests.get(info["url"], stream=True, timeout=30) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or info.get("tamano") or 0)
                done = 0
                with open(path, "wb") as fh:
                    for chunk in r.iter_content(64 * 1024):
                        fh.write(chunk)
                        done += len(chunk)
                        on_progress(done, total)
            if info.get("sha256") and sha256_file(path).lower() != str(info["sha256"]).lower():
                os.remove(path)
                raise ValueError("El archivo descargado está dañado (la verificación no coincide).")
            on_done(path)
        except Exception as e:  # noqa: BLE001
            on_error(str(e))
    threading.Thread(target=work, daemon=True).start()


# ---------------------------------------------------------------- Android
def _call_ids(method, download_id):
    """Métodos Java `long...` (setFilterById, remove): pyjnius ya arma el arreglo con los
    argumentos sueltos; pasar una lista lo anida y da «an integer is required»."""
    try:
        return method(int(download_id))
    except TypeError:
        return method([int(download_id)])  # versiones de pyjnius sin soporte de varargs


class AndroidDownload:
    """DownloadManager del sistema. `poll()` se llama desde la UI (Clock) cada ~0.5 s y
    devuelve ("running", hecho, total) | ("done", ruta, uri) | ("error", mensaje, None)."""

    def __init__(self, info: dict):
        from jnius import autoclass, cast
        self.info = info
        self.activity = autoclass("org.kivy.android.PythonActivity").mActivity
        Context = autoclass("android.content.Context")
        Environment = autoclass("android.os.Environment")
        self.DM = autoclass("android.app.DownloadManager")
        self.dm = cast("android.app.DownloadManager", self.activity.getSystemService(Context.DOWNLOAD_SERVICE))
        name = apk_name(info)
        folder = self.activity.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
        self.path = os.path.join(folder.getAbsolutePath(), name)
        if os.path.exists(self.path):
            os.remove(self.path)
        Uri = autoclass("android.net.Uri")
        Request = autoclass("android.app.DownloadManager$Request")
        req = Request(Uri.parse(info["url"]))
        # setTitle/setDescription reciben CharSequence: pyjnius no convierte str solo
        String = autoclass("java.lang.String")
        req.setTitle(cast("java.lang.CharSequence", String(f"Maxs Wifi {info.get('version', '')}")))
        req.setDescription(cast("java.lang.CharSequence", String("Descargando actualización")))
        req.setMimeType(APK_MIME)
        req.setNotificationVisibility(Request.VISIBILITY_VISIBLE)
        req.setDestinationInExternalFilesDir(self.activity, Environment.DIRECTORY_DOWNLOADS, name)
        self.id = self.dm.enqueue(req)

    def poll(self):
        q = __import__("jnius").autoclass("android.app.DownloadManager$Query")()
        _call_ids(q.setFilterById, self.id)
        c = self.dm.query(q)
        try:
            if not c.moveToFirst():
                return ("error", "La descarga se canceló.", None)
            status = c.getInt(c.getColumnIndex(self.DM.COLUMN_STATUS))
            done = c.getLong(c.getColumnIndex(self.DM.COLUMN_BYTES_DOWNLOADED_SO_FAR))
            total = c.getLong(c.getColumnIndex(self.DM.COLUMN_TOTAL_SIZE_BYTES))
        finally:
            c.close()
        if status == self.DM.STATUS_SUCCESSFUL:
            expected = str(self.info.get("sha256") or "").lower()
            if expected and sha256_file(self.path).lower() != expected:
                return ("error", "El archivo descargado está dañado (la verificación no coincide). "
                                 "Inténtalo de nuevo.", None)
            return ("done", self.path, self.dm.getUriForDownloadedFile(self.id))
        if status == self.DM.STATUS_FAILED:
            return ("error", "No se pudo descargar la actualización. Revisa tu conexión.", None)
        return ("running", done, total if total > 0 else int(self.info.get("tamano") or 0))

    def cancel(self):
        try:
            _call_ids(self.dm.remove, self.id)
        except Exception:  # noqa: BLE001
            pass


def can_install_android() -> bool:
    """Android 8+: el usuario debe permitir que esta app instale apps."""
    from jnius import autoclass
    if autoclass("android.os.Build$VERSION").SDK_INT < 26:
        return True
    activity = autoclass("org.kivy.android.PythonActivity").mActivity
    return bool(activity.getPackageManager().canRequestPackageInstalls())


def open_install_permission_android() -> None:
    from jnius import autoclass
    Intent = autoclass("android.content.Intent")
    Settings = autoclass("android.provider.Settings")
    Uri = autoclass("android.net.Uri")
    activity = autoclass("org.kivy.android.PythonActivity").mActivity
    intent = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + activity.getPackageName()))
    activity.startActivity(intent)


def install_android(uri) -> None:
    """Abre el instalador de Android para el APK descargado."""
    from jnius import autoclass
    Intent = autoclass("android.content.Intent")
    activity = autoclass("org.kivy.android.PythonActivity").mActivity
    intent = Intent(Intent.ACTION_VIEW)
    intent.setDataAndType(uri, APK_MIME)
    intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK)
    activity.startActivity(intent)
