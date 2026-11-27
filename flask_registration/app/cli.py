"""
Beheer-commando's voor de flask CLI.

    flask --app run reset-content              # dry-run: toont alleen wat er zou gebeuren
    flask --app run reset-content --list-users # idem, plus de te verwijderen accounts
    flask --app run reset-content --execute    # verwijdert echt (vraagt om bevestiging)
    flask --app run remove-issue TEST          # dry-run: toont vraagstukken waarvan de titel met TEST begint
    flask --app run remove-issue TEST --execute  # verwijdert ze echt (vraagt om bevestiging)

reset-content verwijdert alle gebruikers en alle content, behalve:
  * adminaccounts: is_admin in de database, plus het account met ADMIN_EMAIL
    (omgevingsvariabele, staat niet in de code) als die gezet is;
  * de demo-accounts uit _seed_forum() (DEMO_USER_EMAILS in app/routes/forum.py)
    en de demo-draden en -berichten (is_demo).

Draden hebben geen auteur-kolom, dus "door gebruikers gemaakte content" is:
elke niet-demo-draad, elk niet-demo-bericht (ook echte reacties in een
demo-draad, en berichten van de admin), elk eens/oneens-signaal en elke
kwaliteitsbeoordeling op zo'n bericht, en wat van een verwijderde gebruiker
is. De rate-limit-tellers blijven staan.

Alles gebeurt in één transactie; bij een fout wordt niets verwijderd.
Geüploade afbeeldingen (static/uploads) staan op de schijf van de server,
niet in de database; het commando telt ze alleen.
"""
import click
from sqlalchemy import false, func, or_


def register_cli(app):
    app.cli.add_command(reset_content)
    app.cli.add_command(proposals_tick)
    app.cli.add_command(seed_demo_issues)
    app.cli.add_command(seed_demo_voorbeeld)
    app.cli.add_command(remove_issue)
    app.cli.add_command(background_figures_trial)
    app.cli.add_command(background_figures_example)
    app.cli.add_command(eurostat_catalog_refresh)
    app.cli.add_command(check_translations)


@click.command('seed-demo-issues')
@click.option('--force', is_flag=True, help='Eerst de bestaande demo-vraagstukken weg, dan opnieuw maken.')
def seed_demo_issues(force):
    """Maak demo-vraagstukken met voorstellen, feiten, discussie, steun en stemmen."""
    from app.demo_seed import seed_demo_issues as run
    click.echo(run(force=force))


@click.command('seed-demo-voorbeeld')
@click.option('--force', is_flag=True, help='Eerst het bestaande voorbeeld weg, dan opnieuw maken.')
def seed_demo_voorbeeld(force):
    """Maak een uitgebreid voorbeeld-vraagstuk voor een test met echte gebruikers."""
    from app.demo_voorbeeld import seed_demo_voorbeeld as run
    click.echo(run(force=force))


@click.command('remove-issue')
@click.argument('prefix')
@click.option('--execute', is_flag=True, help='Echt verwijderen. Zonder deze vlag: alleen tonen wat er zou verdwijnen.')
@click.option('--yes', is_flag=True, help='Sla de bevestigingsvraag over (alleen met --execute).')
def remove_issue(prefix, execute, yes):
    """Verwijder vraagstukken waarvan de titel met PREFIX begint, met alles eraan."""
    from app import db
    from app.models import Issue, IssueProposal, Post
    from app.demo_voorbeeld import _remove_issue

    prefix = prefix.strip()
    if len(prefix) < 3:
        raise click.ClickException('Geef minstens 3 tekens van de titel op.')
    found = (Issue.query.filter(Issue.title.startswith(prefix, autoescape=True))
             .order_by(Issue.id).all())
    if not found:
        click.echo(f'Geen vraagstuk gevonden waarvan de titel met "{prefix}" begint.')
        return
    click.echo(f'Database: {db.engine.url.render_as_string(hide_password=True)}')
    for issue in found:
        posts = Post.query.filter_by(thread_id=issue.thread_id).count() if issue.thread_id else 0
        proposals = IssueProposal.query.filter_by(issue_id=issue.id).count() + len(issue.proposals)
        click.echo(f'  #{issue.id:<5} {str(issue.kind):<7} "{issue.title}"  ({posts} berichten, {proposals} voorstellen)')
    if not execute:
        click.echo('\nDry-run: er is niets verwijderd. Voeg --execute toe om deze te verwijderen.')
        return
    if not yes and not click.confirm(f'\n{len(found)} vraagstuk(ken) met alles eraan definitief verwijderen?'):
        click.echo('Afgebroken, niets verwijderd.')
        return
    for issue in found:
        _remove_issue(db, issue)
    click.echo(f'{len(found)} vraagstuk(ken) verwijderd.')


