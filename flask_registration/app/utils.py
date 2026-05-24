import re
from urllib.parse import urlparse
from markupsafe import escape, Markup

_URL_RE = re.compile(r'(https?://[^\s<>"\']+)', re.IGNORECASE)


def _short_label(url):
    """Return domain without www. prefix, truncated to 15 chars, plus '...'."""
    try:
        host = urlparse(url).netloc or url
    except Exception:
        host = url
    if host.startswith('www.'):
        host = host[4:]
    return host[:15] + '...'


def linkify(text):
    """Convert bare URLs in text to clickable <a> links. HTML-safe."""
    if not text:
        return Markup('')
    parts = _URL_RE.split(text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            href = str(escape(part))
            label = str(escape(_short_label(part)))
            result.append(
                f'<a href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'
            )
        else:
            result.append(str(escape(part)))
    return Markup(''.join(result))
