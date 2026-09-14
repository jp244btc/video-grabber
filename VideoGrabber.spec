# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the packaged Windows build.
#
#   pyinstaller --noconfirm VideoGrabber.spec
#
# yt_dlp is deliberately NOT frozen into the exe. build/build.ps1 copies it
# into dist/VideoGrabber/yt_dlp as a plain folder, so "Check for update" in
# the app can drop a newer copy under %LOCALAPPDATA% and have it win on
# sys.path. Its dependencies, which never need updating, are bundled here.

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("templates", "templates")]
binaries = []

# Analyse yt_dlp in full so every standard-library and third-party module it
# touches (optparse, xml.etree, http.cookiejar, websockets, ...) is bundled.
hiddenimports = collect_submodules("yt_dlp")

for pkg in (
    "certifi", "requests", "urllib3", "websockets", "brotli", "mutagen",
    "Cryptodome", "charset_normalizer", "idna",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc"],
    noarchive=False,
)

# ...then drop yt_dlp itself from the archive. Its dependencies stay; the
# package is copied next to the exe as plain files by build\build.ps1, so the
# app's update button can replace it without a rebuild.
a.pure = [entry for entry in a.pure
          if not (entry[0] == "yt_dlp" or entry[0].startswith("yt_dlp."))]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VideoGrabber",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="extension/icons/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VideoGrabber",
)
