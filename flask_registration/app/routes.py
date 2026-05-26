import os
import secrets
import uuid
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, jsonify, abort, current_app
from flask_login import login_user, logout_user, login_required, current_user
from flask_mail import Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from sqlalchemy import func

from app import db, bcrypt, mail
from app.models import User, Thread, Post, PostLike
from app.forms import RegistrationForm, LoginForm, ChangePasswordForm
from app.translations import TRANSLATIONS

main = Blueprint("main", __name__)

_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'static', 'uploads')


@main.before_request
def detect_language():
    if 'lang' not in session:
        best = request.accept_languages.best_match(['nl', 'en'], default='en')
        session['lang'] = 'nl' if best == 'nl' else 'en'


_ALLOWED_EXT = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
_MAX_SIZE = 5 * 1024 * 1024  # 5 MB


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


def _t():
    return TRANSLATIONS[session.get('lang', 'nl')]


@main.route("/lang/<code>")
def set_lang(code):
    if code in ('nl', 'en'):
        session['lang'] = code
    return redirect(request.referrer or url_for('main.index'))


@main.route("/")
def index():
    modal = session.pop('modal', None)
    return render_template("index.html", modal=modal)


@main.route("/manifest")
def manifest():
    return render_template("manifest.html")


@main.route("/about")
def about():
    return render_template("about.html")


@main.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    form = RegistrationForm()
    if form.validate_on_submit():
        existing_user = User.query.filter_by(email=form.email.data.lower()).first()
        if existing_user:
            flash(_t()['flash_email_in_use'], "error")
            return render_template("register.html", form=form)

        password_hash = bcrypt.generate_password_hash(form.password.data).decode("utf-8")
        user = User(
            first_name=form.first_name.data.strip(),
            last_name=form.last_name.data.strip(),
            email=form.email.data.lower().strip(),
            password_hash=password_hash,
        )
        db.session.add(user)
        db.session.commit()
        return redirect(url_for("main.success", name=user.first_name))

    return render_template("register.html", form=form)


@main.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("main.index"))
        flash(_t()['flash_invalid_credentials'], "error")

    return render_template("login.html", form=form)


@main.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.index"))


@main.route("/dashboard")
@login_required
def dashboard():
    return render_template("landing.html")


@main.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    lang = session.get('lang', 'nl')
    linkedin_error = False
    form = ChangePasswordForm()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "linkedin":
            linkedin_url = request.form.get("linkedin_url", "").strip()
            if not linkedin_url.startswith("https://www.linkedin.com/"):
                linkedin_error = True
            else:
                current_user.linkedin_url = linkedin_url
                db.session.commit()
                flash(
                    "LinkedIn profiel opgeslagen." if lang == 'nl' else "LinkedIn profile saved.",
                    "success"
                )
                return redirect(url_for("main.profile"))
        elif form.validate_on_submit():
            if not bcrypt.check_password_hash(current_user.password_hash, form.current_password.data):
                flash(_t()['flash_wrong_password'], "error")
            else:
                current_user.password_hash = bcrypt.generate_password_hash(
                    form.new_password.data
                ).decode("utf-8")
                db.session.commit()
                flash(_t()['flash_password_changed'], "success")
                return redirect(url_for("main.profile"))

    return render_template("profile.html", form=form, linkedin_error=linkedin_error)


@main.route("/success")
def success():
    name = request.args.get("name", "")
    if not name:
        return redirect(url_for("main.register"))
    return render_template("success.html", name=name)


# ── Forum ─────────────────────────────────────────────────────────────────────

