# Dossier de déploiement — préparé, non exécuté

> **Rien de ce document n'a été exécuté.** Aucune commande distante n'a été
> lancée, aucun port ouvert, aucun DNS modifié, aucune ressource commandée.
> Le déploiement requiert une autorisation explicite et une validation de
> chaque étape.

## Principe directeur

Growth Agent se déploie sur une **instance dédiée**, distincte de celles de
Maintrix et d'EduCore. Pas de conteneur ajouté à une pile existante, pas de
base partagée, pas de reverse proxy mutualisé.

La raison est simple : une erreur d'exploitation sur Growth Agent ne doit pas
pouvoir affecter un produit en service. L'isolation coûte une vingtaine
d'euros par mois ; une interruption d'EduCore un jour de rentrée coûte
beaucoup plus.

## Sur les adresses IP

Le cahier des charges mentionne deux adresses historiques (`57.130.73.41` pour
Maintrix, `57.130.75.49` pour EduCore). **Elles n'ont pas été vérifiées et ne
doivent pas être présumées valides.** Elles ne figurent ici que pour être
explicitement écartées : Growth Agent ne s'y déploie pas, et ne les contacte
pas.

Avant toute opération, vérifier l'identité de l'hôte cible : nom d'hôte,
adresse, inventaire des conteneurs, volumes, espace disque, et absence de tout
service Maintrix ou EduCore.

## Dimensionnement

Pour une équipe de 2 à 5 personnes et quelques milliers de prospects :

| Ressource | Recommandation | Justification |
|---|---|---|
| vCPU | 2 | Charge dominée par les attentes réseau |
| Mémoire | 4 Go | PostgreSQL 1 Go, API 512 Mo, le reste en cache |
| Disque | 40 Go SSD | Base < 1 Go la première année ; la marge sert aux sauvegardes |
| Bande passante | Standard | Trafic faible |

Référence de coût : une instance d'entrée de gamme chez un hébergeur européen,
de l'ordre de 15 à 25 €/mois. **À confirmer** : les tarifs changent.

Budget de fonctionnement mensuel de référence, repris du cahier des charges :
hébergement 20 €, modèle de langage 30 €, prospection 50 €, CRM 0 € selon
l'offre, divers 20 € — soit environ 120 €, dans une fourchette de 60 à 400 €
selon les volumes. Ce sont des hypothèses à valider fournisseur par
fournisseur avant tout abonnement.

## Architecture réseau proposée

```
                    Internet
                        │
                   [ Pare-feu ]
                 22/tcp ← adresses d'administration uniquement
                 80/tcp → redirection vers 443
                443/tcp → public
                        │
              [ Reverse proxy TLS ]
                        │
          ┌─────────────┴─────────────┐
          │                           │
   /  → interface statique     /api → API (127.0.0.1:8000)
                                      │
                        ┌─────────────┴─────────────┐
                        │                           │
                 PostgreSQL                      Redis
              (127.0.0.1:5432)              (127.0.0.1:6379)
```

PostgreSQL et Redis n'écoutent que sur la boucle locale. Seuls 443 — et 22
depuis des adresses connues — sont exposés.

## Préparatifs

### Décisions à prendre d'abord

1. Nom de domaine. Un sous-domaine dédié suffit. **Pas le domaine d'expédition
   des emails** — voir D2 dans le registre des dépendances.
2. Hébergeur et région. Une région européenne simplifie le RGPD.
3. Qui détient les secrets, et où.
4. Qui est autorisé à déployer.

### Secrets requis

| Variable | Génération |
|---|---|
| `GA_SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| Mot de passe PostgreSQL | `openssl rand -base64 32` |
| Jeton CRM | Depuis le CRM, portée minimale |
| Clé du modèle de langage | Depuis le fournisseur, avec plafond de dépense |

Aucun de ces secrets ne transite par un canal de discussion, ne figure dans le
dépôt, ni n'apparaît dans l'historique du shell.

## Procédure, par étapes

Chaque étape se termine par une vérification. Si elle échoue, on s'arrête.

### Étape 0 — Vérifier l'hôte

```bash
hostname -f && ip -4 addr show | grep inet
docker ps -a --format '{{.Names}}\t{{.Image}}'
docker volume ls
df -h /
```

**Point d'arrêt.** Confirmer qu'aucun conteneur ni volume Maintrix ou EduCore
n'est présent. Si l'un apparaît : **changer d'hôte**.

### Étape 1 — Durcir le système

```bash
sudo apt update && sudo apt upgrade -y
sudo adduser --disabled-password --gecos "" growth
sudo usermod -aG docker growth

sudo sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl reload ssh

sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from <ADRESSE_ADMIN> to any port 22 proto tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

**Vérification.** `sudo ufw status verbose` ; ouvrir une seconde session SSH
**avant** de fermer la première.

### Étape 2 — Base de données et file

Déployer PostgreSQL et Redis, sur la boucle locale uniquement.

