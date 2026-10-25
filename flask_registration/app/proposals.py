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

Overgangen (stap 3): een publiek voorstel schuift op zijn vaste datums door
(advance, advance_due en het commando "flask voorstellen-tick"); een event
schuift alleen via de host (de indiener of een moderator). Tijdens het
bijstellen kan de indiener de stelling één keer aanpassen. Stemmen zelf
komen in stap 4.
"""
from datetime import datetime, timedelta

from sqlalchemy import and_, or_

from app import db
from app.models import PROPOSAL_KINDS, PROPOSAL_PHASES, PROPOSAL_TIMED_PHASES, AppSetting, Proposal

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


# ── Faseovergangen ───────────────────────────────────────────────────────

def due_phase(proposal, now=None):
    """De fase waar een publiek voorstel volgens zijn vaste datums nu hoort:
    de eerste fase waarvan de einddatum nog niet bereikt is, anders vervolg."""
    now = now or datetime.utcnow()
    for phase in PROPOSAL_TIMED_PHASES:
        end = getattr(proposal, f'{phase}_end')
        if end is None or now < end:
            return phase
    return 'vervolg'


def advance(proposal, now=None):
    """Laat een publiek voorstel doorschuiven als de einddatum van zijn fase
    bereikt is (eventueel meerdere fasen tegelijk). Alleen vooruit, nooit
    terug; een event schuift nooit vanzelf. Idempotent: de doelfase volgt
    uit de vaste datums. Geeft True als de fase veranderde. Commit niet zelf."""
    if proposal is None or proposal.kind != 'publiek':
        return False
    now = now or datetime.utcnow()
    target = due_phase(proposal, now)
    if PROPOSAL_PHASES.index(target) <= proposal.phase_index:
        return False
    proposal.phase = target
    proposal.phase_changed_at = now
    return True


def advance_due(now=None):
    """Alle publieke voorstellen waarvan de huidige fase is afgelopen laten
    doorschuiven (het commando "flask voorstellen-tick"). Veilig om vaak en
    gelijktijdig te draaien: de doelfase volgt uit de datums. Geeft het
    aantal voorstellen dat doorschoof."""
    now = now or datetime.utcnow()
    due = Proposal.query.filter(
        Proposal.kind == 'publiek',
        or_(and_(Proposal.phase == 'feiten', Proposal.feiten_end <= now),
            and_(Proposal.phase == 'bijstellen', Proposal.bijstellen_end <= now),
            and_(Proposal.phase == 'stemmen', Proposal.stemmen_end <= now))).all()
    moved = sum(1 for proposal in due if advance(proposal, now))
    if moved:
        db.session.commit()
    return moved


def advance_loaded(proposals_seen, now=None):
    """De lichte guard bij het bekijken: de voorstellen zijn al geladen (ze
    komen met het gesprek mee), dus dit kost geen query zolang er niets
    doorschuift. Commit alleen als er iets veranderde."""
    now = now or datetime.utcnow()
    changed = [p for p in proposals_seen if advance(p, now)]
    if changed:
        db.session.commit()
    return len(changed)


def is_host(proposal, user):
    """Host van een event: de indiener of een moderator."""
    return (getattr(user, 'is_authenticated', False)
            and (proposal.host_id == user.id or user.can_moderate()))


def step(proposal, direction, now=None):
    """Handmatige overgang door de host van een event: één fase vooruit
    ('volgende') of terug ('vorige'). Geeft True als de fase veranderde."""
    if proposal.kind != 'event':
        return False
    index = proposal.phase_index + (1 if direction == 'volgende' else -1 if direction == 'vorige' else 0)
    if index == proposal.phase_index or not 0 <= index < len(PROPOSAL_PHASES):
        return False
    proposal.phase = PROPOSAL_PHASES[index]
    proposal.phase_changed_at = now or datetime.utcnow()
    return True


# ── Bijstellen ───────────────────────────────────────────────────────────

STATEMENT_MAX = 300


def can_revise(proposal, user):
    """Alleen de indiener (de auteur van het openingsbericht), alleen in de
    bijstelfase en maar één keer."""
    if not getattr(user, 'is_authenticated', False) or proposal.phase != 'bijstellen' or proposal.revised_at:
        return False
    op = next((p for p in proposal.thread.posts if p.is_op), None)
    return op is not None and op.user_id == user.id


def revise(proposal, statement, now=None):
    """Pas de stelling (de titel van het gesprek) één keer aan; de
    oorspronkelijke tekst blijft bewaard voor de markering. Commit niet zelf."""
    thread = proposal.thread
    proposal.original_statement = ' '.join(
        part for part in (thread.title_prefix_nl, thread.title_accent_nl, thread.title_suffix_nl) if part)
    proposal.revised_at = now or datetime.utcnow()
    thread.title_prefix_nl = thread.title_prefix_en = statement
    thread.title_accent_nl = thread.title_accent_en = None
    thread.title_suffix_nl = thread.title_suffix_en = ''


# ── Opruimen ─────────────────────────────────────────────────────────────

def delete_for_threads(thread_ids):
    """Vóór het in bulk verwijderen van gesprekken: hun voorstellen weg."""
    thread_ids = list(thread_ids)
    if thread_ids:
        Proposal.query.filter(Proposal.thread_id.in_(thread_ids)).delete(synchronize_session='evaluate')
