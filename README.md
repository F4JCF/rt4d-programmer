# RT4D Programmer

Ce logiciel programme le talkie-walkie **Radtel RT-4D** sous **Windows et Linux**. Il reconnaît automatiquement deux firmwares :

- le **firmware officiel Radtel**, à partir de la V3.18 (testé avec la V3.22 ; la V3.25 utilise la même organisation de la mémoire). Les données sont lues et écrites exactement comme le fait le logiciel officiel Radtel (CPS V2.07) ;
- l'**ancien firmware officiel Radtel** (V3.17 et antérieurs, testé avec la V3.14) : lecture et écriture, avec la même séquence que le logiciel Radtel CPS V1.19. Après une mise à jour du firmware, *Fichier > Importer un codeplug d'ancien firmware* convertit aussi un ancien codeplug au nouveau format ;
- le firmware communautaire **REFW** de Dual Tachyon, à partir de la beta 41.

## Fonctions

- **Lire et écrire le codeplug** par le câble USB :
  - canaux analogiques (FM/AM/SSB) et numériques DMR ;
  - contacts DMR ;
  - groupes RX ;
  - zones, avec le choix des canaux balayés ;
  - ID et nom de la radio.
- **Sauvegarde automatique** : avant chaque écriture, le logiciel relit la radio et enregistre son contenu dans un fichier `.4rdmf`.
- **Réglages des touches** (firmware officiel) : action des touches latérales, de la touche alarme et des appuis longs 0 à 9, parmi les 41 fonctions proposées par Radtel.
- **Menu démarrage** (firmware officiel) : message d'accueil et sa position, logo, bip, mode de travail et zone des voies A/B à l'allumage, affichage (nom, fréquence ou canal), luminosité, rétroéclairage, contraste, sons et voyants.
- **Limites de fréquences** (firmware officiel) : les 4 plages du poste, pour interdire l'émission sur certaines fréquences. Un bouton limite l'émission aux bandes radioamateur 144-146 et 430-440 MHz. L'onglet signale les canaux qui ne pourront plus émettre. Par défaut, le poste peut émettre de 18 à 1000 MHz (sauf 108-136 MHz, toujours refusé).
- **Onglet « Carnet adresse DMR »** : bouton de mise à jour de la liste RadioID.net (gardée sur le PC), recherche, choix des pays, puis envoi à la radio, qui affiche l'indicatif et le nom des correspondants.
- **Sauvegarde complète de la flash** (4 Mo) et **restauration**. Par défaut, la calibration est préservée.
- **Flash du firmware** pour mettre à jour la radio ou revenir à une version antérieure. Il accepte directement le paquet officiel Radtel (le zip « RT-4D Upgrade Vx_xx_xxxxxx.zip » ou le .exe qu'il contient) : le firmware y est extrait sans lancer l'outil Radtel, puis flashé avec la même procédure que celle-ci. Un fichier `.bin` (REFW…) est aussi accepté.
- **Import et export CSV** des canaux et des contacts. Le séparateur « ; » s'ouvre directement dans Excel ou LibreOffice.
- Fichiers : `.rt4d` pour le firmware officiel, `.4rdmf` (compatible RT4D Editor) pour REFW. Une sauvegarde complète `.bin` peut aussi être ouverte, pour consulter son contenu.

## Installation

Dans la page **Releases** du dépôt :

- Windows : `RT4D-Programmer-Setup-x.y.z.exe` installe le logiciel dans votre profil (sans droits administrateur), avec un raccourci. Vous pouvez aussi lancer directement `RT4D-Programmer.exe`. Si SmartScreen affiche un avertissement, cliquez sur « Informations complémentaires » puis « Exécuter quand même ».
- Linux : décompressez `RT4D-Programmer-Linux.zip` puis lancez `sh install.sh`. Le logiciel est copié dans `~/.local/bin`, avec son icône dans le menu des applications et sur le bureau. Vous pouvez aussi lancer directement `RT4D-Programmer-Linux` (rendez-le exécutable) : l'icône et l'entrée de menu sont alors ajoutées au premier lancement. Il peut être nécessaire de lancer une fois :
  ```
  sudo usermod -aG dialout $USER      # accès au port série (fermez puis rouvrez la session)
  sudo apt install libxcb-cursor0     # nécessaire à l'interface Qt sur certaines distributions
  ```

Le câble USB du RT-4D crée un port COM (Windows) ou `/dev/ttyUSB0` / `/dev/ttyACM0` (Linux).

Ensuite, les mises à jour fonctionnent comme dans CAT Pilot : au démarrage, une fenêtre propose « Mettre à jour maintenant », « Plus tard » ou « Ignorer cette version ». Le menu **Mise à jour** passe en orange tant qu'une version attend (vérification discrète toutes les 6 heures). Le nouvel exécutable est téléchargé, contrôlé par son empreinte SHA-256, puis remplace l'ancien à la fermeture du logiciel, qui redémarre tout seul.

## Utilisation

1. Allumez la radio **normalement**, câble branché, puis choisissez le port.
2. **Radio → Tester la connexion** : le logiciel indique le firmware détecté.
3. **Radio → Lire la radio**, puis modifiez les canaux, contacts, groupes RX et zones.
4. **Fichier → Enregistrer** pour garder une copie, puis **Radio → Écrire dans la radio**.

### Changer de firmware

1. **Radio → Sauvegarde complète** (environ 6 minutes) : gardez ce fichier précieusement.
2. **Firmware → Flasher un firmware**, puis choisissez le `.bin`.
3. Éteignez la radio. **Maintenez le PTT** et rallumez-la : l'écran reste éteint (mode bootloader).
4. Cliquez sur « Flasher » et ne débranchez rien jusqu'à la fin, puis redémarrez la radio normalement.

Limites :

- seul le firmware principal (MCU) est flashé, pas celui de la puce DMR ;
- la puce DMR et la table Unicode (SMS) se mettent à jour avec les outils séparés du paquet Radtel ;
- après un changement de version REFW, relisez la radio avant d'écrire, car la disposition de la mémoire peut changer d'une beta à l'autre.

## Sécurité

- L'écriture du codeplug est **bloquée** si le format de la radio n'est pas reconnu, ou s'il ne correspond pas au codeplug ouvert.
- Avec le firmware officiel, la séquence d'écriture est celle du logiciel Radtel : mot de passe et fin des réglages relus dans la radio, puis toutes les régions écrites dans le même ordre.
- Les octets inconnus des enregistrements sont conservés tels qu'ils ont été lus.
- Ce logiciel est fourni **sans aucune garantie**. Faites des sauvegardes.

## Développement

```
pip install -r requirements.txt pytest
python -m pytest tests        # tests avec une radio simulée
python main.py
```

Les exécutables sont compilés par GitHub Actions (`.github/workflows/build.yml`) à chaque envoi sur `main`. La publication est automatique : quand le numéro de version de `rt4dprog/__init__.py` change, le tag `vX.Y.Z` et la release sont créés avec les zips Windows et Linux.

## Licence

GPL v3. Le protocole série est adapté des outils de Dual Tachyon (Apache 2.0) : voir `NOTICE`.
