"""
The manifest intro (section 1, "Like-minded countries as a positive force",
anchor #vertrekpunt) and the corrected EU/Canada sentence on the homepage, in
both languages.
"""
import pytest

INTRO = {
    'nl': ('Gelijkgezinde landen als positieve kracht', (
        'Grootmachten trekken zich te weinig aan van internationaal recht. De Global Union (GU) zet daar iets tegenover: een vaste samenwerking tussen functionerende democratieën, als aanvulling op de EU en de VN. Samen willen we onze marktmacht inzetten voor welvaart, sociale zekerheid, een leefbare planeet, veiligheid en handhaving van internationale afspraken.',
        'Meedoen kan elk land dat goed scoort op democratie, rechtsstaat, mensenrechten en corruptiebestrijding. Er ligt al een stevige basis. In oktober 2026 sloten de EU en Canada een alliantie voor de toekomst. Gelijkgezinde landen zijn uitgenodigd mee te doen. Gezamenlijk is onze economie ruim een derde van de wereld en groter dan de VS.',
        'De GU is principieel én pragmatisch. Principieel omdat handelstarieven afhangen van hoe een land scoort op het nakomen van internationale afspraken en gedeelde waarden. Wie aan alle criteria voldoet, betaalt geen invoerrechten. Wie agressie pleegt, wordt uitgesloten. Pragmatisch omdat we klein beginnen, met beperkte tarieven. We willen aantrekkelijk zijn en onnodige escalatie voorkomen. Zo winnen we tijd om samen strategisch onafhankelijk te worden.',
        'Wie meedoet, profiteert. Dat geeft landen een reden om te hervormen, zoals eerder bij de EU. Zo groeit de unie uit aantrekkingskracht.',
    )),
    'en': ('Like-minded countries as a positive force', (
        'Major powers pay too little attention to international law. The Global Union (GU) offers an alternative: a permanent cooperation between functioning democracies, as a complement to the EU and the UN. Together we want to use our market power for prosperity, social security, a liveable planet, security and the enforcement of international agreements.',
        "Any country that scores well on democracy, the rule of law, human rights and anti-corruption can join. A solid foundation already exists. In October 2026, the EU and Canada formed an alliance for the future. Like-minded countries have been invited to join. Together, our economy is more than one third of the world's and larger than that of the US.",
        'The GU is principled and pragmatic. Principled, because trade tariffs depend on how a country scores on honouring international agreements and shared values. Countries that meet all criteria pay no import duties. Countries that commit aggression are excluded. Pragmatic, because we start small, with limited tariffs. We want to be attractive and avoid unnecessary escalation. This gives us time to become strategically independent together.',
        'Those who join benefit. That gives countries a reason to reform, as happened earlier with the EU. This is how the union grows: through attraction.',
    )),
}
# Sentences of earlier versions of the intro that must be gone.
REPLACED = {
    'nl': ('Vertrekpunt', 'Grootmachten kiezen steeds vaker voor zichzelf.',
           'Samen zijn ze goed voor ongeveer een vijfde van de wereldeconomie.',
           'Doen die allemaal mee, dan wordt dat ruim een derde.', 'Dat is meer dan de VS of China.',
           'Die kracht willen we inzetten voor welvaart',
           'hoe een land scoort op gedeelde waarden.', 'regionaal project'),
    'en': ('Starting point', 'Major powers increasingly put themselves first.',
           'Together they account for about one fifth of the world economy.',
           'If they all join, that becomes more than one third.', 'That is more than the US or China.',
           'We want to use this strength for prosperity',
           'how a country scores on shared values.', 'regional project'),
}
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
    assert section.count('<p>') == 4
    for paragraph in paragraphs:
        assert f'<p>{paragraph}</p>' in section
    assert f'<span class="gu-manifest__tocLabel">{heading}</span>' in html
    for gone in REPLACED[lang]:
        assert gone not in html, gone


@pytest.mark.parametrize('lang', ['nl', 'en'])
def test_homepage_eu_canada_sentence(client, lang):
    client.get(f'/lang/{lang}')
    html = client.get('/').get_data(as_text=True)
    assert HOME[lang] in html
    assert 'associated member' not in html and 'geassocieerde lid' not in html
