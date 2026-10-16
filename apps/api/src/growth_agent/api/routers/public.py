"""Routes publiques, sans authentification.

Une seule fonction y vit : permettre à quelqu'un de ne plus être contacté.
C'est la contrepartie du droit d'opposition annoncé dans chaque message, et
elle doit fonctionner sans compte, sans jeton d'API et sans explication.

Deux verbes, parce que les deux usages existent :

* `POST` — exigé par la RFC 8058 pour la désinscription en un clic. C'est ce
  que Gmail et Outlook appellent automatiquement depuis le bouton natif de
  leur interface, sans que l'utilisateur voie une page.
* `GET` — le clic humain sur le lien du message. Renvoie une page de
  confirmation lisible.

Un `GET` ne devrait pas modifier d'état. Ici il le fait, délibérément :
imposer un second clic à quelqu'un qui demande qu'on le laisse tranquille
serait une mauvaise façon de respecter son choix. Le risque de préchargement
par un antivirus de messagerie est réel, mais il désinscrit une personne qui
l'avait demandé — l'erreur va dans le bon sens.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from growth_agent.api.deps import SessionDep, SettingsDep
from growth_agent.services.unsubscribe import process_unsubscribe

router = APIRouter(tags=["désinscription"])


@router.post("/unsubscribe/{token}", include_in_schema=True)
def unsubscribe_one_click(token: str, session: SessionDep, settings: SettingsDep) -> JSONResponse:
    """Désinscription en un clic (RFC 8058). Appelée par le client de messagerie."""
    result = process_unsubscribe(session, raw_token=token, source="one_click")
    if not result.accepted:
        # 404 quel que soit le motif : ne pas renseigner un curieux sur
        # l'existence d'un jeton.
        return JSONResponse(status_code=404, content={"status": "unknown"})
    return JSONResponse(status_code=200, content={"status": "unsubscribed"})


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_page(
    token: str, request: Request, session: SessionDep, settings: SettingsDep
) -> HTMLResponse:
    """Page de confirmation, pour le clic humain depuis le message."""
    result = process_unsubscribe(session, raw_token=token, source="link_click")

    if not result.accepted:
        return HTMLResponse(
            status_code=404,
            content=_page(
                titre="Lien non reconnu",
                message=(
                    "Ce lien de désinscription n'est pas valide. Il a peut-être été "
                    "tronqué par votre messagerie."
                ),
                aide=(
                    "Vous pouvez répondre « STOP » au message reçu : votre adresse "
                    "sera retirée de nos envois."
                ),
            ),
        )

    message = (
        "Votre adresse était déjà retirée de nos envois. Rien de plus à faire."
        if result.already_done
        else "Votre adresse a été retirée de nos envois."
    )
    return HTMLResponse(
        status_code=200,
        content=_page(
            titre="C'est fait",
            message=message,
            aide="Vous ne recevrez plus de message de prospection de notre part.",
        ),
    )


def _page(*, titre: str, message: str, aide: str) -> str:
    """Page autonome : ni script, ni ressource externe, ni cookie.

    Une page de désinscription qui chargerait une ressource tierce
    renseignerait cette tierce partie sur la personne qui se désinscrit.
    """
    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{titre}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    line-height: 1.6; margin: 0; min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
    padding: 24px; background: #f6f7f9; color: #16191d;
  }}
  main {{
    background: #fff; border: 1px solid #d8dce2; border-radius: 8px;
    padding: 32px; max-width: 480px;
  }}
  h1 {{ margin: 0 0 12px; font-size: 1.3rem; }}
  p {{ margin: 0 0 10px; }}
  .aide {{ color: #5b6471; font-size: 0.9rem; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #14171c; color: #e8eaed; }}
    main {{ background: #1c2027; border-color: #333a45; }}
    .aide {{ color: #9aa4b2; }}
  }}
</style>
</head>
<body>
  <main>
    <h1>{titre}</h1>
    <p>{message}</p>
    <p class="aide">{aide}</p>
  </main>
</body>
</html>"""
