from datetime import datetime
from flask_login import AnonymousUserMixin, UserMixin
from app import db, login_manager

# Contentrollen (forum fase A), van laag naar hoog. "Reader" is geen rol maar
# simpelweg niet ingelogd, of ingelogd zonder akkoord op de spelregels
# (role is dan NULL). Admin (is_admin) staat los hiervan en boven alles.
# Voter is gedefinieerd maar dwingt in deze fase nog niets extra's af.
ROLES = ('contributor', 'voter', 'moderator', 'senior_moderator')
_MODERATING_ROLES = ('moderator', 'senior_moderator')


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

    def __repr__(self):
        return f"<User {self.email}>"

    # ── Permissies (centrale helpers, gebruikt door routes en templates) ──
    def _rank(self):
        return ROLES.index(self.role) + 1 if self.role in ROLES else 0

    def can_contribute(self):
        """Posten, reageren, een draad starten."""
        return bool(self.is_admin) or (self.rules_accepted_at is not None and self._rank() >= 1)

    def can_vote(self):
        """Voor latere fasen (stemmen); nu nog nergens afgedwongen."""
        return bool(self.is_admin) or (self.rules_accepted_at is not None and self._rank() >= 2)

    def can_moderate(self):
        """Draden sluiten/heropenen, andermans berichten bewerken/verwijderen.
        is_moderator telt ook mee als vangnet, zodat wie vóór fase A
        moderator was dat altijd blijft (set_role houdt beide synchroon)."""
        return bool(self.is_admin) or bool(self.is_moderator) or self._rank() >= ROLES.index('moderator') + 1

    def can_manage_rules(self):
        return bool(self.is_admin) or self.role == 'senior_moderator'

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
        """Hoe deze gebruiker publiek verschijnt: echte naam of @pseudoniem."""
        return self.real_name if self.shows_real_name else '@' + self.username

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
    return db.session.get(User, int(user_id))


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
    thread_id      = db.Column(db.Integer, db.ForeignKey('threads.id'), nullable=False)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    parent_id      = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=True)
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
        delta = datetime.utcnow() - self.created_at
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
QUALITY_STATUSES = ('toegekend', 'afgewezen')


class QualityAward(db.Model):
    """Eén kwaliteitsoordeel per bijdrage. De AI oordeelt automatisch
    (source 'ai'): toegekend met K1/K2, of afgewezen met een reden. De auteur
    kan bezwaar maken (QualityObjection), anderen kunnen melden
    (QualityReport); een moderator kan het oordeel aanpassen. Staat volledig
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
    award_id      = db.Column(db.Integer, db.ForeignKey('quality_awards.id'), nullable=False)
    reporter_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    decision      = db.Column(db.String(12), nullable=True)        # handhaven/intrekken, None = open
    decision_note = db.Column(db.String(400), nullable=True)
    decided_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    decided_at    = db.Column(db.DateTime, nullable=True)

    award      = db.relationship('QualityAward', backref=db.backref('reports', lazy=True))
    reporter   = db.relationship('User', foreign_keys=[reporter_id])
    decided_by = db.relationship('User', foreign_keys=[decided_by_id])


class QualityObjection(db.Model):
    """Bezwaar van de auteur tegen een kwaliteitsoordeel, met de reactie van
    de AI (verdedigt het oordeel of geeft toe) en het besluit van een
    moderator (handhaven, criterium aanpassen of intrekken). Samen met het
    oordeel vormt dit de thread op de discussiepagina."""
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

    def ai_response(self, lang):
        return (self.ai_response_nl if lang == 'nl' else self.ai_response_en) or \
               self.ai_response_en or self.ai_response_nl or ''

    @property
    def text_lang(self):
        """Taal waarin de AI reageerde: die van de bijdrage (zie quality.target_lang)."""
        lang = self.award.post.source_lang
        return lang if lang in ('nl', 'en') else 'en'

    def original_response(self):
        """De reactie zoals de AI hem schreef; de andere taal is alleen cache."""
        lang = self.text_lang
        return (self.ai_response_nl if lang == 'nl' else self.ai_response_en) or self.ai_response(lang)
