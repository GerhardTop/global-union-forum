"""De `lang`-variabele in templates moet dezelfde taal volgen als gettext
(sessie, anders Accept-Language), zodat losse NL/EN-teksten niet botsen met
de vertaalde chrome. Regressie: eerder stond `lang` hard op 'nl'."""


def test_overview_submitter_language_follows_accept_language(app, db, verified_user):
    from app import issues
    issues.create({'title': 'Taalcheck', 'intro': 'Uitleg.', 'kind': 'online'}, verified_user)
    db.session.commit()
    c = app.test_client()
    nl = c.get('/gesprekken', headers={'Accept-Language': 'nl'}).get_data(as_text=True)
    en = c.get('/gesprekken', headers={'Accept-Language': 'en-US,en;q=0.9'}).get_data(as_text=True)
    assert 'ingebracht door' in nl and 'submitted by' not in nl
    assert 'submitted by' in en and 'ingebracht door' not in en
