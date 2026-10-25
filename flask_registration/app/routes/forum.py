import secrets
from collections import deque
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, session, jsonify, abort, current_app, flash, g
from flask_babel import gettext
from flask_login import login_required, current_user
from sqlalchemy import func, or_

from app import db, bcrypt, limiter
from app.forms import ForumActionForm, ForumReplyForm
from app.models import (FACT_DIRECTIONS, FACT_KIND, STANCES, QUALITY_CRITERIA, User, Thread, Post,
                        PostLike, QualityAward, QualityReport, time_ago)
from app import conduct, quality
from app.utils import _pop_form_state, _stash_form_state, safe_next

forum_bp = Blueprint("forum", __name__)


@forum_bp.app_context_processor
def _inject_forum_action_form():
    # Lui gebruikt: het token wordt pas gemaakt als een template het rendert.
    from app import status
    return {'forum_action_form': ForumActionForm(), 'quality_labels': quality.CRITERIA_LABELS,
            # Fase C2: statuslabels en (lui) de ongeziene promotie voor de melding.
            'status_labels': status.STATUS_LABELS,
            'pending_promotion': lambda: status.pending_promotion(current_user),
            # Fase D: gedragsregels.
            'conduct_rule_label': conduct.rule_label,
            'conduct_can_decide': lambda case: conduct.can_decide_objection(case, current_user),
            # Fase E: statuslabel van een voorstel.
            'proposal_status': _proposal_status}


def _proposal_status(proposal):
    from app import proposals
    return proposals.status(proposal)


def _rules_first_redirect():
    """Ingelogd maar (nog) geen akkoord op de gedragsregels: eerst naar de
    spelregelpagina (daar staat de akkoordknop)."""
    flash(gettext('Accept the conduct rules first to take part.'), 'error')
    return redirect(url_for('main.spelregels', next=request.path))


def _auto_translate():
    """Vertaalvoorkeur van de lezer: vertaalknoppen bij het laden automatisch
    aanklikken. Gebruikt door de threadpagina en de kwaliteitsdiscussie."""
    user_lang = session.get('lang', 'nl')
    return current_user.is_authenticated and (
        current_user.auto_translate is True
        or (current_user.auto_translate is None and user_lang == 'en')
    )


def _require_csrf():
    """CSRF per actie, zelfde aanpak als /feedback en de reactie-stroom:
    leeg FlaskForm, 400 bij een ontbrekend of ongeldig token."""
    if not ForumActionForm().validate_on_submit():
        abort(400)

# Maximale diepte van de gelaagde weergave: openingsbericht = niveau 1,
# reacties niveau 2 tot 4; dieper via een doorlink (forum_branch).
MAX_LEVEL = 4

# De demo-accounts die _seed_forum() aanmaakt. Ook gebruikt door het
# beheer-commando `flask reset-content` (app/cli.py) als keep-set.
DEMO_USER_EMAILS = {
    'jan':    'jan.hofman.demo@globalunionforum.org',
    'fatima': 'fatima.elidrissi.demo@globalunionforum.org',
    'sarah':  'sarah.chen.demo@globalunionforum.org',
    'marcus': 'marcus.osei.demo@globalunionforum.org',
}


def _get_or_create_demo_user(first_name, last_name, email, username):
    user = User.query.filter_by(email=email).first()
    if not user:
        pw = bcrypt.generate_password_hash(secrets.token_hex(24)).decode('utf-8')
        user = User(
            username=username,
            first_name=first_name,
            last_name=last_name,
            email=email,
            password_hash=pw,
            verified=True,
            linkedin_url=None,
        )
        user.accept_rules()  # demo-accounts zijn bijdragers, net als na de migratie
        user.show_real_name = True  # demo-personages tonen bewust hun (demo)naam
        db.session.add(user)
        db.session.flush()
    return user


