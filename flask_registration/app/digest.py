"""
Fase E, stap 5b: volgen en de dagelijkse samenvatting (alleen publieke
voorstellen; bij een event is iedereen live bij).

- Volgen: de knop "Volg dit voorstel" en ontvolgen. De indiener volgt zijn
  eigen voorstel; wie meedoet (een feit toevoegt, reageert of stemt) gaat
  automatisch volgen. Wie bewust ontvolgde, wordt niet opnieuw automatisch
  volger.
- Samenvatting: hoogstens één per kalenderdag (Europe/Amsterdam) per
  gebruiker, voor al zijn gevolgde voorstellen samen: een fase is
  overgegaan, het stemmen sluit binnenkort, de uitslag, plus een lichte
  vermelding van nieuwe activiteit. Alleen als er minstens één belangrijke
  verandering is; activiteit alleen is geen reden voor een bericht.
- Kanaal: in het forum (/meldingen) en per e-mail, tenzij de gebruiker de
  e-mail in zijn profiel uitzette. Geen losse meldingen per gebeurtenis.

Alles hier is idempotent en veilig om elk uur te draaien (POST /intern/tik).
"""
import json
from datetime import datetime, time, timedelta, timezone

from flask import render_template, url_for
from flask_babel import force_locale
from sqlalchemy import func, or_

from app import db
from app.models import (FACT_KIND, REMOVING_ACTIONS, ConductCase, Digest, Post, Proposal, ProposalFollow,
                        Thread, User)

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo('Europe/Amsterdam')
except Exception:  # pragma: no cover, zonder tijdzonedatabase: UTC
    _TZ = timezone.utc

CLOSING_SOON = timedelta(hours=24)


# ── Volgen ───────────────────────────────────────────────────────────────

def _row(proposal, user):
    return ProposalFollow.query.filter_by(proposal_id=proposal.id, user_id=user.id).first()


def _public_filter():
    """SQL: een publiek voorstel, of een event met een publieke ronde (5c)."""
    return or_(Proposal.kind == 'publiek', Proposal.rounds.any())


def is_following(proposal, user):
    if not getattr(user, 'is_authenticated', False) or not proposal.is_public:
        return False
    row = _row(proposal, user)
    return bool(row and row.active)


def follow(proposal, user):
    """Volgen via de knop (of als indiener). Alleen publieke voorstellen (ook
    een event met een publieke ronde)."""
    if not proposal.is_public:
        return None
    row = _row(proposal, user)
    if row is None:
        row = ProposalFollow(proposal_id=proposal.id, user_id=user.id, active=True)
        db.session.add(row)
    else:
        row.active = True
    return row


def unfollow(proposal, user):
    row = _row(proposal, user)
    if row is None:
        row = ProposalFollow(proposal_id=proposal.id, user_id=user.id, active=False)
        db.session.add(row)
    else:
        row.active = False
    return row


def auto_follow(proposal, user):
    """Wie meedoet gaat volgen, behalve wie bewust ontvolgde."""
    if proposal is None or not proposal.is_public or not getattr(user, 'is_authenticated', False):
        return None
    if _row(proposal, user) is None:
        return follow(proposal, user)
    return None


# ── De samenvatting opbouwen ─────────────────────────────────────────────

def local_date(moment):
    """De kalenderdag (Europe/Amsterdam) van een naïeve UTC-tijd."""
    return moment.replace(tzinfo=timezone.utc).astimezone(_TZ).date()


def start_of_local_day_utc(moment):
    day = local_date(moment)
    local_midnight = datetime.combine(day, time(0, 0), tzinfo=_TZ)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def _iso(moment):
    return moment.isoformat() if moment else None


def _changes(proposal, since, now):
    """De belangrijke veranderingen van één voorstel sinds `since`."""
    changes = []
    changed = proposal.phase_changed_at is not None and proposal.phase_changed_at > since
    if changed and proposal.phase == 'bijstellen':
        changes.append({'type': 'bijstellen'})
    if changed and proposal.phase == 'stemmen':
        changes.append({'type': 'stemmen_open', 'until': _iso(proposal.stemmen_end)})
    elif (proposal.phase == 'stemmen' and proposal.stemmen_end
          and now < proposal.stemmen_end <= now + CLOSING_SOON):
        changes.append({'type': 'sluit_binnenkort', 'until': _iso(proposal.stemmen_end)})
    if proposal.phase == 'vervolg' and proposal.result and proposal.result_at and proposal.result_at > since:
        changes.append({'type': 'uitslag', 'result': proposal.result, 'pct': proposal.result_pct})
    # Stap 5c: extra publieke rondes.
    for rnd in proposal.rounds:
        if rnd.opened_at > since:
            changes.append({'type': 'ronde_open', 'number': rnd.number, 'until': _iso(rnd.voting_end)})
        elif rnd.closed_at is None and now < rnd.voting_end <= now + CLOSING_SOON:
            changes.append({'type': 'sluit_binnenkort', 'until': _iso(rnd.voting_end)})
        if rnd.result and rnd.result_at and rnd.result_at > since:
            changes.append({'type': 'ronde_uitslag', 'number': rnd.number, 'result': rnd.result,
                            'pct': rnd.result_pct})
    return changes


def _activity(proposal, user, since):
    """(nieuwe reacties, nieuwe feiten) sinds `since`, van anderen, zonder wat
    wegens de gedragsregels uit het gesprek is."""
    removed = db.session.query(ConductCase.post_id).filter(ConductCase.action.in_(REMOVING_ACTIONS))
    rows = (db.session.query(Post.kind, func.count(Post.id))
            .filter(Post.thread_id == proposal.thread_id, Post.created_at > since,
                    Post.is_op.isnot(True), or_(Post.user_id.is_(None), Post.user_id != user.id),
                    Post.body != '[bericht verwijderd]', Post.id.notin_(removed))
            .group_by(Post.kind).all())
    posts = sum(n for kind, n in rows if kind != FACT_KIND)
    facts = sum(n for kind, n in rows if kind == FACT_KIND)
    return posts, facts


