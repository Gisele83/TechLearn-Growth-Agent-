# Audit correctif — préparation à l'exploitation commerciale réelle

**Date :** 16 octobre 2026 · **Révision auditée :** `d6ea77b`
**Nature :** audit en lecture seule. **Aucune modification de code n'a été faite.**

> Contraintes respectées pendant l'audit : aucun dépôt, serveur ou base
> Maintrix/EduCore touché ; aucun envoi réel ; aucune modification DNS ;
> aucun secret dans ce rapport.

---

## Verdict en une page

Le système tient ses promesses **en mode simulé**. Il n'est pas prêt pour un
envoi réel, et l'écart ne tient pas à des détails.

Trois anomalies P0 concernent toutes le même point : **ce qui se passe quand
un message part vraiment**. Elles sont invisibles en simulation parce que le
fournisseur mock garde tout en mémoire dans un processus unique.

| | P0 | P1 | P2 |
|---|---|---|---|
| Sécurité des communications | 3 | 3 | 1 |
| Intégrations | 0 | 3 | 2 |
| Conservation et exploitation | 0 | 1 | 2 |
| Efficacité commerciale | 0 | 2 | 1 |

**Recommandation : ne pas activer `GA_EMAIL_SENDING_ENABLED` avant correction
des trois P0.** Le reste peut suivre par étapes.

---

# Priorité 1 — Sécurité des communications

## P0-1 · La désinscription annoncée dans chaque message n'existe pas

**Où.** `services/outreach.py:72` et `:177`

```python
unsubscribe_base_url: str = "https://growth.example.org/opposition",   # ligne 72
unsubscribe_url = f"{unsubscribe_base_url}?c={content_digest}"          # ligne 177
```

**Le fait.** Chaque message porte un en-tête `List-Unsubscribe` pointant vers
`growth.example.org` — un domaine d'exemple réservé (RFC 2606) — sur un chemin
`/opposition` qui **n'est servi par aucune route**. La seule route publique de
l'application est `/health` (`api/routers/system.py:22`) ; toutes les autres
exigent un jeton (`api/deps.py`, `current_user`).

**Pourquoi c'est P0.** Trois conséquences cumulatives :

1. **Conformité.** Depuis février 2024, Gmail et Yahoo exigent des expéditeurs
   de volume une désinscription en un clic **fonctionnelle**. Un en-tête qui
   mène à une erreur est pire que pas d'en-tête : il signale une tentative.
2. **Juridique.** Le message promet un droit d'opposition qu'il ne permet pas
   d'exercer. Le RGPD (art. 21) exige que l'opposition soit aussi facile à
   exercer qu'à annoncer.
3. **Opérationnel.** Le destinataire qui ne peut pas se désinscrire clique sur
   « courrier indésirable ». C'est précisément le mécanisme qui dégrade la
   réputation du domaine — le risque documenté en D2.

**Correction proposée.**

- Route publique `POST /unsubscribe/{token}` (RFC 8058 impose POST pour le
  un-clic) **et** `GET /unsubscribe/{token}` servant une page de confirmation
  pour le clic humain.
- Exemptée d'authentification, exemptée de la limitation de débit globale mais
  dotée d'une limitation propre par IP.
- Elle écrit dans `suppression_entries` via `add_suppression`, portée
  `global`, motif `opt_out`, source `unsubscribe_link`.
- Idempotente : un second clic renvoie la même confirmation.
- `GA_PUBLIC_BASE_URL` remplace l'URL en dur ; l'application **refuse de
  démarrer** si l'envoi réel est actif et que cette variable est vide.

**Tests associés.** Désinscription sans jeton d'authentification ; second clic
idempotent ; jeton inconnu → 404 sans fuite d'information ; après
désinscription, `send_draft` refuse avec `reason_code="suppressed"` ;
l'en-tête `List-Unsubscribe` contient bien l'URL publique configurée.

---

## P0-2 · Un incident entre l'envoi et la validation en base provoque un second envoi

**Où.** `services/outreach.py:188` → `:199`

```python
result = email_provider.send(message, idempotency_key=f"send:{content_digest}")   # 188
...
_record_event(session, draft=draft, event_type=OutreachEventType.SENT, ...)       # 199
```

Le message est remis au fournisseur **avant** que la trace ne soit durable.
La transaction n'est validée que plus tard, par la route appelante.

**Démontré.** Exécution reproductible : envoi, puis `rollback` simulant un
processus tué avant le commit, puis reprise.