def _seed_forum():
    """Seed 4 demo threads with demo users on first run. Alleen bij het
    opstarten (create_app), niet per verzoek: dat kostte op elke forumpagina
    een databaseronde. reset-content laat de democontent staan."""
    if Thread.query.filter_by(is_demo=True).first():
        return

    old = Thread.query.filter_by(eyebrow_nl='Het idee · scorecard').first()
    if old:
        PostLike.query.filter(PostLike.post_id.in_(
            db.session.query(Post.id).filter_by(thread_id=old.id)
        )).delete(synchronize_session=False)
        quality.delete_for_posts(p.id for p in Post.query.filter_by(thread_id=old.id).with_entities(Post.id))
        Post.query.filter_by(thread_id=old.id).delete()
        db.session.delete(old)
        db.session.flush()

    now = datetime.utcnow()

    jan     = _get_or_create_demo_user('Jan',    'Hofman (demo)',       DEMO_USER_EMAILS['jan'],    'jan_h')
    fatima  = _get_or_create_demo_user('Fatima', 'El Idrissi (demo)',   DEMO_USER_EMAILS['fatima'], 'fatima_e')
    sarah   = _get_or_create_demo_user('Sarah',  'Chen (demo)',         DEMO_USER_EMAILS['sarah'],  'sarah_c')
    marcus  = _get_or_create_demo_user('Marcus', 'Osei (demo)',         DEMO_USER_EMAILS['marcus'], 'marcus_o')

    # ── Thread 1: Scorecard manipulatie ──────────────────────────────
    t1 = Thread(
        eyebrow_nl='Het idee · principe 1',
        eyebrow_en='The idea · principle 1',
        title_prefix_nl='Wat als landen de scorecard gaan',
        title_accent_nl='manipuleren',
        title_suffix_nl='?',
        title_prefix_en='What if countries start',
        title_accent_en='gaming the scorecard',
        title_suffix_en='?',
        is_demo=True,
        created_at=now - timedelta(days=6),
    )
    db.session.add(t1)
    db.session.flush()

    p1_op = Post(
        thread_id=t1.id, user_id=jan.id,
        author_name='Jan Hofman (demo)', author_username='jan_h', author_role='member', author_age=52,
        source_lang='nl', is_op=True, vote_count=0, is_demo=True,
        body_nl=(
            "Ik steun het idee van de scorecard, maar ik maak me zorgen over de wet van Goodhart: "
            "zodra een maatstaf een doel wordt, houdt het op een goede maatstaf te zijn.\n\n"
            "China werd al eerder aangehaald: spectaculaire anticorruptiecijfers, ondertussen een "
            "van de meest onderdrukkende regimes ter wereld. Maar dit is geen Chinees probleem — "
            "ook westerse landen vertonen dit gedrag. Griekenland heeft jarenlang zijn "
            "begrotingscijfers mooier gemaakt voor de EU.\n\n"
            "Mijn vraag: heeft Global Union een mechanisme nodig dat landen bestraft die "
            "aantoonbaar de cijfers manipuleren? En zo ja, wie stelt vast dat er gemanipuleerd is?"
        ),
        body_en=(
            "I support the idea of the scorecard, but I'm worried about Goodhart's Law: once "
            "a measure becomes a target, it ceases to be a good measure.\n\n"
            "China has already been cited: spectacular anti-corruption numbers, while being one "
            "of the most repressive regimes in the world. But this isn't a Chinese problem — "
            "Western countries do this too. Greece manipulated its budget figures for years to "
            "meet EU requirements.\n\n"
            "My question: does Global Union need a mechanism to penalise countries that "
            "demonstrably manipulate the figures? And if so, who decides that manipulation has occurred?"
        ),
        created_at=now - timedelta(days=6),
    )
    db.session.add(p1_op)
    db.session.flush()

    p1_r1 = Post(
        thread_id=t1.id, user_id=sarah.id, parent_id=p1_op.id,
        author_name='Sarah Chen (demo)', author_username='sarah_c', author_role='expert', author_age=34,
        author_badge_nl='Internationale betrekkingen · LSE',
        author_badge_en='International relations · LSE',
        source_lang='en', vote_count=31, is_demo=True,
        body=(
            "Jan raises exactly the right concern. The technical term is 'metric gaming' "
            "and it's endemic to any scoring system.\n\n"
            "One partial solution: an independent verification body with investigative powers "
            "— not unlike a Global Audit Court. Countries submit their figures, but the body "
            "can cross-check against satellite data, NGO reports, academic studies. Not "
            "perfect, but much harder to game than self-reported statistics.\n\n"
            "The real question is mandate and funding. Who appoints its members? If wealthy "
            "member states control the budget, smaller countries will never trust it."
        ),
        created_at=now - timedelta(days=5, hours=3),
    )
    db.session.add(p1_r1)
    db.session.flush()

    p1_r2 = Post(
        thread_id=t1.id, user_id=marcus.id, parent_id=p1_op.id,
        author_name='Marcus Osei (demo)', author_username='marcus_o', author_role='member', author_age=31,
        source_lang='en', vote_count=18, is_demo=True,
        body=(
            "The independent body idea is appealing but circular: you need trust to create "
            "the body, and the body is supposed to create trust. This is exactly where the "
            "EU got stuck with Hungary.\n\n"
            "Maybe start smaller: a temporary 'red flag' system. If three or more member "
            "states formally challenge a country's score, an automatic third-party audit "
            "kicks in. Puts the burden of proof on challengers, not on the body itself."
        ),
        created_at=now - timedelta(days=4, hours=8),
    )
    db.session.add(p1_r2)

    # ── Thread 2: Handelszone, beperkt vrij personenverkeer ──────────
    t2 = Thread(
        eyebrow_nl='Het idee · principe 4',
        eyebrow_en='The idea · principle 4',
        title_prefix_nl='Eén handelszone, beperkt vrij verkeer van personen -',
        title_accent_nl='is dat houdbaar',
        title_suffix_nl='?',
        title_prefix_en='A single trade zone, limited free movement of people -',
        title_accent_en='is that sustainable',
        title_suffix_en='?',
        is_demo=True,
        created_at=now - timedelta(days=4),
    )
    db.session.add(t2)
    db.session.flush()

    p2_op = Post(
        thread_id=t2.id, user_id=fatima.id,
        author_name='Fatima El Idrissi (demo)', author_username='fatima_e', author_role='expert', author_age=38,
        author_badge_nl='Handelsrecht · Universiteit Leiden',
        author_badge_en='Trade law · Leiden University',
        source_lang='nl', is_op=True, vote_count=0, is_demo=True,
        body_nl=(
            "Principe 4 introduceert een interessante hybride: geen volledig vrij "
            "personenverkeer, maar ook geen gesloten grenzen. Tijdelijke werkvisa voor schaars "
            "talent — uit GU-landen én daarbuiten — gekoppeld aan een circulaire gedachte: "
            "kennis en ervaring vloeien terug naar het land van herkomst.\n\n"
            "Ik zie de aantrekkingskracht van dit model. Maar ik heb drie kritische vragen.\n\n"
            "Ten eerste: hoe realistisch is de circulaire aanname? In de migratie-economie is "
            "'tijdelijk' zelden echt tijdelijk. Zodra iemand geworteld raakt — carrière, "
            "netwerk, kinderen op school — vervalt de terugkeerprikkel. Zonder harde afspraken "
            "of positieve incentives wordt tijdelijk structureel.\n\n"
            "Ten tweede: wie bepaalt wat 'hoogwaardig' is? Dit begrip is politiek geladen. In "
            "de praktijk bepalen rijkere lidstaten de vraag, armere landen leveren het aanbod. "
            "Dat verhoudt zich slecht tot het partnerschap dat de GU beoogt.\n\n"
            "Ten derde: de opleidingsprogramma's en remote-werkfaciliteiten klinken "
            "veelbelovend — maar zijn dat aanvullingen op, of vervangers van, echte mobiliteit? "
            "Als de ambities daar liggen, vraagt dat om een veel concreter uitvoeringskader."
        ),
        body_en=(
            "Principle 4 introduces an interesting hybrid: no full free movement of people, "
            "but no closed borders either. Temporary work visas for scarce talent — from GU "
            "countries as well as non-GU countries — linked to a circular idea: knowledge and "
            "experience flow back to the country of origin.\n\n"
            "I see the appeal of this model. But I have three critical questions.\n\n"
            "First: how realistic is the circular assumption? In migration economics, "
            "'temporary' is rarely truly temporary. Once someone puts down roots — career, "
            "networks, children in school — the incentive to return fades. Without firm "
            "commitments or positive incentives, temporary becomes permanent.\n\n"
            "Second: who decides what counts as 'high-value'? This is a politically loaded "
            "concept. In practice, wealthier member states define the demand, poorer countries "
            "supply the talent. That sits uneasily with the partnership the GU aims to embody.\n\n"
            "Third: the training programmes and remote-work facilitation sound promising — but "
            "are these supplements to, or substitutes for, real mobility? If that is where the "
            "ambitions lie, it requires a much more concrete implementation framework."
        ),
        created_at=now - timedelta(days=4),
    )
    db.session.add(p2_op)
    db.session.flush()

    p2_r1 = Post(
        thread_id=t2.id, user_id=jan.id, parent_id=p2_op.id,
        author_name='Jan Hofman (demo)', author_username='jan_h', author_role='member', author_age=52,
        source_lang='nl', vote_count=14, is_demo=True,
        body=(
            "Fatima stelt de juiste vragen. Laat me ze één voor één langslopen.\n\n"
            "Over de circulaire aanname: je hebt gelijk dat 'tijdelijk' zelden vanzelf "
            "tijdelijk blijft. Maar het manifest koppelt de visa juist aan terugkeerincentives "
            "— de investeringen in opleidingsprogramma's en remote-werkmogelijkheden zijn "
            "bedoeld om terugkeer aantrekkelijk te maken, niet alleen moreel te verwachten. "
            "Ierland en India laten zien dat diaspora's onder de juiste omstandigheden wél "
            "kennis en netwerken terugbrengen. Dat is geen utopie, het is beleid.\n\n"
            "Over 'hoogwaardig': je punt over machtsverhoudingen is scherp. Maar het hoeft "
            "niet te betekenen dat armere landen uitgeleverd zijn. Een scorecard-logica — "
            "vergelijkbaar met hoe de GU handelstarieven bepaalt — kan ook hier als basis "
            "dienen: transparant, meetbaar, voor iedereen dezelfde criteria.\n\n"
            "Over remote werk als vervanging: ik lees het andersom. Remote werk is geen "
            "substituut voor mobiliteit, maar een manier om de waarde van tijdelijke mobiliteit "
            "te verlengen. Iemand die terugkeert maar remote blijft bijdragen is zowel "
            "economisch als kennismatig waardevoller dan iemand die definitief emigreert."
        ),
        created_at=now - timedelta(days=3, hours=5),
    )
    db.session.add(p2_r1)

    # ── Thread 3: Onafhankelijke media ───────────────────────────────
    t3 = Thread(
        eyebrow_nl='Het idee · principe 3',
        eyebrow_en='The idea · principle 3',
        title_prefix_nl='Onafhankelijke media als Global Union-instrument —',
        title_accent_nl='werkt het BBC-model',
        title_suffix_nl='?',
        title_prefix_en='Independent media as a Global Union instrument —',
        title_accent_en='does the BBC model work',
        title_suffix_en='?',
        is_demo=True,
        created_at=now - timedelta(days=2),
    )
    db.session.add(t3)
    db.session.flush()

    p3_op = Post(
        thread_id=t3.id, user_id=sarah.id,
        author_name='Sarah Chen (demo)', author_username='sarah_c', author_role='expert', author_age=34,
        author_badge_nl='Internationale betrekkingen · LSE',
        author_badge_en='International relations · LSE',
        source_lang='en', is_op=True, vote_count=0, is_demo=True,
        body=(
            "Principle 3 proposes independent journalism in local languages, modelled on the "
            "BBC. I work in media policy and I find this the most promising — and the most "
            "underspecified — part of the proposal.\n\n"
            "The BBC works because of three things: public funding (licence fee), statutory "
            "independence (Royal Charter), and brand trust built over decades. Replicating "
            "that globally means answering:\n\n"
            "1. Who funds it? A Global Union levy? Voluntary contributions from member "
            "states? Both are vulnerable to political capture.\n"
            "2. Who protects it legally? A charter is only as strong as the body that "
            "enforces it.\n"
            "3. How does it operate in countries where the state controls internet "
            "infrastructure?\n\n"
            "I'm genuinely enthusiastic about this idea, but it needs a full governance "
            "model before it can be taken seriously."
        ),
        created_at=now - timedelta(days=2),
    )
    db.session.add(p3_op)
    db.session.flush()

    p3_r1 = Post(
        thread_id=t3.id, user_id=jan.id, parent_id=p3_op.id,
        author_name='Jan Hofman (demo)', author_username='jan_h', author_role='member', author_age=52,
        source_lang='nl', vote_count=9, is_demo=True,
        body=(
            "Goed punt over de financiering. Maar misschien hoeft het geen BBC te worden. "
            "Er is al een ecosysteem van onafhankelijke media die internationaal actief zijn: "
            "Deutsche Welle, Radio Free Europe, France 24.\n\n"
            "Wat als Global Union deze bestaande organisaties versterkt en coördineert, "
            "in plaats van een nieuw instituut te bouwen? Dat is sneller, goedkoper, en "
            "heeft al bewezen geloofwaardig te zijn in autocratische contexten."
        ),
        created_at=now - timedelta(days=1, hours=14),
    )
    db.session.add(p3_r1)
    db.session.flush()

    p3_r2 = Post(
        thread_id=t3.id, user_id=fatima.id, parent_id=p3_r1.id,
        author_name='Fatima El Idrissi (demo)', author_username='fatima_e', author_role='expert', author_age=38,
        author_badge_nl='Handelsrecht · Universiteit Leiden',
        author_badge_en='Trade law · Leiden University',
        source_lang='nl', vote_count=22, is_demo=True,
        body=(
            "Jan's suggestie is pragmatisch, maar die bestaande organisaties zijn niet "
            "neutraal — ze worden gesponsord door nationale overheden met eigen geopolitieke "
            "belangen. Radio Free Europe is al decennia de facto een Amerikaans "
            "buitenlandsinstrument.\n\n"
            "Als Global Union geloofwaardig wil zijn als niet-westerse doctrine, moet ze "
            "financiering en governance echt onafhankelijk maken. Dat is juist het "
            "moeilijkste deel — en het meest noodzakelijke."
        ),
        created_at=now - timedelta(days=1, hours=8),
    )
    db.session.add(p3_r2)

    # ── Thread 4: Toetreding — wie beslist ───────────────────────────
    t4 = Thread(
        eyebrow_nl='Het idee · toetreding',
        eyebrow_en='The idea · accession',
        title_prefix_nl='Wie mogen er als eerste bij, en',
        title_accent_nl='wie beslist dat',
        title_suffix_nl='?',
        title_prefix_en='Who gets in first, and',
        title_accent_en='who decides',
        title_suffix_en='?',
        is_demo=True,
        created_at=now - timedelta(hours=18),
    )
    db.session.add(t4)
    db.session.flush()

    p4_op = Post(
        thread_id=t4.id, user_id=marcus.id,
        author_name='Marcus Osei (demo)', author_username='marcus_o', author_role='member', author_age=31,
        source_lang='en', is_op=True, vote_count=0, is_demo=True,
        body=(
            "The manifesto describes Global Union as open to 'functioning democracies'. "
            "But who decides when a country qualifies?\n\n"
            "The EU has a formal accession process: the Commission assesses, the Council "
            "approves by qualified majority. It's slow, political, and — as we've seen "
            "with Western Balkans countries waiting 20+ years — often gridlocked.\n\n"
            "Global Union could do it differently. Some options:\n"
            "- An independent accreditation body, like a global democracy audit\n"
            "- Existing member states vote by qualified majority (80%? 85%?)\n"
            "- Self-certification against the scorecard, with a mandatory review period\n\n"
            "Each has obvious weaknesses. But the question matters enormously: the "
            "credibility of the whole system depends on whether the criteria are applied "
            "consistently, or whether big members can block candidates they find inconvenient."
        ),
        created_at=now - timedelta(hours=18),
    )
    db.session.add(p4_op)
    db.session.flush()

    p4_r1 = Post(
        thread_id=t4.id, user_id=fatima.id, parent_id=p4_op.id,
        author_name='Fatima El Idrissi (demo)', author_username='fatima_e', author_role='expert', author_age=38,
        author_badge_nl='Handelsrecht · Universiteit Leiden',
        author_badge_en='Trade law · Leiden University',
        source_lang='nl', vote_count=19, is_demo=True,
        body=(
            "De vergelijking met de EU-uitbreidingsprocedure is treffend maar ook een "
            "waarschuwing. De EU heeft 'kopenhaagencriteria' — democratie, rechtsstaat, "
            "mensenrechten, markteconomie. Op papier helder. In de praktijk heeft politieke "
            "wil meer bepaald dan juridische haalbaarheid.\n\n"
            "Mijn voorstel: combineer een scorecard-drempel (objectief) met een peer review "
            "door bestaande leden (democratisch draagvlak). Landen kunnen pas lid worden als "
            "ze beide halen. Dat voorkomt dat grote leden democratische kandidaten blokkeren "
            "op basis van economisch eigenbelang."
        ),
        created_at=now - timedelta(hours=11),
    )
    db.session.add(p4_r1)
    db.session.flush()

    p4_r2 = Post(
        thread_id=t4.id, user_id=jan.id, parent_id=p4_r1.id,
        author_name='Jan Hofman (demo)', author_username='jan_h', author_role='member', author_age=52,
        source_lang='nl', vote_count=8, is_demo=True,
        body=(
            "Ik ben voor een zo objectief mogelijke drempel, maar Fatima's punt over "
            "politieke blokkades is reëel. Misschien helpt een 'kandidaatstatus' — zoals "
            "de EU dat ook kent.\n\n"
            "Landen die aan de minimumscore voldoen krijgen direct handelsprivileges, ook "
            "als ze nog niet volledig lid zijn. Dat geeft een incentive zonder dat je "
            "vastloopt in politieke onderhandelingen over volwaardig lidmaatschap."
        ),
        created_at=now - timedelta(hours=4),
    )
    db.session.add(p4_r2)

    db.session.commit()


