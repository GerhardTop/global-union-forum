"""Homepage "Feedback & invitation": the other card (Invite for members,
Join the thinking for visitors) on the left, the feedback card on the right,
and the feedback text without tool names, in both languages."""
import pytest

from tests.test_issue_polish import _client, _req, make_user  # noqa: F401  (fixture)

TEXT = {
    'nl': 'Deze site is ontworpen en gebouwd met behulp van AI. Heb je feedback over de site zelf? Ik hoor het graag! '
          'Inhoudelijke reacties op het idee zijn welkom in het forum. Alvast bedankt.',
    'en': "This site was designed and built with the help of AI. Do you have feedback about the site itself? "
          "I&#39;d love to hear it! Substantive reactions to the idea are welcome in the forum. Thanks in advance.",
}
LEFT = {('nl', True): 'Nodig uit', ('en', True): 'Invite',
        ('nl', False): 'Denk mee', ('en', False): 'Have your say'}
RIGHT = {('nl', True): 'Stuur feedback', ('en', True): 'Send feedback',
         ('nl', False): 'Log in om feedback te sturen', ('en', False): 'Log in to send feedback'}


def _cards(html):
    grid = html.split('class="gu-feedback-grid"')[1].split('</section>')[0]
    return grid.split('<div class="gu-feedback-card">')[1:]


@pytest.mark.parametrize('lang', ['nl', 'en'])
@pytest.mark.parametrize('signed_in', [True, False])
def test_feedback_card_is_on_the_right_with_the_new_text(app, db, make_user, lang, signed_in):
    user = make_user() if signed_in else None
    html = _req(_client(app, user, lang), 'GET', '/').get_data(as_text=True)
    cards = _cards(html)
    assert len(cards) == 2
    left, right = cards
    assert LEFT[(lang, signed_in)] in left and TEXT[lang] not in left
    assert TEXT[lang] in right and RIGHT[(lang, signed_in)] in right
    assert 'Claude' not in html.split('class="gu-feedback-grid"')[1].split('</section>')[0]