```
envoi    : sent=True · boîte d'envoi = 1
incident : processus tué avant le commit — la transaction est perdue
reprise  : sent=True motif='' · boîte d'envoi = 1
>>> LE DESTINATAIRE A REÇU 2 FOIS LE MÊME MESSAGE
```

**Aggravant.** La mémoire d'idempotence du fournisseur SMTP est un `set` en
RAM (`connectors/email/smtp.py:59`, `:62`, `:87`). Elle disparaît au
redémarrage et n'est pas partagée entre processus : elle ne protège que contre
un rejeu immédiat dans le même worker.

**Correction proposée.** Inverser l'ordre et rendre la réservation durable :

1. **Réserver** avant d'envoyer : insérer l'événement en statut `sending` avec
   la clé d'idempotence, puis **commit**. La contrainte d'unicité sur
   `idempotency_key` devient alors un verrou distribué réel.
2. **Envoyer** une fois la réservation acquise.
3. **Confirmer** : passer l'événement à `sent`, enregistrer l'identifiant
   renvoyé par le fournisseur, commit.
4. Une réservation restée en `sending` au-delà d'un délai est **incertaine** :
   elle n'est ni rejouée ni effacée automatiquement ; elle est signalée pour
   arbitrage humain. En cas de doute sur un envoi, ne pas renvoyer est le
   moindre mal.

**Tests associés.** Incident après remise au fournisseur → la reprise refuse
avec `reason_code="already_sent"` et la boîte d'envoi reste à 1 ; incident
avant remise → la reprise envoie une fois ; réservation orpheline → statut
`uncertain` et aucun renvoi automatique.

---

## P0-3 · Deux envois concurrents franchissent ensemble le contrôle anti-doublon

**Où.** `services/outreach.py:126-135`

```python
already_sent = session.execute(select(func.count())...).scalar_one()   # 128
if already_sent:
    return _block("duplicate", ...)
```

Lecture puis écriture, sans verrou : classique *time-of-check to time-of-use*.

**Démontré.**

```
worker A lit : 0 envoi enregistré -> contrôle franchi
worker B lit : 0 envoi enregistré -> contrôle franchi
>>> LES DEUX SONT AUTORISÉS À ENVOYER.
```

La contrainte d'unicité sur `outreach_events.idempotency_key` fait bien échouer
la **seconde écriture** — mais après que les deux messages sont partis. Elle
protège la base, pas le destinataire.

*Note méthodologique :* la démonstration porte sur la lecture, car SQLite
sérialise les écritures. PostgreSQL, la cible de production, utilise MVCC et
laisserait les deux transactions progresser jusqu'au commit.

**Correction proposée.** Identique à P0-2 : la réservation committée avant
l'envoi transforme la contrainte d'unicité en verrou effectif. Le perdant de
la course reçoit une violation d'unicité **avant** d'avoir envoyé quoi que ce
soit, et retourne `duplicate`.

**Tests associés.** Deux sessions concurrentes sur le même brouillon → un seul
message dans la boîte d'envoi, un seul événement `sent`, le second retourne
`duplicate`. À exécuter sur PostgreSQL, pas sur SQLite (voir plan de tests).

---

## P1-4 · Le jeton de désinscription est un condensat du contenu

**Où.** `services/outreach.py:127`

```python
content_digest = digest(f"{draft.recipient_email}|{draft.subject}|{draft.body}", length=32)
```

Ce condensat sert **à la fois** de clé d'idempotence et de jeton dans l'URL de
désinscription (`:177`). Trois défauts :

- **Déterministe.** Qui connaît l'adresse, l'objet et le corps reconstitue le
  jeton. Les gabarits étant connus, l'espace à deviner est réduit.
- **Non révocable.** Aucun moyen d'invalider un lien diffusé.
- **Non résoluble.** Le jeton n'est stocké nulle part : même avec un endpoint,
  rien ne permet de remonter au prospect.

**Correction proposée.** Séparer les deux usages. Table
`unsubscribe_tokens` : jeton aléatoire (`secrets.token_urlsafe(32)`), stocké
**haché** (SHA-256), lié au prospect et à la campagne, horodaté, révocable.
La clé d'idempotence reste un condensat de contenu — c'est son rôle.

**Tests associés.** Deux messages au même contact → deux jetons distincts ;
le jeton en clair n'apparaît ni en base ni dans les journaux ; un jeton révoqué
est refusé ; entropie ≥ 256 bits.

---

## P1-5 · Simulé et réel sont indistinguables dans les statuts et les indicateurs

**Où.** `services/outreach.py:189`, `services/analytics.py:140`

