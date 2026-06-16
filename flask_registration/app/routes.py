import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta

_log = logging.getLogger('babel_locale')

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, jsonify, abort, current_app
from flask_login import login_user, logout_user, login_required, current_user
from flask_mail import Message
from flask_babel import gettext as _
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from sqlalchemy import func

from app import db, bcrypt, mail, limiter, oauth
from app.models import User, Thread, Post, PostLike
from app.forms import RegistrationForm, LoginForm, ChangePasswordForm

main = Blueprint("main", __name__)

_SPECIAL = re.compile(r'[^A-Za-z0-9]')

def _password_strong(pw):
    return (
        len(pw) >= 8 and
        bool(re.search(r'[A-Z]', pw)) and
        bool(re.search(r'[0-9]', pw)) and
        bool(_SPECIAL.search(pw))
    )

_PW_ERROR = {
    'nl': ('Wachtwoord moet minimaal 8 tekens bevatten, '
           'een hoofdletter, een cijfer en een speciaal teken.'),
    'en': ('Password must contain at least 8 characters, '
           'one uppercase letter, one number and one special character.'),
}

_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'static', 'uploads')


@main.before_request
def enforce_session_timeout():
    if not current_user.is_authenticated:
        return
    last = session.get('_last_active')
    now = datetime.utcnow().timestamp()
    if last and (now - last) > 1800:  # 30 minutes inactivity
        lang = session.get('lang', 'nl')
        logout_user()
        session.clear()
        session['lang'] = lang
        flash(
            'Je sessie is verlopen. Log opnieuw in.' if lang == 'nl'
            else 'Your session has expired. Please log in again.',
            'error'
        )
        return redirect(url_for('main.login'))
    session['_last_active'] = now


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


@main.route("/lang/<code>")
def set_lang(code):
    if code in ('nl', 'en'):
        session['lang'] = code
        session.permanent = True
        _log.debug('set_lang → code=%s | session_id keys=%s | SECRET_KEY_set=%s',
                   code, list(session.keys()),
                   bool(current_app.config.get('SECRET_KEY') != 'change-this-in-production'))
        if current_user.is_authenticated:
            current_user.auto_translate = True if code == 'en' else None
            db.session.commit()
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


@main.route("/register")
def register():
    return redirect(url_for("main.aanmelden"))


@main.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    form = LoginForm()
    login_error = False
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.lower().strip()).first()
        if user and bcrypt.check_password_hash(user.password_hash, form.password.data):
            session.permanent = True
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("main.index"))
        login_error = True

    return render_template("login.html", form=form, login_error=login_error)


# ── Wachtwoord vergeten ───────────────────────────────────────────────────────

def _make_reset_token(email):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return s.dumps(email, salt='password-reset')


def _verify_reset_token(token):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        return s.loads(token, salt='password-reset', max_age=3600)
    except (SignatureExpired, BadSignature):
        return None


def _send_reset_email(user, reset_url, lang):
    if lang == 'en':
        subject = "Reset your password — Global Union Forum"
        body = (
            f"Hi {user.first_name},\n\n"
            f"You requested a password reset. Click the link below to choose a new password:\n\n"
            f"{reset_url}\n\n"
            f"This link expires in 1 hour.\n\n"
            f"If you did not request this, you can safely ignore this email.\n\n"
            f"Global Union Forum"
        )
    else:
        subject = "Wachtwoord opnieuw instellen — Global Union Forum"
        body = (
            f"Hoi {user.first_name},\n\n"
            f"Je hebt een wachtwoordreset aangevraagd. Klik op de link hieronder om een nieuw wachtwoord in te stellen:\n\n"
            f"{reset_url}\n\n"
            f"Deze link verloopt na 1 uur.\n\n"
            f"Als jij dit niet hebt aangevraagd, kun je deze e-mail negeren.\n\n"
            f"Global Union Forum"
        )
    msg = Message(subject=subject, recipients=[user.email], body=body)
    try:
        mail.send(msg)
        print(f"[MAIL] Reset email verstuurd naar {user.email}", flush=True)
    except Exception as e:
        print(f"[MAIL] FOUT reset email naar {user.email}: {e}", flush=True)


@main.route('/wachtwoord-vergeten', methods=['GET', 'POST'])
@limiter.limit("3 per hour", methods=["POST"])
def wachtwoord_vergeten():
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    lang = session.get('lang', 'nl')
    sent = False
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            reset_url = url_for('main.wachtwoord_reset', token=_make_reset_token(email), _external=True)
            _send_reset_email(user, reset_url, lang)
        sent = True  # altijd tonen — voorkomt e-mailenumeratie
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'ok': True})
    return render_template('wachtwoord_vergeten.html', sent=sent)


