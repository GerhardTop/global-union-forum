"""
Twee losstaande, volledig zelfstandige preview-pagina's voor het
WikiPolitics-ontwerp.  Bewust géén onderdeel van de site: niet in
_header.html, geen link vanuit een bestaande route of template.  Alleen
bereikbaar via de directe URL.

De HTML-bestanden in app/wikipolitics/ zijn kant-en-klare artifact-bundles
(alle CSS/JS/fonts inline of als data:/blob:-URI, geen netwerkverkeer).  Ze
worden 1-op-1 geserveerd — geen Jinja-rendering.

De globale CSP van de site (Talisman, zie app/__init__.py) staat geen
blob:-scripts of data:-fonts toe; de bundle-loader heeft die wél nodig om
zichzelf uit te pakken.  Daarom krijgt élk van deze twee routes via de
Talisman-decorator een eigen, ruimere CSP — puur voor deze twee endpoints,
de rest van de site blijft op het strikte beleid.
"""
from pathlib import Path

from flask import Blueprint, send_from_directory

from app import talisman

wikipolitics = Blueprint("wikipolitics", __name__)

_PAGES_DIR = Path(__file__).resolve().parent.parent / "wikipolitics"

# Ruimere CSP, alleen voor de twee preview-pagina's.  De bundle-loader mint
# runtime blob:-URL's voor z'n scripts en data:-URI's voor de fonts; babel/
# canvas-runtime gebruikt new Function(), vandaar 'unsafe-eval'.  Nog steeds
# 'self'-gebaseerd: geen externe origins, geen framing.
_WIKIPOLITICS_CSP = {
    "default-src": "'self'",
    "script-src": ["'self'", "'unsafe-inline'", "'unsafe-eval'", "blob:"],
    "style-src": ["'self'", "'unsafe-inline'"],
    "font-src": ["'self'", "data:"],
    "img-src": ["'self'", "data:", "blob:"],
    "connect-src": ["'self'", "blob:", "data:"],
    "frame-src": ["'self'", "blob:"],
    "child-src": ["'self'", "blob:"],
    "frame-ancestors": "'none'",
    "object-src": "'none'",
    "base-uri": "'self'",
}


def _serve(filename):
    return send_from_directory(_PAGES_DIR, filename, mimetype="text/html")


@wikipolitics.route("/wikipolitics/design")
@talisman(content_security_policy=_WIKIPOLITICS_CSP)
def design():
    return _serve("wikipolitics-wireframes.html")


@wikipolitics.route("/wikipolitics/overwegingen")
@talisman(content_security_policy=_WIKIPOLITICS_CSP)
def overwegingen():
    return _serve("wikipolitics-rationale.html")
