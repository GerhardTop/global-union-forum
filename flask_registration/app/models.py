from datetime import datetime, timedelta
from flask_login import AnonymousUserMixin, UserMixin
from app import db, login_manager

# Contentrollen (forum fase A), van laag naar hoog. "Reader" is geen rol maar
# simpelweg niet ingelogd, of ingelogd zonder akkoord op de spelregels
# (role is dan NULL). Admin (is_admin) staat los hiervan en boven alles.
# Voter is gedefinieerd maar dwingt in deze fase nog niets extra's af.
ROLES = ('contributor', 'voter', 'moderator', 'senior_moderator')
_MODERATING_ROLES = ('moderator', 'senior_moderator')

# Forum fase C2: verdiende status op basis van toegekende kwaliteitsbijdragen
# (zie app/status.py), van laag naar hoog. Staat los van de handmatige rol;
# getoond wordt de hoogste van de twee. Een verdiende 'moderator' of 'senior'
# geeft GEEN moderatiemacht (can_moderate kijkt hier niet naar).
STATUS_LADDER = ('reader', 'contributor', 'voter', 'moderator', 'senior')
# Handmatige rol (fase A) op dezelfde ladder, alleen voor de weergave. De rol
# 'contributor' is sinds fase A de standaard voor iedereen die de spelregels
# accepteerde, geen bewuste verhoging; die telt hier dus niet (anders zou
# iedereen 'Bijdrager' tonen, terwijl Bijdrager in C2 een toegekende
# kwaliteitsbijdrage vereist).
_MANUAL_STATUS = {'voter': 'voter', 'moderator': 'moderator', 'senior_moderator': 'senior'}


def status_rank(status):
    return STATUS_LADDER.index(status) if status in STATUS_LADDER else 0


# A deleted account is anonymised, not removed: it shows as @verwijderd.
DELETED_HANDLE = 'verwijderd'


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    # Hoofdletter-ongevoelige uniciteit wordt afgedwongen via een functionele
    # index (LOWER(username)) in _migrate_columns(), niet via unique=True hier.
    # Let op: account_verwijderen() (auth.py) hard-delete't het eigen account
    # nu al — dat maakt de username direct vrij voor hergebruik door een
    # ander. Relevant voor toekomstig ontwerp (bv. cooldown-periode of
    # gereserveerde namen) als dit ooit een probleem blijkt.
    username = db.Column(db.String(30), nullable=False)
    first_name = db.Column(db.String(50), nullable=True)
    last_name = db.Column(db.String(50), nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    is_moderator = db.Column(db.Boolean, nullable=False, default=False)
    verified = db.Column(db.Boolean, nullable=False, default=False)
    linkedin_url   = db.Column(db.String(255), nullable=True)
    google_id      = db.Column(db.String(100), nullable=True, unique=True)
    auto_translate      = db.Column(db.Boolean, nullable=True, default=None)
    password_changed_at = db.Column(db.DateTime, nullable=True, default=None)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)
    # Forum fase A: contentrol (zie ROLES) en akkoord op de spelregels. Een
    # nieuwe registratie heeft allebei NULL tot het akkoord; bestaande
    # gebruikers zijn bij de migratie gegrandfatherd (_migrate_roles).
    # is_moderator blijft bestaan en wordt via set_role() synchroon gehouden.
    role                = db.Column(db.String(20), nullable=True, default=None)
    rules_accepted_at   = db.Column(db.DateTime, nullable=True, default=None)
    # Naamweergave in het forum: standaard het pseudoniem (@username). Alleen
    # als de gebruiker dit zelf aanzet (met bevestiging, zie profiel) komen
    # de echte naam en LinkedIn publiek bij zijn bijdragen te staan.
    show_real_name      = db.Column(db.Boolean, nullable=True, default=False)
    # Forum fase C2: verdiende status (alleen omhoog, zie app/status.py).
    # NULL betekent reader. Promoties met tijdstip staan in QualityPromotion.
    quality_status      = db.Column(db.String(12), nullable=True, default=None)
    # Fase E, stap 5b: de dagelijkse samenvatting van gevolgde voorstellen.
    # digest_email NULL of True = ook per e-mail; False = alleen in het forum.
    # has_unread_digest staat op de gebruiker zelf, zodat de melding in beeld
    # geen extra query kost.
    digest_email        = db.Column(db.Boolean, nullable=True, default=None)
    last_digest_at      = db.Column(db.DateTime, nullable=True, default=None)
    has_unread_digest   = db.Column(db.Boolean, nullable=True, default=None)
    # Stored language preference ("nl" or "en"): set at registration and on
    # every language switch, applied to the session at login, and used for
    # the daily digest. NULL means no preference yet (the digest falls back to
    # Dutch). Independent of auto_translate.
    lang                = db.Column(db.String(5), nullable=True, default=None)
    # User moderation (Gebruiker.dc.html, step 1): a suspension ends at
    # suspended_until (the history is in Suspension). deleted_* are for
    # anonymising an account later; the row itself then stays.
    suspended_until     = db.Column(db.DateTime, nullable=True, default=None)
    deleted_at          = db.Column(db.DateTime, nullable=True, default=None)
    deleted_by_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    deleted_reason      = db.Column(db.Text, nullable=True)
    # Notifications step 1 (docs/design/notificaties.html): per category
    # {"app": bool, "email": bool} as JSON, merged over the defaults by
    # app/notifications.effective_prefs; and the summary email frequency
    # (off/daily/weekly). Separate from the existing daily digest.
    notif_prefs         = db.Column(db.Text, nullable=True)
    summary_frequency   = db.Column(db.String(8), nullable=True, default='weekly')

    def __repr__(self):
        return f"<User {self.email}>"

    @property
    def is_suspended(self):
        """Suspended right now: suspended_until is set and still in the future."""
        return self.suspended_until is not None and self.suspended_until > datetime.utcnow()

    @property
    def is_deleted(self):
        """The account was deleted by moderation (anonymised, row kept)."""
        return self.deleted_at is not None

    is_anonymised = is_deleted

    @property
    def is_senior_moderator(self):
        """Senior moderator or admin: decides objections (conduct.can_decide_objection)
        and gates suspending and deleting accounts. Not while suspended."""
        if self.is_suspended:
            return False
        return bool(self.is_admin) or self.role == 'senior_moderator'

    # ── Permissies (centrale helpers, gebruikt door routes en templates) ──
    def _rank(self):
        return ROLES.index(self.role) + 1 if self.role in ROLES else 0

    def can_contribute(self):
        """Posten, reageren, een draad starten. Not while suspended."""
        if self.is_suspended:
            return False
        return bool(self.is_admin) or (self.rules_accepted_at is not None and self._rank() >= 1)

    def can_vote(self):
        """Stemrecht: verdiend (status voter of hoger, fase C2) of handmatig
        toegekend (rol voter of hoger). Er is nog geen bindende stemfunctie;
        dit recht wordt bewaard voor later en nu alleen als status getoond.
        Not while suspended."""
        if self.is_suspended:
            return False
        return (bool(self.is_admin)
                or status_rank(self.quality_status) >= status_rank('voter')
                or (self.rules_accepted_at is not None and self._rank() >= 2))

    @property
    def manual_status(self):
        """De handmatige rol (fase A) vertaald naar de statusladder."""
        if self.is_admin:
            return 'senior'
        status = _MANUAL_STATUS.get(self.role, 'reader')
        if self.is_moderator and status_rank(status) < status_rank('moderator'):
            status = 'moderator'
        return status

    @property
    def earned_status(self):
        return self.quality_status if self.quality_status in STATUS_LADDER else 'reader'

    @property
    def effective_status(self):
        """Getoonde status: de hoogste van verdiend en handmatig."""
        return max(self.earned_status, self.manual_status, key=status_rank)

    def can_moderate(self):
        """Draden sluiten/heropenen, andermans berichten bewerken/verwijderen.
        is_moderator telt ook mee als vangnet, zodat wie vóór fase A
        moderator was dat altijd blijft (set_role houdt beide synchroon).
        Not while suspended (admins cannot be suspended)."""
        if self.is_suspended:
            return False
        return bool(self.is_admin) or bool(self.is_moderator) or self._rank() >= ROLES.index('moderator') + 1

    def can_manage_rules(self):
        return self.is_senior_moderator

    @property
    def real_name(self):
        """Voor- en achternaam, of None als er geen echte naam bekend is."""
        name = ' '.join(p for p in (self.first_name, self.last_name) if p).strip()
        return name or None

    @property
    def shows_real_name(self):
        """Echte naam publiek: alleen als zelf gekozen én er een naam is."""
        return bool(self.show_real_name) and self.real_name is not None

    @property
    def public_name(self):
        """Hoe deze gebruiker publiek verschijnt: echte naam of @pseudoniem.
        A deleted (anonymised) account is '@verwijderd'."""
        if self.is_deleted:
            return '@' + DELETED_HANDLE
        return self.real_name if self.shows_real_name else '@' + self.username

    @property
    def handle(self):
        """The @handle for moderator screens: '@verwijderd' for a deleted account."""
        return '@' + (DELETED_HANDLE if self.is_deleted else self.username)

    def set_role(self, role):
        """Zet de contentrol en houd het oude is_moderator-vlag synchroon."""
        if role not in ROLES:
            raise ValueError(f'onbekende rol: {role}')
        self.role = role
        self.is_moderator = role in _MODERATING_ROLES

    def accept_rules(self):
        """Akkoord op de spelregels; een nieuwe gebruiker wordt zo Contributor."""
        if self.rules_accepted_at is None:
            self.rules_accepted_at = datetime.utcnow()
        if self.role is None:
            self.set_role('contributor')


