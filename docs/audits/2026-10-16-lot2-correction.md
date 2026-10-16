# Lot 2 — Rapport de correction

**Date** : 16 octobre 2026
**Périmètre autorisé** : fiabiliser le CRM — P1-8, P1-9, P1-10, P2-11.
**Hors périmètre** : lots 3 et 4 — non commencés.
**Déploiement** : aucun. Rien n'a été mis en production, et aucun portail CRM
réel n'a été contacté.

---

## 1. Le défaut central, reproduit avant correction

HubSpot n'offre pas d'en-tête d'idempotence. L'adaptateur compensait en
écrivant la clé dans une propriété personnalisée, puis en la relisant via
`POST /crm/v3/objects/{type}/search` avant chaque création.

**Cet index est à cohérence différée.** Un objet créé n'y apparaît qu'après un
délai. Une reprise après incident dans cette fenêtre ne trouve rien — et crée
un doublon, exactement ce que le mécanisme devait empêcher.

Reproduit contre un portail HubSpot simulé au niveau HTTP, dont l'index ne
rattrape jamais son retard :

```
objets créés dans le CRM : 2  -> ['1000', '1001']
identifiants rendus      : 1000, 1001
doublon                  : OUI
appels pour 2 upserts    : 4   (search + write, puis search + write)
```

Le mock applicatif, lui, est immédiatement cohérent : aucun test existant ne
pouvait révéler l'écart. C'est la raison d'être de la doublure HTTP
(`tests/hubspot_double.py`), qui reproduit le portail — délai d'indexation,
429 avec `Retry-After`, 5xx, associations v4 — et compte les appels.

---

## 2. Corrections

### 2.1 L'idempotence est tenue localement (P1-8, P2-11)

`crm_mappings` devient la mémoire d'idempotence des écritures CRM. La clé et
l'identifiant distant y sont écrits **dans la même transaction que le reste du
travail**, donc immédiatement relisibles. La recherche distante ne sert plus
que de filet, quand la mémoire locale est vide.

L'ordre compte : l'identifiant est mémorisé avant tout autre appel. Si le
processus meurt entre la création et l'écriture locale, la reprise ne saura
pas que l'objet existe.

| | Avant | Après |
|---|---|---|
| Source de vérité de « déjà créé ? » | index HubSpot, différé | base locale, immédiate |
| Rejeu immédiat | **doublon** | même identifiant rendu |
| Après redémarrage | doublon | même identifiant rendu |
| Base locale perdue | — | filet : recherche distante |
| Appels pour une synchro répétée | 2 | 1 (0 pour tâches et notes) |

