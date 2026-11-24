"""The language register helpers: the one place that answers which languages
the platform offers. Everything (locale selection, the language switcher,
registration, mails) asks these functions instead of keeping its own list.

The register itself lives in config.py (LANGUAGES, ENABLED_LANGUAGES,
DEFAULT_LANGUAGE, SOURCE_LANGUAGE). The values are read on every call, so a
test can switch a language on by patching config.ENABLED_LANGUAGES.
"""
import config


def _register():
    return {entry["code"]: entry for entry in config.LANGUAGES}


def default_language():
    return config.DEFAULT_LANGUAGE


def source_language():
    return config.SOURCE_LANGUAGE


def visitor_fallback_language():
    """The language for a visitor whose browser asks for no enabled language."""
    return config.VISITOR_FALLBACK_LANGUAGE


# The language choice, remembered beyond the session (functional cookie,
# privacy page): set by /lang/<code> and at log in, read by get_locale.
LANG_COOKIE = 'lang'
LANG_COOKIE_MAX_AGE = 365 * 24 * 3600  # one year


def current():
    """The language of this request, as gettext picks it (app get_locale:
    session, cookie, browser, visitor fallback). Use it wherever a request
    needs the reader's language (the source language of new content, the
    language to keep across session.clear())."""
    from flask_babel import get_locale
    return normalize(str(get_locale() or ''))


def remember(code):
    """Set the language cookie to `code` on this response (app after_request)."""
    from flask import g
    if is_enabled(code):
        g._remember_lang = code


def enabled_codes():
    """The codes of the enabled languages, in register order of ENABLED_LANGUAGES."""
    return list(config.ENABLED_LANGUAGES)


def enabled_languages():
    """The enabled languages as dicts with code, name and dir (for templates)."""
    return [{"code": code, "name": language_name(code), "dir": text_direction(code)}
            for code in enabled_codes()]


def is_enabled(code):
    return code in config.ENABLED_LANGUAGES


def language_name(code):
    """The language's name in its own language; the code when it is unknown."""
    entry = _register().get(code)
    return entry["name"] if entry else code


def english_name(code):
    """The language's English name, for AI prompts ("Translate ... to German")."""
    entry = _register().get(code)
    if entry is None:
        raise ValueError(f'unknown language code: {code!r}')
    return entry["english"]


def language_name_in(code, in_lang):
    """The name of language `code` in language `in_lang` ("Engels" for en in
    nl), from Babel's locale data; the code in capitals when unknown."""
    from babel import Locale, UnknownLocaleError
    try:
        return Locale.parse(str(in_lang)).languages.get(code) or str(code).upper()
    except (UnknownLocaleError, ValueError, TypeError):
        return str(code).upper()


def is_reviewed(code):
    """True when a native speaker reviewed the translation; False for a
    machine translation (and for an unknown code)."""
    entry = _register().get(code)
    return bool(entry and entry.get("reviewed"))


def text_direction(code):
    """'ltr' or 'rtl'; left to right for an unknown code."""
    entry = _register().get(code)
    return entry["dir"] if entry else "ltr"


def normalize(code):
    """An enabled language code: the given one, or else the default language."""
    code = str(code) if code else None
    return code if is_enabled(code) else default_language()


def auto_translate_default(code):
    """Automatic translation is on by default for readers whose language is not
    the default language (they read the mostly Dutch conversation translated)."""
    return normalize(code) != default_language()
