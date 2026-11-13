"""
The manifest intro (section 1, "Like-minded countries as a positive force")
and the corrected EU/Canada sentence on the homepage, in both languages.
"""
import pytest

INTRO = {
    'nl': ('Gelijkgezinde landen als positieve kracht', (
        'Grootmachten kiezen steeds vaker voor zichzelf.',
        'In oktober 2026 sloten de EU en Canada een alliantie voor de toekomst.',
        'De GU is principieel én pragmatisch.',
        'Zo groeit de unie uit aantrekkingskracht.')),
    'en': ('Like-minded countries as a positive force', (
        'Major powers increasingly put themselves first.',
        'In October 2026, the EU and Canada formed an alliance for the future.',
        'The GU is principled and pragmatic.',
        'This is how the union grows: through attraction.')),
}
OLD = ('Vertrekpunt', 'Starting point', 'regionaal project', 'regional project')
HOME = {
    'nl': 'In oktober 2026 is een eerste stap gezet: de EU en Canada sloten een alliantie voor de toekomst, die openstaat voor gelijkgezinde landen.',
    'en': 'A first step was taken in October 2026: the EU and Canada formed an alliance for the future, open to like-minded countries.',
}


@pytest.mark.parametrize('lang', ['nl', 'en'])
def test_manifest_intro(client, lang):
    client.get(f'/lang/{lang}')
    html = client.get('/manifest').get_data(as_text=True)
    heading, paragraphs = INTRO[lang]
    section = html.split('id="vertrekpunt"')[1].split('</section>')[0]
    assert f'<h2>{heading}</h2>' in section
    assert section.count('<p>') == 4 and all(p in section for p in paragraphs)
    assert f'<span class="gu-manifest__tocLabel">{heading}</span>' in html
    assert not any(old in html for old in OLD)


@pytest.mark.parametrize('lang', ['nl', 'en'])
def test_homepage_eu_canada_sentence(client, lang):
    client.get(f'/lang/{lang}')
    html = client.get('/').get_data(as_text=True)
    assert HOME[lang] in html
    assert 'associated member' not in html and 'geassocieerde lid' not in html
