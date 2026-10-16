# Lot 1 — Rapport de correction

**Date** : 16 octobre 2026
**Périmètre autorisé** : désinscription publique, réservation atomique avant
envoi, jetons aléatoires, séparation simulé/réel, migration 0003.
**Hors périmètre** : lots 2, 3 et 4 — non commencés, conformément à la consigne.
**Déploiement** : aucun. Rien n'a été mis en production.

---

## 1. Ce qui a été fait

### 1.1 Désinscription réellement fonctionnelle

Avant, le lien de désinscription pointait vers une page qui n'existait pas, et
le « jeton » était un condensat du contenu du message : déterministe,
reconstituable par quiconque connaissait le texte envoyé, non révocable et
impossible à rattacher à une personne.

Désormais :

| | Avant | Après |
|---|---|---|
| Jeton | condensat du contenu | `secrets.token_urlsafe(32)` — 256 bits |
| Stockage | — | empreinte SHA-256 ; le clair n'existe qu'une fois, dans le message |
| Route | inexistante | `POST /unsubscribe/{jeton}` (RFC 8058) et `GET` (clic humain) |
| Authentification | — | aucune, par construction |
| Révocation | impossible | `revoked_at`, et le jeton cesse de fonctionner |

Fichiers : `services/unsubscribe.py` (nouveau), `api/routers/public.py`
(nouveau), `models/outreach.py` (table `unsubscribe_tokens`).

Les routes vivent **hors** du préfixe `/api/v1` : un lien qui part dans un
email doit rester court et stable, et ne pas casser à la prochaine version de
l'API. La page de confirmation est autonome — ni script, ni ressource tierce,
ni cookie : une page de désinscription qui chargerait une police distante
renseignerait ce tiers sur la personne qui se désinscrit.

Le `GET` modifie l'état, ce qui n'est pas canonique. C'est assumé : imposer un
second clic à quelqu'un qui demande qu'on le laisse tranquille est une plus
mauvaise réponse que le risque de préchargement par un antivirus de messagerie
— lequel désinscrit une personne qui l'avait de toute façon demandé.

`GA_PUBLIC_BASE_URL` devient obligatoire pour l'envoi réel : sans adresse
publique, le lien pointerait vers `localhost`. `can_send_email()` le refuse.

### 1.2 Réserver, envoyer, confirmer

L'ancien contrôle anti-doublon lisait la base, puis écrivait. Deux défauts
démontrés avant correction :

1. **Reprise après incident** — le message était remis au fournisseur, puis
   l'événement était écrit. Un processus tué entre les deux repartait de zéro
   et renvoyait le message.
2. **Concurrence** — deux processus franchissaient le contrôle ensemble, aucun
   ne voyant l'écriture de l'autre.

Le protocole est maintenant en trois temps :

1. **Réserver** — l'intention d'envoyer est écrite et validée *avant* toute
   remise. La contrainte d'unicité sur `idempotency_key` devient un verrou que
   deux processus ne peuvent pas franchir ensemble.
2. **Envoyer** — remise au fournisseur.
3. **Confirmer** — la réservation passe à `sent`, avec l'identifiant rendu par
   le fournisseur.

Les issues sont distinguées, et c'est le point qui compte :

| Issue | `delivery_state` | Réservation | Reprise |
|---|---|---|---|
| Remis | `sent` | conservée | refusée (`already_sent`) |
| Refus explicite | — | **effacée** | autorisée : rien n'est parti |
| Réponse illisible | `uncertain` | conservée | **jamais automatique** |
| Processus tué | `reserved` | conservée | refusée (`duplicate`) |

Un envoi d'issue inconnue n'est pas rejoué. Renvoyer un message déjà reçu est
pire que de ne rien faire, et seule une personne peut trancher.

### 1.3 Simulé et réel ne se mélangent plus

