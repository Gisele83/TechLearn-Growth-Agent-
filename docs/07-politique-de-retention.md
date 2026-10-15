# Politique de rétention et purge des données

> **État : implémentée, testée, et désactivée.** `GA_PURGE_ENABLED=false` par
> défaut. Rien n'est supprimé tant que quelqu'un ne l'a pas activée
> explicitement, et même activée, la purge simule tant qu'on n'écrit pas
> `--apply`.

## Pourquoi la purge anonymise au lieu de supprimer

C'est la décision structurante, et elle vient d'une contrainte du modèle de
données : `outreach_events.prospect_id` est en suppression en cascade.
Supprimer un prospect détruirait ses événements de prospection — donc les
chiffres du trimestre passé.

Un système dont les indicateurs changent rétroactivement n'est plus un système
de mesure. La purge **anonymise** donc le prospect : ses champs personnels sont
effacés, sa ligne et son historique subsistent.

| Conservé | Effacé |
|---|---|
| Nom de l'organisation, secteur, pays, effectif | Nom du contact |
| Étape commerciale, score, statut de vérification | Adresse email |
| Événements de prospection (envois, réponses, démos) | Téléphone |
| URL et date des sources — preuve de provenance | Fonction du contact |
| Statut et horodatage des messages | Extrait des sources, corps des messages |

Un prospect n'ayant **aucun** historique commercial n'a rien à préserver : la
question ne se pose pas, puisque l'anonymisation suffit également et laisse
une trace moins trompeuse qu'une ligne disparue.

## Durées de conservation

| Donnée | Durée | Action | Justification |
|---|---|---|---|
| Prospects non convertis | 365 j | Anonymisation | Les coordonnées professionnelles collectées pour une prise de contact n'ont plus de finalité un an après, sans conversion |
| Contenu des messages | 180 j | Effacement du texte | Le corps contient le nom et la fonction du destinataire ; passé six mois, seuls le statut et l'horodatage restent utiles |
| Journal d'audit | 3 ans | Suppression | Traçabilité des décisions commerciales. Le journal ne contient ni secret ni donnée personnelle en clair : le masquage est appliqué à l'écriture |
| Événements de pilote | 5 ans | Suppression | Éléments de la relation client. Leur suppression chez Growth Agent n'affecte pas Maintrix ni EduCore, qui restent seules maîtresses de leurs données |
| **Oppositions** | **illimitée** | **jamais purgées** | voir ci-dessous |

Toutes ces durées sont configurables. Aucune n'est inscrite en dur.

## Pourquoi une opposition n'est jamais effacée

C'est le point qui surprend : une liste d'opposition contient des adresses
email, donc des données personnelles, et pourtant elle n'est jamais purgée.

La raison est que l'effacer se retournerait contre la personne. Quelqu'un
demande à ne plus être contacté ; six mois plus tard, son adresse disparaît de
la liste ; la campagne suivante la recontacte. On lui aurait fait exactement ce
qu'elle a refusé.

Le RGPD prévoit ce cas : l'article 17.3.b écarte le droit à l'effacement quand
la conservation est nécessaire au respect d'une obligation légale, et la CNIL
admet explicitement la conservation des données strictement nécessaires à la
prise en compte d'une opposition. La donnée conservée est minimale — une
adresse, un motif, une date — et sa finalité est exclusivement de protéger la
personne.

La table `suppression_entries` figure dans `PROTECTED_TABLES`, et un test
vérifie qu'aucune règle de purge ne peut la viser.

## Blocage légal

`legal_hold` gèle un enregistrement quelle que soit son échéance : litige,
obligation comptable, réquisition. La levée est manuelle et journalisée. Le
plan de purge indique combien d'enregistrements échus sont ainsi gelés, pour
que ce ne soit pas un angle mort.

## Garde-fous

Six, appliqués dans cet ordre.

1. **Purge désactivée par défaut.** Une application qui supprime des données
   dès l'installation est dangereuse.
2. **Simulation par défaut.** Même activée, il faut `--apply` pour écrire.
3. **Échéance obligatoire.** Un enregistrement sans date d'échéance n'est
   jamais purgé : une date absente est une inconnue, pas une autorisation.
4. **Plafond par exécution** (5 000 par défaut). Le reliquat est traité à
   l'exécution suivante.
