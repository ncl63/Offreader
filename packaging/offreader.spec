# Spécification PyInstaller : application graphique Offreader (dossier autonome).
# Utiliser build_exe.bat, qui prépare aussi le Chromium embarqué.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent

# Pilote Playwright (node + paquet JS) et, si présent, Chromium embarqué
# (installé avec PLAYWRIGHT_BROWSERS_PATH=0 dans playwright/driver/package/.local-browsers).
datas = collect_data_files("playwright", include_py_files=False)
datas += collect_data_files("customtkinter")
hiddenimports = collect_submodules("playwright") + collect_submodules("offreader")

a = Analysis(
    [str(ROOT / "offreader_gui.pyw")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Offreader",
    console=False,  # application fenêtrée : pas de console
    upx=False,  # UPX déclenche souvent des faux positifs antivirus
)
coll = COLLECT(exe, a.binaries, a.datas, name="Offreader", upx=False)