class AnonymousUser(AnonymousUserMixin):
    """Reader: niet ingelogd, mag alleen lezen."""
    is_admin = False
    is_moderator = False
    role = None
    is_senior_moderator = False
    is_suspended = False

    def can_contribute(self):
        return False

    def can_vote(self):
        return False

    def can_moderate(self):
        return False

    def can_manage_rules(self):
        return False


login_manager.anonymous_user = AnonymousUser


@login_manager.user_loader
def load_user(user_id):
    user = db.session.get(User, int(user_id))
    # A deleted (anonymised) account has no session any more.
    return None if user is None or user.is_deleted else user


class Thread(db.Model):
    __tablename__ = "threads"

    id           = db.Column(db.Integer, primary_key=True)
    eyebrow_nl   = db.Column(db.String(200), nullable=False)
    eyebrow_en   = db.Column(db.String(200), nullable=False)
    title_prefix_nl = db.Column(db.String(300), nullable=False, default='')
    title_accent_nl = db.Column(db.String(200), nullable=True)
    title_suffix_nl = db.Column(db.String(50),  nullable=False, default='')
    title_prefix_en = db.Column(db.String(300), nullable=False, default='')
    title_accent_en = db.Column(db.String(200), nullable=True)
    title_suffix_en = db.Column(db.String(50),  nullable=False, default='')
    is_closed    = db.Column(db.Boolean, nullable=False, default=False)
    is_demo      = db.Column(db.Boolean, nullable=False, default=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    posts        = db.relationship('Post', backref='thread', lazy=True,
                                   order_by='Post.created_at')

    def title(self, lang):
        if lang == 'nl':
            return (self.title_prefix_nl, self.title_accent_nl, self.title_suffix_nl)
        return (self.title_prefix_en, self.title_accent_en, self.title_suffix_en)


class Post(db.Model):
    __tablename__ = "posts"

    id             = db.Column(db.Integer, primary_key=True)
    # Indexen (ook via _migrate_indexes voor bestaande databases): Postgres
    # maakt bij een foreign key niet vanzelf een index.
    thread_id      = db.Column(db.Integer, db.ForeignKey('threads.id'), nullable=False, index=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    parent_id      = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=True, index=True)
    author_name    = db.Column(db.String(100), nullable=False)
    author_username = db.Column(db.String(30), nullable=False)
    author_role    = db.Column(db.String(20),  nullable=False, default='member')
    author_badge_nl = db.Column(db.String(200), nullable=True)
    author_badge_en = db.Column(db.String(200), nullable=True)
    author_age     = db.Column(db.Integer, nullable=True)
    body           = db.Column(db.Text, nullable=True)
    body_nl        = db.Column(db.Text, nullable=True)
    body_en        = db.Column(db.Text, nullable=True)
    source_lang    = db.Column(db.String(5), nullable=False, default='nl')
    is_op          = db.Column(db.Boolean, default=False)
    is_demo        = db.Column(db.Boolean, nullable=False, default=False)
    vote_count     = db.Column(db.Integer, default=0)
    image_url      = db.Column(db.String(500), nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    # Laatste bewerking door de auteur (alleen zolang er geen reacties zijn);
    # toont het label "bewerkt" en start een nieuwe AI-beoordeling.
    edited_at      = db.Column(db.DateTime, nullable=True)
    # Fase E: een feit in het feitenblok van een voorstel is ook een Post, met
    # kind 'feit' (NULL = gewoon bericht), een richting en een bron. Zo geldt de
    # handhaving (fase D) er vanzelf voor; feiten staan buiten de discussie,
    # krijgen geen keurmerk en tellen niet voor de verdiende status.
    kind           = db.Column(db.String(10), nullable=True)
    fact_direction = db.Column(db.String(10), nullable=True)   # voor/tegen/neutraal
    fact_source    = db.Column(db.String(500), nullable=True)
    # Polijstronde P5: een reactie bij een voorstel onder een vraagstuk, met
    # de versie van dat voorstel waarop ze slaat (NULL: een gewone reactie).
    issue_proposal_id = db.Column(db.Integer, db.ForeignKey('issue_proposals.id'), nullable=True, index=True)
    proposal_version  = db.Column(db.Integer, nullable=True)
    # Polijstronde P2: het soort feit (FACT_TYPES); NULL telt als 'feit'.
    # 'media' is nieuws, een podcast, een video en dergelijke.
    fact_type      = db.Column(db.String(10), nullable=True)
    # Een korte, handgeschreven kern (max 80 tekens) als kopregel in de lijst;
    # fact_title_en is de vertaalcache, net als body/body_en.
    fact_title     = db.Column(db.String(100), nullable=True)
    fact_title_en  = db.Column(db.String(100), nullable=True)
    author         = db.relationship('User', foreign_keys=[user_id], lazy='joined')
    # Geneste reacties: parent_id wijst naar het bericht waarop gereageerd
    # wordt (NULL = bericht op het hoogste niveau). De threadpagina bouwt de
    # boom zelf uit één query (replies_map); deze relatie is voor overig gebruik.
    replies        = db.relationship('Post', backref=db.backref('parent', remote_side=[id]),
                                     lazy='select', order_by='Post.created_at')

    # ── Publieke auteursweergave ─────────────────────────────────────────
    # Altijd op basis van de HUIDIGE instelling van de auteur (author-
    # relatie), zodat een wijziging meteen bij al zijn berichten doorwerkt.
    # Zonder author (account verwijderd) blijft het opgeslagen pseudoniem.
    def public_author_name(self):
        a = self.author
        return a.public_name if a is not None else '@' + self.author_username

    def public_author_initial(self):
        return self.public_author_name().lstrip('@')[:1].upper()

    def public_linkedin_url(self):
        a = self.author
        if a is not None and a.shows_real_name and a.linkedin_url:
            return a.linkedin_url
        return None

    @property
    def time_ago(self):
        return time_ago(self.created_at)

    @property
    def edited_ago(self):
        return time_ago(self.edited_at)

    @property
    def is_fact(self):
        return self.kind == FACT_KIND

    @property
    def fact_source_is_url(self):
        return bool(self.fact_source) and self.fact_source.lower().startswith(('http://', 'https://'))

    @property
    def is_media(self):
        return self.fact_type == 'media'


def time_ago(moment):
    """{'nl': ..., 'en': ...}: hoe lang geleden, voor bijdragen en besluiten."""
    if moment is None:
        return {'nl': '', 'en': ''}
    delta = datetime.utcnow() - moment
    d, s = delta.days, delta.seconds
    if d >= 2:
        return {'nl': f'{d} dagen geleden', 'en': f'{d} days ago'}
    if d == 1:
        return {'nl': '1 dag geleden', 'en': '1 day ago'}
    h = s // 3600
    if h >= 1:
        return {'nl': f'{h} uur geleden', 'en': f'{h} hour{"s" if h > 1 else ""} ago'}
    m = s // 60
    if m >= 1:
        return {'nl': f'{m} min geleden', 'en': f'{m} min ago'}
    return {'nl': 'zojuist', 'en': 'just now'}


# Fase E: feiten bij een voorstel.
FACT_KIND = 'feit'
FACT_DIRECTIONS = ('voor', 'tegen', 'neutraal')
# Polijstronde P2: feit of onderzoek, of media (nieuws, podcast, video).
FACT_TYPES = ('feit', 'media')

# Herontwerp stap 3: nuttig en betwisten op een feit (zie app/facts.py).
FACT_DISPUTE_REASONS = ('geen_bron', 'klopt_niet', 'niet_relevant', 'misleidend')
FACT_DISPUTE_STATUSES = ('open', 'resolved')


class FactUsefulVote(db.Model):
    """Eén positief "nuttig"-signaal per gebruiker per feit, aan en uit te
    zetten. Geen omlaag: een waar maar onwelgevallig feit mag niet wegzakken."""
    __tablename__ = "fact_useful_votes"

    id           = db.Column(db.Integer, primary_key=True)
    fact_post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False, index=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('fact_post_id', 'user_id', name='uq_fact_useful_vote'),)


class FactDispute(db.Model):
    """Een feit betwisten met een reden uit een vaste lijst en een optionele
    toelichting. Gaat naar de moderatorweergave; het feit blijft staan."""
    __tablename__ = "fact_disputes"

    id             = db.Column(db.Integer, primary_key=True)
    fact_post_id   = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False, index=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    reason         = db.Column(db.String(16), nullable=False)               # FACT_DISPUTE_REASONS
    note           = db.Column(db.String(500), nullable=True)
    status         = db.Column(db.String(10), nullable=False, default='open')  # open/resolved
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    resolved_at    = db.Column(db.DateTime, nullable=True)

    fact        = db.relationship('Post', foreign_keys=[fact_post_id])
    user        = db.relationship('User', foreign_keys=[user_id])
    resolved_by = db.relationship('User', foreign_keys=[resolved_by_id])


# Forum fase B: eens/oneens per bijdrage, los van elk kwaliteitssignaal.
STANCES = ('eens', 'oneens')


class PostLike(db.Model):
    """Eén eens/oneens-signaal per gebruiker per bijdrage (unieke combinatie
    post_id + user_id). Vroeger een like; oude likes zijn bij de migratie
    'eens' geworden (zie _migrate_stance). Wisselen werkt de rij bij."""
    __tablename__ = "post_likes"

    id         = db.Column(db.Integer, primary_key=True)
    post_id    = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    stance     = db.Column(db.String(10), nullable=True, default='eens')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('post_id', 'user_id', name='uq_post_like'),)