def _thread_list(closed=False):
    last_post_sq = (
        db.session.query(Post.thread_id, func.max(Post.created_at).label('last_at'))
        .group_by(Post.thread_id)
        .subquery()
    )
    rows = (
        Thread.query
        .filter(Thread.is_closed == closed)
        .outerjoin(last_post_sq, Thread.id == last_post_sq.c.thread_id)
        .add_columns(last_post_sq.c.last_at)
        .order_by(last_post_sq.c.last_at.desc().nulls_last(), Thread.created_at.desc())
        .all()
    )
    # Fase D: wat voor deze lezer uit het gesprek is (verplaatst of verborgen)
    # telt niet mee, en een gesprek met zo'n openingsbericht staat er niet in.
    removed, hidden_threads = conduct.removed_post_ids(current_user)
    # Eén query voor de berichten van alle gesprekken (geen query per gesprek);
    # alleen id, gesprek en tijdstip, meer is voor het overzicht niet nodig.
    thread_ids = [thread.id for thread, _ in rows]
    visible_times = {}
    if thread_ids:
        for post_id, thread_id, created_at in (
                db.session.query(Post.id, Post.thread_id, Post.created_at)
                .filter(Post.thread_id.in_(thread_ids), or_(Post.kind.is_(None), Post.kind != FACT_KIND))
                .order_by(Post.created_at, Post.id)):
            if post_id not in removed:
                visible_times.setdefault(thread_id, []).append(created_at)
    result = []
    for thread, _ in rows:
        if thread.id in hidden_threads:
            continue
        times = visible_times.get(thread.id, [])
        result.append({
            'thread': thread,
            'reply_count': len(times),
            'last_time_ago': time_ago(times[-1]) if times else None,
        })
    return result


@forum_bp.route('/forum')
def forum():
    return render_template('forum/index.html', threads=_thread_list(closed=False))


@forum_bp.route('/forum/gesloten')
@login_required
def forum_gesloten():
    if not current_user.can_moderate():
        abort(403)
    return render_template('forum/closed.html', threads=_thread_list(closed=True))


@forum_bp.route('/forum/<int:thread_id>', methods=['GET', 'POST'])
# Rate limits alleen op POST (reageren) en alleen voor ingelogde gebruikers:
# een anonieme POST doet niets (zie hieronder), net zoals @login_required
# op /feedback en /uitnodiging anoniem verkeer buiten de limiter houdt. De
# GET van de threadpagina blijft publiek, dus @login_required kan hier niet.
#
# Volgorde zoals bij /uitnodiging: smalste limiet (per gebruiker) dichtst
# bij def, bredere (per IP) erboven. Flask-Limiter stopt bij de eerste
# overschrijding, dus een verzoek dat al op de per-gebruiker-limiet stuk
# loopt, verhoogt de per-IP-teller niet meer. Persistente SQL-opslag, zie
# app/rate_limit_storage.py.
@limiter.limit("100 per hour", methods=["POST"],
               exempt_when=lambda: not current_user.is_authenticated)
@limiter.limit("5 per minute", methods=["POST"],
               key_func=lambda: f"forum-reply-user-{current_user.get_id()}",
               exempt_when=lambda: not current_user.is_authenticated)