def _seed_forum():
    """Create the example thread from Forum.jsx on first run."""
    if Thread.query.first():
        return
    now = datetime.utcnow()
    t = Thread(
        eyebrow_nl='Het idee · scorecard',
        eyebrow_en='The idea · scorecard',
        title_prefix_nl='Hoe houden we de scorecard eerlijk tegen',
        title_accent_nl='landen die op papier goed scoren',
        title_suffix_nl='?',
        title_prefix_en='How do we keep the scorecard honest against',
        title_accent_en='countries that look good on paper',
        title_suffix_en='?',
        created_at=now - timedelta(days=3),
    )
    db.session.add(t)
    db.session.flush()

    NL_OP = (
        "Ik denk vaak na over wat de zwakste plek is in het idee. Als we landen beoordelen "
        "op vijf criteria — corruptie, vrijheid, mensenrechten, duurzaamheid, internationale "
        "rechtsorde — dan hebben we cijfers en lijsten nodig. Die cijfers komen van overheden "
        "zelf, of van internationale organen.\n\n"
        "Een land als China kan op papier prima scoren op corruptiebestrijding (er worden veel "
        "corrupte mensen opgepakt), terwijl het in de praktijk een autoritair regime is. Hoe "
        "houden we de meetlat eerlijk zonder dat we een eindeloze ruzie krijgen over welke bron klopt?"
    )
    EN_OP = (
        "I often think about the weakest point in this idea. If we evaluate countries on five "
        "criteria — corruption, freedom, human rights, sustainability, the international rule of "
        "law — we need numbers and rankings. Those numbers come from governments themselves, or "
        "from international bodies.\n\n"
        "A country like China can look excellent on paper at fighting corruption (plenty of corrupt "
        "people get arrested), while in practice being an authoritarian regime. How do we keep the "
        "measuring stick honest without endless arguments about which source is correct?"
    )

    posts = [
        Post(thread_id=t.id, author_name='Lieke Van Doorn', author_role='founder',
             source_lang='nl', is_op=True, vote_count=0,
             body_nl=NL_OP, body_en=EN_OP,
             created_at=now - timedelta(days=3)),
        Post(thread_id=t.id, author_name='James Whitfield', author_role='member',
             author_age=19, source_lang='en', vote_count=12,
             body=(
                 "Maybe a dumb question, but: good indices already exist — Reporters Without "
                 "Borders, Transparency International, Freedom House. All independent. Why are "
                 "we building our own scoring system instead of combining existing indices? "
                 "Seems faster and more credible."
             ),
             created_at=now - timedelta(days=2, hours=4)),
        Post(thread_id=t.id, author_name='Dr. Aisha Nkrumah', author_role='expert',
             author_badge_nl='Politicoloog · UvA', author_badge_en='Political scientist · UvA',
             source_lang='en', vote_count=47,
             body=(
                 "James' suggestion is pragmatic, but it underestimates one problem: Western "
                 "indices can be dismissed as a colonial lens. A meta-score from multiple "
                 "independent sources only works politically if the composition is representative "
                 "— include Afrobarometer and the Asia Foundation, not just Anglo-American institutes.\n\n"
                 "Otherwise dictatorships will weaponise it and Global Union becomes vulnerable "
                 "to the charge of neocolonialism."
             ),
             created_at=now - timedelta(days=2, hours=2)),
        Post(thread_id=t.id, author_name='Marc Janssen', author_role='member',
             author_age=42, source_lang='nl', vote_count=23,
             body=(
                 "Eens met Aisha. Maar belangrijker: wie corrigeert als een 'goed' land slecht "
                 "begint te scoren? In de EU duurt zoiets jaren — kijk naar Hongarije. Als we "
                 "niet vooraf afspreken dat de score binnen 12 maanden directe tariefgevolgen "
                 "heeft, hebben we hetzelfde vetorecht-probleem met andere woorden."
             ),
             created_at=now - timedelta(days=1)),
        Post(thread_id=t.id, author_name='Lieke Van Doorn', author_role='founder',
             source_lang='nl', is_op=True, vote_count=0,
             body=(
                 "Marc, je punt raakt precies waar het manifest mee begint — vetorecht eruit, "
                 "drempelregel erin. Voor de scorecard moeten we hetzelfde doen: jaarlijkse "
                 "meting, jaarlijkse aanpassing, geen onderhandeling achteraf. Aisha's punt over "
                 "representatieve bronnen pak ik mee in de volgende versie van het essay."
             ),
             created_at=now - timedelta(hours=6)),
    ]
    for p in posts:
        db.session.add(p)
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
        .order_by(func.isnull(last_post_sq.c.last_at), last_post_sq.c.last_at.desc(), Thread.created_at.desc())
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


