# Mi Router — app Android para routers TP-Link y Tenda

Funciones:
- **Cambiar la clave Wi‑Fi** (2.4 GHz, 5 GHz o ambas), tocando solo ese campo.
- **Ver quién está conectado**: nombre, IP, MAC y tipo de conexión (cable, 2.4/5 GHz, invitados).
- **Bloquear y desbloquear dispositivos** con confirmación. La app marca "este teléfono"
  y no deja que te bloquees a ti mismo.

Siempre usa la contraseña de administrador real del router.

## App Android (APK)

`main.py` es la interfaz Kivy y `buildozer.spec` la configuración del APK.
La forma más simple de compilar es GitHub Actions (no necesitas el SDK de Android):

1. Crea un repositorio en GitHub y sube **el contenido** de esta carpeta (incluida
   la carpeta oculta `.github`).
2. En la pestaña **Actions**, el workflow *Compilar APK* arranca solo con cada push
   (o pulsa *Run workflow*). La primera vez tarda ~30–40 min; luego usa caché.
3. Descarga el artefacto **mi-router-apk** (un .zip con el .apk).
4. En el teléfono, permite "Instalar apps de origen desconocido" e instala el APK.

Compilar en tu PC (Linux o WSL): `pip install buildozer cython==0.29.34` y luego
`buildozer android debug` → el APK queda en `bin/`.

Uso: conéctate al Wi‑Fi del router, abre la app (la IP se rellena sola), escribe la
contraseña de administrador y pulsa **Conectar**. Arriba tienes dos pestañas:
**Clave Wi‑Fi** y **Dispositivos** (con **Actualizar**, **Bloquear** y **Desbloquear**).

## Herramienta de línea de comandos (para probar en el PC)
```bash
pip install -r requirements.txt
python -m wifichanger listar                       # redes Wi‑Fi
python -m wifichanger cambiar --banda 5            # o --todas
python -m wifichanger dispositivos                 # conectados y bloqueados
python -m wifichanger bloquear AA:BB:CC:DD:EE:FF
python -m wifichanger desbloquear AA:BB:CC:DD:EE:FF
python -m wifichanger --host 192.168.0.1 --marca tplink dispositivos
```

## Estructura
| Archivo | Rol |
|---|---|
| `wifichanger/base.py` | `RouterDriver` (interfaz), modelos `WifiNetwork`/`ClientDevice`, validación WPA |
| `wifichanger/detect.py` | gateway (Android vía WifiManager, Linux/macOS/Windows) + detección de marca |
| `wifichanger/drivers/tplink.py` | TP-Link/Mercusys sobre `tplinkrouterc6u` + Control de acceso |
| `wifichanger/drivers/tenda.py` | Tenda, implementación propia del panel `goform` |
| `wifichanger/service.py` | operaciones que usa la interfaz (sin Kivy, testeable) |
| `wifichanger/cli.py` | línea de comandos |
| `main.py` | interfaz Android (Kivy) |

Agregar una marca = una clase que implemente `matches`, `login`, `get_wifi_networks`,
`set_wifi_password`, `get_clients`, `block_client` y `unblock_client`, registrada en
`detect.DRIVERS`.

## Cobertura por marca

**TP-Link.**
- Login: `tplinkrouterc6u` resuelve el cifrado de casi todas las familias (Archer, Deco, EX, MR, VR…).
  Usa la *contraseña local*, no el TP-Link ID.
- Clave Wi‑Fi: en clientes cuyo `set_wifi` acepta `psk_key` (Archer C6/C6U/AX y similares).
- Dispositivos conectados: en todos los modelos que la librería soporta.
- Bloqueo: usa *Seguridad → Control de acceso* en modo **lista negra**
  (`admin/access_control`, firmwares luci: Archer C6/C6U/AX10/AX20/AX53/AX55…).
  Si está apagado, la app lo activa. Si está en **lista blanca**, la app no lo cambia
  y te pide hacerlo en el panel web. El bloqueo corta todo el acceso a la red, no solo internet.
- Modelos antiguos (WR841N, C50, C80, WDR…): solo listar; clave y bloqueo dan "no soportado".

**Tenda** (firmwares `goform`: AC6, AC7, AC8, AC10, AC15, AC1200, F6…).
- Login con MD5 de la clave (reintento en Base64).
- Clave: `WifiBasicGet/Set`, respeta "unificar bandas" y pasa de red abierta a WPA/WPA2.
- Dispositivos: `getOnlineList`; bloqueo con la lista negra rápida
  (`setBlackRule` / `getBlackRuleList` / `delBlackRule`, máximo 30 equipos).
- Los Tenda Wi‑Fi 6 (TX/RX) y los muy antiguos (N301) usan otra API.

## Pruebas
```bash
python -m pytest -q
```
21 pruebas contra un Tenda simulado (servidor HTTP real) y clientes TP-Link falsos
(incluido el Control de acceso). Antes de dar un modelo por soportado, pruébalo con
el equipo real usando la CLI.
