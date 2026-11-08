# RT4D Programmer

Ce logiciel programme le talkie-walkie **Radtel RT-4D** sous **Windows et Linux**. Il reconnaît automatiquement deux firmwares :

- le **firmware officiel Radtel**, à partir de la V3.18 (testé avec la V3.22). Les données sont lues et écrites exactement comme le fait le logiciel officiel Radtel (CPS V2.07) ;
- le firmware communautaire **REFW** de Dual Tachyon, à partir de la beta 41.

## Fonctions

- **Lire et écrire le codeplug** par le câble USB :
  - canaux analogiques (FM/AM/SSB) et numériques DMR ;
  - contacts DMR ;
  - groupes RX ;
  - zones, avec le choix des canaux balayés ;
  - ID et nom de la radio.
- **Sauvegarde automatique** : avant chaque écriture, le logiciel relit la radio et enregistre son contenu dans un fichier `.4rdmf`.
- **Sauvegarde complète de la flash** (4 Mo) et **restauration**. Par défaut, la calibration est préservée.
- **Flash du firmware** pour mettre à jour la radio ou revenir à une version antérieure, avec un fichier `.bin`.
- **Import et export CSV** des canaux et des contacts. Le séparateur « ; » s'ouvre directement dans Excel ou LibreOffice.
- Fichiers : `.rt4d` pour le firmware officiel, `.4rdmf` (compatible RT4D Editor) pour REFW. Une sauvegarde complète `.bin` peut aussi être ouverte, pour consulter son contenu.

## Installation

Téléchargez le zip de votre système dans la page **Releases** du dépôt (ou dans l'onglet **Actions**, dernière compilation, rubrique *Artifacts*), décompressez-le et lancez :

- Windows : `RT4D-Programmer.exe`. Si SmartScreen affiche un avertissement, cliquez sur « Informations complémentaires » puis « Exécuter quand même ».
- Linux : `./RT4D-Programmer`. Il peut être nécessaire de lancer une fois :
  ```
  sudo usermod -aG dialout $USER      # accès au port série (fermez puis rouvrez la session)
  sudo apt install libxcb-cursor0     # nécessaire à l'interface Qt sur certaines distributions
  ```

Le câble USB du RT-4D crée un port COM (Windows) ou `/dev/ttyUSB0` / `/dev/ttyACM0` (Linux).

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
- les mises à jour officielles Radtel sont des `.exe` sans `.bin` exploitable ;
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

Les exécutables sont compilés par GitHub Actions (`.github/workflows/build.yml`) à chaque envoi sur `main`. Pour publier une version, créez un tag `vX.Y.Z`.

## Licence

GPL v3. Le protocole série est adapté des outils de Dual Tachyon (Apache 2.0) : voir `NOTICE`.