# ── Forum fase C1: kwaliteitssignaal (los van eens/oneens en van stemrecht) ──
QUALITY_CRITERIA = ('K1', 'K2', 'K3')
# 'voorgesteld' komt alleen nog voor in oude rijen van vóór het automatische
# oordeel; _migrate_quality zet die om naar toegekend of afgewezen.
# 'afgewezen' betekent alleen: geen keurmerk. Kwaliteit is uitsluitend een
# beloning; een bijdrage zonder keurmerk blijft gewoon zichtbaar.
QUALITY_STATUSES = ('toegekend', 'afgewezen')


class QualityAward(db.Model):
    """Eén kwaliteitsoordeel per bijdrage. De AI oordeelt automatisch
    (source 'ai'): toegekend met K1/K2 (medaille), anders geen keurmerk.
    Anderen kunnen een toegekend keurmerk melden (QualityReport); een
    moderator kan het keurmerk handmatig toevoegen of weghalen. Staat volledig
    los van eens/oneens (PostLike)."""
    __tablename__ = "quality_awards"

    id               = db.Column(db.Integer, primary_key=True)
    post_id          = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False, unique=True)
    criterion        = db.Column(db.String(2), nullable=True)      # K1/K2/K3, None = geen
    status           = db.Column(db.String(12), nullable=False, default='voorgesteld')
    explanation_nl   = db.Column(db.String(400), nullable=True)
    explanation_en   = db.Column(db.String(400), nullable=True)
    source           = db.Column(db.String(10), nullable=False, default='ai')  # ai/moderator/demo
    post_lang        = db.Column(db.String(5), nullable=True)      # voor C2: percentage per taal
    # Wat de AI vond (alleen bij source 'ai'), voor de moderator en voor C2.
    ai_criterion     = db.Column(db.String(2), nullable=True)
    ai_k3_possible   = db.Column(db.Boolean, nullable=True)
    ai_has_source    = db.Column(db.Boolean, nullable=True)
    ai_source_is_new = db.Column(db.Boolean, nullable=True)
    ai_argument_is_new = db.Column(db.Boolean, nullable=True)
    decided_by_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    decided_at       = db.Column(db.DateTime, nullable=True)
    review_requested_at = db.Column(db.DateTime, nullable=True)    # oud (meekijken), niet meer gebruikt
    # Transparantie: de volledige beoordelingsprompt en het model, zichtbaar
    # op de discussiepagina van dit oordeel.
    prompt_text      = db.Column(db.Text, nullable=True)
    model            = db.Column(db.String(40), nullable=True)
    # Toelichting van de moderator bij een aanpassing. Het oorspronkelijke
    # AI-oordeel (ai_criterion + explanation_*) blijft zo onveranderd in de
    # discussie staan; de huidige stand is criterion/status + deze notitie.
    moderator_note   = db.Column(db.String(400), nullable=True)
    # Fase D: het handhavingsoordeel uit dezelfde AI-aanroep, zoals de AI het
    # gaf (geen/overtreding/twijfel/spam, de regel en de uitleg in de taal van
    # de bijdrage). Wat ermee gebeurde staat in ConductCase.
    conduct_verdict     = db.Column(db.String(12), nullable=True)
    conduct_rule        = db.Column(db.String(12), nullable=True)
    conduct_explanation = db.Column(db.Text, nullable=True)
    # Polijstronde P2: het kwaliteitsmerk van een feit, uit dezelfde AI-
    # aanroep. De AI beoordeelt alleen wat na te gaan is (niet hoe
    # betrouwbaar de bron is): een bron, een vindbare link, waar in de bron,
    # op het onderwerp en een nieuw feit. Het merk staat als alle vijf
    # kloppen. Telt niet als keurmerk (status blijft 'afgewezen').
    fact_mark           = db.Column(db.Boolean, nullable=True)
    fact_has_source     = db.Column(db.Boolean, nullable=True)
    fact_link_findable  = db.Column(db.Boolean, nullable=True)
    fact_has_location   = db.Column(db.Boolean, nullable=True)
    fact_on_topic       = db.Column(db.Boolean, nullable=True)
    fact_is_new         = db.Column(db.Boolean, nullable=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    post       = db.relationship('Post', backref=db.backref('quality_award', uselist=False))
    decided_by = db.relationship('User', foreign_keys=[decided_by_id])

    def explanation(self, lang):
        return (self.explanation_nl if lang == 'nl' else self.explanation_en) or \
               self.explanation_en or self.explanation_nl or ''

    @property
    def text_lang(self):
        """Taal waarin de AI de uitleg schreef (de taal van de bijdrage), of
        None bij handgeschreven tekst (demo, moderator): die wordt niet
        vertaald, net zoals vooraf vertaalde demo-posts."""
        if self.source != 'ai':
            return None
        return self.post_lang if self.post_lang in ('nl', 'en') else 'en'

    def original_explanation(self):
        """De uitleg zoals de AI hem schreef; een vertaling in de andere taal
        is alleen een cache (zelfde aanpak als body_nl/body_en bij posts)."""
        lang = self.text_lang
        if lang is None:
            return self.explanation('nl')
        return (self.explanation_nl if lang == 'nl' else self.explanation_en) or self.explanation(lang)

    def current_explanation(self, lang):
        """De uitleg bij de huidige stand: de notitie van de moderator als die
        het oordeel aanpaste, anders de oorspronkelijke uitleg."""
        return self.moderator_note or self.explanation(lang)

    @property
    def ai_status(self):
        """Wat de AI oorspronkelijk besloot (alleen bij source 'ai')."""
        return 'toegekend' if self.ai_criterion else 'afgewezen'

    @property
    def changed_after_ai(self):
        return self.source == 'ai' and (self.status != self.ai_status or self.criterion != self.ai_criterion)


class QualityReport(db.Model):
    """Melding "geen kwaliteit" op een toegekende bijdrage, met het besluit
    van een moderator (handhaven of intrekken) en wie dat nam."""
    __tablename__ = "quality_reports"

    id            = db.Column(db.Integer, primary_key=True)
    award_id      = db.Column(db.Integer, db.ForeignKey('quality_awards.id'), nullable=False, index=True)
    reporter_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    decision      = db.Column(db.String(12), nullable=True)        # handhaven/intrekken, None = open
    decision_note = db.Column(db.String(400), nullable=True)
    decided_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    decided_at    = db.Column(db.DateTime, nullable=True)

    award      = db.relationship('QualityAward', backref=db.backref('reports', lazy=True))
    reporter   = db.relationship('User', foreign_keys=[reporter_id])
    decided_by = db.relationship('User', foreign_keys=[decided_by_id])


class PostReport(db.Model):
    """Meldknop (fundament): een melding van een bijdrage of feit door een
    deelnemer, met een verplichte reden, voor een moderator. Eén open melding
    per persoon per bijdrage; meerdere mensen kunnen dezelfde bijdrage melden."""
    __tablename__ = "post_reports"

    id            = db.Column(db.Integer, primary_key=True)
    post_id       = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False, index=True)
    reporter_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    reason        = db.Column(db.String(500), nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    decision      = db.Column(db.String(12), nullable=True)        # handhaven/verplaatst/verborgen, None = open
    decision_note = db.Column(db.String(400), nullable=True)
    decided_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    decided_at    = db.Column(db.DateTime, nullable=True)

    post       = db.relationship('Post', foreign_keys=[post_id])
    reporter   = db.relationship('User', foreign_keys=[reporter_id])
    decided_by = db.relationship('User', foreign_keys=[decided_by_id])


class ProposalReport(db.Model):
    """Meldknop: een melding van een voorstel door een deelnemer, met een
    verplichte reden, voor een moderator. Eén open melding per persoon per
    voorstel; meerdere mensen kunnen hetzelfde voorstel melden."""
    __tablename__ = "proposal_reports"

    id            = db.Column(db.Integer, primary_key=True)
    proposal_id   = db.Column(db.Integer, db.ForeignKey('issue_proposals.id'), nullable=False, index=True)
    reporter_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    reason        = db.Column(db.String(500), nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    decision      = db.Column(db.String(12), nullable=True)
    decision_note = db.Column(db.String(400), nullable=True)
    decided_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    decided_at    = db.Column(db.DateTime, nullable=True)

    proposal   = db.relationship('IssueProposal', foreign_keys=[proposal_id])
    reporter   = db.relationship('User', foreign_keys=[reporter_id])
    decided_by = db.relationship('User', foreign_keys=[decided_by_id])


class QualityObjection(db.Model):
    """Bezwaar van de auteur tegen een kwaliteitsoordeel, met de reactie van
    de AI (verdedigt het oordeel of geeft toe) en het besluit van een
    moderator (handhaven, criterium aanpassen of intrekken). Niet meer in
    gebruik sinds kwaliteit alleen een beloning is; de tabel blijft staan
    (geen destructieve migratie) en kan in fase D weer van pas komen."""
    __tablename__ = "quality_objections"

    id              = db.Column(db.Integer, primary_key=True)
    award_id        = db.Column(db.Integer, db.ForeignKey('quality_awards.id'), nullable=False)
    author_id       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    text            = db.Column(db.Text, nullable=False)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    ai_stance       = db.Column(db.String(12), nullable=True)      # verdedigt/geeft_toe
    ai_response_nl  = db.Column(db.Text, nullable=True)
    ai_response_en  = db.Column(db.Text, nullable=True)
    ai_prompt_text  = db.Column(db.Text, nullable=True)
    ai_responded_at = db.Column(db.DateTime, nullable=True)
    decision        = db.Column(db.String(12), nullable=True)      # handhaven/aanpassen/intrekken, None = open
    new_criterion   = db.Column(db.String(2), nullable=True)
    decision_note   = db.Column(db.String(400), nullable=True)
    decided_by_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    decided_at      = db.Column(db.DateTime, nullable=True)

    award      = db.relationship('QualityAward', backref=db.backref('objections', lazy=True,
                                                                     order_by='QualityObjection.created_at'))
    author     = db.relationship('User', foreign_keys=[author_id])
    decided_by = db.relationship('User', foreign_keys=[decided_by_id])


class AppSetting(db.Model):
    """Door de admin instelbare instellingen (sleutel, geheel getal), zoals de
    drempels voor de verdiende status (zie app/status.py)."""
    __tablename__ = "app_settings"

    key        = db.Column(db.String(50), primary_key=True)
    value      = db.Column(db.Integer, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)


class QualityPromotion(db.Model):
    """Promotielog: wanneer een gebruiker een verdiend niveau bereikte. seen_at
    is voor de eenmalige melding (alleen bij voter en hoger)."""
    __tablename__ = "quality_promotions"

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    level       = db.Column(db.String(12), nullable=False)
    promoted_at = db.Column(db.DateTime, default=datetime.utcnow)
    seen_at     = db.Column(db.DateTime, nullable=True)

    # Hoort volledig bij de gebruiker: verwijder je de gebruiker, dan ook zijn log.
    user = db.relationship('User', backref=db.backref('promotions', lazy=True,
                                                      order_by='QualityPromotion.promoted_at',
                                                      cascade='all, delete-orphan'))


# ── Fase D: handhaving van de gedragsregels ──────────────────────────────
# Handhaafbaar uit de tekst: respect ("Over het idee, niet de persoon") en
# "Bij het onderwerp". "Eén account per persoon" is accountbeheer en de
# kwaliteitsrichtlijnen worden niet gehandhaafd.
CONDUCT_RULES = ('respect', 'onderwerp')
# verplaatst: naar de openbare achtergrondpagina, niet meer in het gesprek.
# verborgen: spam of grof misbruik, volledig verborgen, niet op de achtergrond.
# twijfel: blijft staan, gemarkeerd voor een moderator.
# teruggeplaatst / vrijgegeven: weer (of nog steeds) gewoon in het gesprek.
CONDUCT_ACTIONS = ('verplaatst', 'verborgen', 'twijfel', 'teruggeplaatst', 'vrijgegeven')
REMOVING_ACTIONS = ('verplaatst', 'verborgen')
OBJECTION_DAYS = 7


class ConductCase(db.Model):
    """De handhavingsstand van één bijdrage (hooguit één rij per bijdrage).
    Niets wordt verwijderd: een verplaatste bijdrage staat met de regel en de
    uitleg op de achtergrondpagina, met een momentopname van de tekst."""
    __tablename__ = "conduct_cases"

    id               = db.Column(db.Integer, primary_key=True)
    post_id          = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False, unique=True)
    action           = db.Column(db.String(16), nullable=False)
    rule             = db.Column(db.String(12), nullable=True)      # None bij spam/misbruik
    source           = db.Column(db.String(10), nullable=False, default='ai')  # ai/moderator
    explanation      = db.Column(db.Text, nullable=True)
    explanation_lang = db.Column(db.String(5), nullable=True)
    body_snapshot    = db.Column(db.Text, nullable=True)
    on_background    = db.Column(db.Boolean, nullable=False, default=False)
    acted_at         = db.Column(db.DateTime, nullable=True)        # begin van de bezwaartermijn
    acted_by_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # None = AI
    confirmed_by_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    confirmed_at     = db.Column(db.DateTime, nullable=True)
    restored_by_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    restored_at      = db.Column(db.DateTime, nullable=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    post         = db.relationship('Post', backref=db.backref('conduct_case', uselist=False,
                                                              cascade='all, delete-orphan'))
    acted_by     = db.relationship('User', foreign_keys=[acted_by_id])
    confirmed_by = db.relationship('User', foreign_keys=[confirmed_by_id])
    restored_by  = db.relationship('User', foreign_keys=[restored_by_id])

    @property
    def removes_from_thread(self):
        return self.action in REMOVING_ACTIONS

    @property
    def objection_deadline(self):
        return self.acted_at + timedelta(days=OBJECTION_DAYS) if self.acted_at else None

    @property
    def current_objections(self):
        """Bezwaren tegen het huidige besluit (na de laatste verplaatsing of verberging)."""
        return [o for o in self.objections if self.acted_at is None or o.created_at >= self.acted_at]

    @property
    def open_objection(self):
        return next((o for o in self.objections if o.decision is None), None)

    def can_object(self, now=None):
        """De auteur kan binnen zeven dagen één keer bezwaar maken."""
        now = now or datetime.utcnow()
        return (self.removes_from_thread and self.objection_deadline is not None
                and now <= self.objection_deadline and not self.current_objections)

    @property
    def background_status(self):
        """De status op de achtergrondpagina."""
        if self.action == 'teruggeplaatst':
            return 'teruggeplaatst_bezwaar' if any(o.decision == 'terugplaatsen' for o in self.objections) \
                else 'teruggeplaatst'
        if self.confirmed_at is not None:
            return 'bevestigd'
        if self.open_objection is not None:
            return 'bezwaar_open'
        if self.objection_deadline and datetime.utcnow() <= self.objection_deadline:
            return 'bezwaar_mogelijk'
        return 'termijn_verlopen'

    @property
    def acted_ago(self):
        return time_ago(self.acted_at or self.created_at)


class ConductObjection(db.Model):
    """Bezwaar van de auteur tegen een verplaatsing of verberging. Een mens
    beslist (handhaven of terugplaatsen); er is geen AI-reactie."""
    __tablename__ = "conduct_objections"

    id            = db.Column(db.Integer, primary_key=True)
    case_id       = db.Column(db.Integer, db.ForeignKey('conduct_cases.id'), nullable=False, index=True)
    author_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    text          = db.Column(db.Text, nullable=False)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    decision      = db.Column(db.String(14), nullable=True)        # handhaven/terugplaatsen, None = open
    decision_note = db.Column(db.String(400), nullable=True)
    decided_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    decided_at    = db.Column(db.DateTime, nullable=True)

    case       = db.relationship('ConductCase', backref=db.backref(
        'objections', lazy=True, order_by='ConductObjection.created_at', cascade='all, delete-orphan'))
    author     = db.relationship('User', foreign_keys=[author_id])
    decided_by = db.relationship('User', foreign_keys=[decided_by_id])


class Warning(db.Model):
    """A moderator's warning to a user (user moderation, Gebruiker.dc.html).
    by_id is None once the moderator's account is deleted."""
    __tablename__ = "user_warnings"

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    by_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    message    = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id],
                           backref=db.backref('warnings', lazy=True, order_by='Warning.created_at'))
    by   = db.relationship('User', foreign_keys=[by_id])


