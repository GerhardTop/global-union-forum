"""
Beheer-commando's voor de flask CLI.

    flask --app run reset-content              # dry-run: toont alleen wat er zou gebeuren
    flask --app run reset-content --list-users # idem, plus de te verwijderen accounts
    flask --app run reset-content --execute    # verwijdert echt (vraagt om bevestiging)

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
