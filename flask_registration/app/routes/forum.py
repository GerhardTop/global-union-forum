import os
import secrets
import uuid
from collections import deque
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, session, jsonify, abort, current_app, flash, g
from flask_login import login_required, current_user
from sqlalchemy import func

from app import db, bcrypt, limiter
from app.forms import ForumActionForm, ForumReplyForm
from app.models import STANCES, QUALITY_CRITERIA, User, Thread, Post, PostLike, QualityAward, QualityReport
from app import quality
from app.utils import safe_next

forum_bp = Blueprint("forum", __name__)


@forum_bp.app_context_processor
def _inject_forum_action_form():
    # Lui gebruikt: het token wordt pas gemaakt als een template het rendert.
    return {'forum_action_form': ForumActionForm(), 'quality_labels': quality.CRITERIA_LABELS}


def _rules_first_redirect():
    """Ingelogd maar (nog) geen akkoord op de spelregels: eerst daarheen."""
    flash(
        'Accepteer eerst de spelregels om mee te doen aan het gesprek.'
        if session.get('lang', 'nl') == 'nl'
        else 'Please accept the ground rules first to join the conversation.',
        'error'
    )
    return redirect(url_for('main.spelregels', next=request.path))


def _require_csrf():
    """CSRF per actie, zelfde aanpak als /feedback en de reactie-stroom:
    leeg FlaskForm, 400 bij een ontbrekend of ongeldig token."""
    if not ForumActionForm().validate_on_submit():
        abort(400)

_ALLOWED_EXT = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
_MAX_SIZE = 5 * 1024 * 1024
# Aantal inspringniveaus voor reacties op desktop (mobiel: 2, via CSS).
MAX_REPLY_INDENT = 4

# De demo-accounts die _seed_forum() aanmaakt. Ook gebruikt door het
# beheer-commando `flask reset-content` (app/cli.py) als keep-set.
DEMO_USER_EMAILS = {
    'jan':    'jan.hofman.demo@globalunionforum.org',
    'fatima': 'fatima.elidrissi.demo@globalunionforum.org',
    'sarah':  'sarah.chen.demo@globalunionforum.org',
    'marcus': 'marcus.osei.demo@globalunionforum.org',
}
_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'uploads')


def _save_upload(file):
    if not file or not file.filename:
        return None
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in _ALLOWED_EXT:
        return None
    file.seek(0, 2)
    if file.tell() > _MAX_SIZE:
        file.seek(0)
        return None
    file.seek(0)
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    filename = f'{uuid.uuid4().hex}.{ext}'
    file.save(os.path.join(_UPLOAD_DIR, filename))
    return f'/static/uploads/{filename}'


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
    """Seed 4 demo threads with demo users on first run."""
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
    result = []
    for thread, _ in rows:
        last_post_obj = thread.posts[-1] if thread.posts else None
        result.append({
            'thread': thread,
            'reply_count': len(thread.posts),
            'last_time_ago': last_post_obj.time_ago if last_post_obj else None,
        })
    return result


