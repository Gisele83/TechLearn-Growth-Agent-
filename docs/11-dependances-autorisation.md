# Dépendances exigeant une autorisation explicite

> **Statut : aucune autorisation n'a été demandée ni obtenue à ce jour.**
> Tout ce qui figure dans ce document est **à l'arrêt**. Growth Agent
> fonctionne aujourd'hui sans aucune de ces dépendances, en mode simulé.

Ce registre existe à cause d'une contrainte posée dès le départ : Growth Agent
ne modifie ni Maintrix, ni EduCore — ni leurs dépôts, ni leurs bases, ni leurs
configurations Docker, ni leurs serveurs, ni leurs environnements.

Chaque fois qu'une fonctionnalité souhaitable s'est heurtée à cette contrainte,
elle a été consignée ici plutôt que contournée. Rien n'a été commencé côté
Maintrix ou EduCore.

## Comment lire ce document

Chaque dépendance indique :

- **ce qu'elle apporterait**, concrètement, et à qui ;
- **ce qu'elle exige** de la plateforme concernée ;
- **qui doit autoriser** ;
- **ce qui a été fait en attendant**, pour que l'absence d'autorisation ne
  bloque pas le reste.

Les contrats d'API proposés sont décrits dans
[`packages/contracts/openapi/product-adapter.openapi.yaml`](../packages/contracts/openapi/product-adapter.openapi.yaml).
Ce sont des **propositions** : aucun de ces endpoints n'existe.

---

## D1 — Lecture des statuts de pilote et de licence

**Apport.** Savoir qu'un pilote a été activé, qu'un retour a été déposé ou
qu'une licence arrive à échéance. Sans cela, le suivi des pilotes repose sur
une saisie manuelle, qui dérive en quelques semaines.

**Exigence.** Quatre endpoints en **lecture seule** sur chaque plateforme :

| Endpoint | Rôle |
|---|---|
| `GET /growth/v1/health` | Sonde de disponibilité |
| `GET /growth/v1/events` | Événements de pilote et de licence depuis une date |
| `GET /growth/v1/organizations/{ref}/status` | État courant d'une organisation |
| *(optionnel)* webhook signé | Notification au lieu d'une interrogation périodique |

Les données échangées se limitent à : référence opaque d'organisation, statut
de pilote, statut de licence, date de renouvellement, et retours **explicitement
marqués comme partageables**.

**Hors périmètre, définitivement.** Aucune donnée d'élève, de parent ou de
personnel (EduCore). Aucune donnée d'exploitation, d'équipement ou d'incident
(Maintrix). Le connecteur filtre d'ailleurs les champs inattendus à la
frontière : même si une plateforme en envoyait, ils ne seraient pas stockés
(`ALLOWED_PAYLOAD_KEYS` dans `connectors/product/http.py`, et le test
`test_unexpected_payload_fields_are_stripped`).

**Qui autorise.** Le responsable technique de Maintrix et celui d'EduCore,
séparément. Une autorisation sur l'une ne vaut pas pour l'autre.

**Ce qui a été fait en attendant.** `MockMaintrixAdapter` et
`MockEduCoreAdapter` simulent le cycle complet invitation → activation →
retour → licence. Toute la chaîne — synchronisation, rapprochement avec les
prospects, alertes de renouvellement, KPI de pilote — est implémentée et
testée sur ces données fictives. Le jour où les endpoints existent, seules
deux variables de configuration changent.

**Travail estimé côté plateforme.** Trois à cinq jours par plateforme, en
grande partie de la conception d'API et du contrôle d'accès. Estimation à
confirmer par les équipes concernées.

---

## D2 — Séparation du domaine d'expédition

**Apport.** Protéger la délivrabilité des emails **transactionnels** de
Maintrix et d'EduCore — convocations, notifications, factures. C'est le point
le plus risqué de tout le projet, et il ne coûte presque rien à traiter.

**Le risque, concrètement.** Si la prospection commerciale part du même domaine
que les emails transactionnels, une campagne qui génère des plaintes dégrade la
réputation de ce domaine. Les messages transactionnels commencent alors à
arriver en spam. Un établissement ne reçoit plus ses convocations. Le dommage
touche des clients existants, pas des prospects.

