"""Background figures, step 1k: rule 4 (countries) enforced by the app. The
app decides the allowed countries before the AI is asked: the countries
named in the issue and its proposals (originals and translations), else the
fixed set of the region (Europe unless a country outside Europe or "the
world" is named); a country outside the set falls away.

Never network, never the real AI.
"""
import uuid

from app import background_figures as bf, content_i18n, issue_proposals as ip, issues
from tests.test_background_figures import (SIPRI, FakeClient, FakeHttp, _answer, _chart, _charts,  # noqa: F401
                                           _run_for, made, started, watched)
from tests.test_delete_issue import _clean_session  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import make_user  # noqa: F401  (fixture)


def _issue(db, user, made, intro, title=None):
    issue = issues.create({'title': title or f'Een vraagstuk {uuid.uuid4().hex[:8]}', 'intro': intro,
                           'kind': 'online'}, user)
    db.session.commit()
    made.append(issue.id)
    return issue


# ── Named countries ──────────────────────────────────────────────────────

def test_named_in_dutch_german_english_and_owid_names():
    assert bf.named_countries(['Moet Polen meer uitgeven?']) == ['PL']
    assert bf.named_countries(['Was macht Frankreich anders?']) == ['FR']
    assert bf.named_countries(['¿Qué hace Alemania?']) == ['DE']
    assert bf.named_countries(['Que fait la Pologne ?']) == ['PL']
    assert bf.named_countries(['Spain and Czechia']) == ['CZ', 'ES']
    assert bf.named_countries(['NEDERLAND en duitsland']) == ['DE', 'NL']


def test_a_word_that_only_looks_like_a_country_is_not_named():
    assert bf.named_countries(['Polenta met Indiaas eten en een Frankrijkreis']) == []
    assert bf.named_countries(['De Nigeriaanse economie']) == []


def test_named_in_a_proposal_and_in_a_translation(app, db, make_user, made, started):
    user = make_user()
    issue = _issue(db, user, made, 'Hoe verdelen we de kosten van de energietransitie eerlijk?')
    ctx = bf.gather(issue)
    assert ctx.named == [] and ctx.region == 'Europe' and ctx.allowed == list(bf.EUROPE_SET)
    proposal = ip.create(issue, user, 'Polen krijgt extra steun uit het Europese fonds.')
    db.session.commit()
    content_i18n.set_translation(issue, 'intro', 'de', 'Wie verteilen wir die Kosten, auch in Frankreich?',
                                 is_machine=True)
    db.session.commit()
    ctx = bf.gather(issue, proposal, 'proposal')
    assert ctx.named == ['FR', 'PL']
    # Two named: the fixed set may fill up to four.
    assert ctx.allowed == ['FR', 'PL', 'DE', 'NL']


def test_four_named_leave_no_room_for_the_fixed_set(app, db, make_user, made, started):
    issue = _issue(db, make_user(), made, 'Spanje, Italië, Portugal en Griekenland: wat werkt?')
    ctx = bf.gather(issue)
    assert ctx.named == ['ES', 'GR', 'IT', 'PT'] and ctx.allowed == ctx.named and ctx.region == 'Europe'


# ── The region ───────────────────────────────────────────────────────────

def test_nothing_named_gives_the_fixed_set_of_europe(app, db, make_user, made, started):
    issue = _issue(db, make_user(), made, 'Hoe maken we wonen betaalbaar?')
    ctx = bf.gather(issue)
    assert ctx.region == 'Europe' and ctx.allowed == ['DE', 'FR', 'NL', 'PL']


def test_a_country_outside_europe_gives_the_world(app, db, make_user, made, started):
    ctx = bf.gather(_issue(db, make_user(), made, 'Wat kunnen we leren van Brazilië?'))
    assert ctx.named == ['BR'] and ctx.region == 'World'
    assert ctx.allowed == ['BR', 'US', 'CN', 'IN']


def test_the_world_named_gives_the_world_set(app, db, make_user, made, started):
    ctx = bf.gather(_issue(db, make_user(), made, 'Hoe halen we de klimaatdoelen wereldwijd?'))
    assert ctx.named == [] and ctx.region == 'World' and ctx.allowed == list(bf.WORLD_SET)


# ── The prompt and the check ─────────────────────────────────────────────

def test_the_ai_gets_only_the_allowed_countries(app, db, make_user, made, started, watched):
    issue = _issue(db, make_user(), made, 'Moeten Polen en Duitsland meer uitgeven aan defensie?')
    client = FakeClient(_answer(_chart()))
    bf.run(_run_for(issue).id, client=client, http=FakeHttp())
    prompt = client.calls[1]['messages'][0]['content']
    assert 'Countries you may choose (named in the issue or the proposals): Germany, Poland' in prompt
    assert 'Only to fill up to 4 countries, from the fixed set of the region (Europe): France, Netherlands' in prompt
    assert 'Any other country is dropped by the app.' in prompt
    # The candidate's entities: only the allowed countries, the averages and (step 1l) the world blocks.
    entities = prompt.split(f'slug: {SIPRI}')[1].split('entities:')[1].split('\n')[0]
    assert 'Poland' in entities and 'Spain' not in entities and 'United States' in entities
    assert 'Burkina Faso' not in entities


def test_a_country_outside_the_set_falls_away(app, db, make_user, made, started, watched):
    issue = _issue(db, make_user(), made, 'Moeten Polen en Duitsland meer uitgeven aan defensie?')
    answer = _answer(_chart(countries=['United States', 'Spain', 'Poland']))
    assert bf.run(_run_for(issue).id, client=FakeClient(answer), http=FakeHttp()) == 'done'
    (chart,) = _charts(issue)
    assert chart.entity_keys == ['POL']
    notes = _run_for(issue).error
    assert "'United States' country_not_allowed" in notes and "'Spain' country_not_allowed" in notes


def test_no_allowed_country_left_drops_the_chart(app, db, make_user, made, started, watched):
    issue = _issue(db, make_user(), made, 'Moeten Polen en Duitsland meer uitgeven aan defensie?')
    answer = _answer(_chart(countries=['United States', 'Spain']))
    assert bf.run(_run_for(issue).id, client=FakeClient(answer), http=FakeHttp()) == 'nothing'
    assert 'no country left' in _run_for(issue).error and _charts(issue) == []
