"""Détection des tournures qui trahissent un texte écrit par une machine.

Un destinataire reconnaît un message généré en trois secondes, et il le classe
mentalement avant de l'avoir lu. Ce module cherche donc ce qui déclenche ce
réflexe, dans un premier message de prospection en français.

La méthode est la même que pour le scoring des prospects : déterministe,
explicable, et chaque constat cite le passage en cause. Un modèle de langage
n'est pas juge de son propre style — il reproduirait ses propres habitudes.

Trois familles de signaux :

* **les clichés** — des formules qu'aucun commercial n'écrit spontanément
  mais qu'un modèle produit par défaut (« je me permets de vous contacter ») ;
* **la forme** — longueur excessive, phrases toutes de la même longueur,
  connecteurs empilés, URL brute collée dans le texte ;
* **l'absence de personne** — un message signé « Cordialement » et rien
  d'autre. C'est le signal le plus fort, et le plus facile à corriger.

Chaque constat porte une suggestion de remplacement : signaler sans proposer
d'alternative ne fait pas progresser le texte.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import Any

#: Un message plus long que cela ne sera pas lu, et sa longueur même signale
#: la machine : un humain pressé écrit court.
MAX_WORDS_BLOCKING = 220
MAX_WORDS_WARNING = 150

#: En dessous, le texte est trop pauvre pour qu'on juge son rythme.
MIN_SENTENCES_FOR_RHYTHM = 4
#: Une « phrase » plus courte que cela est une salutation ou une signature,
#: pas de la prose : elle ne dit rien du rythme de l'auteur.
MIN_WORDS_FOR_PROSE = 4

SEVERITY_BLOCKING = "blocking"
SEVERITY_WARNING = "warning"

#: Pénalité appliquée au score de naturel.
PENALTY = {SEVERITY_BLOCKING: 25, SEVERITY_WARNING: 8}


@dataclass(slots=True)
class StyleFinding:
    """Un passage qui sonne faux, et ce qu'il faudrait écrire à la place."""

    code: str
    severity: str
    label: str
    excerpt: str
    suggestion: str

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "label": self.label,
            "excerpt": self.excerpt,
            "suggestion": self.suggestion,
        }


@dataclass(slots=True)
class StyleReport:
    naturalness: int
    findings: list[StyleFinding] = field(default_factory=list)
    word_count: int = 0
    sentence_count: int = 0

    @property
    def blocking_findings(self) -> list[StyleFinding]:
        return [f for f in self.findings if f.severity == SEVERITY_BLOCKING]

    @property
    def is_blocking(self) -> bool:
        return bool(self.blocking_findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "naturalness": self.naturalness,
            "word_count": self.word_count,
            "sentence_count": self.sentence_count,
            "is_blocking": self.is_blocking,
            "findings": [f.as_dict() for f in self.findings],
        }


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _n(value: str) -> str:
    """Forme comparable : minuscules, sans accent, apostrophes unifiées."""
    return _strip_accents(value.lower()).replace("’", "'")


#: Clichés rédhibitoires. Chacun est une formule qu'un modèle produit
#: spontanément et qu'un commercial n'écrit pas. La suggestion dit quoi faire,
#: pas seulement quoi éviter.
BLOCKING_CLICHES: tuple[tuple[str, str, str], ...] = (
    (
        "je me permets de vous",
        "Je me permets de…",
        "Entrez dans le sujet. Ce que vous avez remarqué chez eux, en premier mot.",
    ),
    (
        "j'espere que ce message vous trouve",
        "J'espère que ce message vous trouve bien",
        "Traduction littérale de l'anglais. À supprimer, sans remplacement.",
    ),
    (
        "dans un monde ou",
        "Dans un monde où…",
        "Ouverture de dissertation. Commencez par un fait les concernant.",
    ),
    (
        "a l'heure ou",
        "À l'heure où…",
        "Même chose : un fait concret plutôt qu'un préambule.",
    ),
    (
        "je reste a votre entiere disposition",
        "Je reste à votre entière disposition",
        "Formule creuse. Posez une question précise à la place.",
    ),
    (
        "n'hesitez pas a me contacter",
        "N'hésitez pas à me contacter",
        "Personne n'hésite, on ignore. Proposez un créneau ou une question fermée.",
    ),
    (
        "solution innovante",
        "solution innovante",
        "Dites ce que fait le produit, en une phrase, avec un verbe d'action.",
    ),
    (
        "revolutionner",
        "révolutionner",
        "Promesse invérifiable. Décrivez le changement concret attendu.",
    ),
    (
        "parfaitement adapte a vos besoins",
        "parfaitement adapté à vos besoins",
        "Affirmation non étayée. Citez le besoin précis que vous avez lu.",
    ),
    (
        "repond precisement a ce type de besoin",
        "répond précisément à ce type de besoin",
        "Formule vide. Dites ce que le produit fait, concrètement.",
    ),
)