def forum_thread(thread_id):
    thread = Thread.query.get_or_404(thread_id)
    reply_form = ForumReplyForm()
    if request.method == 'POST' and current_user.is_authenticated and not thread.is_closed:
        _abort_if_thread_removed(thread)
        if not current_user.can_contribute():
            return _rules_first_redirect()
        # CSRF, zelfde aanpak als /feedback: leeg FlaskForm, 400 bij een
        # ontbrekend of ongeldig token. Geen honeypot: reageren vereist login.
        if not reply_form.validate_on_submit():
            abort(400)
        body      = request.form.get('body', '').strip()
        parent_id = request.form.get('parent_id', type=int)
        anchor    = 'composer'
        # parent_id komt uit de form en moet daadwerkelijk bij dit thread_id
        # horen — anders kan een reply-koppeling naar een post in een ander
        # thread wijzen (geen IDOR, alle posts zijn al publiek, maar wel een
        # corrupte reply-boomstructuur).
        parent = Post.query.filter_by(id=parent_id, thread_id=thread_id).first() if parent_id is not None else None
        if parent is not None and (parent.is_fact or conduct.is_removed_for(parent, current_user)):
            parent = None  # een feit, of uit het gesprek voor deze lezer: alsof hij niet bestaat
        if parent is not None and parent.user_id == current_user.id:
            # Punt 7: niet reageren op je eigen bericht (de knop is ook verborgen).
            flash(
                'Je kunt niet reageren op je eigen bericht.' if session.get('lang', 'nl') == 'nl'
                else 'You cannot reply to your own post.',
                'error'
            )
            return redirect(url_for('forum.forum_thread', thread_id=thread_id) + f'#post-{parent.id}')
        if parent_id is not None and parent is None:
            flash(
                'Ongeldige reactie: het bericht waarop je reageert bestaat niet in dit gesprek.'
                if session.get('lang', 'nl') == 'nl'
                else 'Invalid reply: the post you are replying to does not exist in this conversation.',
                'error'
            )
            return redirect(url_for('forum.forum_thread', thread_id=thread_id) + '#composer')
        # Afbeeldingen bijvoegen kan niet meer (testronde punt 5); bestaande
        # afbeeldingen bij oude berichten blijven zichtbaar.
        if body:
            post = Post(
                thread_id=thread_id,
                user_id=current_user.id,
                author_name=(f'{current_user.first_name} {current_user.last_name}'
                             if current_user.first_name else ''),
                author_username=current_user.username,
                author_role='member',
                body=body or None,
                source_lang=session.get('lang', 'nl'),
                parent_id=parent_id,
            )
            db.session.add(post)
            db.session.commit()
            # Kwaliteit (fase C1): AI-suggestie op de achtergrond, best-effort.
            quality.schedule_judgement(post.id)
            # Naar de weergave waar de nieuwe reactie staat (ook voorbij niveau 4).
            return redirect(post_url(post))
        return redirect(url_for('forum.forum_thread', thread_id=thread_id) + f'#{anchor}')

    return _render_thread(thread)


# ── Gelaagde weergave (threading) ─────────────────────────────────────────
# Niveau 1 is het openingsbericht; reacties zijn niveau 2, 3 en 4. Reacties
# op het hoogste niveau (via de composer onderaan, zonder parent) horen ook
# op niveau 2, onder het openingsbericht. Dieper dan niveau 4 wordt niet
# ingesprongen: onder een niveau-4-bericht staat een doorlink naar een eigen
# weergave (forum_branch) met dat bericht als niveau 1, met dezelfde regels.

def _tree(all_posts):
    """(openingsbericht of None, kinderen per post-id) voor de weergave."""
    children = {}
    for p in all_posts:
        if p.parent_id is not None:
            children.setdefault(p.parent_id, []).append(p)
    op = next((p for p in all_posts if p.is_op), None)
    if op is not None:
        extra = [p for p in all_posts if p.parent_id is None and p.id != op.id]
        if extra:
            children[op.id] = sorted(children.get(op.id, []) + extra, key=lambda x: (x.created_at, x.id))
    for kids in children.values():
        kids.sort(key=lambda x: (x.created_at, x.id))
    return op, children


def _full_level(post):
    """Niveau van een bijdrage in de volledige weergave (openingsbericht = 1)."""
    chain = [post]
    seen = {post.id}
    while chain[-1].parent_id is not None:
        parent = db.session.get(Post, chain[-1].parent_id)
        if parent is None or parent.id in seen:
            break
        chain.append(parent)
        seen.add(parent.id)
    root = chain[-1]
    return len(chain) if root.is_op else len(chain) + 1, chain


def post_url(post):
    """Waar een bijdrage staat: in het gesprek zelf (niveau 1 tot 4), of in de
    doorlinkweergave van zijn voorouder op niveau 4, 7, 10, ... (die daar
    niveau 1 is, zodat de bijdrage op niveau 2 tot 4 valt)."""
    level, chain = _full_level(post)
    if post.is_op or level <= MAX_LEVEL:
        return url_for('forum.forum_thread', thread_id=post.thread_id) + f'#post-{post.id}'
    root_level = MAX_LEVEL + (MAX_LEVEL - 1) * ((level - MAX_LEVEL - 1) // (MAX_LEVEL - 1))
    root = chain[level - root_level]
    return url_for('forum.forum_branch', thread_id=post.thread_id, post_id=root.id) + f'#post-{post.id}'


@forum_bp.app_context_processor
def _inject_post_url():
    return {'post_url': post_url}


def _abort_if_thread_removed(thread):
    """Fase D: is het openingsbericht verplaatst of verborgen, dan is het hele
    gesprek weg voor anderen; de auteur en moderators zien het wel. Voor het
    reageren; de weergave doet dezelfde controle in _render_thread."""
    op = next((p for p in thread.posts if p.is_op), None)
    if op is not None and conduct.is_removed_for(op, current_user):
        abort(404)


@forum_bp.route('/forum/<int:thread_id>/tak/<int:post_id>')
def forum_branch(thread_id, post_id):
    """Doorlinkweergave: een bijdrage (in de volledige weergave op niveau 4)
    als nieuwe bovenkant, met de telling weer vanaf 1."""
    thread = Thread.query.get_or_404(thread_id)
    return _render_thread(thread, branch_root_id=post_id)


def _render_thread(thread, branch_root_id=None):
    """De gesprekspagina (of een tak). De berichten en de handhavingsgegevens
    worden hier één keer opgehaald en overal hergebruikt."""
    reply_form = ForumReplyForm()
    # Eén query voor alle bijdragen; de feiten (fase E) staan in een eigen
    # blok en buiten de discussieboom, de tellingen en eens/oneens.
    everything = Post.query.filter_by(thread_id=thread.id).order_by(Post.created_at).all()
    facts = [p for p in everything if p.is_fact]
    all_posts = [p for p in everything if not p.is_fact]
    posts_by_id = {p.id: p for p in everything}
    op, tree_children = _tree(all_posts)
    branch_root = None
    if branch_root_id is not None:
        branch_root = posts_by_id.get(branch_root_id)
        if branch_root is None or branch_root.is_fact:
            abort(404)

    # Kwaliteit is alleen een beloning: niets wordt op kwaliteit verborgen.
    all_ids = [p.id for p in all_posts]
    every_id = [p.id for p in everything]
    quality_awards = {a.post_id: a for a in
                      QualityAward.query.filter(QualityAward.post_id.in_(every_id)).all()} if every_id else {}

    # Fase D: gedragsregels. Voor anderen is een verplaatste bijdrage een
    # verwijzing naar de achtergrondpagina en een verborgen bijdrage weg (of
    # een neutrale placeholder als er zichtbare reacties onder staan). De
    # auteur en moderators zien ze gemarkeerd met de reden.
    conduct_cases = conduct.cases_for(every_id)
    removed_ids = {pid for pid, case in conduct_cases.items()
                   if conduct.removed_for(posts_by_id[pid], case, current_user)}
    # Een verplaatst of verborgen openingsbericht verbergt het hele gesprek
    # voor anderen (zelfde regel als _abort_if_thread_removed, zonder extra query's).
    if op is not None and op.id in removed_ids:
        abort(404)
    moved_ids = {pid for pid in removed_ids if conduct_cases[pid].action == 'verplaatst'}
    hidden_ids = removed_ids - moved_ids

    if branch_root is not None:
        top_posts = [branch_root]
    elif op is not None:
        top_posts = [op]
    else:  # geen openingsbericht (oude data): de hoofdberichten zelf bovenaan
        top_posts = [p for p in all_posts if p.parent_id is None]

    # Aantal zichtbare onderliggende reacties per bericht (alle niveaus), voor
    # "n reacties verborgen" en de doorlink. Breedte-eerst, zonder recursie.
    order, queue = [], deque(top_posts)
    while queue:
        p = queue.popleft()
        order.append(p)
        queue.extend(tree_children.get(p.id, []))
    descendant_counts = {}
    for p in reversed(order):
        descendant_counts[p.id] = sum((0 if c.id in removed_ids else 1) + descendant_counts.get(c.id, 0)
                                      for c in tree_children.get(p.id, []))
    if branch_root is not None and branch_root.id in hidden_ids and not descendant_counts.get(branch_root.id):
        abort(404)
    # Onderliggende berichten die iets tonen: zichtbaar, of een verwijzing naar
    # de achtergrondpagina, of een placeholder boven zichtbare reacties.
    shown_counts = {}
    for p in reversed(order):
        shown_counts[p.id] = sum(
            (0 if (c.id in hidden_ids and not descendant_counts.get(c.id)) else 1) + shown_counts.get(c.id, 0)
            for c in tree_children.get(p.id, []))

    stance_counts = _stance_counts(all_ids)
    # Feitenblok (fase E): fouten en ingevulde waarden van een geweigerd feit (PRG).
    fact_errors, fact_data = _pop_form_state(f'feit-{thread.id}') if thread.proposal else ({}, {})
    my_quality_reports = set()
    if current_user.is_authenticated and quality_awards:
        my_quality_reports = {r.award_id for r in QualityReport.query.filter(
            QualityReport.reporter_id == current_user.id,
            QualityReport.award_id.in_([a.id for a in quality_awards.values()]),
            QualityReport.decision.is_(None)).all()}
    my_stances = {}
    if current_user.is_authenticated and all_ids:
        my_stances = {
            pl.post_id: pl.stance for pl in
            PostLike.query.filter(
                PostLike.post_id.in_(all_ids),
                PostLike.user_id == current_user.id
            ).all()
        }

    # Punt 8: eigen, verse bijdragen zonder oordeel worden gepold tot het
    # (best-effort) oordeel er is. Ouder dan 2 minuten: niet meer wachten.
    poll_ids = set()
    if current_user.is_authenticated:
        fresh = datetime.utcnow() - timedelta(minutes=2)
        # Ook na een bewerking (edited_at): dan loopt de beoordeling opnieuw.
        poll_ids = {p.id for p in everything
                    if p.user_id == current_user.id and p.id not in quality_awards and not p.is_demo
                    and p.body != '[bericht verwijderd]'
                    and (p.edited_at or p.created_at) and (p.edited_at or p.created_at) >= fresh}
    return render_template(
        'forum/thread.html',
        thread=thread,
        reply_form=reply_form,
        posts=[p for p in all_posts if p.id not in removed_ids],  # zichtbare tellingen
        poll_ids=poll_ids,
        facts=facts,
        visible_fact_count=sum(1 for f in facts if f.id not in removed_ids),
        fact_errors=fact_errors,
        fact_data=fact_data,
        conduct_cases=conduct_cases,
        moved_ids=moved_ids,
        hidden_ids=hidden_ids,
        top_posts=top_posts,
        tree_children=tree_children,
        branch_root=branch_root,
        posts_by_id=posts_by_id,
        descendant_counts=descendant_counts,
        shown_counts=shown_counts,
        max_level=MAX_LEVEL,
        stance_counts=stance_counts,
        my_stances=my_stances,
        quality_awards=quality_awards,
        my_quality_reports=my_quality_reports,
        auto_translate=_auto_translate(),
    )


@forum_bp.route('/forum/translate/<int:post_id>', methods=['POST'])
@login_required
# Per gebruiker (de route vereist login) en alleen echte API-aanroepen tellen
# mee: een vertaling uit de cache (body_nl/body_en) is gratis. Voorheen per
# IP en elke klik telde: achter de proxy van Render delen bezoekers dan één
# IP, en de automatische vertaling bij het laden van een pagina klikt alle
# vertaalknoppen tegelijk, waardoor het budget voor iedereen snel op was.
@limiter.limit("30 per hour",
               key_func=lambda: f"forum-translate-user-{current_user.get_id()}",
               deduct_when=lambda response: g.get('translation_api_called', False))
def forum_translate(post_id):
    _require_csrf()
    post = Post.query.get_or_404(post_id)
    if conduct.is_removed_for(post, current_user):
        abort(404)
    user_lang = session.get('lang', 'nl')

    if user_lang == post.source_lang:
        return jsonify({'error': 'no translation needed'}), 400

    if user_lang == 'nl':
        if post.body_nl:
            return jsonify({'translation': post.body_nl})
        source = post.body_en or post.body or ''
        target_lang = 'Dutch'
    else:
        if post.body_en:
            return jsonify({'translation': post.body_en})
        source = post.body_nl or post.body or ''
        target_lang = 'English'

    if not source.strip():
        return jsonify({'error': 'no content'}), 400

    api_key = current_app.config.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'translation unavailable'}), 503

    g.translation_api_called = True  # telt mee voor de rate limit hierboven
    try:
        translation = quality.translate_text(source, target_lang)

        if user_lang == 'nl':
            post.body_nl = translation
        else:
            post.body_en = translation
        db.session.commit()

        return jsonify({'translation': translation})
    except Exception as e:
        current_app.logger.error('Translation error: %s', e)
        return jsonify({'error': 'translation failed'}), 500


