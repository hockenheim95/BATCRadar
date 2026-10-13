# PyInstaller spec: folder build (not a single-file EXE).
from PyInstaller.utils.hooks import collect_all

datas = [
    ("web", "web"),
    ("data/actypes.json", "data"),
    ("data/airline_names.json", "data"),
    ("data/airlines.json", "data"),
    ("data/iata.json", "data"),
    ("data/msfs_names.json", "data"),
    ("data/places.json", "data"),
    ("data/runways.json", "data"),
    ("VERSION", "."),
    ("native/SimConnect.dll", "native"),
    ("native/SimConnect.cfg", "native"),
]
binaries = []
hiddenimports = [
    "flask",
    "waitress",
    "SimConnect",
    "SimConnect.Enum",
    "SimConnect.SimConnect",
]
for pkg in ("flask", "waitress", "SimConnect"):
    extra_datas, extra_binaries, extra_hidden = collect_all(pkg)
    datas += extra_datas
    binaries += extra_binaries
    hiddenimports += extra_hidden

a = Analysis(
    ["server.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BATCRadar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BATCRadar",
)