`OutreachEvent.is_simulated` sépare les deux à la source. Un brouillon répété
porte le statut `sent_simulated`, distinct de `sent`. Les KPI excluent les
répétitions par défaut et exposent `simulated_total` et `uncertain_total`
comme champs structurés — pas seulement dans une note en français, qu'un
tableau de bord aurait dû analyser à la main. `GET /api/v1/kpis?include_simulated=true`
les réintègre, explicitement.

La clé d'idempotence porte le préfixe `sim:` en simulation : essayer puis
envoyer pour de vrai reste possible.

---

## 2. Quatre défauts trouvés *par* les tests de concurrence

Ces quatre-là n'étaient pas dans le diagnostic initial. Ils sont apparus en
lançant douze travailleurs réels sur un PostgreSQL réel — aucun n'aurait été
visible sur SQLite, qui sérialise les écrivains.

### D1 — Le perdant sortait en exception, sans trace

`IntegrityError` était attrapée autour de `session.commit()`. Or la violation
d'unicité remonte au **premier** accès base, c'est-à-dire au `flush()` interne
de `_record_event`, en amont. Résultat : onze travailleurs sur douze
terminaient sur une exception non gérée, et **aucun événement `send_blocked`
n'était écrit**. Un refus invisible dans les KPI — exactement ce que la
conception interdit.

*Correction* : le `try` couvre l'écriture et la validation.
*Preuve* : `motifs de refus : ['duplicate']`, `événements send_blocked : 11`.

### D2 — Le perdant écrasait le statut du gagnant

`_block("duplicate")` affectait `draft.status = BLOCKED` en mémoire. Le
message était bel et bien parti, et le brouillon s'affichait « bloqué ».
Quelqu'un l'aurait renvoyé à la main.

*Correction* : écriture conditionnelle en SQL — `UPDATE … WHERE status NOT IN
('sent','sent_simulated')`. Le fait accompli n'est plus réversible par un
perdant.
*Preuve* : `statut final du brouillon : sent`.

### D3 — La contrainte d'unicité des oppositions ne protégeait rien

`uq_suppression_value_scope` porte sur `(normalized_value, scope, product_id)`.
Pour une opposition **globale**, `product_id` vaut `NULL` — et deux `NULL` ne
sont jamais égaux en SQL. La contrainte ne s'appliquait donc jamais au cas le
plus courant. Huit clics simultanés sur un même lien ont créé huit lignes.

*Correction* : index unique partiel `uq_suppression_global` (`WHERE product_id
IS NULL`), et `add_suppression()` rattrape la collision par point de reprise
(`SAVEPOINT`) plutôt que de perdre la transaction. La migration 0003 déduplique
l'existant en gardant la ligne la plus ancienne — c'est elle qui protège le
mieux le contact.
*Preuve* : `test_concurrent_unsubscribes_do_not_duplicate_the_suppression`.

### D4 — Un message simulé restait modifiable et ré-envoyable

Trois gardes testaient `status == DraftStatus.SENT` et ignoraient le nouveau
`SENT_SIMULATED` : modification du brouillon, demande d'approbation, et le
contrôle d'approbation lui-même. Régression introduite par le nouveau statut.

*Correction* : `SENT_STATUSES` dans `models/enums.py`, utilisé aux trois
endroits.

---

## 3. Preuves reproductibles

### 3.1 Concurrence réelle sur PostgreSQL

```
$ GA_TEST_PG_URL="postgresql+psycopg://growthtest@/growth_test?host=…/sock" \
    pytest tests/test_concurrency_pg.py -v -s

--- concurrence PostgreSQL ---
travailleurs lancés        : 12
réservations réussies      : 1
travailleurs refusés       : 11
motifs de refus            : ['duplicate']
appels au fournisseur      : 1
événements « sent » en base: 1
états en base              : ['sent']
événements « send_blocked »: 11
statut final du brouillon  : sent

4 passed
```

Les douze travailleurs sont de vrais fils d'exécution, avec douze connexions
distinctes, synchronisés par une barrière. Sans la barrière, le premier aurait
fini avant le démarrage du dernier et le test ne prouverait rien.