```python
simulated = email_provider.provider == "mock" or not settings.can_send_email()  # 189
...
contacted=counts.get(OutreachEventType.SENT, 0),                                 # analytics:140
```

`simulated` n'existe que dans la charge utile JSON de l'événement et dans la
réponse HTTP. Ce n'est **ni une colonne, ni un statut, ni un filtre
d'indicateur**. `DraftStatus` ne comporte pas de valeur distincte
(`models/enums.py:64-70`).

**Conséquence.** Après une répétition en simulation, le tableau de bord
affiche des « contactées » que rien ne distingue d'envois réels. Le bilan à
90 jours des campagnes pilotes serait faussé par les essais.

**Correction proposée.** Colonne `is_simulated` (booléenne, indexée) sur
`outreach_events` ; statut `DraftStatus.SENT_SIMULATED` distinct ; les KPI ne
comptent **que** le réel par défaut, avec un paramètre explicite pour inclure
les simulations ; l'interface affiche un bandeau « données de répétition »
quand des simulations sont incluses.

**Tests associés.** Un envoi simulé ne compte pas dans `contacted` ; un envoi
réel compte ; migration marquant l'existant comme simulé (puisque aucun envoi
réel n'a eu lieu à ce jour).

---

## P1-6 · Idempotence SMTP non durable

**Où.** `connectors/email/smtp.py:59`, `:62`, `:87`

Couverte par la correction P0-2 : l'idempotence remonte au niveau de la base,
le fournisseur n'en porte plus la responsabilité.

---

## P2-7 · Aucune limitation de débit à l'envoi

Les plafonds existants portent sur la **fréquence commerciale** (par contact,
par campagne). Rien ne limite le **débit technique** vers le serveur SMTP, qui
coupe généralement au-delà d'un certain rythme. À traiter au moment du choix
du prestataire.

---

# Priorité 2 — Validation des intégrations

## P1-8 · L'idempotence HubSpot repose sur un index à cohérence différée

**Où.** `connectors/crm/hubspot.py:194-216`, appelé en `:218`, `:240`, `:263`,
`:283`, `:299`

L'adaptateur relit sa propre clé via `POST /crm/v3/objects/{type}/search`.
Or **l'index de recherche HubSpot n'est pas immédiatement cohérent** : un objet
créé n'y apparaît qu'après un délai. Une reprise dans cette fenêtre ne trouve
rien et **crée un doublon** — exactement ce que le mécanisme vise à éviter.

Le mock (`connectors/crm/mock.py`) est, lui, immédiatement cohérent : les tests
actuels ne peuvent pas révéler cet écart.

**Correction proposée.** Tenir la correspondance côté Growth Agent dans
`crm_mappings` (la table existe déjà, `models/prospect.py`) : on y écrit la
clé d'idempotence et l'identifiant distant dès la création. La recherche
HubSpot ne sert plus que de filet en cas de table désynchronisée.

## P1-9 · Le plafond de débit CRM configuré n'est appliqué nulle part

**Où.** `config.py:56` — `crm_rate_limit_per_second: float = 4.0`