La séparation passe par un port, `CRMIdempotencyStore`, pour que les
connecteurs n'importent pas les modèles. Trois implémentations :
`DatabaseIdempotencyStore` (durable), `InMemoryIdempotencyStore` (tests),
`NullIdempotencyStore` (défaut explicite — un adaptateur sans mémoire reste
exposé au doublon, et c'est écrit noir sur blanc plutôt que laissé en oubli).

L'adaptateur est partagé entre requêtes, la mémoire appartient à une
transaction : `with_idempotency_store()` renvoie une **vue** plutôt que de
muter l'adaptateur. Un attribut mutable partagé ferait écrire une requête dans
la transaction d'une autre.

Le filet distant est désactivable (`GA_CRM_SEARCH_FALLBACK`). Il reste actif
par défaut : un doublon dans le CRM d'un client se voit, et se paie plus cher
qu'un appel d'API.

### 2.2 Le plafond de débit s'applique vraiment (P1-9)

`crm_rate_limit_per_second` était déclaré et référencé nulle part. Un
`TokenBucket` l'applique désormais à **chaque requête, réessais compris** — un
service qui répond 429 est précisément celui qu'il ne faut pas marteler.

Le seau autorise une petite rafale puis régule : c'est le comportement attendu
d'un quota par seconde, qui n'interdit pas deux appels rapprochés, seulement
un rythme soutenu trop élevé.

### 2.3 Les objets créés sont associés (P1-10)

Aucune association n'était posée. Dans HubSpot, une affaire non associée
n'apparaît pas dans la fiche de son entreprise : l'équipe commerciale verrait
des objets orphelins. L'API v4 est appelée après chaque création liée —
contact→entreprise, affaire→entreprise, affaire→contact, tâche→contact,
note→contact et note→entreprise. Une association vers un identifiant absent
n'est pas tentée : ce serait un 404 assuré.

### 2.4 Tests d'intégration marqués et exclus (P2-11)

Marqueur `integration`, exclu par `addopts` dans `pyproject.toml`. Une suite
qui part sur le réseau n'est ni reproductible, ni gratuite, ni exécutable hors
ligne. `tests/test_crm_integration.py` est écrit et prêt ; il vérifie ce que la
doublure ne peut pas vérifier — l'existence de la propriété personnalisée dans
le portail, les noms de propriétés réels, l'acceptation des chemins
d'association, le délai d'indexation effectif.

---

## 3. Trois défauts trouvés en chemin

### D5 — Une deuxième tâche pour le même prospect plantait

Non listé dans l'audit initial. `crm_mappings` portait
`uq_crm_mapping_local (crm_provider, object_type, local_id)`. Or un prospect
reçoit plusieurs tâches : une relance, puis une démonstration. La seconde
échouait.

```
task-1 : enregistré
task-2 : ÉCHEC — IntegrityError
          UNIQUE constraint failed: crm_mappings.crm_provider,
          crm_mappings.object_type, crm_mappings.local_id
```

La contrainte est juste pour une entreprise ou un contact, fausse pour une
tâche ou une note. L'unicité qui compte est celle de la clé d'idempotence ;
c'est elle qui la porte désormais. La recherche par objet local garde un
index, non unique.

### D6 — Mon propre limiteur de débit enflait

Trouvé par le test qui mesure l'attente, pas par relecture. Avec 2 appels/s et
6 appels d'affilée, l'attente cumulée observée était **5,0 s au lieu de 2,0 s**,
et elle croissait à chaque appel.

Cause : `_last` est une horloge *virtuelle*, en avance sur le temps réel quand
un appelant a déjà réservé sa part du débit. La comparer au temps réel sans
précaution produisait un écoulement négatif, un solde de jetons négatif, et des
attentes qui enflaient. Un limiteur dont le retard s'accumule finit par bloquer
le connecteur au lieu de le réguler. Corrigé, et le test vérifie désormais que
l'attente reste constante.

### D7 — Une note déjà écrite pouvait être réécrite

Les tâches et les notes sont des traces datées d'un moment, pas des fiches
qu'on tient à jour. L'ancien code les traitait comme les autres objets ; une
reprise aurait réécrit le corps d'une note par le texte courant, effaçant ce
qu'elle disait. `_write(..., patch_existing=False)` les en exempte.

---

## 4. Preuves

### 4.1 Suite automatisée

```
376 passed, 4 deselected            (dont 5 sur PostgreSQL)
  tests/test_crm_idempotency.py     16 tests
  tests/test_connectors_crm.py      +4 tests de limiteur
  tests/test_crm_integration.py     4 tests, exclus par défaut
 27 passed                          (web)
ruff check / format                 All checks passed
tsc --noEmit                        OK
make secrets                        Aucun secret détecté
```

Un test conserve volontairement la **démonstration du défaut** :
`test_without_local_memory_an_immediate_retry_creates_a_duplicate` échoue si
la doublure cesse de reproduire la cohérence différée. Sans lui, la correction
n'aurait plus de point de comparaison.

### 4.2 Concurrence réelle sur PostgreSQL

Dix travailleurs écrivent la même clé d'idempotence au même instant, chacun
croyant avoir créé « son » objet distant :

```
test_concurrent_crm_writes_create_one_mapping PASSED
→ 1 correspondance en base ; les dix travailleurs lisent le même identifiant
```

Comme pour l'envoi, c'est une contrainte d'unicité qui tranche, pas une
lecture préalable. Le perdant est rattrapé par un point de reprise
(`SAVEPOINT`) : il relit et accepte la ligne de l'autre, sans perdre la
transaction en cours.

### 4.3 Migration 0004, vérifiée sur PostgreSQL

Sur des données synthétiques représentatives — trois correspondances
importées, sans clé d'idempotence :

```
=== ALLER 0003 -> 0004 ===
indexes : crm_mappings_pkey, ix_crm_mapping_local,
          uq_crm_mapping_idempotency, uq_crm_mapping_remote
contraintes : crm_mappings_pkey, uq_crm_mapping_remote   (uq_crm_mapping_local retirée)
lignes conservées : 3   dont sans clé : 3
```

Sémantique de l'index partiel, vérifiée directement en SQL :

```
1. plusieurs tâches pour un prospect           : ACCEPTÉ
2. deux lignes avec la même clé                : REFUSÉ
   ERROR: duplicate key value violates unique constraint "uq_crm_mapping_idempotency"
3. plusieurs correspondances sans clé          : ACCEPTÉ
```

Le point 3 est celui qui justifie un index *partiel* : les correspondances
importées d'un CRM existant n'ont pas de clé, et un index total les aurait
rendues mutuellement exclusives.

Aller-retour, avec trois tâches sur un même prospect en base :

```
=== RETOUR 0004 -> 0003 ===
contrainte locale rétablie : 1
colonne idempotency_key restante : 0
lignes restantes : 5   (les 2 correspondances surnuméraires supprimées)
=== ALLER À NOUVEAU ===
0004_crm_idempotency (head)
```

Le retour ne peut pas rétablir `uq_crm_mapping_local` sans supprimer les
correspondances que la contrainte interdisait. La migration le fait
explicitement, en gardant la plus ancienne, plutôt que d'échouer ou de le
faire en silence.

---

## 5. Respect des contraintes

| Contrainte | Vérification |
|---|---|
| Aucune modification Maintrix/EduCore | Aucune |
| Aucun appel vers un CRM réel | Toutes les épreuves passent par `httpx.MockTransport`. `GA_CRM_PROVIDER` reste `mock`, `GA_CRM_API_TOKEN` vide |
| Aucun secret dans le code, les logs, les rapports | `make secrets` : rien détecté. Le jeton bac à sable se lit dans l'environnement |
| Ne pas confondre simulé et intégration réelle | Marqueur `integration`, exclu par défaut, et la ligne « Validé contre un compte HubSpot réel » reste ❌ dans la recette |
| Ne rien annoncer sans preuve reproductible | § 4 |
| Aucun envoi réel, aucune modification DNS | Aucun |
| Base PostgreSQL temporaire supprimée | Fait — § 7 |

---

## 6. Risques résiduels et limites

**R8 — L'adaptateur HubSpot n'a toujours jamais vu un portail réel.** C'est la
limite principale, et elle est inchangée. Tout ce qui précède est prouvé contre
une doublure écrite d'après la documentation. Trois choses ne peuvent pas être
vérifiées autrement : la propriété personnalisée
`growth_agent_idempotency_key` doit exister dans le portail (sinon la
recherche échoue en 400), les noms de propriétés doivent être exacts, et les
identifiants d'étape d'affaire (`dealstage`) dépendent du pipeline configuré.
`tests/test_crm_integration.py` les couvre — il reste à l'exécuter contre un
bac à sable.

**R9 — Le filet distant coûte un appel par première écriture.** P2-11 est
réduit, pas supprimé : une synchronisation répétée passe de 2 appels à 1, mais
la première création en coûte toujours 2. Le désactiver suppose la mémoire
locale fiable, donc une politique de sauvegarde éprouvée — qui ne l'est pas
encore (risque hérité du lot 1).

**R10 — Les associations ne sont pas vérifiées après coup.** On les pose, on ne
relit pas. Un échec partiel — objet créé, association refusée — laisserait un
orphelin sans que rien ne le signale. L'erreur remonterait à l'appelant, mais
aucune réconciliation n'est prévue.

**R11 — Le type d'association est celui par défaut.** HubSpot permet des types
d'associations étiquetés (« décideur », « utilisateur »). Nous utilisons
`associations/default`, qui convient pour un démarrage mais ne porte aucune
sémantique commerciale.

**R12 — Le limiteur est par processus.** Plusieurs instances de l'application
consommeraient chacune leur propre quota et dépasseraient le plafond réel du
portail. Un limiteur partagé via Redis serait nécessaire au-delà d'un
processus — à traiter au lot 3, avec la supervision.

**R13 — La mémoire d'idempotence est liée à la transaction.** C'est voulu : si
la requête échoue, les correspondances disparaissent avec elle. Conséquence
assumée — un objet peut exister dans le CRM sans correspondance locale, si le
processus meurt entre la réponse de HubSpot et le `commit`. Le filet distant
le rattrape ; c'est une raison de plus de ne pas le désactiver.

---

## 7. Nettoyage

Le cluster PostgreSQL temporaire (`/var/tmp/growth-pgtest`) a été recréé pour
vérifier la migration 0004 et la concurrence, puis arrêté et supprimé, données
et volumes compris. Mêmes conditions qu'au lot 1 : aucun port TCP à l'écoute,
socket Unix seule, données entièrement synthétiques, aucune connexion vers
Maintrix ou EduCore.

---

## 8. Ce qui n'a pas été fait

Les lots 3 et 4 n'ont pas été commencés. Aucun déploiement. Aucun portail CRM
réel contacté. `GA_EMAIL_SENDING_ENABLED` reste à `false`.