Les quatre tests : la course elle-même ; la vérification que la contrainte
d'unicité existe bien en base (interrogation de `pg_indexes`, pour qu'une
migration future qui la perdrait fasse rougir le test) ; le comportement après
redémarrage ; et la désinscription concurrente.

### 3.2 Comportement après redémarrage

`test_state_survives_a_restart` ferme le moteur, en ouvre un neuf — nouveau
pool, rien en mémoire — et relit :

- statut du brouillon : `sent` ;
- un seul événement `sent`, à l'état `sent` ;
- une nouvelle tentative est refusée (`already_sent`) **sans appeler le
  fournisseur**.

### 3.3 Pannes aux trois instants demandés

`tests/test_send_resilience.py`, 9 tests :

| Instant de la panne | Observé |
|---|---|
| Avant la remise | réservation `reserved` persistée ; reprise refusée `duplicate` ; boîte d'envoi vide |
| Après la réservation, réponse illisible | `uncertain` ; reprise refusée ; **le fournisseur n'est pas appelé** |
| Après remise, avant confirmation | message parti une fois ; reprise refusée ; le destinataire ne reçoit pas de doublon |
| Refus explicite | réservation effacée ; nouvelle tentative autorisée et réussie |

La panne est simulée par une exception héritant de `BaseException`, hors
d'atteinte de tout `except Exception` du service : c'est fidèle à un processus
tué net.

### 3.4 Imprévisibilité des jetons

