"""Background figures, step 1j: charts with one measure per country, and one
retry in the same round when every newly chosen chart falls away because it
shows several measures ('unsupported').

Never network, never the real AI.
"""
import json
from types import SimpleNamespace

import pytest

from app import background_figures as bf, quality
from app.models import ChartSelectionRun, OwidRejectedSlug
from app.sources import owid
from tests.test_background_figures import (SIPRI, FakeHttp, _chart, _charts, _issue, _read, _run_for,  # noqa: F401
                                           made, started, watched)
from tests.test_delete_issue import _clean_session  # noqa: F401  (autouse fixture)
from tests.test_issue_polish import make_user  # noqa: F401  (fixture)

MIXES = ('electricity-mix', 'energy-by-source')


class MixHttp(FakeHttp):
    """Like FakeHttp; the two 'mix' charts have two data columns (unsupported)."""

    def get(self, url, params=None, headers=None, timeout=None):
        if url != owid.SEARCH_URL and url.split('/grapher/')[1].split('.')[0] in MIXES:
            self.calls.append((url, params))
            if url.endswith('.metadata.json'):
                return SimpleNamespace(status_code=200, content=b'{}')
            return SimpleNamespace(status_code=200, content=_read('two-columns.csv').encode())
        return super().get(url, params=params, headers=headers, timeout=timeout)


class SequenceClient:
    """Search terms, then the selection answers in order (one per call)."""

    def __init__(self, *selections):
        self.selections = list(selections)
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if kw['model'] == quality.JUDGE_MODEL:
            text = json.dumps({'terms': ['coal power']})
        else:
            text = json.dumps(self.selections.pop(0))
        return SimpleNamespace(content=[SimpleNamespace(text=text)])

    def selection_prompts(self):
        return [c['messages'][0]['content'] for c in self.calls if c['model'] == bf.SELECTION_MODEL]


@pytest.fixture
def hits(monkeypatch):
    found = [owid.SearchHit(slug=s, title=s, subtitle='', entities=['Poland', 'Germany'])
             for s in (*MIXES, SIPRI, 'regions-chart')]
    monkeypatch.setattr(owid, 'search', lambda *a, **kw: found)
    return found


def _answer(*charts, links=()):
    return {'charts': list(charts), 'link_existing': list(links), 'none_reason': None}


def test_the_prompts_ask_for_one_measure():
    assert ("Prefer terms for a single measure per country (for example 'share of electricity from coal', "
            "'wind power generation'), not for a breakdown or mix of several sources.") in bf._TERMS_SYSTEM
    assert ("Choose charts that show one measure per country. Avoid charts that show a mix or breakdown (by "
            "source, by sector, by type), because the app can only draw one measure. For example, choose 'Share "
            "of electricity from coal' rather than 'Electricity mix' or 'Electricity by source'.") \
        in bf._SELECTION_SYSTEM


def test_all_unsupported_gives_one_retry_that_publishes(app, db, make_user, made, started, watched, hits):
    issue = _issue(db, make_user(), made)
    client = SequenceClient(_answer(_chart(slug=MIXES[0]), _chart(slug=MIXES[1])), _answer(_chart()))
    run_row = _run_for(issue)
    assert bf.run(run_row.id, client=client, http=MixHttp()) == 'done'
    first, retry = client.selection_prompts()
    assert MIXES[0] in first and MIXES[1] in first
    retry_candidates = retry.split('Candidates:')[1].split('These charts could not be used')[0]
    assert MIXES[0] not in retry_candidates and MIXES[1] not in retry_candidates and SIPRI in retry_candidates
    assert retry.endswith(f'These charts could not be used because they show several measures: '
                          f'{MIXES[0]}, {MIXES[1]}. Choose again, or choose none.')
    assert [c.slug for c in _charts(issue)] == [SIPRI]
    row = db.session.get(ChartSelectionRun, run_row.id)
    assert row.prompt_text.count('[system]') == 3 and row.prompt_text.count('========') == 2
    assert row.response_text.count('========') == 2
    assert {r.slug for r in OwidRejectedSlug.query} == set(MIXES)


def test_a_failed_retry_gives_nothing_and_remembers_the_slugs(app, db, make_user, made, started, watched, hits):
    issue = _issue(db, make_user(), made)
    client = SequenceClient(_answer(_chart(slug=MIXES[0])), _answer(_chart(slug=MIXES[1])))
    run_row = _run_for(issue)
    assert bf.run(run_row.id, client=client, http=MixHttp()) == 'nothing'
    assert len(client.selection_prompts()) == 2                    # one retry, never a second
    assert _charts(issue) == []
    # The retry may only choose among the remaining candidates: mix 2 was one, and falls away too.
    assert {r.slug: r.reason for r in OwidRejectedSlug.query} == {MIXES[0]: 'unsupported', MIXES[1]: 'unsupported'}


def test_a_retry_choosing_a_dropped_chart_again_gets_nothing(app, db, make_user, made, started, watched, hits):
    issue = _issue(db, make_user(), made)
    client = SequenceClient(_answer(_chart(slug=MIXES[0])), _answer(_chart(slug=MIXES[0])))
    assert bf.run(_run_for(issue).id, client=client, http=MixHttp()) == 'nothing'
    assert 'electricity-mix: not one of the candidates' in _run_for(issue).error


def test_no_retry_when_one_chart_is_fine(app, db, make_user, made, started, watched, hits):
    issue = _issue(db, make_user(), made)
    client = SequenceClient(_answer(_chart(slug=MIXES[0]), _chart()))
    assert bf.run(_run_for(issue).id, client=client, http=MixHttp()) == 'done'
    assert len(client.selection_prompts()) == 1
    assert [c.slug for c in _charts(issue)] == [SIPRI]


def test_no_retry_when_a_chart_falls_away_for_another_reason(app, db, make_user, made, started, watched, hits):
    issue = _issue(db, make_user(), made)
    client = SequenceClient(_answer(_chart(slug=MIXES[0]), _chart(slug='regions-chart', countries=['Atlantis'])))
    assert bf.run(_run_for(issue).id, client=client, http=MixHttp()) == 'nothing'
    assert len(client.selection_prompts()) == 1


def test_no_retry_without_new_charts(app, db, make_user, made, started, watched, hits):
    issue = _issue(db, make_user(), made)
    client = SequenceClient(_answer())
    assert bf.run(_run_for(issue).id, client=client, http=MixHttp()) == 'nothing'
    assert len(client.selection_prompts()) == 1
