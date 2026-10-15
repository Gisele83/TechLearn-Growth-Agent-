# Demande d'autorisation D2 — domaine d'expédition dédié

**Destinataire :** détenteur de la zone DNS des domaines Maintrix et EduCore
**Émetteur :** équipe TechLearn Growth Agent
**Date :** à compléter à l'envoi
**Décision attendue :** accord écrit, nominatif et daté
**Réponse souhaitée avant :** à fixer — rien d'urgent, mais rien ne démarre sans

---

## En une page

Nous demandons l'autorisation de créer **un sous-domaine dédié à la
prospection commerciale**, avec ses propres enregistrements SPF, DKIM et
DMARC.

**Aucun enregistrement existant n'est modifié.** La demande porte uniquement
sur l'ajout d'entrées pour un nom qui n'existe pas encore.

La raison n'est pas le confort de l'équipe commerciale. C'est qu'en l'absence
de séparation, une campagne de prospection qui se passe mal peut dégrader la
délivrabilité des emails **transactionnels** de Maintrix et d'EduCore —
convocations, notifications, factures. Le dommage toucherait alors des clients
existants, pas des prospects.

**Aucune modification DNS n'a été effectuée.** Ce document est une demande.

---

## 1. Le risque, concrètement

La réputation d'expéditeur se calcule par domaine. Les grands fournisseurs de
messagerie — Gmail, Outlook, Yahoo — associent un score à chaque domaine
expéditeur, alimenté par les plaintes, les rebonds et les signalements en
courrier indésirable.

La prospection à froid génère structurellement des plaintes. Même irréprochable
— ciblage correct, désinscription en un clic, volumes faibles — elle produit un
taux de signalement supérieur à celui d'un email transactionnel attendu par son
destinataire.

Si prospection et transactionnel partagent le même domaine, les deux partagent
le même score.

### Le scénario à éviter

1. Une campagne EduCore part vers 200 établissements depuis `educore.exemple`.
2. Quinze destinataires cliquent « courrier indésirable » plutôt que de se
   désinscrire. C'est un taux ordinaire, pas un accident.
3. La réputation de `educore.exemple` baisse chez un grand fournisseur.
4. Trois semaines plus tard, un établissement **client** ne reçoit plus ses
   notifications de rentrée : elles arrivent en indésirable.
5. L'établissement appelle le support. Personne ne fait le lien avec une
   campagne commerciale vieille de trois semaines.

Ce scénario n'est ni rare ni théorique : c'est le mode de défaillance le plus
courant lorsqu'on ajoute de la prospection à un domaine transactionnel.

### Pourquoi la réparation coûte cher

Reconstruire la réputation d'un domaine prend des semaines, parfois des mois,
et suppose de réduire fortement les volumes pendant la période — y compris les
volumes transactionnels, qu'on ne peut pas réduire. Le coût de la prévention
est de quelques enregistrements DNS.

---

## 2. Ce que nous demandons

### Le sous-domaine

Un sous-domaine dédié, par exemple `growth.exemple` ou `contact.exemple` — le
nom exact vous revient. Il ne doit **pas** être celui qui sert aux emails
transactionnels.

### Les enregistrements à créer

Pour le seul sous-domaine retenu, noté ci-dessous `SOUSDOMAINE` :

| Type | Nom | Valeur | Rôle |
|---|---|---|---|
| TXT | `SOUSDOMAINE` | `v=spf1 include:<fournisseur> -all` | Déclare les serveurs autorisés à émettre |
| TXT | `<sélecteur>._domainkey.SOUSDOMAINE` | clé publique fournie par le prestataire | Signature DKIM |
| TXT | `_dmarc.SOUSDOMAINE` | `v=DMARC1; p=none; rua=mailto:<boîte>; pct=100` | Politique et rapports |
| MX | `SOUSDOMAINE` | serveur du prestataire | Réception des réponses et des rebonds |
| CNAME | `<suivi>.SOUSDOMAINE` | domaine du prestataire | Suivi des liens, facultatif |

Les valeurs exactes dépendent du prestataire d'envoi, qui n'est pas encore
choisi. Nous les fournirons avant toute création.

### La progression DMARC

Démarrer en `p=none` : la politique n'applique rien, mais les rapports
remontent. Après quatre à six semaines d'observation, passer à `p=quarantine`,
puis à `p=reject`.

Commencer directement en `p=reject` ferait rejeter des messages légitimes mal
configurés, sans qu'on ait eu le moyen de s'en apercevoir.

---

## 3. Ce qui reste strictement inchangé

| Élément | État |
|---|---|
| Enregistrements DNS des domaines existants | **Inchangés** |
| SPF, DKIM, DMARC des domaines transactionnels | **Inchangés** |
| Enregistrements MX existants | **Inchangés** |
| Serveurs de messagerie de Maintrix et EduCore | **Non touchés** |
| Dépôts, bases, conteneurs des deux plateformes | **Non touchés** |

La demande est strictement additive : créer un nom qui n'existe pas. Si elle
est refusée, rien ne change ; la prospection par email reste simplement
impossible, et l'équipe se rabat sur le téléphone et les rendez-vous.

---

## 4. Ce que nous avons déjà mis en place

Pour que l'autorisation ne soit pas un chèque en blanc, les protections sont
implémentées **avant** la demande, et testées.