@main.route('/forum')
def forum():
    _seed_forum()
    return render_template('forum/index.html', threads=_thread_list(closed=False))


@main.route('/forum/gesloten')
@login_required
def forum_gesloten():
    if not (current_user.is_admin or current_user.is_moderator):
        abort(403)
    return render_template('forum/closed.html', threads=_thread_list(closed=True))


@main.route('/forum/<int:thread_id>', methods=['GET', 'POST'])
def forum_thread(thread_id):
    _seed_forum()
    thread = Thread.query.get_or_404(thread_id)
    if request.method == 'POST' and current_user.is_authenticated and not thread.is_closed:
        body      = request.form.get('body', '').strip()
        parent_id = request.form.get('parent_id', type=int)
        image_url = _save_upload(request.files.get('image'))
        anchor    = 'composer'
        if body or image_url:
            post = Post(
                thread_id=thread_id,
                user_id=current_user.id,
                author_name=f'{current_user.first_name} {current_user.last_name}',
                author_role='member',
                body=body or None,
                source_lang=session.get('lang', 'nl'),
                parent_id=parent_id,
                image_url=image_url,
            )
            db.session.add(post)
            db.session.commit()
            anchor = f'post-{post.id}'
        return redirect(url_for('main.forum_thread', thread_id=thread_id) + f'#{anchor}')

    all_posts = Post.query.filter_by(thread_id=thread_id).order_by(Post.created_at).all()
    root_posts  = [p for p in all_posts if p.parent_id is None]
    replies_map = {}
    for p in all_posts:
        if p.parent_id is not None:
            replies_map.setdefault(p.parent_id, []).append(p)

    all_ids = [p.id for p in all_posts]
    like_counts_q = (
        db.session.query(PostLike.post_id, func.count(PostLike.id))
        .filter(PostLike.post_id.in_(all_ids))
        .group_by(PostLike.post_id)
        .all()
    ) if all_ids else []
    like_counts = {pid: cnt for pid, cnt in like_counts_q}

    liked_ids = set()
    if current_user.is_authenticated and all_ids:
        liked_ids = {
            pl.post_id for pl in
            PostLike.query.filter(
                PostLike.post_id.in_(all_ids),
                PostLike.user_id == current_user.id
            ).all()
        }

    return render_template(
        'forum/thread.html',
        thread=thread,
        posts=all_posts,
        root_posts=root_posts,
        replies_map=replies_map,
        like_counts=like_counts,
        liked_ids=liked_ids,
    )


@main.route('/forum/translate/<int:post_id>', methods=['POST'])
def forum_translate(post_id):
    return jsonify({'translation': 'API key nog niet ingesteld'})


@main.route('/forum/like/<int:post_id>', methods=['POST'])
@login_required
def forum_like(post_id):
    post = Post.query.get_or_404(post_id)
    if post.user_id == current_user.id:
        return jsonify({'error': 'cannot like own post'}), 400
    existing = PostLike.query.filter_by(post_id=post_id, user_id=current_user.id).first()
    if existing:
        db.session.delete(existing)
        liked = False
    else:
        db.session.add(PostLike(post_id=post_id, user_id=current_user.id))
        liked = True
    db.session.commit()
    count = PostLike.query.filter_by(post_id=post_id).count()
    return jsonify({'likes': count, 'liked': liked})