@click.command('voorstellen-tick')
def proposals_tick():
    """Laat publieke voorstellen waarvan de huidige fase is afgelopen
    doorschuiven naar de volgende fase. Idempotent; veilig om vaak te draaien
    (de echte periodieke uitvoering volgt in stap 5)."""
    from app import proposals
    moved = proposals.advance_due()
    click.echo(f'{moved} voorstel(len) doorgeschoven.')


def _plan():
    from app import ADMIN_EMAIL
    from app.models import Post, PostLike, Thread, User
    from app.routes.forum import DEMO_USER_EMAILS

    demo_emails = {e.lower() for e in DEMO_USER_EMAILS.values()}
    admin_email = ADMIN_EMAIL.strip().lower()
    keep_emails = demo_emails | ({admin_email} if admin_email else set())

    keep_users = (User.query
                  .filter(or_(User.is_admin.is_(True), func.lower(User.email).in_(keep_emails)))
                  .order_by(User.id).all())
    keep_ids = {u.id for u in keep_users}
    delete_users = User.query.filter(~User.id.in_(keep_ids)).order_by(User.id).all() \
        if keep_ids else User.query.order_by(User.id).all()
    delete_user_ids = {u.id for u in delete_users}

    delete_thread_ids = {t.id for t in Thread.query.filter(Thread.is_demo.is_(False))}
    delete_posts = Post.query.filter(or_(
        Post.is_demo.is_(False),
        Post.thread_id.in_(delete_thread_ids) if delete_thread_ids else false(),
    )).all()
    delete_post_ids = {p.id for p in delete_posts}

    kept_posts = Post.query.filter(~Post.id.in_(delete_post_ids)).all() \
        if delete_post_ids else Post.query.all()
    likes_q = PostLike.query.filter(or_(
        PostLike.post_id.in_(delete_post_ids) if delete_post_ids else false(),
        PostLike.user_id.in_(delete_user_ids) if delete_user_ids else false(),
    ))

    return {
        'admin_email': admin_email,
        'demo_emails': demo_emails,
        'keep_users': keep_users,
        'delete_users': delete_users,
        'delete_user_ids': delete_user_ids,
        'delete_thread_ids': delete_thread_ids,
        'delete_posts': delete_posts,
        'delete_post_ids': delete_post_ids,
        # Behouden berichten die naar iets verwijderds wijzen (hoort niet voor
        # te komen, maar dan breken we de koppeling in plaats van te falen).
        'kept_orphans': [p for p in kept_posts if p.parent_id in delete_post_ids],
        'kept_by_deleted_user': [p for p in kept_posts if p.user_id in delete_user_ids],
        'likes_count': likes_q.count(),
        'likes_q': likes_q,
    }


