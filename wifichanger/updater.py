"""Actualización de la app desde el panel Red Maxs Solution (igual que APP Max Fibra).

API pública (no requiere iniciar sesión):
  GET https://redmax.maxsolution.pe/api/v1/app-wifi/version?version=<instalada>
      -> {"disponible": bool, "version": "x.y.z", "hay_nueva": bool, "obligatoria": bool,
          "notas": str|null, "tamano": bytes, "sha256": hex, "fecha": iso, "url": ".../descargar"}

Android y escritorio: la app descarga el APK con requests (progreso propio) en su carpeta
privada y verifica el SHA-256. En Android el archivo se registra en el DownloadManager solo para
obtener un content:// URI y abrir el instalador.
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
    """Métodos Java `long...` (p. ej. DownloadManager.remove): pyjnius ya arma el arreglo con
    los argumentos sueltos; pasar una lista lo anida y da «an integer is required»."""
    try:
        return method(int(download_id))
    except TypeError:
        return method([int(download_id)])  # versiones de pyjnius sin soporte de varargs


class AndroidDownload:
    """Descarga hecha por la propia app (requests, en un hilo) a su carpeta privada.

    Antes se usaba el DownloadManager del sistema, pero en algunos celulares la descarga se
    quedaba «en espera» en 0 % para siempre (red sin internet validada, ahorro de datos,
    restricciones del fabricante). Ahora la app descarga sola, como en escritorio, y al
    terminar registra el archivo en el DownloadManager solo para obtener un content:// URI
    que el instalador de Android pueda abrir.

    `poll()` se llama desde la UI (Clock) cada ~0.5 s y devuelve
    ("running", hecho, total) | ("done", ruta, uri) | ("error", mensaje, None)."""

    def __init__(self, info: dict):
        from jnius import autoclass
        self.info = info
        self.activity = autoclass("org.kivy.android.PythonActivity").mActivity
        Environment = autoclass("android.os.Environment")
        folder = self.activity.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
        os.makedirs(folder.getAbsolutePath(), exist_ok=True)
        self.path = os.path.join(folder.getAbsolutePath(), apk_name(info))
        self.done, self.total = 0, int(info.get("tamano") or 0)
        self.state, self.error, self.uri = "running", "", None
        self._cancel = False
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        tmp = self.path + ".part"
        try:
            with requests.get(self.info["url"], stream=True, timeout=(15, 60)) as r:
                r.raise_for_status()
                self.total = int(r.headers.get("content-length") or self.total or 0)
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(256 * 1024):
                        if self._cancel:
                            raise InterruptedError("cancelada")
                        fh.write(chunk)
                        self.done += len(chunk)
            expected = str(self.info.get("sha256") or "").lower()
            if expected and sha256_file(tmp).lower() != expected:
                raise ValueError("El archivo descargado está dañado (la verificación no coincide). "
                                 "Inténtalo de nuevo.")
            os.replace(tmp, self.path)
            self.state = "downloaded"
        except InterruptedError:
            self.state = "cancelled"
        except requests.RequestException:
            self.state, self.error = "error", ("No se pudo descargar la actualización. Revisa que el "
                                               "celular tenga internet (datos o un Wi‑Fi con internet).")
        except Exception as e:  # noqa: BLE001
            self.state, self.error = "error", str(e)
        finally:
            if self.state != "downloaded" and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _register(self):
        """content:// URI para el instalador (se hace en el hilo de la UI)."""
        from jnius import autoclass, cast
        Context = autoclass("android.content.Context")
        dm = cast("android.app.DownloadManager",
                  self.activity.getSystemService(Context.DOWNLOAD_SERVICE))
        dl_id = dm.addCompletedDownload(f"Maxs Wifi {self.info.get('version', '')}",
                                        "Actualización descargada", False, APK_MIME, self.path,
                                        os.path.getsize(self.path), True)
        return dm.getUriForDownloadedFile(dl_id)

    def poll(self):
        if self.state == "running":
            return ("running", self.done, self.total)
        if self.state == "downloaded":
            try:
                self.uri = self._register()
            except Exception as e:  # noqa: BLE001
                self.state, self.error = "error", f"No se pudo preparar el instalador: {e}"
            else:
                if self.uri is None:
                    self.state, self.error = "error", "No se pudo preparar el instalador."
                else:
                    self.state = "done"
        if self.state == "done":
            return ("done", self.path, self.uri)
        if self.state == "cancelled":
            return ("error", "La descarga se canceló.", None)
        return ("error", self.error or "No se pudo descargar la actualización.", None)

    def cancel(self):
        self._cancel = True


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