@main.route('/forum/<int:thread_id>/close', methods=['POST'])
@login_required
def forum_close(thread_id):
    if not (current_user.is_moderator or current_user.is_admin):
        abort(403)
    thread = Thread.query.get_or_404(thread_id)
    thread.is_closed = True
    db.session.commit()
    return redirect(url_for('main.forum_thread', thread_id=thread_id))


@main.route('/forum/<int:thread_id>/reopen', methods=['POST'])
@login_required
def forum_reopen(thread_id):
    if not (current_user.is_moderator or current_user.is_admin):
        abort(403)
    thread = Thread.query.get_or_404(thread_id)
    thread.is_closed = False
    db.session.commit()
    return redirect(url_for('main.forum_thread', thread_id=thread_id))


@main.route('/forum/nieuw', methods=['GET', 'POST'])
@login_required
def forum_nieuw():
    _seed_forum()
    if request.method == 'POST':
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
                author_name=f'{current_user.first_name} {current_user.last_name}',
                author_role='member',
                body=body or None,
                source_lang=session.get('lang', 'nl'),
                is_op=True,
                image_url=image_url,
            )
            db.session.add(post)
            db.session.commit()
            return redirect(url_for('main.forum_thread', thread_id=thread.id) + '#post-' + str(post.id))
    return render_template('forum/new_thread.html')


@main.route('/forum/<int:thread_id>/bewerken', methods=['GET', 'POST'])
@login_required
def forum_bewerken(thread_id):
    thread = Thread.query.get_or_404(thread_id)
    op = next((p for p in thread.posts if p.is_op), None)
    is_author = op and op.user_id == current_user.id
    if not (is_author or current_user.is_moderator or current_user.is_admin):
        abort(403)
    if request.method == 'POST':
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
        return redirect(url_for('main.forum_thread', thread_id=thread_id))
    op_body = op.body if op else ''
    title = thread.title_prefix_nl or thread.title_prefix_en
    return render_template('forum/edit_thread.html', thread=thread, op_body=op_body, title=title)


@main.route('/forum/post/<int:post_id>/bewerken', methods=['POST'])
@login_required
def forum_post_bewerken(post_id):
    post = Post.query.get_or_404(post_id)
    if not (post.user_id == current_user.id or current_user.is_moderator or current_user.is_admin):
        abort(403)
    body = request.form.get('body', '').strip()
    if body:
        post.body = body
        db.session.commit()
    return jsonify({'body': post.body})


@main.route('/forum/post/<int:post_id>/verwijderen', methods=['POST'])
@login_required
def forum_post_verwijderen(post_id):
    post = Post.query.get_or_404(post_id)
    if not (post.user_id == current_user.id or current_user.is_moderator or current_user.is_admin):
        abort(403)
    if post.is_op and not (current_user.is_moderator or current_user.is_admin):
        abort(403)
    thread_id = post.thread_id
    post.body = '[bericht verwijderd]'
    post.body_nl = None
    post.body_en = None
    post.user_id = None
    db.session.commit()
    return redirect(url_for('main.forum_thread', thread_id=thread_id))


# ── Email verificatie ─────────────────────────────────────────────────────────

def _make_verify_token(email):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return s.dumps(email, salt='email-verify')