def _report(plan):
    keep = plan['keep_users']
    posts = plan['delete_posts']
    admin_ids = {u.id for u in keep if u.is_admin}

    click.echo('\nBEHOUDEN accounts (keep-set):')
    for u in keep:
        why = []
        if u.is_admin:
            why.append('admin (is_admin)')
        if plan['admin_email'] and u.email.lower() == plan['admin_email']:
            why.append('ADMIN_EMAIL')
        if u.email.lower() in plan['demo_emails']:
            why.append('demo (_seed_forum)')
        click.echo(f'  #{u.id:<5} {u.email:<45} @{u.username:<20} {", ".join(why)}')
    if not plan['admin_email']:
        click.echo('  (ADMIN_EMAIL niet gezet: admin herkend via is_admin)')
    missing_demo = plan['demo_emails'] - {u.email.lower() for u in keep}
    if missing_demo:
        click.echo(f'  Let op: demo-accounts niet gevonden: {", ".join(sorted(missing_demo))}')

    mods = [u for u in plan['delete_users'] if u.is_moderator]
    click.echo('\nTE VERWIJDEREN:')
    click.echo(f'  gebruikers:              {len(plan["delete_users"])}'
               + (f'  (waarvan moderator: {", ".join(u.email for u in mods)})' if mods else ''))
    click.echo(f'  draden (niet-demo):      {len(plan["delete_thread_ids"])}')
    click.echo(f'  berichten totaal:        {len(posts)}')
    click.echo(f'    openingsberichten:     {sum(1 for p in posts if p.is_op)}')
    click.echo(f'    reacties:              {sum(1 for p in posts if p.parent_id is not None)}')
    click.echo(f'    overige (hoofdniveau): {sum(1 for p in posts if p.parent_id is None and not p.is_op)}')
    click.echo(f'    in demo-draden:        {sum(1 for p in posts if p.thread_id not in plan["delete_thread_ids"])}')
    click.echo(f'    van de admin:          {sum(1 for p in posts if p.user_id in admin_ids)}')
    click.echo(f'  eens/oneens:             {plan["likes_count"]}')
    click.echo(f'  berichten met upload:    {sum(1 for p in posts if p.image_url)}'
               '  (bestanden op de server blijven staan)')
    if plan['kept_orphans'] or plan['kept_by_deleted_user']:
        click.echo(f'  koppelingen losgemaakt:  {len(plan["kept_orphans"])} ouder, '
                   f'{len(plan["kept_by_deleted_user"])} auteur (bij behouden berichten)')


def _execute(plan):
    from app import db
    from app.models import Post, Thread, User

    post_ids = plan['delete_post_ids']
    user_ids = plan['delete_user_ids']
    thread_ids = plan['delete_thread_ids']
    try:
        # Volgorde volgens de foreign keys: likes, dan berichten (eerst de
        # self-referentie parent_id los), dan draden, dan gebruikers.
        plan['likes_q'].delete(synchronize_session=False)
        # Kwaliteit (fase C1): beoordelingen en meldingen van verwijderde
        # bijdragen weg, verwijderde gebruikers los als melder/beslisser.
        from app import quality
        quality.delete_for_posts(post_ids)
        for uid in user_ids:
            quality.detach_user(uid)
        if post_ids:
            Post.query.filter(or_(Post.id.in_(post_ids), Post.parent_id.in_(post_ids))) \
                .update({Post.parent_id: None}, synchronize_session=False)
        if user_ids:
            Post.query.filter(Post.user_id.in_(user_ids)) \
                .update({Post.user_id: None}, synchronize_session=False)
        if post_ids:
            Post.query.filter(Post.id.in_(post_ids)).delete(synchronize_session=False)
        if thread_ids:
            from app import proposals
            proposals.delete_for_threads(thread_ids)  # fase E: voorstellen bij die gesprekken
            from app import content_i18n
            content_i18n.delete_for('threads', thread_ids)   # a bulk delete passes the ORM cleanup
            Thread.query.filter(Thread.id.in_(thread_ids)).delete(synchronize_session=False)
        if user_ids:
            User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)

        # Controle vóór de commit: klopt het resultaat niet, dan rollback.
        problems = []
        if User.query.count() != len(plan['keep_users']):
            problems.append('aantal gebruikers klopt niet')
        if Thread.query.filter(Thread.is_demo.is_(False)).count():
            problems.append('niet-demo-draden over')
        if Post.query.filter(Post.is_demo.is_(False)).count():
            problems.append('niet-demo-berichten over')
        if problems:
            raise RuntimeError('Controle mislukt, teruggedraaid: ' + ', '.join(problems))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