class Suspension(db.Model):
    """A suspension of a user until a moment (user moderation). The current
    state is on User.suspended_until; these rows are the history."""
    __tablename__ = "user_suspensions"

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    by_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    reason     = db.Column(db.Text, nullable=False)
    until      = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', foreign_keys=[user_id],
                           backref=db.backref('suspensions', lazy=True, order_by='Suspension.created_at'))
    by   = db.relationship('User', foreign_keys=[by_id])


# ── Herontwerp: het vraagstuk (Issue) ────────────────────────────────────
# Een vraagstuk is de container waar voorstellen onder hangen: een vraag met
# een eerste bericht, online of als event, met een planning per fase en de
# stemregels. Bouwstap 1: het model, de backfill (elk bestaand voorstel in een
# eigen vraagstuk) en de basispagina's. De fase-automatiek draait tot stap 6/7
# nog op het voorstel; de fasevelden hier worden gezet en getoond.
ISSUE_KINDS = ('online', 'event')


class Issue(db.Model):
    __tablename__ = "issues"

    id                     = db.Column(db.Integer, primary_key=True)
    title                  = db.Column(db.String(300), nullable=False)   # de vraag
    intro                  = db.Column(db.Text, nullable=True)           # het eerste bericht
    kind                   = db.Column(db.String(10), nullable=False, default='online')  # online/event
    event_location         = db.Column(db.String(200), nullable=True)
    event_date             = db.Column(db.DateTime, nullable=True)
    voting_deadline        = db.Column(db.Date, nullable=True)
    # Planning per fase in dagen.
    days_facts             = db.Column(db.Integer, nullable=False, default=21)
    days_adjust            = db.Column(db.Integer, nullable=False, default=5)
    days_vote              = db.Column(db.Integer, nullable=False, default=7)
    # Stemregels.
    support_threshold      = db.Column(db.Integer, nullable=False, default=5)
    proposal_threshold_pct = db.Column(db.Integer, nullable=False, default=50)
    show_interim           = db.Column(db.Boolean, nullable=False, default=True)
    final_vote             = db.Column(db.Boolean, nullable=False, default=True)
    # Fase: de waarden van ISSUE_PHASES (polijstronde P6: met 'samenvatten'
    # tussen stemmen en vervolg als final_vote aanstaat; zie phases).
    phase                  = db.Column(db.String(12), nullable=False, default='feiten')
    phase_started_at       = db.Column(db.DateTime, nullable=True)
    phase_deadline         = db.Column(db.DateTime, nullable=True)
    created_by_id          = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    created_at             = db.Column(db.DateTime, default=datetime.utcnow)
    lang                   = db.Column(db.String(5), nullable=True)
    is_closed              = db.Column(db.Boolean, nullable=False, default=False)
    # Stap 2: het gesprek met de gedeelde discussie (de middenkolom van de
    # cockpit). Bij een ingepakt voorstel het gesprek van dat voorstel.
    thread_id              = db.Column(db.Integer, db.ForeignKey('threads.id'), nullable=True, index=True)
    # Stap 7: wat er met het vraagstuk gebeurt (de vervolgtekst), door de
    # initiatiefnemer of een moderator, met het moment van de laatste wijziging.
    followup_text          = db.Column(db.Text, nullable=True)
    followup_at            = db.Column(db.DateTime, nullable=True)
    # Polijstronde P4: de planning als momenten (UTC). Online: het begin van
    # elke fase en het einde van het stemmen; NULL bij vraagstukken van
    # vóór P4 (dan geldt de planning in dagen vanaf de aanmaak). Event: een
    # naam, een toelichting, de dagen (IssueEventDay) en of online bijdragen
    # tussen de eerste en de laatste dag openstaan.
    starts_at              = db.Column(db.DateTime, nullable=True)
    adjust_starts_at       = db.Column(db.DateTime, nullable=True)
    vote_starts_at         = db.Column(db.DateTime, nullable=True)
    vote_ends_at           = db.Column(db.DateTime, nullable=True)
    event_name             = db.Column(db.String(200), nullable=True)
    event_note             = db.Column(db.Text, nullable=True)
    event_online_between   = db.Column(db.Boolean, nullable=True)
    # Archived (Toegang.dc.html, delete flow step 1): hidden from the public
    # listings, still reachable by its URL; archiving also closes the issue.
    archived_at            = db.Column(db.DateTime, nullable=True)

    @property
    def is_archived(self):
        return self.archived_at is not None

    created_by = db.relationship('User', foreign_keys=[created_by_id])
    # Met de backref zet ook een ORM-delete van het gesprek thread_id op NULL
    # (de bulkpaden lopen via proposals.delete_for_threads en issues.detach_threads).
    thread     = db.relationship('Thread', foreign_keys=[thread_id], backref=db.backref('issues', lazy=True))

    def days(self, phase):
        return {'feiten': self.days_facts, 'bijstellen': self.days_adjust,
                'stemmen': self.days_vote}.get(phase)

    def plan(self):
        """De geplande (begin, eind) per fase met een doorlooptijd. Met een
        planning in momenten (polijstronde P4) die momenten; anders gerekend
        vanaf de aanmaak: elke fase begint waar de vorige eindigt."""
        if self.starts_at and self.adjust_starts_at and self.vote_starts_at and self.vote_ends_at:
            return {'feiten': (self.starts_at, self.adjust_starts_at),
                    'bijstellen': (self.adjust_starts_at, self.vote_starts_at),
                    'stemmen': (self.vote_starts_at, self.vote_ends_at)}
        windows, moment = {}, self.created_at or datetime.utcnow()
        for phase in PROPOSAL_TIMED_PHASES:
            end = moment + timedelta(days=self.days(phase) or 0)
            windows[phase] = (moment, end)
            moment = end
        return windows

    @property
    def phases(self):
        """De fasen van dit vraagstuk: vier, of vijf met het samenvattende
        voorstel als eigen fase (polijstronde P6)."""
        return ISSUE_PHASES if self.final_vote else PROPOSAL_PHASES

    @property
    def phase_index(self):
        return self.phases.index(self.phase) if self.phase in self.phases else 0