#: Tournures à signaler sans bloquer : maladroites, pas disqualifiantes.
WARNING_CLICHES: tuple[tuple[str, str, str], ...] = (
    (
        "je m'adresse a vous en tant que",
        "Je m'adresse à vous en tant que…",
        "Le destinataire connaît sa propre fonction. Commencez par votre observation.",
    ),
    (
        "je vous contacte car",
        "Je vous contacte car…",
        "Inutile : vous le contactez, c'est acquis. Passez au motif directement.",
    ),
    (
        "saviez-vous que",
        "Saviez-vous que… ?",
        "Question rhétorique de publicité. Affirmez plutôt le fait.",
    ),
    (
        "nous sommes ravis",
        "Nous sommes ravis de…",
        "Votre enthousiasme n'intéresse pas un inconnu. Parlez de lui.",
    ),
    (
        "nous sommes heureux de",
        "Nous sommes heureux de…",
        "Même remarque.",
    ),
    (
        "leader du marche",
        "leader du marché",
        "Argument d'autorité invérifiable. Un cas client précis convainc davantage.",
    ),
    (
        "incontournable",
        "incontournable",
        "Superlatif creux. Supprimez.",
    ),
    (
        "cle en main",
        "clé en main",
        "Jargon commercial. Dites en combien de temps c'est en service.",
    ),
    (
        "sur-mesure",
        "sur-mesure",
        "Vague. Précisez ce qui s'adapte réellement.",
    ),
    (
        "scenario verifiable",
        "scénario vérifiable",
        "Jargon interne au projet. Dites ce que vous montrerez concrètement.",
    ),
    (
        "au sein de votre structure",
        "au sein de votre structure",
        "Périphrase administrative. « chez vous » suffit.",
    ),
    (
        "d'ores et deja",
        "d'ores et déjà",
        "Lourdeur. « déjà » suffit.",
    ),
    (
        "force est de constater",
        "Force est de constater",
        "Tournure de dissertation.",
    ),
)

#: Connecteurs logiques : un ou deux passent, empilés ils trahissent un plan
#: rédigé par une machine.
CONNECTORS = (
    "de plus",
    "en outre",
    "par ailleurs",
    "en effet",
    "ainsi",
    "de surcroit",
    "qui plus est",
    "enfin",
)

#: Formules de politesse finales. Suivies de rien, elles signalent l'absence
#: d'expéditeur.
CLOSINGS = (
    "cordialement",
    "bien cordialement",
    "bien a vous",
    "sincerement",
    "respectueusement",
    "salutations distinguees",
)

URL_RE = re.compile(r"https?://\S+")
EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff]", re.UNICODE
)
SENTENCE_SPLIT_RE = re.compile(r"[.!?]+(?:\s|$)")


def analyze_message(
    body: str,
    *,
    subject: str = "",
    organization_name: str = "",
    product_name: str = "",
) -> StyleReport:
    """Évalue le naturel d'un message. Fonction pure et déterministe."""
    text = body or ""
    normalized = _n(text)
    findings: list[StyleFinding] = []

    words = [w for w in re.split(r"\s+", text.strip()) if w]
    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s.strip()]

    findings.extend(_check_cliches(normalized))
    findings.extend(_check_length(len(words)))
    findings.extend(_check_rhythm(sentences))
    findings.extend(_check_connectors(normalized))
    findings.extend(_check_signature(text, normalized))
    findings.extend(_check_raw_urls(text))
    findings.extend(_check_internal_jargon(text))
    findings.extend(_check_emoji(text))
    findings.extend(_check_repetition(normalized, organization_name, product_name))
    findings.extend(_check_subject(subject, product_name))

    score = 100
    for finding in findings:
        score -= PENALTY.get(finding.severity, 0)

    return StyleReport(
        naturalness=max(0, min(100, score)),
        findings=findings,
        word_count=len(words),
        sentence_count=len(sentences),
    )


