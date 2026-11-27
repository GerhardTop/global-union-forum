"""
The content of the FAQ page (/faq): editorial text in the translation
catalog (multilingual phase 0, step 3). One list of themes, each with
(question, answer); the msgids are the English text, the translations live
in app/translations. Every msgid has a translator comment (faq, thema N /
vraag N.M / antwoord N.M) so the catalog stays readable.

"Global Union", "Global Union Forum" and "Freedom for all" stay
untranslated. The answer with a link has an %(url)s placeholder and is
rendered as Markup; the rest is plain text that the template escapes.

Extract with: pybabel extract -F babel.cfg -k lazy_gettext -c TRANSLATORS: -o messages.pot app
"""
from flask_babel import lazy_gettext
from markupsafe import Markup

# TRANSLATORS: faq, antwoord met een link naar de privacy-pagina
_PRIVACY = lazy_gettext('As little as possible. Your pseudonym, your email address for recovery and what you post '
                        'yourself. Extra profile details only if you fill them in yourself. The details are on the <a '
                        'href="%(url)s">privacy page</a>.')

# Representativeness 1e: only with PARTICIPANT_PROFILE on (themes(participants=True)).
# TRANSLATORS: faq, vraag 4.3b (the voluntary questions about age, gender, education and country after the first vote)
_PARTICIPANTS_Q = lazy_gettext('Why do you ask about my age and education after my first vote?')
# TRANSLATORS: faq, antwoord 4.3b, met een link naar de privacy-pagina
_PARTICIPANTS = lazy_gettext('So that we can show with the result how diverse the group of voters is. It is voluntary '
                             'and your vote counts either way. We only show totals of groups of at least 10 people, '
                             'never who voted what. There is more on the <a href="%(url)s">privacy page</a>.')

