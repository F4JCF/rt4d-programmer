"""Intégration au bureau Linux : icône et entrée dans le menu des applications.

Au lancement de l'exécutable Linux, le logiciel installe (ou met à jour) pour
l'utilisateur courant, sans droits administrateur :
  ~/.local/share/icons/hicolor/256x256/apps/rt4d-programmer.png
  ~/.local/share/applications/rt4d-programmer.desktop
L'entrée pointe vers l'emplacement actuel de l'exécutable : si celui-ci est
déplacé, elle est corrigée au lancement suivant.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

APP_ID = "rt4d-programmer"


def _data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def icon_source() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "icon.png")


def desktop_entry(exe: str, icon: str) -> str:
    q = exe.replace("\\", "\\\\").replace('"', '\\"')
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=RT4D Programmer\n"
        "GenericName=Programmation Radtel RT-4D\n"
        "Comment=Programmer le Radtel RT-4D et mettre à jour son firmware\n"
        f'Exec="{q}"\n'
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=HamRadio;Utility;\n"
        "Keywords=radio;RT-4D;Radtel;DMR;codeplug;\n"
        "StartupWMClass=RT4D-Programmer\n"
    )


def install(exe: str | None = None, desktop_shortcut: bool = False) -> bool:
    """Installe ou met à jour l'icône et l'entrée de menu. Retourne True si un
    fichier a été écrit. Ne lève jamais d'exception (confort, pas indispensable)."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        exe = os.path.abspath(exe or sys.executable)
        src = icon_source()
        if not os.path.exists(src):
            return False
        data = _data_home()
        icon_dir = os.path.join(data, "icons", "hicolor", "256x256", "apps")
        icon_path = os.path.join(icon_dir, APP_ID + ".png")
        app_dir = os.path.join(data, "applications")
        entry_path = os.path.join(app_dir, APP_ID + ".desktop")
        changed = False

        os.makedirs(icon_dir, exist_ok=True)
        with open(src, "rb") as f:
            icon_data = f.read()
        old = None
        if os.path.exists(icon_path):
            with open(icon_path, "rb") as f:
                old = f.read()
        if old != icon_data:
            with open(icon_path, "wb") as f:
                f.write(icon_data)
            changed = True

        # chemin absolu de l'icône : fonctionne même sans rafraîchir le cache
        text = desktop_entry(exe, icon_path)
        os.makedirs(app_dir, exist_ok=True)
        cur = None
        if os.path.exists(entry_path):
            with open(entry_path, encoding="utf-8", errors="replace") as f:
                cur = f.read()
        if cur != text:
            with open(entry_path, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(entry_path, 0o755)
            changed = True

        if desktop_shortcut:
            desk = _desktop_dir()
            if desk:
                link = os.path.join(desk, APP_ID + ".desktop")
                shutil.copyfile(entry_path, link)
                os.chmod(link, 0o755)
                _run(["gio", "set", link, "metadata::trusted", "true"])
                changed = True

        if changed:
            _run(["update-desktop-database", app_dir])
            _run(["gtk-update-icon-cache", "-f", "-t", os.path.join(data, "icons", "hicolor")])
        return changed
    except Exception:
        return False


def _desktop_dir() -> str | None:
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
        if out and os.path.isdir(out):
            return out
    except Exception:
        pass
    for name in ("Bureau", "Desktop"):
        p = os.path.expanduser("~/" + name)
        if os.path.isdir(p):
            return p
    return None


def _run(cmd):
    if shutil.which(cmd[0]):
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass
