"""Notifications, step 1: the categories, the per-user preferences and the
summary frequency (docs/design/notificaties.html). Only the settings and
saving them; nothing produces notifications yet, and the existing daily
digest (app/digest.py, User.digest_email) is not touched."""
import json

SUMMARY_FREQUENCIES = ('off', 'daily', 'weekly')
DEFAULT_SUMMARY_FREQUENCY = 'weekly'

# (key, section, nl, en, sub_nl, sub_en, senior_only), in display order.
CATEGORIES = (
    ('reactie', 'deelname', 'Reactie op je bijdrage', 'A reply to your contribution', None, None, False),
    ('voorstel', 'deelname', 'Je voorstel haalt de drempel of gaat een fase verder',
     'Your proposal reaches the threshold or moves a phase on', None, None, False),
    ('gevolgd', 'deelname', 'Een gevolgd vraagstuk wisselt van fase of de stemming sluit bijna',
     'An issue you follow changes phase or voting is about to close', None, None, False),
    ('uitslag', 'deelname', 'De uitslag van een vraagstuk dat je volgt', 'The outcome of an issue you follow',
     None, None, False),
    ('feit', 'deelname', 'Je feit wordt betwist of afgehandeld', 'Your fact is challenged or resolved',
     None, None, False),
    ('melding', 'moderatie', 'Een nieuwe melding om te behandelen', 'A new report to handle', None, None, False),
    ('bezwaar', 'moderatie', 'Een nieuw bezwaar tegen een beslissing', 'A new objection to a decision',
     'Senior moderators', 'Senior moderators', True),
    ('betwist', 'moderatie', 'Een betwist feit', 'A challenged fact',
     'Senior moderators', 'Senior moderators', True),
)
CATEGORY_KEYS = tuple(c[0] for c in CATEGORIES)

# Defaults per category: (app, email).
DEFAULTS = {
    'reactie': (True, False), 'voorstel': (True, True), 'gevolgd': (True, False), 'uitslag': (True, True),
    'feit': (True, False), 'melding': (True, False), 'bezwaar': (True, False), 'betwist': (True, False),
}


def _stored(user):
    try:
        data = json.loads(user.notif_prefs) if user.notif_prefs else {}
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def effective_prefs(user):
    """{key: {'app': bool, 'email': bool}}: the stored values over the defaults."""
    stored = _stored(user)
    prefs = {}
    for key in CATEGORY_KEYS:
        app_on, email_on = DEFAULTS[key]
        row = stored.get(key) if isinstance(stored.get(key), dict) else {}
        prefs[key] = {'app': bool(row.get('app', app_on)), 'email': bool(row.get('email', email_on))}
    return prefs


def summary_frequency(user):
    value = getattr(user, 'summary_frequency', None)
    return value if value in SUMMARY_FREQUENCIES else DEFAULT_SUMMARY_FREQUENCY


def allowed_categories(user):
    """The categories this user may see and set: the moderation ones only for
    a moderator, bezwaar and betwist only for a senior moderator (or admin)."""
    keys = []
    for key, section, *_rest, senior_only in CATEGORIES:
        if section == 'moderatie' and not user.can_moderate():
            continue
        if senior_only and not user.is_senior_moderator:
            continue
        keys.append(key)
    return keys


def save_from_form(user, form):
    """Store the switches (checkboxes named '<key>_app' / '<key>_email') for
    the categories the user may set, and the summary frequency. Categories the
    user is not entitled to keep their stored value. Commit niet zelf."""
    stored = _stored(user)
    for key in allowed_categories(user):
        stored[key] = {'app': form.get(f'{key}_app') == '1', 'email': form.get(f'{key}_email') == '1'}
    user.notif_prefs = json.dumps(stored, sort_keys=True)
    freq = form.get('summary_frequency')
    if freq in SUMMARY_FREQUENCIES:
        user.summary_frequency = freq
