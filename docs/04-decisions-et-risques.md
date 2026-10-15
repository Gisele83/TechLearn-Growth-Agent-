# Décisions à confirmer et registre des risques

## Décisions à prendre avant toute campagne réelle

Ces décisions ne relèvent pas du développement. Tant qu'elles ne sont pas
tranchées, le code tourne en mode simulé — ce qui est le bon comportement,
mais ne crée aucune valeur commerciale.

### Par ordre d'urgence

**1. Cadre juridique camerounais.** Les deux campagnes pilotes visent le
Cameroun en priorité, et l'analyse juridique n'a pas été faite. Une
consultation locale sur la loi 2010/012 est le préalable à toute sollicitation.
C'est le point bloquant numéro un, avant même les questions techniques.

**2. Domaine d'expédition.** Un sous-domaine dédié à la prospection, distinct
des domaines transactionnels de Maintrix et d'EduCore, avec SPF, DKIM et
DMARC. Le risque de ne pas le faire est documenté en D2 du registre des
dépendances : une campagne qui génère des plaintes peut dégrader la
délivrabilité des emails transactionnels de clients existants.

**3. Qui valide quoi.** Le système exige qu'une personne autre que l'auteur
valide chaque message. Avec deux commerciaux, c'est tenable. Avec un seul,
c'est bloquant. Il faut nommer les approbateurs — et prévoir le cas où la
personne est absente.

**4. Grilles tarifaires.** Le catalogue livré (`agents/proposal_assistant.py`)
contient des montants de démonstration, explicitement marqués comme
provisoires. Chaque devis le signale. À remplacer par la grille validée.