Cette variable est déclarée et **référencée nulle part ailleurs dans le code**
(vérifié par recherche sur l'ensemble du paquet). Elle donne une assurance
fausse : un lecteur de la configuration croit le débit maîtrisé.

C'est d'autant plus gênant que les endpoints `/search` de HubSpot ont une
limite spécifique plus basse que la limite générale, et que l'adaptateur les
sollicite à chaque écriture (voir P2-11).

**Correction proposée.** Soit appliquer la valeur par un limiteur à jetons dans
`_request`, soit supprimer la variable. Ne pas laisser un réglage inopérant.

## P1-10 · Aucune association entre les objets CRM créés

**Où.** `connectors/crm/hubspot.py` — aucune occurrence de `associations`.

Entreprises, contacts, affaires, tâches et notes sont créés **isolément**. Dans
HubSpot, une affaire non associée à une entreprise n'apparaît pas dans la fiche
de celle-ci : l'équipe commerciale verrait des objets orphelins.

**Correction proposée.** Utiliser l'API d'associations v4 après chaque création
liée, et couvrir par un test contre un portail bac à sable.

## P2-11 · Deux appels API par écriture

Chaque `upsert_*` fait une recherche puis une écriture. La consommation de
quota est doublée. Résolu de fait par P1-8 (la table de correspondance évite
la recherche dans le cas courant).

## P2-12 · Couverture du fournisseur de recherche au Cameroun

`MockSearchProvider` (`connectors/search/mock.py`) contient 8 organisations
fictives, dont 6 camerounaises — un jeu de démonstration, pas une source.
`HTTPSearchProvider` est générique et n'a jamais été branché.

**Évaluation.** Les annuaires d'entreprises commerciaux (Apollo, Clay) couvrent
mal l'Afrique centrale. Le risque, déjà consigné en R5, est que l'abonnement
soit payé pour une couverture quasi nulle sur le marché prioritaire.

**Protocole d'évaluation proposé, avant tout abonnement :** constituer à la
main une liste de 30 organisations camerounaises correspondant aux deux profils
cibles, puis mesurer sur un essai gratuit combien le fournisseur retrouve, avec
un contact nommé et une source datée. En dessous de 30 %, l'automatisation de
la découverte n'a pas d'intérêt sur ce marché et l'import manuel — déjà
implémenté et contraint à au moins une source — reste la bonne voie.

## Préparer les tests d'intégration réels

**Rien de ce qui suit ne doit être lancé sans votre accord explicite.**

| Intégration | Environnement | Ce qui serait vérifié | Prérequis |
|---|---|---|---|
| HubSpot | Portail *developer test account* gratuit | Propriété personnalisée d'idempotence, cohérence différée de l'index, associations, format `hs_timestamp`, 429 et `Retry-After` | Jeton d'application privée en lecture-écriture sur un portail **vide** |
| LLM | Compte du fournisseur avec plafond de dépense | Conformité au schéma, consommation réelle de jetons, latence | Clé + plafond mensuel strict |
| Recherche | Essai gratuit | Protocole de couverture ci-dessus | Essai sans engagement |
| SMTP | **Non testé tant que D2 n'est pas accordé** | — | Autorisation DNS |

Chaque test d'intégration serait marqué `@pytest.mark.integration`, exclu de
la suite par défaut, et jamais exécuté en intégration continue.

---

# Priorité 3 — Conservation et exploitation

## Vérification du service de purge R3

**Résultat : le code est conforme, la planification n'est pas active.**

| Point | Constat |
|---|---|
| Respect des échéances | ✅ `retention_until` obligatoire, `legal_hold` respecté |
| Oppositions préservées | ✅ `suppression_entries` dans `PROTECTED_TABLES`, vérifié par test |
| Garde-fous | ✅ 6, dont l'arrêt avant écriture au-delà de 50 % d'une table |
| Simulation par défaut | ✅ |
| Tests | ✅ 41 |
| **Planification effective** | ❌ **aucune minuterie systemd, aucune entrée cron active** |
| `GA_PURGE_ENABLED` | ❌ `false` — rien ne sera jamais purgé en l'état |

**Ce n'est pas une anomalie** : rien n'est déployé, et la purge est désactivée
par conception. Mais la fonctionnalité **ne protège personne aujourd'hui**.
Elle n'entrera en service qu'avec la procédure progressive décrite dans
`docs/07-politique-de-retention.md`.

## P2-13 · Sauvegardes : procédure documentée, jamais exécutée

La politique (30 j / 90 j / 13 mois) et la procédure de restauration sont
écrites (`docs/03-deploiement.md`, `docs/07`). Le comportement purge/sauvegarde
a été vérifié en laboratoire sur SQLite. **Aucune sauvegarde PostgreSQL réelle
n'a été prise ni restaurée**, puisqu'il n'existe aucune instance.

À faire à l'étape 7 du déploiement, et pas avant : la seule vérification qui
compte est une restauration réussie.

## Santé, surveillance, restauration

Existant : `/health` (base + état des connecteurs), `/api/v1/system/retention`,
`/api/v1/system/retention/runs`, identifiant de corrélation par requête.

Manquant pour l'exploitation : sonde de vivacité distincte de la sonde de
disponibilité, métriques exportables, alertes sur les seuils qui comptent
(envois bloqués, réservations incertaines, échecs de purge, expiration du
certificat, succès de la sauvegarde).

---

# Priorité 4 — Efficacité commerciale

## P1-14 · Aucun objet « opportunité » ni « prochaine action »

**Où.** Recherche sur `models/` : aucune classe `Opportunity`, aucun champ
`next_action`, `next_action_at` ou `owner_id`.

Le parcours s'arrête à l'étape du prospect (`ProspectStage`). Il n'existe
aucun moyen de répondre à : *qui doit faire quoi, sur quel dossier, cette
semaine ?* — la question que se pose une équipe commerciale chaque lundi.

Les tâches créées par `SalesCoordinatorAgent` partent dans le CRM et ne sont
pas relues : le tableau de bord ne les voit pas.

## P1-15 · Le parcours s'interrompt après la démonstration

Découverte → qualification → rédaction → validation → envoi → réponse :
complet et testé de bout en bout. Au-delà :

| Étape | État |
|---|---|
| Démonstration planifiée | ⚠️ événement enregistré, pas de calendrier ni de rappel |
| Démonstration tenue | ❌ aucune saisie de compte rendu |
| Proposition | ⚠️ devis calculé, jamais transmis ni suivi |
| Pilote activé | ⚠️ lu depuis le connecteur produit, **désactivé** |
| Renouvellement | ⚠️ alerte calculée, aucun destinataire |

## P2-16 · Tableau de bord fondé sur les conversions réelles

**Proposé**, après correction de P1-5 (sans quoi il mélangerait répétitions et
réalité) :

- **Entonnoir par cohorte de départ** plutôt qu'en volumes cumulés : seule une
  lecture par cohorte dit si la conversion s'améliore.
- **Délais médians** entre étapes — où le cycle se bloque réellement.
- **Taux par motif de perte**, alimenté par les objections classées.
- **Envois bloqués par motif** : déjà mesuré, pas encore affiché.
- **Prochaines actions en retard**, par personne (dépend de P1-14).

## Protocole de validation sur deux cohortes

Proposé pour l'après-correction, **sous réserve du cadre juridique camerounais
et de l'autorisation D2** :

| | Cohorte A — Maintrix | Cohorte B — EduCore |
|---|---|---|
| Taille | 15 organisations | 15 établissements |
| Géographie | Cameroun | Cameroun |
| Durée | 4 semaines | 4 semaines |
| Critère de poursuite | ≥ 2 réponses et ≥ 1 démonstration | idem |
| Critère d'arrêt | 0 réponse, ou ≥ 1 plainte | idem |

Quinze organisations ne permettent pas de mesurer un taux — c'est assumé.
L'objectif de cette étape n'est pas statistique : il est de vérifier que la
chaîne technique tient en conditions réelles et que les messages ne provoquent
pas de rejet. Les objectifs à 90 jours restent exploratoires.

---

# Risques résiduels après corrections

| Risque | Pourquoi il subsiste |
|---|---|
| Cadre juridique camerounais non établi | Hors périmètre technique. **Reste bloquant.** |
| Autorisation D2 non accordée | Dossier prêt, non transmis |
| Adaptateurs réels jamais exécutés | Levable seulement en bac à sable |
| Couverture de prospection au Cameroun | Mesurable seulement en essai réel |
| Restauration PostgreSQL non éprouvée | Nécessite une instance |
| Réservations d'envoi incertaines | Par conception : un doute humain vaut mieux qu'un doublon |
| Masquage heuristique | Reconnaît des formes, pas tout |

---

# Plan de correction proposé

**Lot 1 — débloquer l'envoi réel** (P0-1, P0-2, P0-3, P1-4, P1-5, P1-6)
Désinscription publique, réservation avant envoi, jetons aléatoires,
séparation simulé/réel. Migration `0003`. Tests de concurrence sur PostgreSQL.
*Rien d'autre ne devrait être entrepris avant.*

**Lot 2 — fiabiliser le CRM** (P1-8, P1-9, P1-10, P2-11)
Correspondance locale, limiteur de débit, associations, tests d'intégration
marqués et exclus par défaut.

**Lot 3 — exploitation** (P2-13, santé et surveillance)
Sondes, métriques, alertes. Déploiement par étapes selon `docs/03`.

**Lot 4 — commercial** (P1-14, P1-15, P2-16)
Opportunités, prochaines actions, tableau de bord par cohorte.

Les lots 2 à 4 sont indépendants. Le lot 1 conditionne tout envoi réel.

---

# Plan de déploiement

Inchangé par rapport à `docs/03-deploiement.md`, avec trois points d'arrêt
ajoutés :

1. **Avant l'étape 5 (TLS)** — vérifier que `/unsubscribe/{token}` répond
   publiquement en HTTP et en HTTPS, depuis l'extérieur.
2. **Avant l'étape 6 (recette)** — exécuter les tests de concurrence sur
   l'instance PostgreSQL réelle, pas sur SQLite.
3. **Avant toute activation d'envoi** — procès-verbal signé attestant : D2
   accordé, cadre juridique camerounais tranché, approbateurs nommés,
   restauration de sauvegarde réussie.

`GA_EMAIL_SENDING_ENABLED` reste à `false` jusqu'à ce procès-verbal.