| Protection | Mise en œuvre | Vérifié par |
|---|---|---|
| Aucun envoi sans validation humaine | L'approbation porte sur un condensat du contenu ; modifier le texte l'invalide | `test_approvals.py` |
| L'auteur ne valide pas son propre message | Comparaison insensible à la casse | `test_author_cannot_approve_their_own_request` |
| Opposition immédiate et définitive | Deuxième des six contrôles d'envoi, avant même l'approbation | `test_suppression.py` |
| Désinscription en un clic | En-têtes `List-Unsubscribe` (RFC 8058) sur chaque message | `test_sent_message_carries_one_click_unsubscribe_headers` |
| Message sans moyen d'opposition bloqué | L'agent refuse de produire un tel brouillon | `test_draft_without_opt_out_mention_is_blocked` |
| Plafond de 2 messages par contact / 30 jours | Configurable, appliqué à l'envoi | `test_contact_frequency_cap_is_enforced` |
| Plafond par campagne et par jour | Idem | `test_campaign_daily_cap_is_enforced` |
| Anti-doublon sur le contenu | Un même texte ne part pas deux fois au même contact | `test_identical_content_to_same_contact_is_deduplicated` |
| Aucun envoi réel possible aujourd'hui | Double verrou de configuration ; le fournisseur SMTP refuse de s'initialiser | `test_smtp_provider_refuses_to_initialize_when_sending_is_off` |

L'envoi réel est actuellement **impossible**, et le restera tant que cette
autorisation n'aura pas été accordée et le domaine configuré.

---

## 5. Montée en volume proposée

Un domaine neuf n'a aucune réputation. Envoyer 200 messages le premier jour
depuis un domaine inconnu est le meilleur moyen d'être classé indésirable
d'emblée.

| Semaine | Volume quotidien | Objectif |
|---|---|---|
| 1 | 5 | Vérifier l'authentification sur les principaux fournisseurs |
| 2 | 10 | Observer les premiers rapports DMARC |
| 3–4 | 20 | Mesurer plaintes et rebonds |
| 5–8 | 30 à 50 | Passer DMARC en `p=quarantine` |
| au-delà | selon les indicateurs | `p=reject` si les signaux sont bons |

Seuils d'arrêt, sans discussion :

- **taux de plainte > 0,3 %** → arrêt immédiat et analyse ;
- **taux de rebond dur > 2 %** → arrêt, le ciblage est en cause ;
- **échecs DMARC > 1 %** → arrêt, la configuration est en cause.

Le plafond quotidien par campagne est déjà implémenté et configurable : la
montée en volume se pilote par configuration, pas par discipline.

---

## 6. Et si vous refusez

C'est une issue acceptable, et il vaut mieux la dire :

- la prospection par email n'a pas lieu ;
- l'équipe travaille par téléphone, rendez-vous et recommandation ;
- Growth Agent reste utile pour la recherche, la qualification, le suivi des
  opportunités et des pilotes — tout fonctionne déjà sans envoi ;
- aucune modification DNS n'est faite.

Ce que nous ne ferons **pas** en cas de refus : utiliser le domaine
transactionnel « juste pour quelques messages ». C'est précisément le risque
que cette demande vise à écarter, et le code l'empêche.

---

## 7. Retrait de l'autorisation

L'autorisation est révocable à tout moment, sans justification et sans délai.

Pour la révoquer : nous le dire. Nous passons `GA_EMAIL_SENDING_ENABLED=false`,
ce qui rend tout envoi impossible immédiatement. Les enregistrements DNS du
sous-domaine peuvent être supprimés ensuite, sans aucun effet sur les domaines
existants.

Nous nous engageons à ne jamais réactiver l'envoi sans un nouvel accord écrit.

---

## 8. Ce que nous attendons de vous

1. **Accord ou refus** sur le principe du sous-domaine dédié.
2. Si accord : le **nom exact** du sous-domaine.
3. Qui crée les enregistrements — vous, ou nous avec une délégation ?
4. L'adresse de réception des **rapports DMARC**.

Nous fournirons les valeurs exactes dès le prestataire d'envoi choisi, et nous
ne demanderons la création des enregistrements qu'à ce moment-là.

---

## Formulaire de décision

```
Demande D2 — sous-domaine dédié à la prospection

[ ] Accordé          [ ] Refusé          [ ] Accordé sous conditions

Sous-domaine retenu : ______________________________________________

Conditions éventuelles : ____________________________________________

_____________________________________________________________________

Création des enregistrements assurée par : [ ] détenteur DNS  [ ] équipe Growth Agent

Adresse de réception des rapports DMARC : ___________________________

Nom : ______________________  Fonction : ____________________________

Date : ____________________   Signature : ___________________________
```

La décision est à consigner dans la table `integration_configs`, champ
`authorization_reference`, afin que la configuration reste auditable.

---

## Annexes

- [Registre des dépendances](../11-dependances-autorisation.md) — D2 et les trois autres
- [Sécurité et conformité](../02-securite-conformite.md) — modèle de menace, menace M2
- [Campagnes pilotes](../06-campagnes-pilotes.md) — volumes et règles d'envoi
- [Politique de rétention](../07-politique-de-retention.md) — durées de conservation