**5. Choix du CRM.** HubSpot est proposé et un adaptateur existe, écrit
d'après la documentation publique mais **jamais exécuté contre un compte
réel**. Avant de s'engager : vérifier les quotas de l'offre gratuite, le coût
au-delà, et la possibilité de créer une propriété personnalisée (nécessaire à
l'idempotence). Un bac à sable d'une journée tranchera.

**6. Fournisseur de modèle de langage.** L'interface est agnostique. À
comparer sur le coût par millier de jetons, la qualité en français, et la
localisation des données. Plafonner la dépense dès l'ouverture du compte.

**7. Fournisseur de prospection.** Apollo et Clay sont cités. Deux questions
avant de souscrire : leurs conditions autorisent-elles l'usage prévu, et quelle
est la couverture réelle sur le Cameroun ? Sur ce second point, la réponse est
probablement « faible », ce qui remet en cause l'intérêt de l'abonnement pour
le marché prioritaire.

**8. Hébergement.** Instance dédiée, région européenne de préférence.

## Registre des risques

Classés par ce qu'ils coûteraient si rien n'était fait.

### R1 — Prospection illégale au Cameroun

*Probabilité moyenne, impact élevé.*

Le cadre juridique n'est pas établi et c'est le marché prioritaire.

**Atténuation.** Aucune sollicitation avant avis juridique. Le champ
`legal_regime` existe déjà sur les campagnes ; la base légale de chaque
prospect vaut `undetermined` par défaut et l'agent de rédaction le signale.
L'envoi réel reste verrouillé.

**Signal d'alerte.** Une campagne camerounaise passée à `active` alors que la
consultation juridique n'a pas eu lieu.

### R2 — Délivrabilité des emails transactionnels dégradée

*Probabilité faible si D2 est traité, élevée sinon ; impact très élevé.*

Détaillé en M2 du modèle de menace et D2 du registre des dépendances. Le
dommage toucherait des clients existants.

**Atténuation.** Domaine séparé, plafonds de fréquence déjà implémentés,
opposition traitée immédiatement, en-têtes de désinscription en un clic.

### R3 — Purge des données non implémentée

*Probabilité élevée, impact moyen.*

`GA_DATA_RETENTION_DAYS` est configuré et chaque prospect porte une date
`retention_until`, mais **rien ne supprime quoi que ce soit**. Au bout d'un an,
la base contiendra des données qui auraient dû disparaître.

**Atténuation actuelle.** Aucune, au-delà d'une suppression manuelle.

**À faire.** Une tâche périodique qui supprime les prospects non convertis
arrivés à échéance, en conservant les entrées d'opposition — sans quoi la
personne serait recontactée. C'est la première dette technique à rembourser.

### R4 — Prospection d'un client existant

*Probabilité moyenne, impact moyen.*

Sans accès au référentiel client (D3), rien n'empêche de solliciter une
organisation déjà cliente.

**Atténuation.** Relecture manuelle de la liste des domaines ciblés avant
chaque campagne, par quelqu'un qui connaît le parc.

### R5 — Qualité du jeu de prospects camerounais

*Probabilité élevée, impact moyen.*

Les annuaires d'entreprises couvrent mal le Cameroun. Le fournisseur de
recherche risque de ne rien renvoyer d'exploitable, ce qui viderait de son
sens l'automatisation de la découverte.

**Atténuation.** L'import manuel avec sources est implémenté et exige au moins
une source — un prospect saisi à la main est traité exactement comme un
prospect découvert automatiquement. Prévoir que la constitution de la liste
camerounaise sera largement manuelle les premiers mois, et ne pas souscrire
d'abonnement de prospection avant d'avoir mesuré la couverture réelle.

### R6 — Validation humaine contournée par lassitude

*Probabilité moyenne, impact moyen.*

Si valider devient une formalité, la garantie devient théorique. Le risque
n'est pas technique : il est humain.

**Atténuation.** L'approbation porte sur un condensat du contenu — on ne peut
pas valider en bloc puis modifier. Les décisions sont nominatives et
journalisées. Reste à ne pas créer de volumes qui rendent la relecture
impossible : les plafonds de fréquence y aident.

### R7 — Dérive des coûts du modèle de langage

*Probabilité faible, impact faible.*

**Atténuation.** Budget de jetons vérifié **avant** l'appel, consommation
enregistrée par action, mode mock pour le développement et les tests — qui ne
coûte rien et sert déjà à l'intégralité de la suite de tests.

### R8 — Compteur en mémoire pris en défaut

*Probabilité faible aujourd'hui, impact faible.*

La limitation de débit est en mémoire. Derrière plusieurs répliques, elle
deviendrait approximative.

**Atténuation.** Une seule instance aujourd'hui. À déporter dans Redis le jour
où une deuxième réplique apparaît. Documenté dans le code.

### R9 — Adaptateurs HTTP jamais exécutés en conditions réelles

*Probabilité élevée, impact faible.*

Les adaptateurs HubSpot, LLM, recherche et produits sont écrits d'après la
documentation et testés contre un transport simulé. Au premier branchement
réel, il y aura des surprises : noms de champs, formats de dates, codes
d'erreur.

**Atténuation.** Les tests contre transport simulé couvrent la logique
difficile (pagination, 429, idempotence, coupe-circuit). Les surprises
porteront sur la projection des champs, qui est localisée et facile à
corriger. Prévoir une demi-journée par connecteur au premier branchement.

## Dette technique assumée

| Élément | Pourquoi c'est resté ainsi | Coût de remboursement |
|---|---|---|
| Purge des données non implémentée | Nécessite un ordonnanceur qui n'existe pas encore | ~1 jour |
| Aucun worker asynchrone | Les volumes actuels ne le justifient pas | ~2 jours |
| Limitation de débit en mémoire | Une seule instance | ~0,5 jour |
| Pas de rapprochement client existant | Dépend d'une autorisation externe (D3) | Dépend de D3 |
| Adaptateurs réels non éprouvés | Impossible sans comptes et sans autorisations | ~0,5 jour par connecteur |

Aucun de ces points ne bloque l'usage du système en mode simulé, ni le travail
de qualification et de préparation des campagnes.
