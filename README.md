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
- **Réglages radio** (firmware officiel et firmware F4JCF) : les réglages des menus Réglages, Analogique et Numérique du poste (double veille, pas, sans relais, économie, balayage, type d'alarme, squelch, bips, gains micro et haut-parleur, DTMF, VOX, débruiteur DMR…), avec les mêmes choix que dans la radio et le numéro de l'entrée du menu.
- **Codes DTMF** (firmware officiel et firmware F4JCF) : liste DTMF-01 à DTMF-16 (codes envoyés au début ou à la fin de l'émission, choisis par « Choix DTMF ») et les 4 codes de commande à distance (blocage, mise hors service, réveil, 4e commande), 15 caractères 0-9, A-D, *, # au plus. Rangés dans les réglages du poste (octet 522, 20 fiches de 16 octets : 15 caractères puis la longueur).
- **Réglages des modes F4JCF** (Radio → Réglages des modes F4JCF, firmware F4JCF15 ou plus récent) : canaux des touches 1 à 4 du mode SATER, valeurs de départ de la balise d'exercice (indicatif, intervalle, ton long, hauteur, numéro) et période du rappel d'indicatif (ou arrêt). Ils sont rangés à côté du logo de démarrage (2e Ko du secteur 0x100000, région 0x9A) ; le logo est relu et réécrit à l'identique, le secteur d'origine est enregistré avant l'écriture, puis relu et comparé. Le poste les lit à l'allumage ; s'ils sont absents ou abîmés, il garde les valeurs d'origine.
- **Logo de démarrage** (Radio → Logo de démarrage) : image de 128 × 64 points affichée à l'allumage. Ouverture d'une image (PNG, JPG, BMP…) convertie en noir et blanc (mise en place, contraste, tramage, inversion), texte en option, aperçu à l'échelle de l'écran, enregistrement en PNG ou .bin. Le logo d'origine est enregistré avant l'écriture et les réglages des modes F4JCF rangés à côté sont conservés.
- **Calibration du poste** (Radio → Calibration) : consultation des tables d'usine (polarisation de l'ampli d'émission en puissance haute et basse, squelch large et étroit, tous les 5 MHz). La modification est verrouillée : l'opérateur doit donner son indicatif, recopier une phrase de confirmation et cocher l'avertissement, puis la recopier encore avant l'écriture. Seules les bandes VHF 136-174 et UHF 400-480 MHz et ces 6 tables sont modifiables, avec un écart limité par rapport à l'origine. La calibration et la copie de secours de la radio sont enregistrées sur l'ordinateur avant chaque écriture, la calibration écrite est relue et comparée, chaque opération est notée dans `journal-calibration.txt`, et « Restaurer une calibration enregistrée » remet l'origine.
- **Limites de fréquences** (firmware officiel) : les 4 plages du poste, pour interdire l'émission sur certaines fréquences. Un bouton limite l'émission aux bandes radioamateur 144-146 et 430-440 MHz. L'onglet signale les canaux qui ne pourront plus émettre. Par défaut, le poste peut émettre de 18 à 1000 MHz (sauf 108-136 MHz, toujours refusé).
- **Onglet « Carnet adresse DMR »** : bouton de mise à jour de la liste RadioID.net (gardée sur le PC), recherche, choix des pays, puis envoi à la radio, qui affiche l'indicatif et le nom des correspondants.
- **Sauvegarde Nextcloud** (Fichier → Sauvegarde Nextcloud), comme dans CAT Pilot : le dossier des sauvegardes, le codeplug ouvert et les réglages (sans le mot de passe) sont envoyés sur votre Nextcloud, ou un autre serveur WebDAV, une fois par jour et après chaque nouvelle sauvegarde. Seuls les fichiers nouveaux partent, et rien n'est effacé sur le serveur.
- **Sauvegarde complète de la flash** (4 Mo) et **restauration**. Par défaut, la calibration est préservée. La restauration utilise la carte mémoire du firmware présent dans la radio (REFW ou officiel Radtel), et elle refuse une sauvegarde qui vient d'un autre firmware.
- **Police avec accents** (firmware officiel) : 7 caractères inutilisés de la police du poste deviennent é è ê à ç î É, pour le firmware traduit en français avec accents. L'ancienne police est enregistrée avant l'écriture, et la police écrite est relue. L'option **Accents dans les noms** permet alors d'écrire é è ê à ç î É dans les noms de canaux, zones, contacts et groupes.
- **Flash du firmware** pour mettre à jour la radio ou revenir à une version antérieure. Il accepte directement le paquet officiel Radtel (le zip « RT-4D Upgrade Vx_xx_xxxxxx.zip » ou le .exe qu'il contient) : le firmware y est extrait sans lancer l'outil Radtel, puis flashé avec la même procédure que celle-ci. Un fichier `.bin` (REFW, firmware F4JCF…) est aussi accepté. La **bibliothèque de firmwares** (dossier `firmwares` des sauvegardes) range les versions disponibles ; chacune est identifiée par son empreinte SHA-256 (versions F4JCF connues) et contrôlée avant le flash (taille d'une image F4JCF, vecteurs de démarrage). La fenêtre rappelle la date de la dernière sauvegarde complète et le dernier firmware flashé, et chaque flash est noté dans `journal-firmware.txt`.
- **Triangulation SATER** (Outils → Triangulation SATER) : saisie des relevés du carnet du mode SATER du firmware F4JCF (position en degrés décimaux ou locator, azimut), calcul du point où les relèvements se croisent (moindres carrés, avec la distance à chaque station et l'écart moyen), carte avec fond OpenStreetMap facultatif, déclinaison magnétique, export KML (Google Earth) et CSV.
- **Import d'un plan de fréquences** (Fichier → Importer un plan de fréquences) : export CHIRP, liste de relais ou tableur en CSV (colonnes reconnues en français ou en anglais : Fréquence, Nom, TX, Décalage, Sous-ton, Mode, Puissance…), ou texte simple (« 145,500 S20 »). Aperçu avant l'ajout, canaux placés dans les emplacements libres à partir d'un numéro choisi, zone créée en option, doublons sautés, canaux hors bandes radioamateur mis en « RX seul ».
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
   Pour une copie de **toute** la mémoire (16 Mo, environ 25 minutes, lecture seule) : **Radio → Sauvegarde de toute la mémoire**. Le fichier de 16 Mo s'utilise aussi pour Restaurer et pour la police (ses 4 premiers Mo).
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
- La calibration n'est jamais écrite sans la validation de l'opérateur (indicatif et phrase recopiée deux fois). Modifiez-la seulement avec un wattmètre et une charge fictive.
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
