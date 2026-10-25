"""
Fase E, stap 1: het voorstel als bijzonder soort gesprek.

Een voorstel is een gesprek met een stelling als titel en een levenscyclus
in vier fasen: feiten en discussie, voorstel bijstellen, stemmen, vervolg.

- Publiek voorstel: vaste planning. De starter geeft per fase met een
  doorlooptijd (feiten, bijstellen, stemmen) een duur op in dagen of uren.
  Geen minimum (wel groter dan nul), per fase een maximum als vangrail; de
  admin stelt die maxima in (AppSetting), met 60, 10 en 14 dagen als
  standaard. Uit de starttijd en de doorlooptijden volgen vaste begin- en
  einddatums per fase.
- Event: de host (de indiener of een moderator) stuurt de fasen later
  handmatig, zonder doorlooptijden en datums.

In deze stap staat elk nieuw voorstel in de eerste fase; stemmen en de
overgangen tussen fasen komen in latere stappen.
"""
from datetime import datetime, timedelta

from app import db
from app.models import PROPOSAL_KINDS, PROPOSAL_TIMED_PHASES, AppSetting, Proposal

DEFAULT_MAX_DAYS = {'feiten': 60, 'bijstellen': 10, 'stemmen': 14}
_SETTING_KEYS = {phase: f'proposal_max_{phase}_dagen' for phase in PROPOSAL_TIMED_PHASES}
UNITS = {'dagen': 24 * 60, 'uren': 60}
DEFAULT_THRESHOLD_PCT = 50


# ── Maxima per fase (admin) ──────────────────────────────────────────────

def get_max_days():
    stored = {s.key: s.value for s in AppSetting.query.filter(AppSetting.key.in_(_SETTING_KEYS.values()))}
    return {phase: stored.get(key, DEFAULT_MAX_DAYS[phase]) for phase, key in _SETTING_KEYS.items()}


def set_max_days(values, user_id=None):
    for phase, days in values.items():
        key = _SETTING_KEYS[phase]
        row = db.session.get(AppSetting, key)
        if row is None:
            db.session.add(AppSetting(key=key, value=int(days), updated_by_id=user_id))
        else:
            row.value, row.updated_by_id = int(days), user_id
    db.session.commit()


# ── Doorlooptijden en planning ───────────────────────────────────────────

def parse_durations(form, max_days):
    """{fase: minuten} uit het formulier (duur_<fase>, eenheid_<fase>), plus
    de fouten per fase: 'invalid' (geen geheel getal groter dan nul of een
    onbekende eenheid) of 'too_long' (boven het maximum)."""
    minutes, errors = {}, {}
    for phase in PROPOSAL_TIMED_PHASES:
        raw = (form.get(f'duur_{phase}') or '').strip()
        unit = form.get(f'eenheid_{phase}') or 'dagen'
        if not raw.isdigit() or int(raw) <= 0 or unit not in UNITS:
            errors[phase] = 'invalid'
            continue
        value = int(raw) * UNITS[unit]
        if value > max_days[phase] * UNITS['dagen']:
            errors[phase] = 'too_long'
            continue
        minutes[phase] = value
    return minutes, errors


def schedule(start, minutes):
    """Vaste begin- en einddatums per fase: elke fase begint waar de vorige
    eindigt. {fase: (begin, eind)}."""
    windows, moment = {}, start
    for phase in PROPOSAL_TIMED_PHASES:
        end = moment + timedelta(minutes=minutes[phase])
        windows[phase] = (moment, end)
        moment = end
    return windows


def create(thread, kind, host, minutes=None, show_live_tally=False, start=None):
    """Maak het voorstel bij een (net aangemaakt) gesprek. Commit niet zelf."""
    if kind not in PROPOSAL_KINDS:
        raise ValueError(f'onbekend soort voorstel: {kind}')
    start = start or datetime.utcnow()
    proposal = Proposal(thread=thread, kind=kind, phase='feiten', threshold_pct=DEFAULT_THRESHOLD_PCT,
                        show_live_tally=bool(show_live_tally), host_id=host.id, started_at=start)
    if kind == 'publiek':
        for phase, (begin, end) in schedule(start, minutes).items():
            setattr(proposal, f'{phase}_minutes', minutes[phase])
            setattr(proposal, f'{phase}_start', begin)
            setattr(proposal, f'{phase}_end', end)
    db.session.add(proposal)
    return proposal


def status(proposal):
    """(sleutel, datum) voor het statuslabel; de template maakt er tekst van.
    in_discussie, bijstellen, stemmen_tot (met einddatum), stemmen_open
    (event, zonder datum) of gesloten."""
    if proposal.phase == 'stemmen':
        return ('stemmen_tot', proposal.stemmen_end) if proposal.stemmen_end else ('stemmen_open', None)
    if proposal.phase == 'bijstellen':
        return 'bijstellen', None
    if proposal.phase == 'vervolg':
        return 'gesloten', None
    return 'in_discussie', None


# ── Opruimen ─────────────────────────────────────────────────────────────

def delete_for_threads(thread_ids):
    """Vóór het in bulk verwijderen van gesprekken: hun voorstellen weg."""
    thread_ids = list(thread_ids)
    if thread_ids:
        Proposal.query.filter(Proposal.thread_id.in_(thread_ids)).delete(synchronize_session='evaluate')
