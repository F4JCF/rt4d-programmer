"""Fenêtre de réglage de la sauvegarde Nextcloud (Fichier → Sauvegarde Nextcloud)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from .backup import DEFAULT_FOLDER


class BackupDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win, self.s = win, win.settings
        self.setWindowTitle("Sauvegarde Nextcloud")
        self.resize(580, 0)
        v = QVBoxLayout(self)
        f = QFormLayout()
        self.ed = {}
        for key, label, ph, pwd in (("nc_url", "Adresse du Nextcloud", "https://cloud.exemple.fr", False),
                                    ("nc_user", "Utilisateur", "", False),
                                    ("nc_pass", "Mot de passe d'application", "", True),
                                    ("nc_folder", "Dossier", DEFAULT_FOLDER, False)):
            e = QLineEdit(self.s.value(key, "") or (DEFAULT_FOLDER if key == "nc_folder" else ""))
            e.setPlaceholderText(ph)
            if pwd:
                e.setEchoMode(QLineEdit.Password)
            f.addRow(label, e)
            self.ed[key] = e
        self.ck = QCheckBox("Sauvegarder automatiquement une fois par jour (au démarrage)")
        self.ck.setChecked(self.s.value("nc_daily", "true") in (True, "true"))
        f.addRow(self.ck)
        self.ck2 = QCheckBox("Envoyer aussi chaque nouvelle sauvegarde dès qu'elle est faite")
        self.ck2.setChecked(self.s.value("nc_after", "true") in (True, "true"))
        f.addRow(self.ck2)
        v.addLayout(f)
        t = QLabel("Créez un « mot de passe d'application » dans Nextcloud (Paramètres > Sécurité) plutôt "
                   "que d'utiliser votre mot de passe.<br><br>Sont envoyés : le <b>dossier des "
                   "sauvegardes</b> (sauvegardes complètes 4 Mo avec la calibration, état de la radio "
                   "avant chaque écriture, anciennes polices), le <b>codeplug ouvert</b> avec une copie "
                   "datée dans « historique », et les <b>réglages</b> (sans le mot de passe). Seuls les "
                   "fichiers nouveaux sont envoyés ; rien n'est effacé sur le serveur.<br><br>"
                   "Une adresse WebDAV complète (kDrive, ownCloud…) est aussi acceptée.")
        t.setWordWrap(True)
        v.addWidget(t)
        last = self.s.value("nc_last", "")
        self.lb = QLabel(f"Dernière sauvegarde : {last}" if last else "Aucune sauvegarde pour l'instant")
        self.lb.setWordWrap(True)
        v.addWidget(self.lb)
        h = QHBoxLayout()
        b = QPushButton("Sauvegarder maintenant")
        b.clicked.connect(self.now)
        h.addWidget(b)
        b = QPushButton("Tout renvoyer")
        b.setToolTip("Renvoie tout le dossier des sauvegardes, même les fichiers déjà envoyés")
        b.clicked.connect(self.all_again)
        h.addWidget(b)
        h.addStretch()
        v.addLayout(h)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.accept)
        v.addWidget(bb)
        win.backup.status.connect(self.lb.setText)

    def store(self):
        for k, e in self.ed.items():
            self.s.setValue(k, e.text() if k == "nc_pass" else e.text().strip())
        self.s.setValue("nc_daily", "true" if self.ck.isChecked() else "false")
        self.s.setValue("nc_after", "true" if self.ck2.isChecked() else "false")

    def now(self):
        self.store()
        if not self.win.backup.configured():
            self.lb.setText("Renseignez l'adresse, l'utilisateur et le mot de passe d'application")
            return
        self.win.backup.run()

    def all_again(self):
        self.store()
        self.win.backup.reset_index()
        self.now()

    def done(self, r):
        self.store()
        try:
            self.win.backup.status.disconnect(self.lb.setText)
        except (RuntimeError, TypeError):
            pass
        super().done(r)
