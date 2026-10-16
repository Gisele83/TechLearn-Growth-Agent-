# Campagnes pilotes

Deux campagnes distinctes, préparées mais **non lancées**. Aucune sollicitation
ne partira avant que les trois points bloquants de la recette ne soient levés.

## Objectifs à 90 jours

Repris du cahier des charges. Ce sont des **cibles exploratoires**, pas des
engagements — l'interface le rappelle sur chaque ligne.

| | Maintrix | EduCore |
|---|---|---|
| Organisations qualifiées | 50 | 100 |
| Démonstrations tenues | 10 | 15 |
| Pilotes actifs | 3 à 5 | 5 à 8 |

Une remarque sur ces chiffres : ils supposent un taux de réponse de l'ordre de
20 % et une conversion démo → pilote de 30 à 50 %. Ce sont des hypothèses
hautes pour une prise de contact à froid. Il vaut mieux les traiter comme un
ordre de grandeur à corriger après trente jours que comme un objectif à tenir.

## Maintrix — industrie

### Profil cible

Sites industriels et prestataires de maintenance dont le suivi se fait encore
sur tableur ou sur papier.

- **Secteurs.** Logistique portuaire, agroalimentaire, énergie, mécanique.
- **Taille.** 50 à 2 000 personnes.
- **Géographie.** Cameroun en priorité, France ciblée.
- **Interlocuteurs.** Responsable maintenance, directeur technique,
  responsable de production.
- **Signaux de besoin.** Recrutement d'un technicien de maintenance, annonce
  de modernisation, mention d'arrêts machine, projet de digitalisation.
- **À écarter.** Éditeurs de GMAO, cabinets de conseil en maintenance — ce
  sont des concurrents, pas des prospects.

### Démonstration, 30 minutes

1. **5 min.** Leur situation, telle qu'on l'a comprise depuis les sources.
   Commencer par écouter : si on s'est trompé, autant le savoir tout de suite.
2. **15 min.** Un scénario vérifiable sur leur type d'équipement : saisie d'une
   intervention, suivi d'un arrêt, historique.
3. **5 min.** Ce que le pilote implique concrètement, de leur côté.
4. **5 min.** Questions, et suite.

**Ne pas promettre de chiffre de gain.** Aucune mesure ne permet aujourd'hui
de l'étayer, et une promesse non tenue au bout de trois mois coûte plus que le
contrat gagné.

## EduCore — établissements

### Profil cible

Établissements dont la gestion administrative reste manuelle et dont les
effectifs augmentent.

- **Secteurs.** Secondaire, supérieur, formation professionnelle.
- **Taille.** 10 à 1 000 personnes.
- **Géographie.** Cameroun en priorité.
- **Interlocuteurs.** Chef d'établissement, directeur administratif,
  promoteur, secrétaire général.
- **Signaux de besoin.** Ouverture de sections, croissance des effectifs,
  mention d'inscriptions ou de scolarité à moderniser.
- **À écarter.** Éditeurs de logiciels scolaires.

### Saisonnalité

C'est le point le plus important de cette campagne, et il n'est pas technique.

Un établissement n'a ni le temps ni l'envie d'examiner un outil pendant la
rentrée ou pendant les examens. Les fenêtres utiles sont **janvier-mars** et
**mai-juin**, pour une décision qui s'applique à la rentrée suivante.

Conséquence : le cycle de vente est long, et l'objectif de 5 à 8 pilotes en
90 jours n'est atteignable que si la campagne démarre en début d'année civile.
Lancée en septembre, elle produira peu, quelle que soit la qualité du ciblage.

### Démonstration, 30 minutes

1. **5 min.** Leur organisation actuelle.
2. **15 min.** Scénario sur leur réalité : inscription d'un élève, suivi des
   absences, édition d'un document administratif.
3. **5 min.** Le pilote : périmètre, durée, ce qu'on attend d'eux.
4. **5 min.** Questions.

## Ce à quoi ressemble un message

Le système produit ceci, et pas autre chose :