class IssueEventDay(db.Model):
    """Polijstronde P4: een dag van een event, met de tijden (UTC) waarop het
    platform voor bijdragen open is. Standaard één dag."""
    __tablename__ = "issue_event_days"

    id         = db.Column(db.Integer, primary_key=True)
    issue_id   = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=False, index=True)
    starts_at  = db.Column(db.DateTime, nullable=False)
    ends_at    = db.Column(db.DateTime, nullable=False)

    issue = db.relationship('Issue', backref=db.backref('event_days', lazy=True, order_by='IssueEventDay.starts_at',
                                                        cascade='all, delete-orphan'))


class IssueFollow(db.Model):
    """Polijstronde P3: een gebruiker volgt een vraagstuk en krijgt de
    meldingen ervan in de dagelijkse samenvatting, naast de initiatiefnemer
    en wie heeft bijgedragen. Eén keer per gebruiker per vraagstuk."""
    __tablename__ = "issue_follows"

    id         = db.Column(db.Integer, primary_key=True)
    issue_id   = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=False, index=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('issue_id', 'user_id', name='uq_issue_follow'),)


# ── Herontwerp stap 4: voorstellen als onderdeel van een vraagstuk ───────
# Een licht voorstel onder een vraagstuk: een stelling, een toelichting en
# een inbrenger, zonder eigen gesprek (de discussie is die van het
# vraagstuk). Bewust een eigen tabel naast Proposal: dat oude model (een
# voorstel is een gesprek, met de tik, stemrondes en samenvattingen) blijft
# ongemoeid tot het in stap 7 wordt uitgefaseerd. Zie app/issue_proposals.py.

