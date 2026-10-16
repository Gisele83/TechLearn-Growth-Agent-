#!/usr/bin/env bash
# Sauvegarde chiffrée de Growth Agent, et pose du témoin lu par la métrique
# growth_agent_backup_last_success_age_seconds.
#
# Le témoin n'est touché qu'à la toute fin, après vérification que l'archive
# est lisible. Le toucher plus tôt ferait croire à une sauvegarde réussie
# alors que seule la commande a été lancée — et c'est le genre de mensonge
# qu'on ne découvre qu'au moment de restaurer.
set -euo pipefail

: "${PGHOST:?}" "${PGUSER:?}" "${PGDATABASE:?}"
: "${BACKUP_DIR:?répertoire de destination}"
: "${BACKUP_GPG_RECIPIENT:?clé de chiffrement}"
: "${GA_BACKUP_STAMP_PATH:?témoin lu par la supervision}"

horodatage="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
archive="${BACKUP_DIR}/growth-agent-${horodatage}.dump.gpg"

mkdir -p "${BACKUP_DIR}"

# Format « custom » plutôt que SQL brut : il se restaure sélectivement et se
# vérifie sans être appliqué.
pg_dump --format=custom --no-owner --no-privileges \
  | gpg --batch --yes --encrypt --recipient "${BACKUP_GPG_RECIPIENT}" --output "${archive}"

# Vérification : l'archive est-elle lisible ? Une sauvegarde qu'on ne peut pas
# ouvrir n'est pas une sauvegarde.
gpg --batch --quiet --decrypt "${archive}" | pg_restore --list > /dev/null

touch "${GA_BACKUP_STAMP_PATH}"
echo "Sauvegarde vérifiée : ${archive}"