def build(user, now=None):
    """De inhoud van de samenvatting voor één gebruiker, of [] als er geen
    belangrijke verandering is."""
    from app import conduct
    now = now or datetime.utcnow()
    follows = (ProposalFollow.query.join(Proposal, ProposalFollow.proposal_id == Proposal.id)
               .filter(ProposalFollow.user_id == user.id, ProposalFollow.active.is_(True),
                       _public_filter()).all())
    items, important = [], False
    for f in follows:
        proposal = f.proposal
        thread = proposal.thread
        op = next((p for p in thread.posts if p.is_op), None)
        if op is not None and conduct.is_removed_for(op, user):
            continue  # het gesprek is voor deze lezer weg
        since = max(m for m in (user.last_digest_at, f.created_at) if m is not None)
        changes = _changes(proposal, since, now)
        posts, facts = _activity(proposal, user, since)
        if not changes and not posts and not facts:
            continue
        important = important or bool(changes)
        items.append({'thread_id': thread.id,
                      'title': ' '.join(p for p in (thread.title_prefix_nl, thread.title_accent_nl,
                                                    thread.title_suffix_nl) if p),
                      'changes': changes, 'new_posts': posts, 'new_facts': facts})
    if not important:
        return []
    # Voorstellen met een belangrijke verandering eerst.
    items.sort(key=lambda i: (not i['changes'], i['title'].lower()))
    return items


# ── Versturen ────────────────────────────────────────────────────────────

def _email_html(items):
    """De e-mail in beide talen (de gebruiker heeft geen vaste taalvoorkeur):
    eerst Nederlands, dan Engels."""
    parts = []
    for lang in ('nl', 'en'):
        with force_locale(lang):
            parts.append(render_template('email/digest.html', items=items, lang=lang,
                                         base=url_for('main.index', _external=True).rstrip('/'),
                                         profile_url=url_for('profile.profile', _external=True)))
    return '<hr style="border:0;border-top:1px solid #ddd;margin:24px 0;">'.join(parts)


def send_for(user, now=None):
    """Maak en verstuur de samenvatting voor één gebruiker, hoogstens één keer
    per kalenderdag. De dag wordt atomair geclaimd, zodat twee gelijktijdige
    tikken nooit twee berichten opleveren. Geeft de Digest of None."""
    from app.mail import send_email
    now = now or datetime.utcnow()
    if user.last_digest_at and local_date(user.last_digest_at) == local_date(now):
        return None
    items = build(user, now)
    if not items:
        return None
    claimed = (User.query.filter(User.id == user.id,
                                 or_(User.last_digest_at.is_(None),
                                     User.last_digest_at < start_of_local_day_utc(now)))
               .update({User.last_digest_at: now, User.has_unread_digest: True}, synchronize_session=False))
    if not claimed:
        db.session.rollback()
        return None
    digest = Digest(user_id=user.id, items_json=json.dumps(items), created_at=now)
    db.session.add(digest)
    db.session.commit()
    db.session.refresh(user)
    if user.digest_email is not False and user.verified and user.email:
        # Geen vaste taalvoorkeur: onderwerp en inhoud in beide talen.
        subject = 'Je dagelijkse samenvatting · Your daily summary'
        if send_email(user.email, subject, _email_html(items)):
            digest.emailed_at = datetime.utcnow()
            db.session.commit()
    return digest


def run_daily(now=None):
    """Alle gebruikers met gevolgde publieke voorstellen langs. Fouten bij één
    gebruiker breken de rest niet. Geeft het aantal verstuurde samenvattingen."""
    from flask import current_app
    now = now or datetime.utcnow()
    user_ids = [uid for (uid,) in (db.session.query(ProposalFollow.user_id)
                                   .join(Proposal, ProposalFollow.proposal_id == Proposal.id)
                                   .filter(ProposalFollow.active.is_(True), _public_filter())
                                   .distinct())]
    sent = 0
    for uid in user_ids:
        user = db.session.get(User, uid)
        if user is None:
            continue
        try:
            if send_for(user, now) is not None:
                sent += 1
        except Exception as exc:  # een fout bij één gebruiker: door met de rest
            db.session.rollback()
            current_app.logger.error('Samenvatting mislukt voor gebruiker %s: %s', uid, exc)
    return sent


def tick(now=None):
    """De periodieke taak: eerst de faseovergangen, dan de samenvattingen."""
    from app import proposals
    now = now or datetime.utcnow()
    moved = proposals.advance_due(now)
    sent = run_daily(now)
    return {'moved': moved, 'digests': sent}


# ── Opruimen ─────────────────────────────────────────────────────────────

def delete_for_proposals(proposal_ids):
    proposal_ids = list(proposal_ids)
    if proposal_ids:
        ProposalFollow.query.filter(ProposalFollow.proposal_id.in_(proposal_ids)).delete(synchronize_session='evaluate')


def delete_for_user(user_id):
    ProposalFollow.query.filter_by(user_id=user_id).delete(synchronize_session='evaluate')
    Digest.query.filter_by(user_id=user_id).delete(synchronize_session='evaluate')


def recent_for(user, limit=30):
    return Digest.query.filter_by(user_id=user.id).order_by(Digest.created_at.desc(), Digest.id.desc()).limit(limit).all()


def thread_exists(thread_ids):
    ids = list(thread_ids)
    return {tid for (tid,) in db.session.query(Thread.id).filter(Thread.id.in_(ids))} if ids else set()
