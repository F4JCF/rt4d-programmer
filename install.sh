#!/bin/sh
# Installation de RT4D Programmer pour l'utilisateur courant (sans droits administrateur).
# Usage : sh install.sh
set -e
cd "$(dirname "$0")"
DEST="$HOME/.local/bin"
mkdir -p "$DEST"
cp RT4D-Programmer "$DEST/RT4D-Programmer"
chmod +x "$DEST/RT4D-Programmer"
"$DEST/RT4D-Programmer" --installer-menu --bureau
echo
echo "RT4D Programmer est installé dans $DEST."
echo "Lancez-le depuis le menu des applications ou l'icône du bureau."
if ! id -nG "$USER" | grep -qw -e dialout -e uucp; then
  echo
  echo "Pour accéder au câble de programmation, votre compte doit faire partie du groupe 'dialout' :"
  echo "    sudo usermod -aG dialout $USER"
  echo "puis fermez la session et reconnectez-vous."
fi