def _stance_counts(post_ids):
    """{post_id: {'eens': n, 'oneens': m}} voor de gegeven bijdragen."""
    counts = {pid: {'eens': 0, 'oneens': 0} for pid in post_ids}
    if post_ids:
        rows = (db.session.query(PostLike.post_id, PostLike.stance, func.count(PostLike.id))
                .filter(PostLike.post_id.in_(post_ids))
                .group_by(PostLike.post_id, PostLike.stance).all())
        for pid, stance, n in rows:
            if stance in STANCES:
                counts[pid][stance] = n
    return counts


@forum_bp.route('/forum/stance/<int:post_id>', methods=['POST'])
@login_required
# Eens/oneens (forum fase B, vervangt de like). Per gebruiker begrensd, zelfde
# patroon als reageren; @login_required staat erboven, dus anoniem verkeer
# raakt de limiter niet. Een verzoek dat al op de limiet stukloopt, telt niet.
@limiter.limit("30 per minute",
               key_func=lambda: f"forum-stance-user-{current_user.get_id()}")
def forum_stance(post_id):
    """Klik op Eens of Oneens: geen eerdere keuze maakt een nieuwe, de andere
    richting wisselt de bestaande rij, dezelfde richting nog eens trekt de
    keuze in. Eén rij per gebruiker per bijdrage."""
    _require_csrf()
    post = Post.query.get_or_404(post_id)
    if post.is_fact or conduct.is_removed_for(post, current_user):
        abort(404)
    if not current_user.can_contribute():
        return jsonify({'error': 'not allowed'}), 403
    if post.user_id == current_user.id:
        return jsonify({'error': 'cannot vote on own post'}), 400
    stance = request.form.get('stance', '')
    if stance not in STANCES:
        return jsonify({'error': 'invalid stance'}), 400
    existing = PostLike.query.filter_by(post_id=post_id, user_id=current_user.id).first()
    if existing is None:
        db.session.add(PostLike(post_id=post_id, user_id=current_user.id, stance=stance))
        mine = stance
    elif existing.stance == stance:
        db.session.delete(existing)
        mine = None
    else:
        existing.stance = stance
        mine = stance
    db.session.commit()
    return jsonify({**_stance_counts([post_id])[post_id], 'mine': mine})


@forum_bp.route('/forum/<int:thread_id>/close', methods=['POST'])
@login_required
def forum_close(thread_id):
    if not current_user.can_moderate():
        abort(403)
    _require_csrf()
    thread = Thread.query.get_or_404(thread_id)
    thread.is_closed = True
    db.session.commit()
    return redirect(url_for('forum.forum_thread', thread_id=thread_id))


@forum_bp.route('/forum/<int:thread_id>/reopen', methods=['POST'])
@login_required
def forum_reopen(thread_id):
    if not current_user.can_moderate():
        abort(403)
    _require_csrf()
    thread = Thread.query.get_or_404(thread_id)
    thread.is_closed = False
    db.session.commit()
    return redirect(url_for('forum.forum_thread', thread_id=thread_id))


@forum_bp.route('/forum/nieuw', methods=['GET', 'POST'])
@login_required
def forum_nieuw():
    """Een nieuw gesprek starten: een gewoon gesprek, of een voorstel (fase E)
    met een stelling als titel en een levenscyclus in vier fasen."""
    from app import proposals
    if not current_user.can_contribute():
        return _rules_first_redirect()
    max_days = proposals.get_max_days()
    if request.method == 'POST':
        _require_csrf()
        title     = request.form.get('title', '').strip()
        body      = request.form.get('body', '').strip()
        is_proposal = request.form.get('type') == 'voorstel'
        kind = request.form.get('kind')
        minutes, errors = {}, {}
        if is_proposal:
            if kind not in ('publiek', 'event'):
                errors['kind'] = 'invalid'
            elif kind == 'publiek':
                minutes, errors = proposals.parse_durations(request.form, max_days)
        if errors:
            # PRG: fouten en ingevulde waarden een GET lang bewaren.
            keep = ('title', 'body', 'type', 'kind', 'live')
            data = {k: request.form.get(k, '') for k in keep}
            for phase in minutes.keys() | errors.keys() | set(proposals.DEFAULT_MAX_DAYS):
                data[f'duur_{phase}'] = request.form.get(f'duur_{phase}', '')
                data[f'eenheid_{phase}'] = request.form.get(f'eenheid_{phase}', 'dagen')
            _stash_form_state('forum_nieuw', errors, data)
            return redirect(url_for('forum.forum_nieuw'), code=303)
        if title and body:
            thread = Thread(
                eyebrow_nl='Forum · voorstel' if is_proposal else 'Forum · gesprek',
                eyebrow_en='Forum · proposal' if is_proposal else 'Forum · conversation',
                title_prefix_nl=title,
                title_prefix_en=title,
            )
            db.session.add(thread)
            db.session.flush()
            if is_proposal:
                proposals.create(thread, kind, current_user, minutes=minutes,
                                 show_live_tally=request.form.get('live') == '1')
            post = Post(
                thread_id=thread.id,
                user_id=current_user.id,
                author_name=(f'{current_user.first_name} {current_user.last_name}'
                             if current_user.first_name else ''),
                author_username=current_user.username,
                author_role='member',
                body=body or None,
                source_lang=session.get('lang', 'nl'),
                is_op=True,
            )
            db.session.add(post)
            db.session.commit()
            quality.schedule_judgement(post.id)
            return redirect(url_for('forum.forum_thread', thread_id=thread.id) + '#post-' + str(post.id))
    errors, data = _pop_form_state('forum_nieuw')
    return render_template('forum/new_thread.html', errors=errors, form_data=data, max_days=max_days)


