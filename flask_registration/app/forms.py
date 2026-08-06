from flask_wtf import FlaskForm
from flask_babel import gettext as _
from wtforms import StringField, PasswordField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Email, Length, Optional, ValidationError
from urllib.parse import urlparse

from app.utils import _password_strong


def _validate_password_strong(form, field):
    if not _password_strong(field.data or ""):
        raise ValidationError(_(
            'Password must be between 8 and 72 characters and contain '
            'one uppercase letter, one number and one special character.'
        ))


def _validate_password_match(base_field_name):
    """
    Fabrieksfunctie: levert een validator die controleert of dit veld gelijk
    is aan form.<base_field_name>.data. Nodig omdat formulieren verschillende
    namen gebruiken voor het eerste wachtwoordveld ('password' bij
    WachtwoordResetForm, 'new_password' bij ChangePasswordForm) — zo delen
    beide formulieren dezelfde, correct vertaalde foutmelding.
    """
    def _validator(form, field):
        if field.data != getattr(form, base_field_name).data:
            raise ValidationError(_('Passwords do not match.'))
    return _validator


class LoginForm(FlaskForm):
    email = StringField(
        "email",
        validators=[
            DataRequired(message="val_required"),
            Email(message="val_email_invalid"),
        ],
    )
    password = PasswordField(
        "password",
        validators=[DataRequired(message="val_required")],
    )
    submit = SubmitField("submit")


class WachtwoordVergetenForm(FlaskForm):
    email = StringField(
        "email",
        validators=[
            DataRequired(message="val_required"),
            Email(message="val_email_invalid"),
        ],
    )
    submit = SubmitField("submit")


class WachtwoordResetForm(FlaskForm):
    password = PasswordField(
        "password",
        validators=[DataRequired(message="val_required"), _validate_password_strong],
    )
    password_confirm = PasswordField(
        "password_confirm",
        validators=[DataRequired(message="val_required"), _validate_password_match("password")],
    )
    submit = SubmitField("submit")


def _validate_linkedin_url(form, field):
    parsed = urlparse(field.data or "")
    if parsed.scheme != "https" or parsed.netloc not in (
        "www.linkedin.com", "linkedin.com"
    ):
        raise ValidationError("val_linkedin_invalid")


class LinkedInAanvullenForm(FlaskForm):
    linkedin_url = StringField(
        "linkedin_url",
        validators=[
            DataRequired(message="val_required"),
            Length(max=255),
            _validate_linkedin_url,
        ],
    )
    submit = SubmitField("submit")


class KiesGebruikersnaamForm(FlaskForm):
    """
    Alleen CSRF + niet-leeg hier; de echte regels (formaat/blacklist/
    uniciteit) lopen via app.utils.is_username_*, zelfde helpers als
    /aanmelden en de blur-check — dezelfde regels op elke invoerplek.
    """
    username = StringField(
        "username",
        validators=[DataRequired(message="val_required"), Length(max=30)],
    )
    submit = SubmitField("submit")


class DeleteAccountForm(FlaskForm):
    """Leeg form — alleen voor CSRF-validatie bij account verwijderen."""
    pass


class VerifyResendForm(FlaskForm):
    """Leeg form — alleen voor CSRF-validatie bij verificatiemail opnieuw versturen."""
    pass


class FeedbackForm(FlaskForm):
    """Leeg form — alleen voor CSRF-validatie bij het feedbackformulier."""
    pass


class InvitationForm(FlaskForm):
    """
    CSRF + validatie voor het uitnodigingsformulier. invite_email moet een
    los, geldig e-mailadres zijn (Email-validator accepteert geen kommagesch-
    eiden lijst) — voorkomt dat /uitnodiging als open mail-relay naar
    meerdere ontvangers tegelijk misbruikt kan worden. invite_message is
    optioneel maar begrensd in lengte om relay-misbruik te dempen.
    """
    invite_email = StringField(
        "invite_email",
        validators=[
            DataRequired(message="val_required"),
            Email(message="val_email_invalid"),
            Length(max=255),
        ],
    )
    invite_message = TextAreaField(
        "invite_message",
        validators=[Optional(), Length(max=1000, message="val_message_too_long")],
    )
    submit = SubmitField("submit")


class AanmeldenForm(FlaskForm):
    """
    Leeg form — alleen voor CSRF-validatie bij /aanmelden. De echte velden
    (username, email, wachtwoord, linkedin_url, ...) worden bewust niet als
    WTForms-velden gemodelleerd: de bestaande handmatige validatie in
    social.py (username format/blacklist/uniciteit, wachtwoordsterkte,
    linkedin-formaat) blijft ongewijzigd, dit form voegt alleen de
    ontbrekende CSRF-laag toe.
    """
    pass


class LinkedInForm(FlaskForm):
    """Leeg form — alleen voor CSRF-validatie bij het bijwerken van LinkedIn in profielinstellingen."""
    pass


class AdminActionForm(FlaskForm):
    """Leeg form — alleen voor CSRF-validatie bij admin-acties (rol wijzigen, account verwijderen)."""
    pass


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "current_password",
        validators=[DataRequired(message="val_required")],
    )
    new_password = PasswordField(
        "new_password",
        validators=[
            DataRequired(message="val_required"),
            _validate_password_strong,
        ],
    )
    confirm_new_password = PasswordField(
        "confirm_new_password",
        validators=[
            DataRequired(message="val_required"),
            _validate_password_match("new_password"),
        ],
    )
    submit = SubmitField("submit")
