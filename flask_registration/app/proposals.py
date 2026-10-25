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
bijstellen kan de indiener de stelling één keer aanpassen.

Stemmen (stap 4): tijdens de stemfase brengt een stemgerechtigde (can_vote)
één stem uit (voor, tegen of onthouding), te wijzigen zolang de fase loopt.
Bij het sluiten (automatisch of door de host) wordt de uitslag berekend en
vastgelegd: het percentage voor van de geldige stemmen (voor plus tegen),
aangenomen als dat groter is dan de drempel. Onthoudingen tellen voor de
opkomst, niet voor de uitslag.
"""
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_

from app import db
from app.models import (PROPOSAL_KINDS, PROPOSAL_PHASES, PROPOSAL_TIMED_PHASES, VOTE_CHOICES, AppSetting,
                        Proposal, ProposalVote, User)

DEFAULT_MAX_DAYS = {'feiten': 60, 'bijstellen': 10, 'stemmen': 14}
_SETTING_KEYS = {phase: f'proposal_max_{phase}_dagen' for phase in PROPOSAL_TIMED_PHASES}
UNITS = {'dagen': 24 * 60, 'uren': 60}
DEFAULT_THRESHOLD_PCT = 50
# Een meerderheid kan niet lager dan 50%; "meer dan 100%" kan niet.
THRESHOLD_MIN, THRESHOLD_MAX = 50, 99


def parse_threshold(raw):
    """De drempel uit het startformulier, of None als hij ongeldig is."""
    raw = (raw or '').strip()
    if not raw:
        return DEFAULT_THRESHOLD_PCT
    if not raw.isdigit() or not THRESHOLD_MIN <= int(raw) <= THRESHOLD_MAX:
        return None
    return int(raw)


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


def create(thread, kind, host, minutes=None, show_live_tally=False, start=None, threshold=DEFAULT_THRESHOLD_PCT):
    """Maak het voorstel bij een (net aangemaakt) gesprek. Commit niet zelf."""
    if kind not in PROPOSAL_KINDS:
        raise ValueError(f'onbekend soort voorstel: {kind}')
    start = start or datetime.utcnow()
    proposal = Proposal(thread=thread, kind=kind, phase='feiten', threshold_pct=threshold,
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
    _set_phase(proposal, target, now)
    return True


def _set_phase(proposal, phase, now):
    """Zet de fase. Naar vervolg: de stemfase is gesloten, dus de uitslag
    vastleggen. Terug uit vervolg (alleen een event, via de host): het stemmen
    gaat weer open, dus de vastgelegde uitslag vervalt."""
    leaving_result = proposal.phase == 'vervolg' and phase != 'vervolg'
    proposal.phase = phase
    proposal.phase_changed_at = now
    if phase == 'vervolg' and proposal.result is None:
        finalize(proposal, now)
    elif leaving_result:
        clear_result(proposal)


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
    _set_phase(proposal, PROPOSAL_PHASES[index], now or datetime.utcnow())
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


# ── Stemmen ──────────────────────────────────────────────────────────────

def can_vote_now(proposal, user):
    """Stemmen kan alleen in de stemfase en alleen met stemrecht."""
    return proposal.phase == 'stemmen' and getattr(user, 'is_authenticated', False) and user.can_vote()


def cast_vote(proposal, user, choice):
    """Breng een stem uit of wijzig hem (één rij per persoon per voorstel).
    Commit niet zelf."""
    if choice not in VOTE_CHOICES:
        raise ValueError(f'onbekende stem: {choice}')
    vote = ProposalVote.query.filter_by(proposal_id=proposal.id, user_id=user.id).first()
    if vote is None:
        vote = ProposalVote(proposal_id=proposal.id, user_id=user.id, choice=choice)
        db.session.add(vote)
    else:
        vote.choice = choice
    return vote


def my_vote(proposal, user):
    if not getattr(user, 'is_authenticated', False):
        return None
    row = (db.session.query(ProposalVote.choice)
           .filter_by(proposal_id=proposal.id, user_id=user.id).first())
    return row[0] if row else None


def tally(proposal):
    """{'voor': n, 'tegen': n, 'onthouding': n} in één query."""
    counts = {choice: 0 for choice in VOTE_CHOICES}
    for choice, n in (db.session.query(ProposalVote.choice, func.count(ProposalVote.id))
                      .filter(ProposalVote.proposal_id == proposal.id).group_by(ProposalVote.choice)):
        if choice in counts:
            counts[choice] = n
    return counts


def for_pct(votes_for, votes_against):
    """Percentage voor van de geldige stemmen, of None zonder geldige stemmen."""
    valid = votes_for + votes_against
    return round(100.0 * votes_for / valid, 1) if valid else None


def eligible_voter_count():
    """Aantal stemgerechtigden, dezelfde regel als User.can_vote(): admin,
    verdiende status voter of hoger, of een handmatige rol voter of hoger."""
    return User.query.filter(or_(
        User.is_admin.is_(True),
        User.quality_status.in_(('voter', 'moderator', 'senior')),
        and_(User.rules_accepted_at.isnot(None), User.role.in_(('voter', 'moderator', 'senior_moderator'))),
    )).count()


def finalize(proposal, now=None):
    """Bereken en leg de uitslag vast bij het sluiten van de stemfase.
    Aangenomen als het percentage voor van de geldige stemmen groter is dan
    de drempel (met gehele getallen vergeleken, zonder afrondingsverschil)."""
    counts = tally(proposal)
    votes_for, votes_against = counts['voor'], counts['tegen']
    valid = votes_for + votes_against
    adopted = valid > 0 and votes_for * 100 > proposal.threshold_pct * valid
    proposal.result = 'aangenomen' if adopted else 'verworpen'
    proposal.result_pct = for_pct(votes_for, votes_against)
    proposal.votes_for, proposal.votes_against = votes_for, votes_against
    proposal.votes_abstain = counts['onthouding']
    proposal.eligible_count = eligible_voter_count()
    proposal.result_at = now or datetime.utcnow()


def clear_result(proposal):
    proposal.result = proposal.result_pct = proposal.result_at = None
    proposal.votes_for = proposal.votes_against = proposal.votes_abstain = proposal.eligible_count = None


# ── Opruimen ─────────────────────────────────────────────────────────────

def delete_for_threads(thread_ids):
    """Vóór het in bulk verwijderen van gesprekken: hun voorstellen weg."""
    thread_ids = list(thread_ids)
    if thread_ids:
        proposal_ids = [pid for (pid,) in db.session.query(Proposal.id).filter(Proposal.thread_id.in_(thread_ids))]
        if proposal_ids:
            ProposalVote.query.filter(ProposalVote.proposal_id.in_(proposal_ids)).delete(synchronize_session='evaluate')
        Proposal.query.filter(Proposal.thread_id.in_(thread_ids)).delete(synchronize_session='evaluate')