```
Objet : Votre projet d'établissement 2026

Bonjour,

Sur votre site, j'ai relevé ceci : « Le projet prévoit de digitaliser la
gestion administrative et le suivi des absences. »

EduCore regroupe les inscriptions, la scolarité et les absences dans un seul
outil. Si c'est utile, je vous montre une inscription d'élève sur vos propres
cas, en 30 minutes.

Est-ce un sujet d'actualité chez vous ?

Si ce n'est pas votre sujet, répondez STOP et je ne vous recontacte pas.

Awa Njoya
Chargée de développement
TechLearn
```

Quatre partis pris, tous vérifiés par des tests :

**On ouvre sur eux, jamais sur soi.** Pas de « je me permets de vous
contacter », pas de « je m'adresse à vous en tant que directeur de… » : le
destinataire connaît sa propre fonction.

**On cite leurs mots entre guillemets.** C'est plus honnête qu'une
reformulation, et plus convaincant : ils reconnaissent leur propre phrase. La
citation est littérale et provient d'une source datée ; si elle ne figure
dans aucune source, elle est écartée.

**On ne colle pas d'URL.** La source reste attachée au brouillon pour que
l'approbateur vérifie, mais une adresse de soixante caractères ne se lit pas
dans un email.

**On signe.** Un message terminé par « Cordialement, » et rien d'autre est le
signal d'automatisation le plus net qui soit. Sans expéditeur configuré, le
système refuse de rédiger.

Sans besoin documenté, le message ne fait pas semblant d'en avoir un : il
demande simplement si l'on s'adresse à la bonne personne.

### Contrôle avant validation

Chaque brouillon porte un **score de naturel sur 100**, recalculé à chaque
lecture. Les clichés rédhibitoires, un message de plus de 220 mots ou une
signature absente **bloquent** le brouillon ; le reste — URL brute,
connecteurs empilés, phrases toutes de la même longueur, répétition du nom de
l'organisation — alerte l'approbateur sans l'empêcher de valider.

Chaque constat cite le passage en cause **et propose la formulation de
remplacement** : signaler sans alternative ne fait pas progresser le texte.

## Règles communes

Elles ne sont pas des recommandations : le système les applique.

1. **Aucun envoi sans validation humaine.** Chaque message est relu par
   quelqu'un d'autre que son auteur.
2. **Aucun message sans moyen de s'opposer.** Un brouillon qui n'en propose
   pas est bloqué automatiquement.
3. **Aucune affirmation sans source.** Un message qui cite une URL non
   rattachée au prospect est bloqué.
4. **Deux messages maximum par contact sur 30 jours.** Plafond configurable,
   appliqué à l'envoi.
5. **Une opposition est définitive.** Traitée immédiatement, y compris quand
   elle arrive au milieu d'une conversation.
6. **Pas de mélange entre produits.** Un prospect EduCore ne reçoit pas de
   message Maintrix.

## Suivi

### À 30 jours — mesurer, pas conclure

Avec moins de 10 envois, aucun taux n'a de sens. L'agent d'analyse refuse
d'ailleurs de conclure en dessous de ce seuil, et le dit explicitement.

Ce qu'on regarde à 30 jours : le nombre d'organisations qualifiées, la part de
prospects « vérifiés » par rapport aux « inconnus », et surtout **ce qui a
bloqué**. Un taux de prospects inconnus élevé signale un problème de sourcing,
pas de ciblage.

### À 60 jours — premiers signaux

Taux de réponse, objections récurrentes, démonstrations tenues. Les objections
qui reviennent sont l'information la plus utile de cette phase : elles disent
ce que le discours a raté.

### À 90 jours — décider

Trois issues possibles, à trancher par produit et non globalement :

- **Poursuivre** si au moins un pilote est actif et si le taux de réponse
  dépasse 10 %.
- **Ajuster** si les réponses viennent mais ne convertissent pas : le ciblage
  est bon, la démonstration ou l'offre ne l'est pas.
- **Arrêter** si le taux de réponse reste sous 5 % après correction du
  ciblage. Insister coûterait la réputation du domaine d'expédition pour un
  rendement nul.

Il est tout à fait possible que la décision diffère entre les deux produits.
Ce serait même le résultat le plus instructif.