# ── Bewerken en verwijderen door de auteur ────────────────────────────────
# Alleen de auteur, nooit een moderator (die verplaatst, verbergt, plaatst
# terug of geeft vrij, zie fase D). Bewerken kan alleen zolang er nog geen
# reacties zijn (ongeacht hun status) en niet bij een verplaatst of verborgen
# bericht (daarvoor is er de bezwaarstroom). Na een bewerking loopt de
# AI-beoordeling (kwaliteit en handhaving) opnieuw op de nieuwe tekst.

def _has_replies(post):
    """Heeft dit bericht reacties? Bij het openingsbericht telt elke andere
    bijdrage in het gesprek (reacties zonder parent horen eronder)."""
    if post.is_op:
        return Post.query.filter(Post.thread_id == post.thread_id, Post.id != post.id).first() is not None
    return Post.query.filter_by(parent_id=post.id).first() is not None


def _edit_refusal(post):
    """None als de huidige gebruiker dit bericht mag bewerken, anders de
    reden: 'forbidden', 'removed' of 'replies'."""
    if (post.user_id is None or post.user_id != current_user.id or post.body == '[bericht verwijderd]'
            or post.is_fact):
        return 'forbidden'
    case = post.conduct_case
    if case is not None and case.removes_from_thread:
        return 'removed'
    if _has_replies(post):
        return 'replies'
    return None


def _edit_refusal_message(reason):
    if reason == 'replies':
        return gettext('This post already has replies, so it can no longer be edited.')
    return gettext('A moved or hidden post cannot be edited. You can object instead.')


def _delete_thread(thread):
    """Het hele gesprek weg, met de opruimlogica van de rest van de app: de
    AI-oordelen, meldingen, handhavingsgegevens en bezwaren via
    quality.delete_for_posts, daarna eens/oneens, de berichten en het gesprek."""
    post_ids = [pid for (pid,) in Post.query.filter_by(thread_id=thread.id).with_entities(Post.id)]
    quality.delete_for_posts(post_ids)
    if post_ids:
        PostLike.query.filter(PostLike.post_id.in_(post_ids)).delete(synchronize_session=False)
        Post.query.filter(Post.id.in_(post_ids)).delete(synchronize_session=False)
    # Ook in bulk: een ORM-delete zou de (al verwijderde) berichten nog bijwerken.
    from app import proposals
    proposals.delete_for_threads([thread.id])
    Thread.query.filter_by(id=thread.id).delete(synchronize_session=False)
    db.session.commit()


def _apply_edit(post, body):
    post.body = body
    post.edited_at = datetime.utcnow()
    quality.reset_for_edit(post)
    db.session.commit()
    quality.schedule_judgement(post.id)


@forum_bp.route('/forum/<int:thread_id>/bewerken', methods=['GET', 'POST'])
@login_required
@limiter.limit("10 per hour", methods=["POST"],
               key_func=lambda: f"forum-edit-user-{current_user.get_id()}")
def forum_bewerken(thread_id):
    """De auteur van het openingsbericht bewerkt titel en opening, zolang er
    nog geen reacties zijn."""
    thread = Thread.query.get_or_404(thread_id)
    op = next((p for p in thread.posts if p.is_op), None)
    if op is None:
        abort(404)
    reason = _edit_refusal(op)
    if reason == 'forbidden':
        abort(403)
    if reason is not None:
        flash(_edit_refusal_message(reason), 'error')
        return redirect(url_for('forum.forum_thread', thread_id=thread_id) + f'#post-{op.id}')
    if request.method == 'POST':
        _require_csrf()
        title = request.form.get('title', '').strip()
        body  = request.form.get('body', '').strip()
        if title and body:
            thread.title_prefix_nl = title
            thread.title_prefix_en = title
            thread.title_accent_nl = None
            thread.title_accent_en = None
            thread.title_suffix_nl = ''
            thread.title_suffix_en = ''
            _apply_edit(op, body)
        return redirect(url_for('forum.forum_thread', thread_id=thread_id) + f'#post-{op.id}')
    title = thread.title_prefix_nl or thread.title_prefix_en
    return render_template('forum/edit_thread.html', thread=thread, op_body=op.body or '', title=title)


@forum_bp.route('/forum/post/<int:post_id>/bewerken', methods=['POST'])
@login_required
@limiter.limit("10 per hour", key_func=lambda: f"forum-edit-user-{current_user.get_id()}")
def forum_post_bewerken(post_id):
    post = Post.query.get_or_404(post_id)
    reason = _edit_refusal(post)
    if reason == 'forbidden':
        abort(403)
    _require_csrf()
    if reason is not None:
        return jsonify({'error': reason, 'message': _edit_refusal_message(reason)}), 409
    body = request.form.get('body', '').strip()
    if body:
        _apply_edit(post, body)
    lang = session.get('lang', 'nl')
    return jsonify({
        'body': post.body,
        'edited': gettext('edited %(when)s', when=post.edited_ago[lang]) if post.edited_at else None,
        # De nieuwe beoordeling verschijnt via de statuspoll, net als bij een nieuw bericht.
        'poll_url': url_for('forum.forum_quality_status', post_id=post.id),
    })


@forum_bp.route('/forum/post/<int:post_id>/verwijderen', methods=['POST'])
@login_required
def forum_post_verwijderen(post_id):
    """De auteur verwijdert zijn eigen bericht (ook met reacties, ook als het
    verplaatst of verborgen is). Zijn openingsbericht alleen zolang er geen
    andere bijdragen zijn (welke status ook): dan verdwijnt het hele gesprek.
    Moderators nooit."""
    post = Post.query.get_or_404(post_id)
    if post.user_id is None or post.user_id != current_user.id or post.is_fact:
        abort(403)
    if post.is_op and _has_replies(post):
        abort(403)
    _require_csrf()
    thread_id = post.thread_id
    if post.is_op:
        _delete_thread(post.thread)
        return redirect(url_for('forum.forum'))
    post.body = '[bericht verwijderd]'
    post.body_nl = None
    post.body_en = None
    post.user_id = None
    # Gedeelde placeholder i.p.v. per-post gok — zelfde principe als de
    # migratie-backfill van bestaande tombstones (zie _migrate_username_and_backfill).
    post.author_username = 'deleted_user'
    db.session.commit()
    return redirect(url_for('forum.forum_thread', thread_id=thread_id))


# ── Kwaliteit (forum fase C1) ─────────────────────────────────────────────

def _quality_back(post):
    return redirect(safe_next(request.form.get('next'), post_url(post)))


@forum_bp.route('/forum/kwaliteit/<int:post_id>/besluit', methods=['POST'])
@login_required
def forum_quality_decide(post_id):
    """Moderator kent het keurmerk handmatig toe of haalt het weg (bijvoorbeeld
    als de AI geen oordeel gaf, of om K3 toe te kennen). Nooit op een eigen
    bijdrage. Weghalen verbergt niets: de bijdrage heeft dan alleen geen medaille."""
    if not current_user.can_moderate():
        abort(403)
    _require_csrf()
    post = Post.query.get_or_404(post_id)
    if post.user_id == current_user.id:
        abort(403)
    if post.is_fact:
        abort(400)  # het kwaliteitskeurmerk geldt niet voor feiten
    action = request.form.get('action')
    criterion = request.form.get('criterion') or None
    note = (request.form.get('explanation') or '').strip()[:400]
    award = post.quality_award
    if action == 'toekennen' and criterion not in QUALITY_CRITERIA:
        abort(400)
    if action not in ('toekennen', 'afwijzen'):
        abort(400)
    if award is None:
        award = QualityAward(post_id=post.id, source='moderator', post_lang=post.source_lang)
        db.session.add(award)
    award.status = 'toegekend' if action == 'toekennen' else 'afgewezen'
    award.criterion = criterion if action == 'toekennen' else award.criterion
    if note:
        # Een AI-oordeel blijft ongewijzigd in de discussie staan; de notitie
        # hoort bij de huidige stand. Bij een eigen oordeel is het de uitleg.
        if award.source == 'ai':
            award.moderator_note = note
        else:
            award.explanation_nl = award.explanation_en = note
    award.decided_by_id = current_user.id
    award.decided_at = datetime.utcnow()
    db.session.commit()
    quality._recalc_author(post)  # fase C2, alleen omhoog
    return _quality_back(post)