@forum_bp.route('/forum')
def forum():
    _seed_forum()
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
    _seed_forum()
    thread = Thread.query.get_or_404(thread_id)
    reply_form = ForumReplyForm()
    if request.method == 'POST' and current_user.is_authenticated and not thread.is_closed:
        if not current_user.can_contribute():
            return _rules_first_redirect()
        # CSRF, zelfde aanpak als /feedback: leeg FlaskForm, 400 bij een
        # ontbrekend of ongeldig token. Geen honeypot: reageren vereist login.
        if not reply_form.validate_on_submit():
            abort(400)
        body      = request.form.get('body', '').strip()
        parent_id = request.form.get('parent_id', type=int)
        image_url = _save_upload(request.files.get('image'))
        anchor    = 'composer'
        # parent_id komt uit de form en moet daadwerkelijk bij dit thread_id
        # horen — anders kan een reply-koppeling naar een post in een ander
        # thread wijzen (geen IDOR, alle posts zijn al publiek, maar wel een
        # corrupte reply-boomstructuur).
        if parent_id is not None and not Post.query.filter_by(id=parent_id, thread_id=thread_id).first():
            flash(
                'Ongeldige reactie: het bericht waarop je reageert bestaat niet in dit gesprek.'
                if session.get('lang', 'nl') == 'nl'
                else 'Invalid reply: the post you are replying to does not exist in this conversation.',
                'error'
            )
            return redirect(url_for('forum.forum_thread', thread_id=thread_id) + '#composer')
        if body or image_url:
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
                image_url=image_url,
            )
            db.session.add(post)
            db.session.commit()
            anchor = f'post-{post.id}'
            # Kwaliteit (fase C1): AI-suggestie op de achtergrond, best-effort.
            quality.schedule_judgement(post.id)
        return redirect(url_for('forum.forum_thread', thread_id=thread_id) + f'#{anchor}')

    all_posts = Post.query.filter_by(thread_id=thread_id).order_by(Post.created_at).all()
    root_posts  = [p for p in all_posts if p.parent_id is None]
    replies_map = {}
    for p in all_posts:
        if p.parent_id is not None:
            replies_map.setdefault(p.parent_id, []).append(p)
    posts_by_id = {p.id: p for p in all_posts}

    # Diepte per bericht, breedte-eerst vanaf de roots (geen recursie, dus
    # ook een extreem lange keten kan de pagina niet laten crashen). In
    # deze volgorde komt een ouder altijd vóór zijn kinderen.
    depths, tree_order = {}, []
    queue = deque((p, 0) for p in root_posts)
    while queue:
        p, d = queue.popleft()
        depths[p.id] = d
        tree_order.append(p)
        queue.extend((c, d + 1) for c in replies_map.get(p.id, []))

    # Totaal aantal onderliggende reacties per bericht (alle niveaus), voor
    # de inklap-knop.
    descendant_counts = {}
    for p in reversed(tree_order):
        descendant_counts[p.id] = sum(1 + descendant_counts[c.id] for c in replies_map.get(p.id, []))

    # Voorbij de dieptegrens wordt niet verder ingesprongen: alles onder een
    # bericht op MAX_REPLY_INDENT komt als één platte, chronologische lijst
    # op dat laatste niveau, elk met een "in antwoord op"-label.
    flat_descendants = {}
    for p in tree_order:
        if depths[p.id] == MAX_REPLY_INDENT and replies_map.get(p.id):
            found, stack = [], list(replies_map[p.id])
            while stack:
                c = stack.pop()
                found.append(c)
                stack.extend(replies_map.get(c.id, []))
            flat_descendants[p.id] = sorted(found, key=lambda x: (x.created_at, x.id))

    # Voor de inklap-knop binnen de platte lijst: per plat bericht de ids
    # van zijn platte voorouders (die zelf ook voorbij de grens staan).
    flat_ancestors = {}
    for p in tree_order:
        if depths[p.id] > MAX_REPLY_INDENT:
            parent = posts_by_id[p.parent_id]
            flat_ancestors[p.id] = (flat_ancestors.get(parent.id, []) + [parent.id]
                                    if depths[parent.id] > MAX_REPLY_INDENT else [])

    all_ids = [p.id for p in all_posts]
    stance_counts = _stance_counts(all_ids)
    quality_awards = {a.post_id: a for a in
                      QualityAward.query.filter(QualityAward.post_id.in_(all_ids)).all()} if all_ids else {}
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

    user_lang = session.get('lang', 'nl')
    auto_translate = current_user.is_authenticated and (
        current_user.auto_translate is True
        or (current_user.auto_translate is None and user_lang == 'en')
    )
    return render_template(
        'forum/thread.html',
        thread=thread,
        reply_form=reply_form,
        posts=all_posts,
        root_posts=root_posts,
        replies_map=replies_map,
        posts_by_id=posts_by_id,
        depths=depths,
        descendant_counts=descendant_counts,
        flat_descendants=flat_descendants,
        flat_ancestors=flat_ancestors,
        max_reply_indent=MAX_REPLY_INDENT,
        stance_counts=stance_counts,
        my_stances=my_stances,
        quality_awards=quality_awards,
        my_quality_reports=my_quality_reports,
        auto_translate=auto_translate,
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
    if not current_user.can_contribute():
        return _rules_first_redirect()
    _seed_forum()
    if request.method == 'POST':
        _require_csrf()
        title     = request.form.get('title', '').strip()
        body      = request.form.get('body', '').strip()
        image_url = _save_upload(request.files.get('image'))
        if title and (body or image_url):
            thread = Thread(
                eyebrow_nl='Forum · gesprek',
                eyebrow_en='Forum · conversation',
                title_prefix_nl=title,
                title_prefix_en=title,
            )
            db.session.add(thread)
            db.session.flush()
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
                image_url=image_url,
            )
            db.session.add(post)
            db.session.commit()
            quality.schedule_judgement(post.id)
            return redirect(url_for('forum.forum_thread', thread_id=thread.id) + '#post-' + str(post.id))
    return render_template('forum/new_thread.html')