class IssueProposal(db.Model):
    __tablename__ = "issue_proposals"

    id                   = db.Column(db.Integer, primary_key=True)
    issue_id             = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=False, index=True)
    statement            = db.Column(db.String(300), nullable=False)   # één zin
    explanation          = db.Column(db.Text, nullable=True)
    created_by_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    created_at           = db.Column(db.DateTime, default=datetime.utcnow)
    lang                 = db.Column(db.String(5), nullable=True)
    # Bijstellen in de bijstelfase van het vraagstuk (polijstronde P5: vaker
    # dan eens, met de versies in IssueProposalVersion); de oorspronkelijke
    # versie blijft ook hier bewaard (zoals bij Proposal).
    original_statement   = db.Column(db.String(300), nullable=True)
    original_explanation = db.Column(db.Text, nullable=True)
    revised_at           = db.Column(db.DateTime, nullable=True)
    # Stap 6: het samenvattende voorstel van de initiatiefnemer (aan het einde, als
    # final_vote van het vraagstuk aanstaat). NULL telt als False.
    is_final             = db.Column(db.Boolean, nullable=True, default=False)
    # Delete rights step 1: soft-hidden by a moderator (app/issues.hide/unhide).
    # Not yet filtered from the public listings; that is a later step.
    hidden_at            = db.Column(db.DateTime, nullable=True)
    hidden_by_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    issue      = db.relationship('Issue', backref=db.backref('issue_proposals', lazy=True))
    created_by = db.relationship('User', foreign_keys=[created_by_id])

    @property
    def is_hidden(self):
        return self.hidden_at is not None


class IssueProposalVersion(db.Model):
    """Polijstronde P5: een versie van een voorstel. Versie 1 is het
    oorspronkelijke voorstel; elke bijstelling (meerdere keren mogelijk in de
    bijstelfase) voegt een versie toe. Voorstellen van vóór P5 hebben nog
    geen rijen; app/issue_proposals.versions leidt ze dan af uit
    original_statement en de huidige tekst."""
    __tablename__ = "issue_proposal_versions"

    id          = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(db.Integer, db.ForeignKey('issue_proposals.id'), nullable=False, index=True)
    number      = db.Column(db.Integer, nullable=False)
    statement   = db.Column(db.String(300), nullable=False)
    explanation = db.Column(db.Text, nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('proposal_id', 'number', name='uq_issue_proposal_version'),)

    proposal = db.relationship('IssueProposal', backref=db.backref('version_rows', lazy=True,
                                                                   order_by='IssueProposalVersion.number',
                                                                   cascade='all, delete-orphan'))


MODERATION_ACTIONS = ('hide', 'unhide', 'delete_proposal', 'delete_issue')