def _quality_page_visible(post, award):
    """Alleen een toegekend keurmerk heeft een (openbare) discussiepagina.
    Zonder keurmerk is er niets te tonen: geen reden, geen bezwaar. Een
    bijdrage die wegens de gedragsregels uit het gesprek is, heeft geen
    keurmerk (fase D), dus ook geen pagina."""
    case = post.conduct_case
    return (award is not None and award.status == 'toegekend' and not post.is_fact
            and not (case is not None and case.removes_from_thread))




@forum_bp.route('/forum/kwaliteit/<int:post_id>')
def forum_quality_page(post_id):
    """Openbare discussiepagina bij een toegekend keurmerk: waarom het is
    toegekend, met de gebruikte prompt, de bevindingen en de uitleg."""
    post = Post.query.get_or_404(post_id)
    award = post.quality_award
    if not _quality_page_visible(post, award):
        abort(404)
    return render_template('forum/quality.html', post=post, award=award,
                           auto_translate=_auto_translate())


def _translate_ai_text(obj, prefix, src_lang):
    """Vertaal een AI-tekst (uitleg of reactie) naar de taal van de lezer, op
    verzoek en precies zoals forum_translate een bericht vertaalt: dezelfde
    vertaalhelper, alleen echte API-aanroepen tellen voor de rate limit, en de
    vertaling gecachet in het veld van die taal (het origineel blijft staan)."""
    user_lang = session.get('lang', 'nl')
    if src_lang is None or user_lang == src_lang:
        return jsonify({'error': 'no translation needed'}), 400
    field = f'{prefix}_{user_lang}'
    if getattr(obj, field):
        return jsonify({'translation': getattr(obj, field)})
    source = getattr(obj, f'{prefix}_{src_lang}') or ''
    if not source.strip():
        return jsonify({'error': 'no content'}), 400
    if not current_app.config.get('ANTHROPIC_API_KEY', ''):
        return jsonify({'error': 'translation unavailable'}), 503
    g.translation_api_called = True  # telt mee voor de rate limit
    try:
        translation = quality.translate_text(source, 'Dutch' if user_lang == 'nl' else 'English')
        setattr(obj, field, translation)
        db.session.commit()
        return jsonify({'translation': translation})
    except Exception as e:
        current_app.logger.error('Translation error: %s', e)
        return jsonify({'error': 'translation failed'}), 500


@forum_bp.route('/forum/kwaliteit/<int:post_id>/vertaal', methods=['POST'])
@login_required
@limiter.limit("30 per hour",
               key_func=lambda: f"forum-translate-user-{current_user.get_id()}",
               deduct_when=lambda response: g.get('translation_api_called', False))
def forum_quality_translate(post_id):
    """Vertaal de uitleg van het kwaliteitsoordeel (zelfde knop als bij posts)."""
    _require_csrf()
    post = Post.query.get_or_404(post_id)
    award = post.quality_award
    if not _quality_page_visible(post, award):
        abort(404)
    return _translate_ai_text(award, 'explanation', award.text_lang)


@forum_bp.route('/forum/kwaliteit/<int:post_id>/status')
@login_required
@limiter.limit("120 per minute", key_func=lambda: f"forum-quality-status-user-{current_user.get_id()}")
def forum_quality_status(post_id):
    """Punt 8: lichte statuspoll na het plaatsen. Alleen of de medaille
    verschijnt: pending (nog geen oordeel), toegekend (met de HTML van het
    keurmerkblok) of geen (geen keurmerk, en geen reden)."""
    post = Post.query.get_or_404(post_id)
    if conduct.is_removed_for(post, current_user):
        abort(404)
    qa = post.quality_award
    if qa is None:
        return jsonify({'status': 'pending'})
    is_own = post.user_id == current_user.id
    case = post.conduct_case
    removed = case is not None and case.removes_from_thread
    q_awarded = qa.status == 'toegekend' and post.body != '[bericht verwijderd]' and not removed
    reported = {qa.id} if QualityReport.query.filter_by(
        award_id=qa.id, reporter_id=current_user.id, decision=None).first() else set()
    html = render_template('forum/_quality_block.html', post=post, qa=qa, q_awarded=q_awarded,
                           is_own=is_own, my_quality_reports=reported,
                           return_path=url_for('forum.forum_thread', thread_id=post.thread_id))
    result = {'status': 'toegekend' if q_awarded else 'geen',
              'criterion': qa.criterion if q_awarded else None, 'html': html, 'conduct': None}
    # Fase D: de handhavingsuitkomst voor de auteur (en moderators), zonder
    # verversen; anderen krijgen deze bijdrage dan al niet meer (404 hierboven).
    if removed:
        result['conduct'] = {
            'action': case.action,
            'html': render_template('forum/_conduct_notice.html', post=post, case=case,
                                    return_path=url_for('forum.forum_thread', thread_id=post.thread_id)),
        }
    return jsonify(result)


@forum_bp.route('/forum/kwaliteit/<int:post_id>/melden', methods=['POST'])
@login_required
@limiter.limit("10 per hour", key_func=lambda: f"forum-quality-report-user-{current_user.get_id()}")
def forum_quality_report(post_id):
    """Meld een toegekende kwaliteitsbijdrage als "geen kwaliteit". Gaat naar
    een moderator. Niet op een eigen bijdrage, één open melding per persoon."""
    _require_csrf()
    post = Post.query.get_or_404(post_id)
    award = post.quality_award
    if post.user_id == current_user.id or not _quality_page_visible(post, award):
        abort(403)
    already = QualityReport.query.filter_by(award_id=award.id, reporter_id=current_user.id,
                                            decision=None).first()
    if already is None:
        db.session.add(QualityReport(award_id=award.id, reporter_id=current_user.id))
        db.session.commit()
    return _quality_back(post)


@forum_bp.route('/forum/kwaliteit/melding/<int:report_id>/besluit', methods=['POST'])
@login_required
def forum_quality_report_decide(report_id):
    """Moderator beslist over een melding: handhaven of intrekken. Besluit,
    naam en tijdstip worden vastgelegd; open meldingen op dezelfde
    toekenning krijgen hetzelfde besluit."""
    if not current_user.can_moderate():
        abort(403)
    _require_csrf()
    report = QualityReport.query.get_or_404(report_id)
    award = report.award
    if award.post.user_id == current_user.id:
        abort(403)
    decision = request.form.get('decision')
    if decision not in ('handhaven', 'intrekken'):
        abort(400)
    note = (request.form.get('note') or '').strip()[:400]
    now = datetime.utcnow()
    for r in QualityReport.query.filter_by(award_id=award.id, decision=None):
        r.decision, r.decision_note, r.decided_by_id, r.decided_at = decision, note or None, current_user.id, now
    if decision == 'intrekken':
        award.status = 'afgewezen'
        if note:
            award.moderator_note = note
        award.decided_by_id = current_user.id
        award.decided_at = now
    db.session.commit()
    quality._recalc_author(award.post)  # fase C2, alleen omhoog (nooit lager)
    return redirect(safe_next(request.form.get('next'), url_for('admin.moderator_dashboard')))


# ── Fase D: handhaving van de gedragsregels ───────────────────────────────

@forum_bp.route('/achtergrond')
def achtergrond():
    """Openbare achtergrondpagina: bijdragen die wegens een gedragsregel uit
    het gesprek zijn verplaatst, met regel, status en uitleg. De auteur blijft
    anoniem. Spam, misbruik en open twijfelgevallen staan hier niet."""
    cases = conduct.background_cases()
    # "In context bekijken" alleen als het gesprek voor deze lezer zichtbaar is.
    _removed, hidden_threads = conduct.removed_post_ids(current_user)
    return render_template('forum/achtergrond.html', cases=cases, hidden_threads=hidden_threads)


def _conduct_back(post):
    return redirect(safe_next(request.form.get('next'), post_url(post)))


