[app]
title = Maxs Wifi
package.name = mirouter
package.domain = com.wifichanger
source.dir = .
source.include_exts = py,png
source.exclude_dirs = tests, bin, .buildozer, .github, .git, __pycache__
version = 0.10.6

# Logo de Group S & M - Max's Solution E.I.R.L.
icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/presplash.png
android.presplash_color = #FFFFFF

# Dependencias que python-for-android empaqueta dentro del APK.
# pycryptodome tiene receta nativa en p4a (la necesita el login cifrado de TP-Link).
requirements = python3,kivy,pyjnius,android,requests,urllib3,idna,certifi,charset-normalizer,pycryptodome,macaddress,tplinkrouterc6u

orientation = portrait
fullscreen = 0

# INTERNET: hablar con el router. ACCESS_WIFI_STATE: leer la IP del gateway.
android.permissions = INTERNET, ACCESS_NETWORK_STATE, ACCESS_WIFI_STATE, CHANGE_WIFI_STATE
android.api = 36
android.minapi = 24
android.ndk = 28c
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
android.allow_backup = False
android.release_artifact = aab
android.debug_artifact = apk

p4a.branch = develop

[buildozer]
log_level = 2
warn_on_root = 1
