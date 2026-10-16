# TechLearn Growth Agent

Plateforme commerciale assistée par IA pour **Maintrix** (maintenance
industrielle) et **EduCore** (gestion scolaire) : recherche et qualification de
prospects, rédaction de messages, validation humaine, suivi des opportunités et
des pilotes.

**Application entièrement autonome.** Elle ne modifie ni Maintrix, ni EduCore —
ni leurs dépôts, ni leurs bases, ni leurs configurations Docker, ni leurs
serveurs. Les deux plateformes ne sont vues qu'à travers un connecteur en
**lecture seule**, aujourd'hui désactivé et soumis à autorisation explicite.

## En deux minutes

```bash
make setup     # environnement Python + dépendances web
make seed      # produits, profils cibles, campagnes, comptes de démonstration
make api       # http://localhost:8000  (documentation sur /docs)
make web       # http://localhost:5173
```

Comptes de démonstration — **environnement local uniquement**, mots de passe
publics :

| Compte | Rôle |
|---|---|
| `admin@growth.example.org` / `DemoAdmin!2026` | Administration |
| `commercial@growth.example.org` / `DemoCommercial!2026` | Commercial |
| `approbateur@growth.example.org` / `DemoApprobateur!2026` | Commercial (valideur) |
| `lecteur@growth.example.org` / `DemoLecteur!2026` | Lecture seule |

Deux comptes commerciaux, et ce n'est pas un hasard : l'auteur d'un message ne
peut pas valider son propre envoi.

Avec Docker : `make up` démarre PostgreSQL, Redis, l'API et l'interface. Les
ports n'écoutent que sur `127.0.0.1`.

## Ce que le système garantit

Ces garanties ne sont pas des intentions : chacune est appliquée par le code et
couverte par des tests.

**Aucun message ne part sans validation humaine.** L'approbation porte sur un
condensat du contenu. Modifier le texte après validation l'invalide — on ne
peut pas faire approuver un message puis en envoyer un autre. L'auteur d'une
demande ne peut pas la valider.

**Un contact opposé n'est jamais sollicité.** C'est le deuxième des six
contrôles d'envoi, avant même la vérification de l'approbation. Une réponse
contenant « STOP » est traitée sans passer par le modèle de langage : trop
important pour dépendre d'une inférence.

**Aucune affirmation sans source.** Un prospect sans provenance documentée
reste « inconnu » et ne peut pas être contacté. Un message qui cite une URL non
rattachée au prospect est bloqué. Un besoin proposé par le modèle mais absent
des sources est écarté.

**Le score est explicable et reproductible.** Il est calculé par du code, pas
par un modèle : secteur 25, besoin documenté 25, décideur accessible 20,
faisabilité 15, potentiel 15. Chaque point cite la preuve qui le justifie.

**Les indicateurs ne mentent pas.** Rien n'est stocké : tout est recalculé
depuis les événements. Quand il n'y a pas de base de calcul, l'interface
affiche « Aucun envoi » et non « 0 % ».

**Les messages ne sonnent pas comme des messages générés.** On ouvre sur ce
qu'on a lu chez eux, on cite leurs mots entre guillemets, on ne colle pas
d'URL, et on signe d'un nom — sans expéditeur configuré, le système refuse de
rédiger. Un score de naturel signale les tournures qui trahissent la machine
et propose la formulation humaine en remplacement.

**Une opposition survit à la purge.** Les données arrivent à échéance et sont
anonymisées, mais la liste d'opposition n'est jamais purgée : effacer l'adresse
de quelqu'un qui a refusé d'être contacté reviendrait à le recontacter.

**Rien n'est touché chez Maintrix ni EduCore.** L'interface du connecteur
produit ne définit aucune méthode d'écriture, et un test le vérifie par
introspection. Le connecteur HTTP refuse de s'initialiser sans autorisation
explicite.

## État actuel

| | |
|---|---|
| Parcours commercial complet | ✅ fonctionnel de bout en bout |
| Tests | ✅ 265 côté API, 23 côté interface |
| Envoi réel d'emails | ⬜ **désactivé** — domaine d'expédition à séparer |
| Connecteurs Maintrix / EduCore | ⬜ **désactivés** — autorisation à obtenir |
| CRM réel | ⬜ adaptateur HubSpot écrit, jamais exécuté contre un compte |
| Déploiement | ⬜ dossier préparé, rien n'a été exécuté |
| Campagnes réelles | ⬜ préparées, non lancées |
| Purge des données échues | ✅ implémentée et testée — ⬜ **désactivée** par défaut |

