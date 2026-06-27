from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError
from urllib.parse import urlparse


class RegistrationForm(FlaskForm):
    first_name = StringField(
        "first_name",
        validators=[DataRequired(message="val_required"), Length(max=50)],
    )
    last_name = StringField(
        "last_name",
        validators=[DataRequired(message="val_required"), Length(max=50)],
    )
    email = StringField(
        "email",
        validators=[
            DataRequired(message="val_required"),
            Email(message="val_email_invalid"),
            Length(max=120),
        ],
    )
    password = PasswordField(
        "password",
        validators=[
            DataRequired(message="val_required"),
            Length(min=8, message="val_password_min"),
        ],
    )
    confirm_password = PasswordField(
        "confirm_password",
        validators=[
            DataRequired(message="val_required"),
            EqualTo("password", message="val_passwords_no_match"),
        ],
    )
    submit = SubmitField("submit")


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
    # Sterktevereisten worden in de route gecontroleerd via _password_strong().
    password = PasswordField(
        "password",
        validators=[DataRequired(message="val_required")],
    )
    password_confirm = PasswordField(
        "password_confirm",
        validators=[DataRequired(message="val_required")],
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


class DeleteAccountForm(FlaskForm):
    """Leeg form — alleen voor CSRF-validatie bij account verwijderen."""
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
            Length(min=8, message="val_password_min"),
        ],
    )
    confirm_new_password = PasswordField(
        "confirm_new_password",
        validators=[
            DataRequired(message="val_required"),
            EqualTo("new_password", message="val_passwords_no_match"),
        ],
    )
    submit = SubmitField("submit")