@forum_bp.route('/forum/<int:thread_id>/bewerken', methods=['GET', 'POST'])
@login_required
def forum_bewerken(thread_id):
    thread = Thread.query.get_or_404(thread_id)
    op = next((p for p in thread.posts if p.is_op), None)
    is_author = op and op.user_id == current_user.id
    if not (is_author or current_user.can_moderate()):
        abort(403)
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
            if op:
                op.body = body
            db.session.commit()
        return redirect(url_for('forum.forum_thread', thread_id=thread_id))
    op_body = op.body if op else ''
    title = thread.title_prefix_nl or thread.title_prefix_en
    return render_template('forum/edit_thread.html', thread=thread, op_body=op_body, title=title)


@forum_bp.route('/forum/post/<int:post_id>/bewerken', methods=['POST'])
@login_required
def forum_post_bewerken(post_id):
    post = Post.query.get_or_404(post_id)
    if not (post.user_id == current_user.id or current_user.can_moderate()):
        abort(403)
    _require_csrf()
    body = request.form.get('body', '').strip()
    if body:
        post.body = body
        db.session.commit()
    return jsonify({'body': post.body})


@forum_bp.route('/forum/post/<int:post_id>/verwijderen', methods=['POST'])
@login_required
def forum_post_verwijderen(post_id):
    post = Post.query.get_or_404(post_id)
    if not (post.user_id == current_user.id or current_user.can_moderate()):
        abort(403)
    if post.is_op and not current_user.can_moderate():
        abort(403)
    _require_csrf()
    thread_id = post.thread_id
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
    return redirect(safe_next(request.form.get('next'),
                              url_for('forum.forum_thread', thread_id=post.thread_id) + f'#post-{post.id}'))


@forum_bp.route('/forum/kwaliteit/<int:post_id>/besluit', methods=['POST'])
@login_required
def forum_quality_decide(post_id):
    """Moderator kent een kwaliteitsbijdrage toe of wijst af: bevestigt een
    AI-suggestie, of beoordeelt zelf (ook als er geen suggestie is). Nooit
    op een eigen bijdrage."""
    if not current_user.can_moderate():
        abort(403)
    _require_csrf()
    post = Post.query.get_or_404(post_id)
    if post.user_id == current_user.id:
        abort(403)
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
    if action == 'afwijzen' and not (note or award.explanation_nl or award.explanation_en):
        abort(400)  # de auteur moet een reden kunnen zien
    award.status = 'toegekend' if action == 'toekennen' else 'afgewezen'
    award.criterion = criterion if action == 'toekennen' else award.criterion
    if note:
        award.explanation_nl = award.explanation_en = note
    award.decided_by_id = current_user.id
    award.decided_at = datetime.utcnow()
    award.review_requested_at = None
    db.session.commit()
    return _quality_back(post)


@forum_bp.route('/forum/kwaliteit/<int:post_id>/meekijken', methods=['POST'])
@login_required
@limiter.limit("10 per hour", key_func=lambda: f"forum-quality-review-user-{current_user.get_id()}")
def forum_quality_review(post_id):
    """De auteur van een niet-toegekende bijdrage laat een moderator meekijken."""
    _require_csrf()
    post = Post.query.get_or_404(post_id)
    award = post.quality_award
    if post.user_id != current_user.id or award is None or award.status != 'afgewezen':
        abort(403)
    award.review_requested_at = datetime.utcnow()
    db.session.commit()
    return _quality_back(post)


@forum_bp.route('/forum/kwaliteit/<int:post_id>/melden', methods=['POST'])
@login_required
@limiter.limit("10 per hour", key_func=lambda: f"forum-quality-report-user-{current_user.get_id()}")
def forum_quality_report(post_id):
    """Meld een toegekende kwaliteitsbijdrage als "geen kwaliteit". Gaat naar
    een moderator. Niet op een eigen bijdrage, één open melding per persoon."""
    _require_csrf()
    post = Post.query.get_or_404(post_id)
    award = post.quality_award
    if post.user_id == current_user.id or award is None or award.status != 'toegekend':
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
            award.explanation_nl = award.explanation_en = note
        award.decided_by_id = current_user.id
        award.decided_at = now
    db.session.commit()
    return redirect(safe_next(request.form.get('next'), url_for('admin.moderator_dashboard')))