class ModerationAction(db.Model):
    """Delete rights step 1: audit log of hiding, unhiding and deleting issues
    and proposals. target_id is no foreign key: the target may be gone, so a
    short snapshot of its title or statement is kept."""
    __tablename__ = "moderation_actions"

    id          = db.Column(db.Integer, primary_key=True)
    actor_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    action      = db.Column(db.String(20), nullable=False)        # see MODERATION_ACTIONS
    target_type = db.Column(db.String(20), nullable=False)        # 'issue' | 'proposal'
    target_id   = db.Column(db.Integer, nullable=False, index=True)
    # Step 2: the issue the action belongs to, so the manage screen still finds
    # actions on proposals that are gone. No foreign key: the issue may be gone too.
    issue_id    = db.Column(db.Integer, nullable=True, index=True)
    snapshot    = db.Column(db.String(300), nullable=True)        # title or statement at the time
    created_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)


class IssueProposalRead(db.Model):
    """Discussie stap 3: wanneer een gebruiker een voorstel voor het laatst
    opende (UTC). Nieuwere reacties maken de reactieteller actief."""
    __tablename__ = "issue_proposal_reads"

    id                = db.Column(db.Integer, primary_key=True)
    user_id           = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    issue_proposal_id = db.Column(db.Integer, db.ForeignKey('issue_proposals.id'), nullable=False, index=True)
    last_seen_at      = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'issue_proposal_id', name='uq_issue_proposal_read'),)


class IssueProposalSupport(db.Model):
    """Eén steunbetuiging per gebruiker per voorstel (niet het eigen voorstel)."""
    __tablename__ = "issue_proposal_supports"

    id          = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(db.Integer, db.ForeignKey('issue_proposals.id'), nullable=False, index=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('proposal_id', 'user_id', name='uq_issue_proposal_support'),)

    proposal = db.relationship('IssueProposal', backref=db.backref('supports', lazy=True,
                                                                   cascade='all, delete-orphan'))


class IssueProposalVote(db.Model):
    """Stap 6: één stem per stemgerechtigde per voorstel (voor, tegen,
    onthouding), te wijzigen zolang het stemmen loopt. De uitslag wordt uit
    deze rijen berekend; bij het verwijderen van een account blijft de stem
    staan zonder naam (user_id NULL), zodat de uitslag niet verandert."""
    __tablename__ = "issue_proposal_votes"

    id          = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(db.Integer, db.ForeignKey('issue_proposals.id'), nullable=False, index=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    choice      = db.Column(db.String(12), nullable=False)              # VOTE_CHOICES
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('proposal_id', 'user_id', name='uq_issue_proposal_vote'),)

    proposal = db.relationship('IssueProposal', backref=db.backref('votes', lazy=True, cascade='all, delete-orphan'))


class IssueProposalFact(db.Model):
    """Stap 5: een feit gekoppeld aan een voorstel. Koppelen, niet kopiëren:
    het feit blijft in de gedeelde lijst van het vraagstuk (een Post met kind
    'feit' in het gesprek van het vraagstuk). Eén keer per voorstel en feit."""
    __tablename__ = "issue_proposal_facts"

    id           = db.Column(db.Integer, primary_key=True)
    proposal_id  = db.Column(db.Integer, db.ForeignKey('issue_proposals.id'), nullable=False, index=True)
    fact_post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False, index=True)
    linked_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('proposal_id', 'fact_post_id', name='uq_issue_proposal_fact'),)

    proposal  = db.relationship('IssueProposal', backref=db.backref('fact_links', lazy=True,
                                                                    cascade='all, delete-orphan'))
    fact      = db.relationship('Post', foreign_keys=[fact_post_id])
    linked_by = db.relationship('User', foreign_keys=[linked_by_id])


# ── Fase E: het voorstel als bijzonder soort gesprek ─────────────────────
# Een voorstel hangt één-op-één aan een gesprek (Thread); een gewoon gesprek
# heeft geen Proposal. Vier fasen; in stap 1 staat elk voorstel in de eerste.
PROPOSAL_KINDS = ('publiek', 'event')
PROPOSAL_PHASES = ('feiten', 'bijstellen', 'stemmen', 'vervolg')
# Polijstronde P6: een vraagstuk met final_vote heeft een eigen fase voor het
# samenvattende voorstel (opstellen en de nieuwe stemming) vóór het vervolg.
ISSUE_PHASES = ('feiten', 'bijstellen', 'stemmen', 'samenvatten', 'vervolg')
# Fasen met een doorlooptijd (bij een publiek voorstel).
PROPOSAL_TIMED_PHASES = ('feiten', 'bijstellen', 'stemmen')


class Proposal(db.Model):
    """Een voorstel: de titel van het gesprek is de stelling. Publiek: vaste
    planning uit de starttijd en de doorlooptijden per fase. Event: de host
    (indiener of moderator) stuurt de fasen later handmatig, zonder datums."""
    __tablename__ = "proposals"

    id                 = db.Column(db.Integer, primary_key=True)
    thread_id          = db.Column(db.Integer, db.ForeignKey('threads.id'), nullable=False, unique=True)
    kind               = db.Column(db.String(10), nullable=False)          # publiek/event
    phase              = db.Column(db.String(12), nullable=False, default='feiten')
    # Aangenomen bij meer dan dit percentage voor (later instelbaar).
    threshold_pct      = db.Column(db.Integer, nullable=False, default=50)
    show_live_tally    = db.Column(db.Boolean, nullable=False, default=False)
    host_id            = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    started_at         = db.Column(db.DateTime, nullable=True)
    # Doorlooptijden in minuten en de daaruit berekende vaste datums (alleen publiek).
    feiten_minutes     = db.Column(db.Integer, nullable=True)
    bijstellen_minutes = db.Column(db.Integer, nullable=True)
    stemmen_minutes    = db.Column(db.Integer, nullable=True)
    feiten_start       = db.Column(db.DateTime, nullable=True)
    feiten_end         = db.Column(db.DateTime, nullable=True)
    bijstellen_start   = db.Column(db.DateTime, nullable=True)
    bijstellen_end     = db.Column(db.DateTime, nullable=True)
    stemmen_start      = db.Column(db.DateTime, nullable=True)
    stemmen_end        = db.Column(db.DateTime, nullable=True)
    # Stap 3: wanneer de fase voor het laatst wisselde, en het eenmalige
    # bijstellen van de stelling (de oorspronkelijke tekst blijft bewaard).
    phase_changed_at   = db.Column(db.DateTime, nullable=True)
    revised_at         = db.Column(db.DateTime, nullable=True)
    original_statement = db.Column(db.Text, nullable=True)
    # Stap 4: de uitslag, berekend en vastgelegd bij het sluiten van de
    # stemfase. Percentage voor van de geldige stemmen (voor plus tegen);
    # onthoudingen tellen voor de opkomst, niet voor de uitslag.
    result             = db.Column(db.String(12), nullable=True)   # aangenomen/verworpen
    result_pct         = db.Column(db.Float, nullable=True)        # None bij nul geldige stemmen
    votes_for          = db.Column(db.Integer, nullable=True)
    votes_against      = db.Column(db.Integer, nullable=True)
    votes_abstain      = db.Column(db.Integer, nullable=True)
    eligible_count     = db.Column(db.Integer, nullable=True)      # stemgerechtigden bij het sluiten
    result_at          = db.Column(db.DateTime, nullable=True)
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)
    # Herontwerp stap 1: het vraagstuk waar dit voorstel onder hangt.
    issue_id           = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=True, index=True)

    # lazy='joined': het voorstel komt mee met elke Thread-query (een LEFT
    # JOIN), dus geen extra databaseronde in het overzicht of op de pagina.
    thread = db.relationship('Thread', backref=db.backref('proposal', uselist=False, lazy='joined',
                                                          cascade='all, delete-orphan'))
    host   = db.relationship('User', foreign_keys=[host_id])
    issue  = db.relationship('Issue', backref=db.backref('proposals', lazy=True))

    def window(self, phase):
        """(begin, eind) van een fase; bij een event (None, None). Vervolg
        begint als het stemmen eindigt en heeft geen eind."""
        if phase == 'vervolg':
            return self.stemmen_end, None
        return getattr(self, f'{phase}_start', None), getattr(self, f'{phase}_end', None)

    @property
    def phase_index(self):
        return PROPOSAL_PHASES.index(self.phase) if self.phase in PROPOSAL_PHASES else 0

    @property
    def votes_total(self):
        return (self.votes_for or 0) + (self.votes_against or 0) + (self.votes_abstain or 0)

    @property
    def against_pct(self):
        return None if self.result_pct is None else round(100 - self.result_pct, 1)

    @property
    def turnout_pct(self):
        if not self.eligible_count:
            return None
        return round(100.0 * self.votes_total / self.eligible_count, 1)

    # Fase E, stap 5c: extra publieke stemrondes.
    @property
    def open_round(self):
        return next((r for r in self.rounds if r.closed_at is None), None)

    @property
    def last_closed_round(self):
        closed = [r for r in self.rounds if r.closed_at is not None]
        return closed[-1] if closed else None

    @property
    def is_public(self):
        """Publiek: een publiek voorstel, of een event met een publieke ronde."""
        return self.kind == 'publiek' or bool(self.rounds)