**Vérification.** `pg_isready`, `redis-cli ping`, et depuis l'extérieur :
`nc -zv <hôte> 5432` doit **échouer**.

### Étape 3 — Migrations

```bash
docker compose -f docker-compose.prod.yml run --rm api alembic upgrade head
```

**Vérification.** `alembic current` affiche la révision attendue.

### Étape 4 — API

**Vérification.** `curl -s http://127.0.0.1:8000/health` renvoie `status: ok`,
et les connecteurs sont dans l'état attendu — `email: disabled` tant que le
domaine d'expédition n'est pas validé.

### Étape 5 — Interface et TLS

Certificat Let's Encrypt, renouvellement automatique.

**Vérification.** `curl -I https://<domaine>` renvoie 200 ; le certificat est
valide ; HTTP redirige vers HTTPS ; les en-têtes de sécurité sont présents.

### Étape 6 — Recette

- Connexion avec chacun des trois rôles.
- Un lecteur ne peut pas écrire (403 attendu).
- Un brouillon ne peut pas partir sans validation.
- Un auteur ne peut pas valider sa propre demande.
- Le journal d'audit enregistre ces tentatives.
- Les connecteurs produits sont bien en mode simulé.

**Point d'arrêt.** Le moindre échec interdit la mise en service.

### Étape 7 — Sauvegarde et restauration

Utiliser `monitoring/backup.sh`, qui fait le dump au format `custom`, le
chiffre, **vérifie que l'archive est lisible**, et seulement alors touche le
fichier témoin lu par la supervision. Le poser en tâche planifiée
quotidienne, avec `GA_BACKUP_STAMP_PATH` pointant sur le même fichier que la
configuration de l'application.

**Vérification — la seule qui compte.** Restaurer la sauvegarde sur une base
vierge et vérifier que l'application démarre dessus. Une sauvegarde jamais
restaurée n'est pas une sauvegarde. Procédure pas à pas dans
`docs/08-supervision.md`, section « Épreuve de restauration ».

**Attention à la rétention des sauvegardes.** Une sauvegarde prise avant une
purge, restaurée après, réintroduit les données purgées en clair — vérifié
en laboratoire sur PostgreSQL. La durée de conservation des sauvegardes doit
donc être cohérente avec celle des données, et toute restauration réelle doit
être suivie d'une purge.

## Retour arrière

```bash
# 1. Revenir à l'image précédente
docker compose -f docker-compose.prod.yml up -d --no-deps api:<version-precedente>

# 2. Si une migration est en cause
docker compose run --rm api alembic downgrade -1

# 3. En dernier recours, restaurer la sauvegarde
gpg --decrypt backup-<date>.sql.gz.gpg | gunzip | \
  docker compose exec -T db psql -U growth growth_agent
```

**Jamais `docker compose down -v`.** Cette commande détruit les volumes, donc
la base. Il n'existe aucune situation où elle est le bon geste en production.

## Supervision

Détail complet dans `docs/08-supervision.md`, qui donne pour chaque alerte le
geste associé. En résumé :

| Sonde | Pour qui | Fréquence |
|---|---|---|
| `/health/live` | l'orchestrateur, pour redémarrer | 10 s |
| `/health/ready` | le répartiteur, pour router | 10 s |
| `/metrics` | le collecteur | 60 s |

`/health/ready` renvoie 503 si la base ne répond pas ou si le schéma n'est
pas à la révision attendue — un conteneur démarré avant la fin des migrations
doit se retirer du trafic, pas servir des erreurs.

`/metrics` exige `GA_METRICS_TOKEN` en `Bearer` et répond 404 sans lui.
`monitoring/alerts.rules.yml` porte les règles ; il est engendré depuis le
code, et un test échoue s'il diverge.

Restent hors du périmètre de l'application, à poser sur l'hôte : l'espace
disque et l'expiration du certificat.

## Intégration continue

La CI du dépôt construit et teste. **Elle ne déploie pas.** Ajouter un
déploiement automatique demanderait de confier des identifiants de production
à un système d'intégration, ce qui n'a pas été jugé souhaitable à ce stade.

## Checklist de mise en service

- [ ] Hôte vérifié, aucun service Maintrix ou EduCore présent
- [ ] Pare-feu actif, accès SSH restreint, mot de passe désactivé
- [ ] Secrets générés et stockés hors du dépôt
- [ ] `GA_ENVIRONMENT=production`, `GA_DEBUG=false`, CORS restreint
- [ ] Migrations appliquées
- [ ] TLS valide, redirection HTTP en place
- [ ] Recette multi-rôles passée
- [ ] Sauvegarde **et restauration** testées sur l'instance de production
- [ ] `GA_METRICS_TOKEN` et `GA_BACKUP_STAMP_PATH` posés
- [ ] Règles d'alerte chargées, destinataire nommé
- [ ] Supervision en place (voir `docs/08-supervision.md`)
- [ ] Retour arrière documenté et essayé
- [ ] Connecteurs produits confirmés en mode simulé
- [ ] Procès-verbal de mise en service signé