Tout fonctionne hors ligne, sans clé d'API et sans coût. C'est délibéré : l'état
par défaut est inoffensif.

## Structure

```
apps/api/            API FastAPI — modèles, agents, connecteurs, services
apps/web/            Interface React / TypeScript
packages/contracts/  Contrat OpenAPI proposé pour Maintrix et EduCore
infra/docker/        Images de développement local
docs/                Architecture, sécurité, déploiement, décisions
scripts/             Outillage (recherche de secrets)
```

## Les huit agents

| Agent | Responsabilité | Garde-fou appliqué |
|---|---|---|
| Market Scout | Découvrir des organisations | Rejette tout résultat sans URL ni date de collecte |
| Lead Intelligence | Qualifier et scorer | Écarte un besoin absent des sources |
| Outreach Composer | Rédiger | Produit des brouillons, n'a aucun accès à l'envoi |
| Conversation Analyst | Classer les réponses | Dans le doute, demande une relecture humaine |
| Sales Coordinator | Tâches et rendez-vous | Jamais de relance vers un contact opposé |
| Proposal Assistant | Devis | Prix issus du catalogue, jamais d'un modèle |
| Customer Success | Suivi des pilotes | Lecture seule, champs limités au contrat |
| Growth Analyst | Mesure | Refuse de conclure sur trop peu de données |

## Documentation

| | |
|---|---|
| [Architecture](docs/01-architecture.md) | Diagrammes C4, décisions et leurs coûts |
| [Sécurité et conformité](docs/02-securite-conformite.md) | Modèle de menace, RGPD, OWASP |
| [Déploiement](docs/03-deploiement.md) | Dossier préparé, **non exécuté** |
| [Décisions et risques](docs/04-decisions-et-risques.md) | Ce qui reste à trancher |
| [Recette](docs/05-recette.md) | Matrice de contrôles et leurs preuves |
| [Campagnes pilotes](docs/06-campagnes-pilotes.md) | Profils, scripts, suivi |
| [Politique de rétention](docs/07-politique-de-retention.md) | Durées, purge, garde-fous, sauvegardes |
| [**Dépendances et autorisations**](docs/11-dependances-autorisation.md) | Ce qui exigerait de toucher aux plateformes |
| [Demande D2](docs/autorisations/D2-domaine-expedition.md) | Dossier prêt à transmettre — domaine d'expédition |

## Développement

```bash
make test      # 225 tests API + 23 tests interface
make lint      # style, format, typage
make secrets   # recherche de secrets committés
make check     # les trois
```

Aucun test n'appelle un service externe. La suite complète tourne hors ligne,
y compris le parcours commercial de bout en bout.

## Configuration

Toute la configuration vient de l'environnement — voir
[`.env.example`](.env.example), qui ne contient aucune valeur réelle.

Les connecteurs retombent sur leur implémentation simulée dès qu'une
information manque. Une configuration incomplète est inoffensive : elle ne
provoque jamais un appel réel « au cas où ».

Pour l'envoi d'emails, deux conditions indépendantes sont requises
(`GA_EMAIL_SENDING_ENABLED=true` **et** un fournisseur non simulé). Pour les
connecteurs produits, trois (`*_ENABLED`, `*_ADAPTER=http`, `*_BASE_URL`).

## Avant toute campagne réelle

Trois points bloquants, aucun technique :

1. **Cadre juridique camerounais** — les campagnes visent le Cameroun en
   priorité et l'analyse n'a pas été faite.
2. **Domaine d'expédition séparé** — pour ne pas dégrader la délivrabilité des
   emails transactionnels de Maintrix et d'EduCore. Le dossier de demande est
   [prêt à transmettre](docs/autorisations/D2-domaine-expedition.md) ; aucune
   modification DNS n'a été faite.
3. **Approbateurs nommés** — le système exige une validation par un tiers.

Détail dans [docs/04-decisions-et-risques.md](docs/04-decisions-et-risques.md).