`tests/test_unsubscribe.py`, 16 tests. 200 jetons tirés : aucun doublon, aucun
préfixe de 8 caractères partagé, aucun symbole dépassant 10 % de l'alphabet
(un compteur, un horodatage ou un condensat d'adresse échoueraient). La base ne
contient que l'empreinte ; un jeton altéré d'un caractère n'est pas reconnu.

### 3.5 Le lien part vraiment, et il fonctionne

`test_link_from_the_sent_message_actually_unsubscribes` extrait l'URL de
l'en-tête `List-Unsubscribe` du message tel qu'il est remis au fournisseur,
l'utilise, puis vérifie que l'envoi suivant vers la même adresse est refusé
(`suppressed`). Le trajet complet, pas une simulation de trajet.

### 3.6 Simulé jamais compté comme réel

Trois envois simulés → `funnel.contacted == 0`, `simulated_total == 3`, note
explicite. Avec `include_simulated=true` → `contacted == 3`. Un envoi
`uncertain` → `contacted == 0`, `uncertain_total == 1`.

### 3.7 Suite complète

```
354 passed            (dont 4 sur PostgreSQL)
27 passed             (web)
ruff check / format   All checks passed
tsc --noEmit          OK
make secrets          Aucun secret détecté
```

---

## 4. Migration 0003

`apps/api/migrations/versions/0003_unsubscribe_and_reservation.py`

Crée `unsubscribe_tokens`, ajoute `is_simulated`, `delivery_state`,
`provider_message_id`, l'index `ix_event_real` et l'index unique partiel
`uq_suppression_global`. Reprise des données : tous les envois enregistrés à
ce jour sont marqués simulés et remis — c'est exact, aucun envoi réel n'a
jamais eu lieu.

**Testée sur PostgreSQL avec des données synthétiques représentatives**, et
c'est ce qui a permis d'attraper un défaut qu'une base vide aurait caché :

```
NotNullViolation: column "is_simulated" of relation "outreach_events"
                  contains null values
```

Ajouter une colonne `NOT NULL` sans `server_default` échoue dès que la table
contient une ligne. Corrigé par un défaut temporaire, retiré juste après — pour
qu'un oubli côté code se voie au lieu d'être masqué.

Aller-retour vérifié : `0002 → 0003 → 0002 → 0003`, tables et colonnes
correctement créées puis retirées, révision stable. Reprise vérifiée sur trois
lignes synthétiques (`is_simulated = t`, `delivery_state = sent`).

Le retrait du défaut est conditionné au dialecte : SQLite ne sait pas modifier
une colonne existante. Il ne sert qu'aux tests, et un défaut inerte n'y change
rien.

---

## 5. Respect des conditions posées

| Condition | Vérification |
|---|---|
| Aucun port PostgreSQL exposé | `listen_addresses = ''` ; 0 port 543x à l'écoute ; accès par socket Unix seulement |
| Aucune connexion Maintrix/EduCore | Bases présentes : `postgres`, `growth_test`. Rien d'autre |
| Aucune donnée réelle, aucun secret de production | Données fabriquées par les tests ; domaines réservés RFC 2606 |
| Base et volumes supprimés après tests | Fait — voir § 7 |
| Migration testée sur données synthétiques | § 4 |
| Contraintes d'unicité et transactions sous concurrence réelle | § 3.1 ; défaut D3 trouvé par ce biais |
| Pannes avant envoi / après réservation / après acceptation | § 3.3 |
| Aucun email réel envoyé | Aucun fournisseur SMTP n'a été configuré. Les doublures de test retiennent les messages en mémoire. `GA_EMAIL_SENDING_ENABLED` reste `false` |
| Aucune opération sur Maintrix/EduCore | Aucune |

---

## 6. Risques résiduels et limites techniques

**R1 — L'état `uncertain` demande une décision humaine, et rien ne la réclame
encore.** Un envoi d'issue inconnue reste en attente indéfiniment. Il est
visible dans les KPI (`uncertain_total`), mais aucune alerte ni file de travail
ne le remonte. *À traiter en lot 2.*

**R2 — Le fournisseur SMTP réel n'a jamais été exercé.** Tout ce qui précède est
prouvé contre des doublures. Le comportement d'un vrai SMTP — délais, codes de
rejet, duplication côté fournisseur — reste non vérifié. C'est l'objet du
dossier D2, et aucun envoi réel ne doit avoir lieu avant.

**R3 — L'idempotence porte sur l'adresse, plus sur la fiche prospect.** La clé
est `destinataire|objet|corps`. Deux fiches distinctes partageant une adresse
ne recevront le message qu'une fois. C'est plus protecteur qu'avant, mais c'est
un changement de comportement : une même adresse ne peut plus recevoir le même
texte au titre de deux organisations.

**R4 — Les plafonds de fréquence ne comptent que les envois réels, mais
s'appliquent à toute tentative.** Une répétition ne consomme pas le quota d'un
contact, et se heurte pourtant au plafond — pour qu'une simulation prédise
fidèlement ce que ferait un envoi réel.

**R5 — Un `GET` de désinscription peut être déclenché par un préchargement.**
Assumé : voir § 1.1.

**R6 — La reprise après `reserved` orphelin n'est pas automatisée.** Une
réservation laissée par un processus mort bloque définitivement ce message.
C'est volontaire — mais il n'existe pas encore d'écran pour l'arbitrer.
*À traiter en lot 2.*

**R7 — Aucune limitation de débit sur la route publique de désinscription.**
Elle est non authentifiée. Un jeton inconnu ne révèle rien et ne coûte qu'une
lecture indexée, mais rien n'empêche de la marteler. *À traiter en lot 2.*

---

## 7. Nettoyage

Le cluster PostgreSQL temporaire (`/var/tmp/growth-pgtest`) a été arrêté et
supprimé, données et volumes compris, après les tests. Les tests de concurrence
restent dans la suite : ils sont ignorés automatiquement tant que
`GA_TEST_PG_URL` n'est pas défini, et se relancent sur n'importe quel
PostgreSQL jetable.

---

## 8. Ce qui n'a pas été fait

Les lots 2, 3 et 4 n'ont pas été commencés. Aucun déploiement n'a eu lieu.
Aucune modification n'a été apportée à Maintrix ni à EduCore — ni dépôt, ni
base, ni configuration, ni serveur.