# --- Contrôles ---------------------------------------------------------------


def _check_cliches(normalized: str) -> list[StyleFinding]:
    findings = []
    for needle, display, suggestion in BLOCKING_CLICHES:
        if needle in normalized:
            findings.append(
                StyleFinding(
                    code="cliche_redhibitoire",
                    severity=SEVERITY_BLOCKING,
                    label="Formule qu'aucun commercial n'écrit spontanément",
                    excerpt=display,
                    suggestion=suggestion,
                )
            )
    for needle, display, suggestion in WARNING_CLICHES:
        if needle in normalized:
            findings.append(
                StyleFinding(
                    code="tournure_mecanique",
                    severity=SEVERITY_WARNING,
                    label="Tournure qui sonne artificielle",
                    excerpt=display,
                    suggestion=suggestion,
                )
            )
    return findings


def _check_length(word_count: int) -> list[StyleFinding]:
    if word_count > MAX_WORDS_BLOCKING:
        return [
            StyleFinding(
                code="message_trop_long",
                severity=SEVERITY_BLOCKING,
                label="Message trop long pour un premier contact",
                excerpt=f"{word_count} mots",
                suggestion=(
                    f"Viser 60 à 110 mots. Au-delà de {MAX_WORDS_BLOCKING}, le message "
                    "n'est pas lu et sa longueur même signale un texte généré."
                ),
            )
        ]
    if word_count > MAX_WORDS_WARNING:
        return [
            StyleFinding(
                code="message_long",
                severity=SEVERITY_WARNING,
                label="Message long pour un premier contact",
                excerpt=f"{word_count} mots",
                suggestion="Couper tout ce qui ne porte ni un fait les concernant ni la demande.",
            )
        ]
    return []


def _check_rhythm(sentences: list[str]) -> list[StyleFinding]:
    """Des phrases toutes de la même longueur trahissent la machine.

    Un humain alterne naturellement : une longue, une courte, une très courte.
    Un modèle produit un rythme régulier, perceptible à la lecture même sans
    savoir pourquoi.

    Les éléments structurels très courts — « Bonjour. », « Merci. » — sont
    écartés du calcul : présents dans tous les messages, humains ou non, ils
    gonflaient la variance au point de rendre le contrôle inopérant.
    """
    prose = [s for s in sentences if len(s.split()) >= MIN_WORDS_FOR_PROSE]
    if len(prose) < MIN_SENTENCES_FOR_RHYTHM:
        return []
    lengths = [len(s.split()) for s in prose]
    deviation = statistics.pstdev(lengths)
    if deviation < 3.0:
        return [
            StyleFinding(
                code="rythme_uniforme",
                severity=SEVERITY_WARNING,
                label="Toutes les phrases font la même longueur",
                excerpt=f"{len(prose)} phrases de {min(lengths)} à {max(lengths)} mots",
                suggestion=(
                    "Casser le rythme : une phrase de trois mots après une longue "
                    "suffit à rendre le texte vivant."
                ),
            )
        ]
    return []


def _check_connectors(normalized: str) -> list[StyleFinding]:
    found = [c for c in CONNECTORS if re.search(rf"\b{re.escape(c)}\b", normalized)]
    if len(found) >= 2:
        return [
            StyleFinding(
                code="connecteurs_empiles",
                severity=SEVERITY_WARNING,
                label="Connecteurs logiques empilés",
                excerpt=", ".join(found[:4]),
                suggestion=(
                    "Dans un message de cinq lignes, aucun n'est nécessaire. "
                    "Les supprimer raccourcit et accélère le texte."
                ),
            )
        ]
    return []


