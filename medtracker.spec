# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for `Medication Organizer.exe`.

    pyinstaller medtracker.spec --noconfirm

One folder, one executable, everything the application *reads* bundled next to
it: the templates, the stylesheet and scripts, the translation catalogs and the
icon. Nothing the application *writes* is in here.

That distinction is the whole point. This folder is replaced wholesale by the
next build, so anything of the user's inside it would be destroyed by an
upgrade. The frozen application therefore keeps its database, backups, exports,
logs and uploaded photographs under %LOCALAPPDATA%\\MedTracker — see the
docstring of `app/config.py` — and this recipe bundles no `data/` folder and,
explicitly, no `static/uploads/`, which would otherwise ship the developer's own
medication photographs to everyone who installs it.

Built as a windowed application (no console). Anything worth saying goes to the
log, and a startup failure raises a message box.
"""

from pathlib import Path

project = Path(SPECPATH)

datas = [
    (str(project / "app" / "templates"), "app/templates"),
    (str(project / "app" / "static"), "app/static"),
    (str(project / "app" / "i18n"), "app/i18n"),
]


def _without_uploads(entries):
    """Drop anything under `static/uploads` from the collected data files.

    `datas` above adds the whole `static` tree, which is what we want for the
    stylesheet, the scripts and the icons — and emphatically not what we want
    for the pictures of somebody's medicine cabinet that happen to be sitting in
    it on the machine doing the build.
    """
    keep = []
    for entry in entries:
        target = str(entry[0]).replace("\\", "/")
        if "/static/uploads/" in f"/{target}" or target.endswith("/static/uploads"):
            continue
        keep.append(entry)
    return keep

hiddenimports = [
    # Imported lazily inside functions, so PyInstaller cannot see them.
    "app.notifications.windows",
    "app.notifications.email",
    "app.services.backup",
    "app.services.export_service",
    "app.services.import_service",
    "app.services.system_status",
    "app.services.applock",
    "app.desktop.tray",
    "app.desktop.startup",
    "app.desktop.messages",
    "app.utils.datamove",
    "app.utils.streams",
    "winotify",
    "pystray._win32",
    # uvicorn resolves these by name at run time.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "apscheduler.triggers.interval",
    "apscheduler.executors.pool",
]

a = Analysis(
    [str(project / "desktop.py")],
    pathex=[str(project)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "matplotlib", "numpy"],
    noarchive=False,
)
a.datas = _without_uploads(a.datas)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Medication Organizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # a tray application, not a terminal one
    disable_windowed_traceback=False,
    icon=str(project / "app" / "static" / "img" / "icon.ico")
    if (project / "app" / "static" / "img" / "icon.ico").exists()
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Medication Organizer",
)