@click.command('reset-content')
@click.option('--execute', is_flag=True, help='Echt verwijderen. Zonder deze vlag: alleen een dry-run.')
@click.option('--yes', is_flag=True, help='Sla de bevestigingsvraag over (alleen met --execute).')
@click.option('--list-users', is_flag=True, help='Toon ook elk te verwijderen account.')
def reset_content(execute, yes, list_users):
    """Verwijder alle gebruikers en content behalve admin en demo."""
    from app import db

    url = db.engine.url
    click.echo(f'Database: {url.render_as_string(hide_password=True)}')
    plan = _plan()
    _report(plan)
    if list_users:
        click.echo('\nTe verwijderen accounts:')
        for u in plan['delete_users']:
            click.echo(f'  #{u.id:<5} {u.email:<45} @{u.username}')

    if not any(u.is_admin for u in plan['keep_users']):
        raise click.ClickException('Geen adminaccount in de keep-set; er wordt niets verwijderd.')

    if not execute:
        click.echo('\nDRY-RUN: er is niets verwijderd. Gebruik --execute om echt te verwijderen.')
        return

    if not yes:
        expected = url.database or 'database'
        typed = click.prompt(f'\nDit is onomkeerbaar. Typ de databasenaam ({expected}) om door te gaan')
        if typed != expected:
            raise click.ClickException('Bevestiging klopt niet; er is niets verwijderd.')

    _execute(plan)
    click.echo('\nKlaar: verwijderd en gecommit.')


@click.command('achtergrondcijfers-proef')
@click.argument('issue_id', type=int)
def background_figures_trial(issue_id):
    """Background figures (step 1d): the selection round for one issue, for
    developers, WITHOUT saving anything. Needs a real ANTHROPIC_API_KEY; calls
    the AI twice and the Search API and grapher of Our World in Data. Prints
    the search terms, the candidates, the AI's answer and what the checks
    accept or drop (with the reason)."""
    import json as _json
    from app import background_figures, db
    from app.models import Issue
    issue = db.session.get(Issue, issue_id)
    if issue is None:
        raise click.ClickException(f'No issue {issue_id}.')
    click.echo(f'Issue {issue.id}: {issue.title} (phase {issue.phase})')
    ctx = background_figures.gather(issue)
    click.echo(f'Region: {ctx.region}; named: {ctx.named or "none"}; fixed set: {ctx.fixed}; '
               f'allowed: {ctx.allowed}')
    outcome = background_figures.dry_run(issue)
    click.echo(f'\nSearch terms: {outcome.terms}')
    click.echo(f'Candidates ({len(outcome.candidates)}):')
    for hit in outcome.candidates:
        click.echo(f'  {hit.slug}: {hit.title}')
    click.echo('\nAnswer of the AI:')
    click.echo(_json.dumps(outcome.answer, indent=2, ensure_ascii=False) if outcome.answer else '(none)')
    if outcome.retried:
        click.echo('\nRetry (every chart showed several measures), answer:')
        click.echo(_json.dumps(outcome.retry_answer, indent=2, ensure_ascii=False))
    click.echo('\nAccepted:')
    for item in outcome.accepted:
        names = [item.parsed.names[k] for k in item.keys]
        average = item.parsed.names.get(item.average_key) if item.average_key else None
        click.echo(f'  {item.slug} (compare {item.compare}): {names}, average {average}, '
                   f'{item.year_from} to {item.year_to}, unit {item.parsed.unit or item.parsed.short_unit!r}, '
                   f'proposals {item.proposal_ids}\n    {item.reason}')
    for post_id, pid in outcome.links:
        click.echo(f'  link: fact {post_id} to proposal {pid}')
    click.echo('\nDropped or noted:')
    for note in outcome.notes or ['(nothing)']:
        click.echo(f'  {note}')
    if outcome.none_reason:
        click.echo(f'\nNone reason: {outcome.none_reason}')
    click.echo(f'\nStatus it would get: {outcome.status}. Nothing was saved.')