FAQ = [
    # TRANSLATORS: faq, thema 1
    ('Global Union Forum', [
        # TRANSLATORS: faq, vraag 1.1 en antwoord 1.1
        (lazy_gettext('What is Global Union?'),
         lazy_gettext('Global Union is the idea of a partnership of functioning democracies worldwide. It is built on '
                       'attraction instead of force, following the example of the European Union. It is open to '
                       'countries that respect the rule of law, human rights, the fight against corruption, '
                       'sustainability and the international legal order.')),
        # TRANSLATORS: faq, vraag 1.2 en antwoord 1.2
        (lazy_gettext('What is the difference between Global Union and the Global Union Forum?'),
         lazy_gettext('Global Union is the idea and the movement. The Global Union Forum is the place where that idea '
                       'is developed and discussed. Here you think along, bring in facts and vote. Experts and beginners'
                       ' do this side by side.')),
        # TRANSLATORS: faq, vraag 1.3 en antwoord 1.3
        (lazy_gettext('Who is behind the platform?'),
         lazy_gettext('It is an independent initiative, set up by founder Gerhard Top. With enough momentum, this '
                       'initiative will be placed in a foundation. The idea is to involve co-initiators first, and then '
                       'a growing group of participants and experts, to develop it further together.')),
        # TRANSLATORS: faq, vraag 1.4 en antwoord 1.4
        (lazy_gettext('Is this linked to a political party or a government?'),
         lazy_gettext('No. The forum is independent and not linked to a party, a government or a company. It is '
                       'reformist and it is not tied to a political colour.')),
        # TRANSLATORS: faq, vraag 1.5 en antwoord 1.5
        (lazy_gettext('How can I take part or contribute?'),
         lazy_gettext('Create an account and read the ground rules. Then you can start: add facts, give your opinion or'
                       ' start an issue yourself. What this becomes is decided by the people who take part.')),
    ]),
    # TRANSLATORS: faq, thema 2
    (lazy_gettext('Manifesto'), [
        # TRANSLATORS: faq, vraag 2.1 en antwoord 2.1
        (lazy_gettext('What is in the manifesto?'),
         lazy_gettext('The manifesto from March 2026 is a personal essay by the founder. In it he sets out the idea of '
                       'a global union of functioning democracies: why it is needed, how it could work and why it is '
                       'worth trying. See it as the start of the discussion.')),
        # TRANSLATORS: faq, vraag 2.2 en antwoord 2.2
        (lazy_gettext('What does "a union of functioning democracies" mean?'),
         lazy_gettext('A partnership of countries that truly uphold democracy and the rule of law. Countries from all '
                       'over the world can join. What counts is whether a country meets those values.')),
        # TRANSLATORS: faq, vraag 2.3 en antwoord 2.3
        (lazy_gettext('Why the example of the European Union?'),
         lazy_gettext('The EU shows that countries can work together voluntarily, based on shared rules. They attract '
                       'each other instead of forcing each other. We want to apply that principle on a world scale.')),
        # TRANSLATORS: faq, vraag 2.4 en antwoord 2.4
        (lazy_gettext('Which countries could join, and on what basis?'),
         lazy_gettext('Countries that meet standards such as the rule of law, human rights, the fight against '
                       'corruption and the international legal order. Membership is based on democratic performance. Not'
                       ' only wealthy Western countries, but also, and especially, model countries from Africa and South'
                       ' America.')),
        # TRANSLATORS: faq, vraag 2.5 en antwoord 2.5
        (lazy_gettext('Is this not too grand or too idealistic?'),
         lazy_gettext('It is a grand idea. Perhaps hard to achieve, but worth it. You start small and build it up '
                       'together, step by step.')),
    ]),
    # TRANSLATORS: faq, thema 3
    (lazy_gettext('How it works'), [
        # TRANSLATORS: faq, vraag 3.1 en antwoord 3.1
        (lazy_gettext('How does an issue go, from discussion to vote?'),
         lazy_gettext('Someone starts an issue with a question or problem. In the first phase, facts and discussion, '
                       'you bring in facts, research and media and discuss it together. Proposals come out of that. '
                       'After that, the proposer can revise a proposal. Then there is a vote. If the initiator chose so,'
                       ' a summary proposal follows, with a new vote. Finally there is the follow-up phase: what happens'
                       ' with it, such as lobbying, media or implementation.')),
        # TRANSLATORS: faq, vraag 3.2 en antwoord 3.2
        (lazy_gettext('What are facts, research and media, and how do they count?'),
         lazy_gettext('Facts, research and media are well-founded contributions with a source. Media are news items, '
                       'podcasts or videos. Each fact has a direction: argues for, argues against or neutral. A fact '
                       'gets a green quality mark when the AI finds a source, a findable link, where in the source it '
                       'is, that it is on the topic and that it is new. The mark does not judge how reliable the source '
                       'is.')),
        # TRANSLATORS: faq, vraag 3.3 en antwoord 3.3
        (lazy_gettext('How do proposals come about, and how is the vote held?'),
         lazy_gettext('Proposals come out of the discussion. A proposal goes on the voting list as soon as enough '
                       'people support it. Each proposal is voted on with for, against or abstain. Based on all '
                       'proposals and the discussion, the initiator can adjust the issue and make a summary proposal. '
                       'The participants can then vote on that again.')),
        # TRANSLATORS: faq, vraag 3.4 en antwoord 3.4
        (lazy_gettext('What is the quality mark?'),
         lazy_gettext('A medal that is given automatically to a contribution that moves the discussion forward. That '
                       'can be a new argument or support with a source. No quality mark is not a punishment. Style and '
                       'language skills do not count.')),
        # TRANSLATORS: faq, vraag 3.5 en antwoord 3.5
        (lazy_gettext('How do I earn the right to vote?'),
         lazy_gettext('By contributing quality. After three contributions with a quality mark, you may vote. That way, '
                       'what you bring in counts, not just having an account.')),
        # TRANSLATORS: faq, vraag 3.6 en antwoord 3.6
        (lazy_gettext('Which ground rules apply, and what happens when they are broken?'),
         lazy_gettext('There are conduct rules: stay respectful, stay on topic and one account per person. These are '
                       'enforced. There are also quality guidelines, such as well-founded and concrete. These are '
                       'rewarded and not enforced. When a rule is clearly broken, a contribution moves to a public '
                       'background page with an explanation. You can object to that there. Nothing is removed for good. '
                       'Only pure spam and gross abuse are hidden completely.')),
        # TRANSLATORS: faq, vraag 3.7 en antwoord 3.7
        (lazy_gettext('How do you prevent double voting or fake accounts?'),
         lazy_gettext('It cannot be prevented completely. A new account starts at zero and has to earn the right to '
                       'vote first, with quality contributions. One account per person is a rule, and moderation steps '
                       'in when there is abuse. This makes mass fake voting a lot of work for little gain.')),
    ]),
    # TRANSLATORS: faq, thema 4
    (lazy_gettext('Account and privacy'), [
        # TRANSLATORS: faq, vraag 4.1 en antwoord 4.1
        (lazy_gettext('How do I create an account?'),
         lazy_gettext('Choose a name and enter an email address. That email address is only for recovery and is never '
                       'shown. Accept the ground rules and you can start. You do not have to pay and you do not need to '
                       'show any ID.')),
        # TRANSLATORS: faq, vraag 4.2 en antwoord 4.2
        (lazy_gettext('Why is a pseudonym the default?'),
         lazy_gettext('We want you to be able to join the discussion freely, without having to answer to anyone here. A'
                       ' pseudonym makes that possible. In many countries, political participation can bring risks. An '
                       'identity barrier would shut out exactly the people we want to reach.')),
        # TRANSLATORS: faq, vraag 4.3 en antwoord 4.3
        (lazy_gettext('Which data do you keep, and what for?'),
         _PRIVACY),
        (_PARTICIPANTS_Q, _PARTICIPANTS),
        # TRANSLATORS: faq, vraag 4.4 en antwoord 4.4
        (lazy_gettext('Who can see what I write?'),
         lazy_gettext('Your contributions are public and visible under your pseudonym. Anyone can read them. Your email'
                       ' address and any private details stay private.')),
        # TRANSLATORS: faq, vraag 4.5 en antwoord 4.5
        (lazy_gettext('What is the minimum age?'),
         lazy_gettext('Sixteen.')),
        # TRANSLATORS: faq, vraag 4.6 en antwoord 4.6
        (lazy_gettext('How do I delete my account or my data?'),
         lazy_gettext('You can do that in your account settings or on request. Your personal data, such as your email '
                       'address and profile, is then erased.')),
        # TRANSLATORS: faq, vraag 4.7 en antwoord 4.7
        (lazy_gettext('What happens to my contributions after I delete my account?'),
         lazy_gettext('Your contributions stay, but are no longer linked to your name. That way, the discussions and '
                       'votes others took part in keep their coherence. Only the link to you disappears.')),
    ]),
]


def themes(participants=False):
    """The FAQ in the current language: [(theme, [(question, answer)])]. The
    answers with a link are Markup; everything else is plain text. The
    question about the voluntary questions only with participants (the switch
    PARTICIPANT_PROFILE)."""
    out = []
    for theme, questions in FAQ:
        rows = []
        for question, answer in questions:
            if answer is _PARTICIPANTS and not participants:
                continue
            if answer is _PRIVACY:
                answer = Markup(str(answer) % {'url': '/privacy'})
            elif answer is _PARTICIPANTS:
                from flask import url_for
                answer = Markup(str(answer) % {'url': url_for('main.privacy', _anchor='wie-stemde-mee')})
            else:
                answer = str(answer)
            rows.append((str(question), answer))
        out.append((str(theme), rows))
    return out