**Exigence.** Un sous-domaine distinct réservé à la prospection, par exemple
`growth.<domaine>` ou un domaine dédié, avec ses propres enregistrements SPF,
DKIM et DMARC. **Aucune modification des domaines existants** — uniquement
l'ajout d'enregistrements DNS pour un nouveau sous-domaine.

**Qui autorise.** Le détenteur de la zone DNS.

**Dossier prêt à transmettre.**
[`autorisations/D2-domaine-expedition.md`](autorisations/D2-domaine-expedition.md)
— risque détaillé, enregistrements à créer, ce qui reste inchangé, montée en
volume, conditions de retrait et formulaire de décision. Aucune modification
DNS n'a été effectuée.

**Ce qui a été fait en attendant.** L'envoi réel est verrouillé par deux
conditions indépendantes (`GA_EMAIL_SENDING_ENABLED` et un fournisseur
non-mock). Le fournisseur SMTP **refuse de s'initialiser** si l'une manque —
ce n'est pas un avertissement, c'est une exception au démarrage
(`test_smtp_provider_refuses_to_initialize_when_sending_is_off`). Tous les
messages produits à ce jour sont restés sur la machine.

---

## D3 — Référentiel partagé des organisations

**Apport.** Éviter de prospecter une organisation qui est déjà cliente, ou que
deux commerciaux approchent la même en même temps.

**Exigence.** Un moyen de vérifier qu'une organisation est déjà cliente. La
forme la plus légère : un endpoint qui prend une liste de domaines et renvoie,
pour chacun, un booléen « déjà client ». **Aucun transfert de liste de clients**
n'est nécessaire ni souhaité.

**Qui autorise.** Les responsables des deux plateformes.

**Ce qui a été fait en attendant.** Le dédoublonnage interne est en place et
testé. Le chevauchement avec le parc client existant reste un angle mort connu,
à traiter manuellement avant chaque campagne : exporter la liste des domaines
ciblés et la faire relire par quelqu'un qui connaît les clients.

---

## D4 — Authentification unique

**Apport.** Éviter un mot de passe de plus, et révoquer un accès en un seul
endroit quand quelqu'un quitte l'équipe.

**Exigence.** Un fournisseur d'identité commun (OIDC) ou la déclaration de
Growth Agent comme client auprès d'un fournisseur existant.

**Qui autorise.** Le responsable de l'identité.

**Ce qui a été fait en attendant.** Authentification locale par email et mot
de passe, condensats PBKDF2-SHA256 (390 000 itérations), jetons signés à durée
limitée, rôles vérifiés en base à chaque requête — de sorte qu'une
rétrogradation prend effet immédiatement, sans attendre l'expiration du jeton.
C'est suffisant pour une petite équipe ; cela ne remplace pas une gestion
centralisée des identités.

---

## Ce qui ne demande aucune autorisation

Pour être complet, et pour qu'on ne cherche pas une autorisation là où il n'en
faut pas :

| Élément | Pourquoi c'est libre |
|---|---|
| Hébergement de Growth Agent | Instance dédiée, distincte des deux plateformes |
| Base de données, file de tâches | Propres à Growth Agent |
| Choix du CRM | Externe aux deux produits |
| Fournisseur de modèle de langage | Externe, interchangeable par configuration |
| Fournisseur de recherche de prospects | Externe, soumis à ses propres conditions |
| Discours commercial, gabarits, tarifs | Matière commerciale, pas technique |

---

## Procédure de demande

1. Choisir la dépendance et **la seule** qui est utile maintenant. D2 est la
   plus urgente et la moins coûteuse ; D1 n'a d'intérêt qu'une fois les
   premiers pilotes lancés.
2. Transmettre au responsable concerné : le contrat OpenAPI proposé, la liste
   exacte des champs lus, et ce document.
3. Obtenir un accord **écrit**, nominatif et daté.
4. Consigner l'accord dans la table `integration_configs`
   (`authorization_reference`) — la configuration devient ainsi auditable.
5. Activer le connecteur en recette d'abord, jamais directement en production.

Tant que l'étape 3 n'est pas franchie, le connecteur correspondant reste
inerte. Ce n'est pas une consigne d'exploitation : c'est le comportement du
code, et il est couvert par des tests.