5. **Proportion maximale** (50 % d'une table par défaut). C'est le garde-fou
   qui rattrape l'erreur la plus plausible : une durée de conservation mal
   saisie — `30` au lieu de `365` — rendrait toute la base soudain « échue ».
   L'exécution s'arrête **avant la première écriture**.
6. **Tables protégées**, énumérées et vérifiées par un test plutôt que laissées
   à la vigilance du lecteur.

Il n'existe **aucun endpoint pour déclencher une purge réelle**. Une
suppression définitive ne doit pas tenir à un bouton dans un navigateur :
elle passe par la ligne de commande, où elle est délibérée et reproductible.
L'interface d'administration expose le calcul et l'historique, rien de plus.

## Exécution planifiée

```bash
python -m growth_agent.retention.cli            # simulation
python -m growth_agent.retention.cli --apply    # exécution réelle
python -m growth_agent.retention.cli --json     # pour la supervision
```

Codes de sortie : `0` succès, `1` erreur, `2` bloqué par un garde-fou,
`3` purge déjà en cours. Les codes 2 et 3 ne sont pas des pannes et l'unité
systemd ne les traite pas comme telles.

Unité et minuterie dans [`infra/scheduling/`](../infra/scheduling/),
déclenchement quotidien à 03h17 — pas à 03h00, parce que les tâches planifiées
se bousculent aux heures rondes.

### Verrou et reprise après incident

Le verrou est porté par la table `purge_runs` plutôt que par un fichier ou un
verrou mémoire : il survit à un redémarrage, et son état se lit en SQL quand on
cherche pourquoi rien ne s'est exécuté cette nuit.

Chaque lot rafraîchit un battement de cœur et valide sa transaction. Si le
processus meurt, le travail déjà fait est acquis. Une exécution `running` dont
le battement date de plus d'une heure est marquée `interrupted` et son verrou
repris ; la nouvelle exécution référence la morte dans `resumed_from_id`.

**La reprise ne rejoue rien.** La purge est idempotente : un prospect anonymisé
ne correspond plus aux critères de sélection, un événement supprimé non plus.
Reprendre, c'est simplement traiter le reliquat.

## Sauvegardes — un constat à connaître

Nous avons vérifié concrètement ce qui se passe quand on sauvegarde, puis on
purge, puis on restaure :

```
1. Donnée créée      : personne@a-purger.example.org
2. Sauvegarde prise
3. Purge appliquée   : 1 prospect anonymisé
4. Après purge       : contact_email = None
5. Après restauration: contact_email = 'personne@a-purger.example.org'
```

**Restaurer une sauvegarde antérieure réintroduit les données purgées.**

Ce n'est pas un défaut du code, c'est une propriété des sauvegardes. Deux
conséquences, qu'il vaut mieux assumer explicitement que découvrir après coup :

**Une purge n'est pleinement effective qu'une fois les sauvegardes antérieures
expirées.** Si l'on promet à quelqu'un un effacement sous 30 jours alors que
les sauvegardes sont conservées 90 jours, la promesse est inexacte.

**Il faut donc borner la rétention des sauvegardes.** La politique proposée :

| Type | Fréquence | Conservation | Chiffrement |
|---|---|---|---|
| Quotidienne | chaque nuit | 30 jours | GPG |
| Hebdomadaire | dimanche | 90 jours | GPG |
| Mensuelle | 1er du mois | 13 mois | GPG |

La rétention mensuelle de 13 mois dépasse la durée de conservation des
prospects (365 jours). C'est délibéré — il faut pouvoir restaurer après un
incident ancien — mais cela signifie qu'une donnée purgée peut théoriquement
subsister dans une sauvegarde mensuelle pendant un mois de plus.

En cas de demande d'effacement formelle, deux options : attendre l'expiration
des sauvegardes concernées en informant la personne du délai réel, ou purger
les sauvegardes ciblées — opération lourde, à n'envisager que sur demande
d'une autorité.

**L'atténuation automatique existe.** La purge étant planifiée et idempotente,
une donnée réintroduite par une restauration repart à l'exécution suivante,
sans intervention. C'est vérifié par un test.

Deux tests encodent ce constat pour qu'il reste vrai :
`test_restoring_a_pre_purge_backup_reintroduces_purged_data` et
`test_the_scheduled_purge_catches_up_after_a_restore`.

## Journalisation

Chaque exécution laisse une entrée d'audit `retention.purge_applied` contenant
**uniquement des compteurs** — jamais une adresse, un nom ni un domaine purgé.
Un test le vérifie en cherchant l'adresse effacée dans le journal.

Une simulation n'écrit aucune entrée `purge_applied` : elle n'a rien appliqué,
et le journal ne doit pas laisser croire le contraire.

## Mise en service

L'activation ne se fait pas d'un coup.

1. Laisser tourner en **simulation quotidienne** une à deux semaines. Lire les
   rapports : les volumes annoncés correspondent-ils à l'intuition ?
2. Vérifier que les prospects sous blocage légal sont bien ceux attendus.
3. Prendre une sauvegarde et **la restaurer** sur une base de test.
4. Activer `GA_PURGE_ENABLED=true`, en gardant `--apply` retiré de l'unité.
5. Ajouter `--apply` une fois les volumes confirmés sur plusieurs jours.
6. Surveiller `GET /api/v1/system/retention/runs` la première semaine.

Un seuil de supervision mérite d'être posé sur le volume purgé : une hausse
soudaine signale un changement de configuration, pas une activité normale.

## Ce que cette implémentation ne fait pas

- **Pas de purge des sauvegardes.** Hors du périmètre applicatif ; relève de
  l'exploitation.
- **Pas d'effacement sur demande individuelle.** L'API permet de supprimer une
  fiche, mais il n'existe pas de flux « droit à l'effacement » outillé avec
  accusé de réception et délai. À ajouter si le volume de demandes le justifie.
- **Pas de purge du CRM.** Le CRM est la source de vérité des comptes et a sa
  propre politique de rétention. Growth Agent ne purge que ses propres tables.