def _check_signature(text: str, normalized: str) -> list[StyleFinding]:
    """Un message signé par personne est le marqueur le plus net.

    On cherche une formule de politesse finale, puis on regarde s'il reste
    quelque chose après elle.
    """
    for closing in CLOSINGS:
        match = re.search(rf"{re.escape(closing)}\s*[,.]?\s*", normalized)
        if not match:
            continue
        tail = text[match.end() :].strip()
        # Ce qui suit doit ressembler à un nom : au moins deux caractères, et
        # pas seulement de la ponctuation.
        if not re.search(r"[A-Za-zÀ-ÿ]{2,}", tail):
            return [
                StyleFinding(
                    code="signature_absente",
                    severity=SEVERITY_BLOCKING,
                    label="Message signé par personne",
                    excerpt=f"« {closing} » suivi de rien",
                    suggestion=(
                        "Signer d'un prénom, d'un nom et de l'organisation. "
                        "Un message anonyme est le signal d'automatisation le plus net."
                    ),
                )
            ]
        return []
    return []


def _check_raw_urls(text: str) -> list[StyleFinding]:
    urls = URL_RE.findall(text)
    long_urls = [u for u in urls if len(u) > 40]
    if long_urls:
        return [
            StyleFinding(
                code="url_brute",
                severity=SEVERITY_WARNING,
                label="URL longue collée dans le texte",
                excerpt=long_urls[0][:60] + "…",
                suggestion=(
                    "Nommer la page plutôt que la coller : « votre projet "
                    "d'établissement » se lit, une URL de soixante caractères non."
                ),
            )
        ]
    return []


PLURAL_MARKER_RE = re.compile(r"\w+\((?:s|x|aux|es)\)")


def _check_internal_jargon(text: str) -> list[StyleFinding]:
    """Les marqueurs de pluriel entre parenthèses trahissent un gabarit.

    « 3 signal(aux) cité(s) » s'écrit dans une interface d'administration,
    jamais dans un message adressé à une personne.
    """
    found = PLURAL_MARKER_RE.findall(text)
    if found:
        return [
            StyleFinding(
                code="jargon_interne",
                severity=SEVERITY_WARNING,
                label="Marqueur de pluriel entre parenthèses",
                excerpt=", ".join(dict.fromkeys(found))[:60],
                suggestion=(
                    "Accorder la phrase au lieu de laisser « (s) » : cette "
                    "notation appartient aux écrans d'administration."
                ),
            )
        ]
    return []


def _check_emoji(text: str) -> list[StyleFinding]:
    found = EMOJI_RE.findall(text)
    if found:
        return [
            StyleFinding(
                code="emoji",
                severity=SEVERITY_WARNING,
                label="Emoji dans un premier contact professionnel",
                excerpt="".join(found[:5]),
                suggestion="À retirer : l'usage est risqué avec un interlocuteur inconnu.",
            )
        ]
    return []


def _check_repetition(
    normalized: str, organization_name: str, product_name: str
) -> list[StyleFinding]:
    """Un modèle répète les variables qu'on lui a données."""
    findings = []
    for value, kind in (
        (organization_name, "nom de l'organisation"),
        (product_name, "nom du produit"),
    ):
        if not value or len(value) < 4:
            continue
        occurrences = normalized.count(_n(value))
        if occurrences > 2:
            findings.append(
                StyleFinding(
                    code="repetition_variable",
                    severity=SEVERITY_WARNING,
                    label=f"Le {kind} revient {occurrences} fois",
                    excerpt=value,
                    suggestion=(
                        "Une fois suffit. La répétition d'une variable est "
                        "caractéristique d'un texte assemblé automatiquement."
                    ),
                )
            )
    return findings


def _check_subject(subject: str, product_name: str) -> list[StyleFinding]:
    """Un objet qui commence par le nom du produit parle de vous, pas d'eux."""
    if not subject or not product_name:
        return []
    if _n(subject).startswith(_n(product_name)):
        return [
            StyleFinding(
                code="objet_centre_produit",
                severity=SEVERITY_WARNING,
                label="L'objet commence par le nom du produit",
                excerpt=subject[:70],
                suggestion=(
                    "Mettre leur sujet en premier. Ils ouvrent un message qui "
                    "parle d'eux, pas un message qui parle de vous."
                ),
            )
        ]
    return []