VOTE_CHOICES = ('voor', 'tegen', 'onthouding')


class ProposalVote(db.Model):
    """Eén stem per persoon per voorstel (unieke koppeling). Wijzigen kan
    zolang de stemfase loopt; daarna staat hij vast."""
    __tablename__ = "proposal_votes"

    id          = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(db.Integer, db.ForeignKey('proposals.id'), nullable=False, index=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    choice      = db.Column(db.String(12), nullable=False)              # voor/tegen/onthouding
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('proposal_id', 'user_id', name='uq_proposal_vote'),)

    proposal = db.relationship('Proposal', backref=db.backref('votes', lazy=True, cascade='all, delete-orphan'))


class _ResultMixin:
    """Gedeelde weergave van een uitslag (voorstel of extra ronde)."""

    @property
    def votes_total(self):
        return (self.votes_for or 0) + (self.votes_against or 0) + (self.votes_abstain or 0)

    @property
    def against_pct(self):
        return None if self.result_pct is None else round(100 - self.result_pct, 1)

    @property
    def turnout_pct(self):
        if not self.eligible_count:
            return None
        return round(100.0 * self.votes_total / self.eligible_count, 1)


class ProposalRound(_ResultMixin, db.Model):
    """Fase E, stap 5c: een extra, altijd publieke stemronde na het vervolg,
    op dezelfde (eventueel bijgestelde) stelling. Ronde 1 is de oorspronkelijke
    cyclus (de uitslag staat op Proposal); extra rondes zijn 2, 3, enzovoort,
    elk met een vaste einddatum en een eigen vastgelegde uitslag."""
    __tablename__ = "proposal_rounds"

    id             = db.Column(db.Integer, primary_key=True)
    proposal_id    = db.Column(db.Integer, db.ForeignKey('proposals.id'), nullable=False, index=True)
    number         = db.Column(db.Integer, nullable=False)
    opened_by_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    opened_at      = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    voting_minutes = db.Column(db.Integer, nullable=False)
    voting_end     = db.Column(db.DateTime, nullable=False)
    closed_at      = db.Column(db.DateTime, nullable=True)
    result         = db.Column(db.String(12), nullable=True)
    result_pct     = db.Column(db.Float, nullable=True)
    votes_for      = db.Column(db.Integer, nullable=True)
    votes_against  = db.Column(db.Integer, nullable=True)
    votes_abstain  = db.Column(db.Integer, nullable=True)
    eligible_count = db.Column(db.Integer, nullable=True)
    result_at      = db.Column(db.DateTime, nullable=True)

    __table_args__ = (db.UniqueConstraint('proposal_id', 'number', name='uq_proposal_round'),)

    # selectin: de rondes komen in één extra query mee met de geladen
    # voorstellen (statuslabel in het overzicht), zonder query per voorstel.
    proposal = db.relationship('Proposal', backref=db.backref(
        'rounds', lazy='selectin', order_by='ProposalRound.number', cascade='all, delete-orphan'))


class RoundVote(db.Model):
    """Eén stem per persoon per extra ronde (unieke koppeling ronde, gebruiker)."""
    __tablename__ = "round_votes"

    id         = db.Column(db.Integer, primary_key=True)
    round_id   = db.Column(db.Integer, db.ForeignKey('proposal_rounds.id'), nullable=False, index=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    choice     = db.Column(db.String(12), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('round_id', 'user_id', name='uq_round_vote'),)

    round = db.relationship('ProposalRound', backref=db.backref('votes', lazy=True, cascade='all, delete-orphan'))


# ── Fase E, stap 5b: volgen en de dagelijkse samenvatting ─────────────────

class ProposalFollow(db.Model):
    """Wie een publiek voorstel volgt. Eén rij per persoon per voorstel.
    active False betekent: bewust ontvolgd, dan volgt meedoen niet opnieuw
    automatisch (alleen de knop zet het weer aan)."""
    __tablename__ = "proposal_follows"

    id          = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(db.Integer, db.ForeignKey('proposals.id'), nullable=False, index=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    active      = db.Column(db.Boolean, nullable=False, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('proposal_id', 'user_id', name='uq_proposal_follow'),)

    proposal = db.relationship('Proposal', backref=db.backref('follows', lazy=True, cascade='all, delete-orphan'))


class Digest(db.Model):
    """Eén dagelijkse samenvatting voor één gebruiker: de belangrijke
    veranderingen in zijn gevolgde voorstellen, als JSON (per voorstel de
    titel, de veranderingen en de nieuwe activiteit). In het forum getoond in
    de taal van de lezer; per e-mail in beide talen."""
    __tablename__ = "digests"

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    items_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    emailed_at = db.Column(db.DateTime, nullable=True)

    @property
    def items(self):
        import json
        try:
            return json.loads(self.items_json)
        except ValueError:
            return []


class Notification(db.Model):
    """One in-app notification for one user (notifications step 3a). Reference
    only: the line and the link are built per category and language when
    rendering (app/notifications.notification_line), never stored as text.
    A 'feit' row with dispute_id set is the resolved case; without it, the
    fact was challenged."""
    __tablename__ = "notifications"

    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    category   = db.Column(db.String(16), nullable=False)
    thread_id  = db.Column(db.Integer, db.ForeignKey('threads.id'), nullable=True)
    issue_id   = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=True)
    post_id    = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=True)
    actor_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    dispute_id = db.Column(db.Integer, db.ForeignKey('fact_disputes.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    read_at    = db.Column(db.DateTime, nullable=True)
    # Step 3a-2: a tick-driven notification carries a stable key (e.g.
    # "issue:7:phase:stemmen:<start>"), so the hourly tick creates it once.
    # The instant events leave it NULL.
    event_key  = db.Column(db.String(80), nullable=True)
    # Step 3b: shown in the app (NULL counts as true: the 3a rows were), and
    # when the per-event email went out.
    in_app     = db.Column(db.Boolean, nullable=True)
    emailed_at = db.Column(db.DateTime, nullable=True)
    # Step 4: the system notifications point at the warning or suspension
    # (the line shows its message, or its end date and reason).
    warning_id    = db.Column(db.Integer, db.ForeignKey('user_warnings.id'), nullable=True)
    suspension_id = db.Column(db.Integer, db.ForeignKey('user_suspensions.id'), nullable=True)

    __table_args__ = (db.Index('ix_notifications_user_id_event_key', 'user_id', 'event_key'),)