@main.route('/wachtwoord-reset/<token>', methods=['GET', 'POST'])
def wachtwoord_reset(token):
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    lang = session.get('lang', 'nl')
    email = _verify_reset_token(token)
    if not email:
        return render_template('wachtwoord_reset.html', token_invalid=True, token=token)
    user = User.query.filter_by(email=email).first_or_404()
    error = None
    if request.method == 'POST':
        pw  = request.form.get('password', '')
        pw2 = request.form.get('password_confirm', '')
        if not _password_strong(pw):
            error = _PW_ERROR[lang]
        elif pw != pw2:
            error = ('Wachtwoorden komen niet overeen.' if lang == 'nl'
                     else 'Passwords do not match.')
        else:
            user.password_hash = bcrypt.generate_password_hash(pw).decode('utf-8')
            db.session.commit()
            session.permanent = True
            login_user(user)
            flash(
                'Wachtwoord gewijzigd. Je bent nu ingelogd.' if lang == 'nl'
                else 'Password changed. You are now logged in.',
                'success'
            )
            return redirect(url_for('main.index'))
    return render_template('wachtwoord_reset.html', token_invalid=False, token=token, error=error)


@main.route('/auth/google')
def auth_google():
    redirect_uri = url_for('main.auth_google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@main.route('/auth/google/callback')
def auth_google_callback():
    lang = session.get('lang', 'nl')
    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        flash(
            'Inloggen met Google mislukt. Probeer het opnieuw.' if lang == 'nl'
            else 'Google sign-in failed. Please try again.',
            'error'
        )
        return redirect(url_for('main.login'))

    userinfo = token.get('userinfo')
    if not userinfo:
        flash(
            'Geen gebruikersgegevens ontvangen van Google.' if lang == 'nl'
            else 'No user information received from Google.',
            'error'
        )
        return redirect(url_for('main.login'))

    email = userinfo.get('email', '').lower().strip()
    google_id = userinfo.get('sub', '')
    first_name = userinfo.get('given_name', '')
    last_name = userinfo.get('family_name', '')

    if not email:
        flash(
            'Geen e-mailadres ontvangen van Google.' if lang == 'nl'
            else 'No email address received from Google.',
            'error'
        )
        return redirect(url_for('main.login'))

    user = User.query.filter_by(email=email).first()
    if user:
        if not user.google_id:
            user.google_id = google_id
            db.session.commit()
        session.permanent = True
        login_user(user)
        return redirect(url_for('main.index'))

    pw_hash = bcrypt.generate_password_hash(secrets.token_hex(32)).decode('utf-8')
    reg_lang = lang
    user = User(
        first_name=first_name or email.split('@')[0],
        last_name=last_name or '',
        email=email,
        password_hash=pw_hash,
        google_id=google_id,
        verified=True,
        auto_translate=True if reg_lang == 'en' else None,
    )
    db.session.add(user)
    db.session.commit()
    session.permanent = True
    login_user(user)
    return redirect(url_for('main.linkedin_aanvullen'))


@main.route('/profiel/linkedin-aanvullen', methods=['GET', 'POST'])
@login_required
def linkedin_aanvullen():
    if current_user.linkedin_url:
        return redirect(url_for('main.index'))
    lang = session.get('lang', 'nl')
    error = False
    if request.method == 'POST':
        linkedin_url = request.form.get('linkedin_url', '').strip()
        if not linkedin_url.startswith('https://www.linkedin.com/'):
            error = True
        else:
            current_user.linkedin_url = linkedin_url
            db.session.commit()
            flash(
                'Welkom bij Global Union Forum!' if lang == 'nl'
                else 'Welcome to Global Union Forum!',
                'success'
            )
            return redirect(url_for('main.index'))
    return render_template('linkedin_aanvullen.html', error=error)


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
                flash(_('Current password is incorrect.'), "error")
            elif not _password_strong(form.new_password.data):
                flash(_PW_ERROR[lang], "error")
            else:
                current_user.password_hash = bcrypt.generate_password_hash(
                    form.new_password.data
                ).decode("utf-8")
                db.session.commit()
                flash(_('Password changed successfully.'), "success")
                return redirect(url_for("main.profile"))

    return render_template("profile.html", form=form, linkedin_error=linkedin_error)


@main.route("/profiel/vertaalvoorkeur", methods=["POST"])
@login_required
def profiel_vertaalvoorkeur():
    lang = session.get('lang', 'nl')
    current_user.auto_translate = request.form.get("auto_translate") == "1"
    db.session.commit()
    next_url = request.form.get('next') or url_for("main.profile")
    return redirect(next_url)


@main.route("/privacy")
def privacy():
    return render_template("privacy.html")


# ── Admin dashboard ───────────────────────────────────────────────────────────

@main.route('/admin')
@login_required
def admin_dashboard():
    if not current_user.is_admin:
        abort(403)

    week_ago      = datetime.utcnow() - timedelta(days=7)
    total_users   = User.query.count()
    new_this_week = User.query.filter(User.created_at >= week_ago).count()

    active_threads = Thread.query.filter_by(is_closed=False).count()
    closed_threads = Thread.query.filter_by(is_closed=True).count()
    total_posts    = Post.query.count()
    demo_posts     = Post.query.filter_by(is_demo=True).count()

    users = User.query.order_by(User.created_at.desc()).all()

    return render_template(
        'admin/dashboard.html',
        total_users=total_users,
        new_this_week=new_this_week,
        active_threads=active_threads,
        closed_threads=closed_threads,
        total_posts=total_posts,
        demo_posts=demo_posts,
        users=users,
    )


@main.route('/moderator')
@login_required
def moderator_dashboard():
    if not (current_user.is_admin or current_user.is_moderator):
        abort(403)

    active_threads = Thread.query.filter_by(is_closed=False).count()

    today = datetime.utcnow().date()
    start_of_day = datetime(today.year, today.month, today.day)
    end_of_day = start_of_day + timedelta(days=1)
    posts_today = Post.query.filter(
        Post.created_at >= start_of_day,
        Post.created_at < end_of_day,
    ).count()

    gerhard = User.query.filter_by(email='top.gerhard@gmail.com').first()
    if gerhard:
        replied_thread_ids = db.session.query(Post.thread_id).filter_by(
            user_id=gerhard.id
        ).distinct()
        threads_no_gerhard = (
            Thread.query.filter_by(is_closed=False)
            .filter(~Thread.id.in_(replied_thread_ids.scalar_subquery()))
            .order_by(Thread.created_at.desc())
            .all()
        )
    else:
        threads_no_gerhard = Thread.query.filter_by(is_closed=False).order_by(Thread.created_at.desc()).all()

    recent_posts = (
        Post.query
        .filter(Post.body != '[bericht verwijderd]')
        .order_by(Post.created_at.desc())
        .limit(10)
        .all()
    )

    closed_threads = Thread.query.filter_by(is_closed=True).order_by(Thread.created_at.desc()).all()

    return render_template(
        'moderator/dashboard.html',
        active_threads=active_threads,
        posts_today=posts_today,
        threads_no_gerhard_count=len(threads_no_gerhard),
        threads_no_gerhard=threads_no_gerhard,
        recent_posts=recent_posts,
        closed_threads=closed_threads,
    )


@main.route('/admin/gebruiker/<int:user_id>/rol', methods=['POST'])
@login_required
def admin_set_rol(user_id):
    if not current_user.is_admin:
        abort(403)
    if user_id == current_user.id:
        return jsonify({'error': 'cannot change own role'}), 400
    user = User.query.get_or_404(user_id)
    role = request.form.get('role', '')
    if role == 'admin':
        user.is_admin = True
        user.is_moderator = True
    elif role == 'moderator':
        user.is_admin = False
        user.is_moderator = True
    elif role == 'gebruiker':
        user.is_admin = False
        user.is_moderator = False
    else:
        return jsonify({'error': 'invalid role'}), 400
    db.session.commit()
    return jsonify({'ok': True, 'role': role})


@main.route('/admin/gebruiker/<int:user_id>/verwijder', methods=['POST'])
@login_required
def admin_verwijder_gebruiker(user_id):
    if not current_user.is_admin:
        abort(403)
    user = User.query.get_or_404(user_id)
    if user.is_admin:
        abort(400)

    uid = user.id
    PostLike.query.filter_by(user_id=uid).delete()
    user_post_ids = [
        p.id for p in Post.query.filter_by(user_id=uid).with_entities(Post.id).all()
    ]
    if user_post_ids:
        PostLike.query.filter(
            PostLike.post_id.in_(user_post_ids)
        ).delete(synchronize_session=False)
        Post.query.filter(Post.parent_id.in_(user_post_ids)).update(
            {Post.parent_id: None}, synchronize_session=False
        )
        Post.query.filter_by(user_id=uid).delete(synchronize_session=False)

    db.session.delete(user)
    db.session.commit()
    return redirect(url_for('main.admin_dashboard'))


@main.route("/account/verwijderen", methods=["POST"])
@login_required
def account_verwijderen():
    lang = session.get('lang', 'nl')
    if current_user.is_admin:
        flash(
            "Adminaccounts kunnen niet worden verwijderd." if lang == 'nl'
            else "Admin accounts cannot be deleted.",
            "error"
        )
        return redirect(url_for("main.profile"))

    user_id = current_user.id

    # Delete likes given by user and likes on user's posts
    PostLike.query.filter_by(user_id=user_id).delete()
    user_post_ids = [p.id for p in Post.query.filter_by(user_id=user_id).with_entities(Post.id).all()]
    if user_post_ids:
        PostLike.query.filter(PostLike.post_id.in_(user_post_ids)).delete(synchronize_session=False)
        # Detach replies to user's posts so they survive
        Post.query.filter(Post.parent_id.in_(user_post_ids)).update(
            {Post.parent_id: None}, synchronize_session=False
        )
        Post.query.filter_by(user_id=user_id).delete(synchronize_session=False)

    user = User.query.get(user_id)
    logout_user()
    db.session.delete(user)
    db.session.commit()

    flash(
        "Je account is verwijderd." if lang == 'nl' else "Your account has been deleted.",
        "success"
    )
    return redirect(url_for("main.index"))


@main.route("/uitnodiging", methods=["POST"])
def uitnodiging():
    lang = request.form.get('lang', session.get('lang', 'nl'))
    invite_email = request.form.get('invite_email', '').strip()
    invite_message = request.form.get('invite_message', '').strip()

    if not invite_email:
        flash(
            "Vul een e-mailadres in." if lang == 'nl' else "Please enter an email address.",
            "error"
        )
        return redirect(url_for("main.index"))

    register_url = url_for('main.aanmelden', _external=True)
    if current_user.is_authenticated:
        sender_name = f"{current_user.first_name} {current_user.last_name}"
    else:
        sender_name = "Gerhard Top"

    if lang == 'en':
        subject = "Invitation: join the Global Union Forum conversation"
        body = (
            f"Hi,\n\n"
            f"{sender_name} invites you to join Global Union Forum — a think tank exploring "
            f"whether the success of the European Union can be replicated worldwide.\n\n"
        )
        if invite_message:
            body += f'Personal message from {sender_name}:\n"{invite_message}"\n\n'
        body += f"Create a free account here:\n{register_url}\n\nGlobal Union Forum"
    else:
        subject = "Uitnodiging: doe mee aan het Global Union Forum gesprek"
        body = (
            f"Hoi,\n\n"
            f"{sender_name} nodigt je uit om mee te denken bij Global Union Forum — een denktank "
            f"die onderzoekt of het succes van de Europese Unie wereldwijd herhaald kan worden.\n\n"
        )
        if invite_message:
            body += f'Persoonlijk bericht van {sender_name}:\n"{invite_message}"\n\n'
        body += f"Maak gratis een account aan:\n{register_url}\n\nGlobal Union Forum"

    msg = Message(subject=subject, recipients=[invite_email], body=body)
    try:
        mail.send(msg)
        flash(
            "Uitnodiging verstuurd!" if lang == 'nl' else "Invitation sent!",
            "success"
        )
    except Exception as e:
        print(f"[MAIL] FOUT uitnodiging naar {invite_email}: {e}", flush=True)
        flash(
            "Er is iets misgegaan. Probeer het later opnieuw."
            if lang == 'nl' else "Something went wrong. Please try again later.",
            "error"
        )
    return redirect(url_for("main.index"))


@main.route("/success")
def success():
    name = request.args.get("name", "")
    if not name:
        return redirect(url_for("main.register"))
    return render_template("success.html", name=name)


# ── Forum ─────────────────────────────────────────────────────────────────────

def _get_or_create_demo_user(first_name, last_name, email):
    user = User.query.filter_by(email=email).first()
    if not user:
        pw = bcrypt.generate_password_hash(secrets.token_hex(24)).decode('utf-8')
        user = User(
            first_name=first_name,
            last_name=last_name,
            email=email,
            password_hash=pw,
            verified=True,
            linkedin_url=None,
        )
        db.session.add(user)
        db.session.flush()
    return user


def _seed_forum():
    """Seed 4 demo threads with demo users on first run."""
    if Thread.query.filter_by(is_demo=True).first():
        return

    # Remove old non-demo seed thread if it exists
    old = Thread.query.filter_by(eyebrow_nl='Het idee · scorecard').first()
    if old:
        PostLike.query.filter(PostLike.post_id.in_(
            db.session.query(Post.id).filter_by(thread_id=old.id)
        )).delete(synchronize_session=False)
        Post.query.filter_by(thread_id=old.id).delete()
        db.session.delete(old)
        db.session.flush()

    now = datetime.utcnow()

    jan     = _get_or_create_demo_user('Jan',    'Hofman (demo)',       'jan.hofman.demo@globalunionforum.org')
    fatima  = _get_or_create_demo_user('Fatima', 'El Idrissi (demo)',   'fatima.elidrissi.demo@globalunionforum.org')
    sarah   = _get_or_create_demo_user('Sarah',  'Chen (demo)',         'sarah.chen.demo@globalunionforum.org')
    marcus  = _get_or_create_demo_user('Marcus', 'Osei (demo)',         'marcus.osei.demo@globalunionforum.org')

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
        author_name='Jan Hofman (demo)', author_role='member', author_age=52,
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
        author_name='Sarah Chen (demo)', author_role='expert', author_age=34,
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
        author_name='Marcus Osei (demo)', author_role='member', author_age=31,
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

    # ── Thread 2: Handelzone zonder personenverkeer ───────────────────
    t2 = Thread(
        eyebrow_nl='Het idee · principe 4',
        eyebrow_en='The idea · principle 4',
        title_prefix_nl='Één handelszone zonder vrij personenverkeer —',
        title_accent_nl='is dat houdbaar',
        title_suffix_nl='?',
        title_prefix_en='One trade zone without free movement —',
        title_accent_en='is that sustainable',
        title_suffix_en='?',
        is_demo=True,
        created_at=now - timedelta(days=4),
    )
    db.session.add(t2)
    db.session.flush()

    p2_op = Post(
        thread_id=t2.id, user_id=fatima.id,
        author_name='Fatima El Idrissi (demo)', author_role='expert', author_age=38,
        author_badge_nl='Handelsrecht · Universiteit Leiden',
        author_badge_en='Trade law · Leiden University',
        source_lang='nl', is_op=True, vote_count=0, is_demo=True,
        body_nl=(
            "Het manifest onderscheidt handelszone en personenverkeer bewust. Ik begrijp de "
            "politieke logica: vrij personenverkeer is het struikelblok dat de EU in veel "
            "discussies onverkoopbaar maakt. Door dat los te koppelen maak je toetreding "
            "politiek aantrekkelijker.\n\n"
            "Maar er zijn twee problemen die ik niet opgelost zie.\n\n"
            "Ten eerste: hoe voorkom je een brain drain? Als goederen vrij mogen bewegen maar "
            "mensen niet, worden hooggekwalificeerde mensen in armere lidstaten extra kwetsbaar "
            "— bedrijven vertrekken, banen volgen.\n\n"
            "Ten tweede: mensenrechten omvatten ook het recht om te reizen. Als Global Union "
            "ernst maakt met mensenrechten als toetredingscriterium, hoe verdedig je dan een "
            "systeem dat bewust mobiliteit beperkt?"
        ),
        body_en=(
            "The manifesto deliberately separates trade zone from freedom of movement. I "
            "understand the political logic: free movement is the stumbling block that makes "
            "EU accession politically toxic in many debates. By decoupling it, accession "
            "becomes more attractive.\n\n"
            "But there are two problems I don't see resolved.\n\n"
            "First: how do you prevent brain drain? If goods can move freely but people "
            "cannot, highly skilled workers in poorer member states become especially "
            "vulnerable — companies leave, jobs follow.\n\n"
            "Second: human rights include the right to travel. If Global Union takes human "
            "rights seriously as an accession criterion, how do you defend a system that "
            "deliberately restricts mobility?"
        ),
        created_at=now - timedelta(days=4),
    )
    db.session.add(p2_op)
    db.session.flush()

    p2_r1 = Post(
        thread_id=t2.id, user_id=jan.id, parent_id=p2_op.id,
        author_name='Jan Hofman (demo)', author_role='member', author_age=52,
        source_lang='nl', vote_count=14, is_demo=True,
        body=(
            "Fatima's brain drain argument raakt een echte spanning. Maar ik denk dat ze de "
            "doelstelling omkeert: Global Union is niet bedoeld als migratiesysteem, maar als "
            "een economische en politieke drukval.\n\n"
            "Het gaat erom dat landen die meedoen intern welvarender worden — zodat er minder "
            "reden is voor brain drain. Als het werkt, hoef je personenverkeer helemaal niet "
            "apart te regelen.\n\n"
            "Het mensenrechtenargument snap ik. Maar reisrecht is al een apart internationaal "
            "regime (UDHR art. 13). Global Union hoeft dat niet opnieuw te regelen — alleen "
            "niet actief te belemmeren."
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
        author_name='Sarah Chen (demo)', author_role='expert', author_age=34,
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
        author_name='Jan Hofman (demo)', author_role='member', author_age=52,
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
        author_name='Fatima El Idrissi (demo)', author_role='expert', author_age=38,
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
        author_name='Marcus Osei (demo)', author_role='member', author_age=31,
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
        author_name='Fatima El Idrissi (demo)', author_role='expert', author_age=38,
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
        author_name='Jan Hofman (demo)', author_role='member', author_age=52,
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

    user_lang = session.get('lang', 'nl')
    auto_translate = current_user.is_authenticated and (
        current_user.auto_translate is True
        or (current_user.auto_translate is None and user_lang == 'en')
    )
    return render_template(
        'forum/thread.html',
        thread=thread,
        posts=all_posts,
        root_posts=root_posts,
        replies_map=replies_map,
        like_counts=like_counts,
        liked_ids=liked_ids,
        auto_translate=auto_translate,
    )


@main.route('/forum/translate/<int:post_id>', methods=['POST'])
def forum_translate(post_id):
    post = Post.query.get_or_404(post_id)
    user_lang = session.get('lang', 'nl')

    # source_lang is de taal van de originele tekst; user_lang is de gewenste taal.
    # Vertaling is alleen zinvol als die twee verschillen.
    if user_lang == post.source_lang:
        return jsonify({'error': 'no translation needed'}), 400

    if user_lang == 'nl':
        # Gebruiker wil Nederlands — origineel is Engels
        if post.body_nl:
            return jsonify({'translation': post.body_nl})
        source = post.body_en or post.body or ''
        target_lang = 'Dutch'
    else:
        # Gebruiker wil Engels — origineel is Nederlands
        if post.body_en:
            return jsonify({'translation': post.body_en})
        source = post.body_nl or post.body or ''
        target_lang = 'English'

    if not source.strip():
        return jsonify({'error': 'no content'}), 400

    api_key = current_app.config.get('ANTHROPIC_API_KEY', '')
    if not api_key:
        return jsonify({'error': 'translation unavailable'}), 503

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=1024,
            messages=[{
                'role': 'user',
                'content': (
                    f'Translate the following forum post to {target_lang}. '
                    'Preserve paragraph breaks. Return only the translation, no preamble.\n\n'
                    f'{source}'
                ),
            }],
        )
        translation = message.content[0].text.strip()

        if user_lang == 'nl':
            post.body_nl = translation
        else:
            post.body_en = translation
        db.session.commit()

        return jsonify({'translation': translation})
    except Exception as e:
        current_app.logger.error('Translation error: %s', e)
        return jsonify({'error': 'translation failed'}), 500


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
@limiter.limit("3 per hour", methods=["POST"])
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
        if not _password_strong(password):
            errors["password"] = True
        elif password != password_confirm:
            errors["password_confirm"] = True
        if not form_data["linkedin_url"].startswith("https://www.linkedin.com/"):
            errors["linkedin_url"] = True

        if not errors:
            password_hash = bcrypt.generate_password_hash(password).decode("utf-8")
            reg_lang = session.get('lang', 'nl')
            user = User(
                first_name=form_data["first_name"],
                last_name=form_data["last_name"],
                email=form_data["email"],
                password_hash=password_hash,
                linkedin_url=form_data["linkedin_url"],
                auto_translate=True if reg_lang == 'en' else None,
            )
            db.session.add(user)
            db.session.commit()
            _send_verify_email(user, session.get('lang', 'nl'))
            session['modal'] = 'email_sent'
            return redirect(url_for("main.index"))

    return render_template("aanmelden/aanmelden.html", form_data=form_data, errors=errors)