def _send_verify_email(user, lang):
    token = _make_verify_token(user.email)
    verify_url = url_for('main.verify_email', token=token, _external=True)
    if lang == 'en':
        subject = "Confirm your email — Global Union Forum"
        body = (
            f"Hi {user.first_name},\n\n"
            f"Please confirm your email address by clicking the link below:\n\n"
            f"{verify_url}\n\n"
            f"This link expires in 24 hours.\n\n"
            f"If you did not create an account, you can ignore this email.\n\n"
            f"Global Union Forum"
        )
    else:
        subject = "Bevestig je e-mailadres — Global Union Forum"
        body = (
            f"Hoi {user.first_name},\n\n"
            f"Bevestig je e-mailadres via de onderstaande link:\n\n"
            f"{verify_url}\n\n"
            f"Deze link verloopt na 24 uur.\n\n"
            f"Als je geen account hebt aangemaakt, kun je deze e-mail negeren.\n\n"
            f"Global Union Forum"
        )
    msg = Message(subject=subject, recipients=[user.email], body=body)
    try:
        print(f"[MAIL] Versturen naar {user.email} via "
              f"{current_app.config.get('MAIL_SERVER')}:{current_app.config.get('MAIL_PORT')} "
              f"als {current_app.config.get('MAIL_USERNAME')}", flush=True)
        mail.send(msg)
        print(f"[MAIL] OK — verstuurd naar {user.email}", flush=True)
    except Exception as e:
        print(f"[MAIL] FOUT bij versturen naar {user.email}: {e}", flush=True)


@main.route("/verify/<token>")
def verify_email(token):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    lang = session.get('lang', 'nl')
    try:
        email = s.loads(token, salt='email-verify', max_age=86400)
    except SignatureExpired:
        flash(
            "Deze verificatielink is verlopen. Vraag een nieuwe aan." if lang == 'nl'
            else "This verification link has expired. Request a new one.",
            "error"
        )
        return redirect(url_for('main.index'))
    except BadSignature:
        flash(
            "Ongeldige verificatielink." if lang == 'nl' else "Invalid verification link.",
            "error"
        )
        return redirect(url_for('main.index'))

    user = User.query.filter_by(email=email).first_or_404()
    if not user.verified:
        user.verified = True
        db.session.commit()
    login_user(user)
    session['modal'] = 'verified'
    return redirect(url_for('main.index'))


@main.route("/verify/resend")
@login_required
def verify_resend():
    lang = session.get('lang', 'nl')
    if current_user.verified:
        return redirect(url_for('main.index'))
    _send_verify_email(current_user, lang)
    flash(
        "Verificatiemail opnieuw verzonden — check je inbox." if lang == 'nl'
        else "Verification email resent — check your inbox.",
        "success"
    )
    return redirect(request.referrer or url_for('main.index'))


# ── Aanmelden ─────────────────────────────────────────────────────────────────

@main.route("/aanmelden", methods=["GET", "POST"])
def aanmelden():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    session.permanent = True

    errors = {}
    form_data = {"first_name": "", "last_name": "", "email": "", "linkedin_url": ""}

    if request.method == "POST":
        form_data["first_name"]   = request.form.get("first_name", "").strip()
        form_data["last_name"]    = request.form.get("last_name", "").strip()
        form_data["email"]        = request.form.get("email", "").strip().lower()
        form_data["linkedin_url"] = request.form.get("linkedin_url", "").strip()
        password         = request.form.get("password", "")
        password_confirm = request.form.get("password_confirm", "")

        if not form_data["first_name"]:
            errors["first_name"] = True
        if not form_data["last_name"]:
            errors["last_name"] = True
        if not form_data["email"] or "@" not in form_data["email"]:
            errors["email"] = True
        elif User.query.filter_by(email=form_data["email"]).first():
            errors["email_in_use"] = True
        if len(password) < 8:
            errors["password"] = True
        elif password != password_confirm:
            errors["password_confirm"] = True
        if not form_data["linkedin_url"].startswith("https://www.linkedin.com/"):
            errors["linkedin_url"] = True

        if not errors:
            password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
            user = User(
                first_name=form_data["first_name"],
                last_name=form_data["last_name"],
                email=form_data["email"],
                password_hash=password_hash,
                linkedin_url=form_data["linkedin_url"],
            )
            db.session.add(user)
            db.session.commit()
            _send_verify_email(user, session.get('lang', 'nl'))
            session['modal'] = 'email_sent'
            return redirect(url_for("main.index"))

    return render_template("aanmelden/aanmelden.html", form_data=form_data, errors=errors)