@click.command('eurostat-catalogus-verversen')
def eurostat_catalog_refresh():
    """Background figures (step 3a): fetch Eurostat's table of contents now and
    replace the catalogue of main tables (otherwise the tick does this once a
    week, with BACKGROUND_FIGURES on). On failure the old catalogue stays."""
    from app.sources import eurostat
    count = eurostat.refresh_catalog()
    if count is None:
        raise click.ClickException('The table of contents could not be fetched or read; the old catalogue stays.')
    click.echo(f'Eurostat catalogue: {count} main tables.')


@click.command('achtergrondcijfers-voorbeeld')
@click.argument('issue_id', type=int)
def background_figures_example(issue_id):
    """Background figures (step 1e): put one chart fact on an issue from the
    fixture in tests/data/owid/ (military spending as a share of GDP), without
    the AI and without the network, to see the card locally. Only with
    BACKGROUND_FIGURES=1. Remove it again as a moderator, or with remove-issue."""
    import os
    from flask import current_app
    from app import background_figures, db
    from app.models import Issue, IssueProposal
    from app.sources import owid
    if not current_app.config.get('BACKGROUND_FIGURES'):
        raise click.ClickException('Set BACKGROUND_FIGURES=1 first.')
    issue = db.session.get(Issue, issue_id)
    if issue is None or issue.thread_id is None:
        raise click.ClickException(f'No issue {issue_id} with a conversation.')
    folder = os.path.join(os.path.dirname(current_app.root_path), 'tests', 'data', 'owid')
    slug = 'military-spending-as-a-share-of-gdp-sipri'
    with open(os.path.join(folder, f'{slug}.csv'), encoding='utf-8') as f:
        csv_text = f.read()
    with open(os.path.join(folder, f'{slug}.metadata.json'), encoding='utf-8') as f:
        metadata = __import__('json').load(f)
    parsed = owid.parse(csv_text, metadata, slug=slug)
    # An average, as some OWID charts supply one: the fixture has none, so the
    # example borrows the series of Spain under the key of the EU (example only).
    parsed.series['OWID_EU27'] = dict(parsed.series['ESP'])
    parsed.names['OWID_EU27'] = 'European Union (27)'
    proposals = IssueProposal.query.filter_by(issue_id=issue.id, is_final=False).order_by(IssueProposal.id).all()
    item = background_figures.Accepted(
        slug=slug, parsed=parsed, metadata=metadata, keys=['POL', 'FRA', 'DEU', 'NLD'], average_key='OWID_EU27',
        year_from=2016, year_to=2025, proposal_ids=[p.id for p in proposals[:2]],
        reason='The chart shows how much each country spends on its armed forces compared with the size of '
               'its economy, which helps to see how far apart European countries are.')
    ctx = background_figures.Context(issue=issue)
    outcome = background_figures.Outcome(status='done', accepted=[item])
    background_figures.publish(ctx, outcome)
    db.session.commit()
    click.echo(f'Chart fact added to issue {issue.id} (example data; the average is made up for the example).')


@click.command('vertalingen-controleren')
@click.option('--verwijderen', is_flag=True, help='Remove the bad machine translations (the route makes them again).')
def check_translations(verwijderen):
    """Check every stored translation with the checks of the translation route
    (quality.translation_problem: an empty answer, a reply instead of a
    translation, a heading or quote in a short field, far too long). Prints
    table, id, field, language and the start of the text per case. With
    --verwijderen the bad MACHINE translations are removed, so the background
    job and the hourly tick make them again; handwritten ones are only shown."""
    from app import content_i18n, db
    bad = content_i18n.bad_translations()
    removed = 0
    for row, problem in bad:
        text = (row.text or '').replace('\n', ' ')[:70]
        hand = '' if row.is_machine else '  (handwritten, kept)'
        click.echo(f'{row.object_type:<24} {row.object_id:>6} {row.field:<14} {row.lang:<3} {problem}: {text!r}{hand}')
        if verwijderen and row.is_machine:
            db.session.delete(row)
            removed += 1
    if verwijderen:
        db.session.commit()
    click.echo(f'{len(bad)} bad translation(s) found' + (f', {removed} removed.' if verwijderen else '.'))