def _moderator_for(post):
    """Een moderator handelt niet op zijn eigen bijdrage."""
    if not current_user.can_moderate():
        abort(403)
    _require_csrf()
    if post.user_id == current_user.id:
        abort(403)


def _moderator_note():
    return (request.form.get('note') or '').strip()[:1000]


@forum_bp.route('/forum/handhaving/<int:post_id>/verplaatsen', methods=['POST'])
@login_required
def conduct_move(post_id):
    """Moderator verplaatst een bijdrage naar de achtergrondpagina, met een
    regel en een uitleg (die staat openbaar op de achtergrondpagina)."""
    post = Post.query.get_or_404(post_id)
    _moderator_for(post)
    rule, note = request.form.get('rule'), _moderator_note()
    if rule not in ('respect', 'onderwerp') or not note or post.body == '[bericht verwijderd]':
        abort(400)
    conduct.move(post, rule, note, session.get('lang', 'nl'), actor=current_user)
    db.session.commit()
    return _conduct_back(post)


@forum_bp.route('/forum/handhaving/<int:post_id>/verbergen', methods=['POST'])
@login_required
def conduct_hide(post_id):
    """Moderator verbergt spam of grof misbruik volledig."""
    post = Post.query.get_or_404(post_id)
    _moderator_for(post)
    conduct.hide(post, _moderator_note() or None, session.get('lang', 'nl'), actor=current_user)
    db.session.commit()
    return _conduct_back(post)


@forum_bp.route('/forum/handhaving/<int:post_id>/terugplaatsen', methods=['POST'])
@login_required
def conduct_restore(post_id):
    """Moderator plaatst een verplaatste of verborgen bijdrage terug."""
    post = Post.query.get_or_404(post_id)
    _moderator_for(post)
    case = post.conduct_case
    if case is None or not case.removes_from_thread:
        abort(400)
    conduct.restore(case, current_user, _moderator_note() or None)
    db.session.commit()
    return _conduct_back(post)


@forum_bp.route('/forum/handhaving/<int:post_id>/vrijgeven', methods=['POST'])
@login_required
def conduct_release(post_id):
    """Moderator beoordeelt een twijfelgeval als geen overtreding."""
    post = Post.query.get_or_404(post_id)
    _moderator_for(post)
    case = post.conduct_case
    if case is None or case.action != 'twijfel':
        abort(400)
    conduct.release(case, current_user)
    db.session.commit()
    return _conduct_back(post)


@forum_bp.route('/forum/handhaving/<int:post_id>/bezwaar', methods=['POST'])
@login_required
@limiter.limit("5 per hour", key_func=lambda: f"conduct-objection-user-{current_user.get_id()}")
def conduct_object(post_id):
    """De auteur maakt binnen zeven dagen bezwaar tegen een verplaatsing of
    verberging, met een korte toelichting. Eén bezwaar per besluit."""
    _require_csrf()
    post = Post.query.get_or_404(post_id)
    case = post.conduct_case
    if post.user_id != current_user.id or case is None or not case.removes_from_thread:
        abort(403)
    if case.current_objections:
        abort(409)
    if not case.can_object():
        abort(400)  # termijn verlopen
    text = (request.form.get('text') or '').strip()[:2000]
    if not text:
        abort(400)
    conduct.object_to(case, current_user, text)
    db.session.commit()
    return _conduct_back(post)


@forum_bp.route('/forum/handhaving/bezwaar/<int:objection_id>/besluit', methods=['POST'])
@login_required
def conduct_objection_decide(objection_id):
    """Een senior moderator (of de admin) beslist over een bezwaar: handhaven
    of terugplaatsen. Niet de auteur en niet wie het besluit nam."""
    from app.models import ConductObjection
    _require_csrf()
    objection = ConductObjection.query.get_or_404(objection_id)
    case = objection.case
    if objection.decision is not None or not conduct.can_decide_objection(case, current_user):
        abort(403)
    decision, note = request.form.get('decision'), _moderator_note()[:400] or None
    if decision == 'handhaven':
        conduct.confirm(case, current_user, note)
    elif decision == 'terugplaatsen':
        conduct.restore(case, current_user, note)
    else:
        abort(400)
    db.session.commit()
    return redirect(safe_next(request.form.get('next'), url_for('admin.moderator_dashboard')))


# ── Fase E: feiten bij een voorstel ───────────────────────────────────────
# Een feit is een Post met kind 'feit', een richting (voor, tegen, neutraal
# ten opzichte van de stelling) en een bron. Alleen tijdens de feitenfase toe
# te voegen, te bewerken en te verwijderen; daarna een vaste lijst. Geen
# auteur in beeld (intern wel bewaard). Dezelfde AI-aanroep doet de
# handhaving van de gedragsregels; het kwaliteitskeurmerk geldt niet.

_FACT_MAX = 1000


def _fact_form():
    """(waarden, fouten) uit het feitformulier."""
    data = {'text': (request.form.get('text') or '').strip(),
            'direction': request.form.get('direction') or '',
            'source': (request.form.get('source') or '').strip()}
    errors = {}
    if not data['text'] or len(data['text']) > _FACT_MAX:
        errors['text'] = True
    if data['direction'] not in FACT_DIRECTIONS:
        errors['direction'] = True
    if not data['source'] or len(data['source']) > 500:
        errors['source'] = True
    return data, errors


def _fact_phase_open(thread):
    proposal = thread.proposal
    return proposal is not None and proposal.phase == 'feiten' and not thread.is_closed


@forum_bp.route('/forum/<int:thread_id>/feiten', methods=['POST'])
@login_required
@limiter.limit("5 per minute", key_func=lambda: f"forum-fact-user-{current_user.get_id()}")
def fact_add(thread_id):
    thread = Thread.query.get_or_404(thread_id)
    if thread.proposal is None:
        abort(404)
    _abort_if_thread_removed(thread)
    if not current_user.can_contribute():
        return _rules_first_redirect()
    _require_csrf()
    if not _fact_phase_open(thread):
        abort(403)  # alleen tijdens de feitenfase
    data, errors = _fact_form()
    if errors:
        _stash_form_state(f'feit-{thread.id}', errors, data)
        return redirect(url_for('forum.forum_thread', thread_id=thread.id) + '#feiten', code=303)
    fact = Post(thread_id=thread.id, user_id=current_user.id, author_username=current_user.username,
                author_name='', author_role='member', body=data['text'], source_lang=session.get('lang', 'nl'),
                kind=FACT_KIND, fact_direction=data['direction'], fact_source=data['source'])
    db.session.add(fact)
    db.session.commit()
    quality.schedule_judgement(fact.id)  # handhaving van de gedragsregels, zie quality.judge_post
    return redirect(url_for('forum.forum_thread', thread_id=thread.id) + f'#post-{fact.id}')


def _own_fact_in_open_phase(post_id):
    fact = Post.query.get_or_404(post_id)
    if not fact.is_fact or fact.user_id is None or fact.user_id != current_user.id:
        abort(403)
    if not _fact_phase_open(fact.thread):
        abort(403)  # na de feitenfase staan de feiten vast
    return fact


@forum_bp.route('/forum/feit/<int:post_id>/bewerken', methods=['POST'])
@login_required
@limiter.limit("10 per hour", key_func=lambda: f"forum-edit-user-{current_user.get_id()}")
def fact_edit(post_id):
    """De inbrenger bewerkt zijn feit zolang de feitenfase loopt, maar niet als
    het verplaatst of verborgen is (daarvoor is de bezwaarstroom). De
    handhaving loopt opnieuw op de nieuwe tekst."""
    fact = _own_fact_in_open_phase(post_id)
    _require_csrf()
    case = fact.conduct_case
    if case is not None and case.removes_from_thread:
        abort(409)
    data, errors = _fact_form()
    if errors:
        _stash_form_state(f'feit-{fact.thread_id}', {f'edit-{fact.id}': errors}, {f'edit-{fact.id}': data})
        return redirect(url_for('forum.forum_thread', thread_id=fact.thread_id) + f'#post-{fact.id}', code=303)
    fact.fact_direction, fact.fact_source = data['direction'], data['source']
    _apply_edit(fact, data['text'])
    return redirect(url_for('forum.forum_thread', thread_id=fact.thread_id) + f'#post-{fact.id}')


@forum_bp.route('/forum/feit/<int:post_id>/verwijderen', methods=['POST'])
@login_required
def fact_delete(post_id):
    """De inbrenger verwijdert zijn feit zolang de feitenfase loopt, met de
    bestaande opruimlogica (oordeel, handhaving, bezwaren)."""
    fact = _own_fact_in_open_phase(post_id)
    _require_csrf()
    thread_id = fact.thread_id
    quality.delete_for_posts([fact.id])
    db.session.delete(fact)
    db.session.commit()
    return redirect(url_for('forum.forum_thread', thread_id=thread_id) + '#feiten')
