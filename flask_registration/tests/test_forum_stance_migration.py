"""
Forum fase B: de migratie van likes naar eens/oneens (_migrate_stance via
_migrate_columns). Bestaande likes (zonder richting) worden 'eens'; een
herhaalde start verandert een gekozen 'oneens' niet.

Gedeelde conftest-app (SQLite-bestand): de kolom wordt gedropt om het oude
schema na te bootsen, daarna draait de volledige _migrate_columns().
"""
from sqlalchemy import text

from app import bcrypt
from app.models import Post, PostLike, Thread, User


def test_existing_likes_become_agree_and_rerun_is_safe(app, db):
    from app import _migrate_columns
    users = []
    for n in ('mig_a', 'mig_b'):
        u = User(username=n, email=f'{n}@example.com', verified=True,
                 password_hash=bcrypt.generate_password_hash('Sterk1!ww').decode('utf-8'))
        db.session.add(u)
        users.append(u)
    t = Thread(eyebrow_nl='M', eyebrow_en='M', title_prefix_nl='Mig', title_prefix_en='Mig')
    db.session.add(t)
    db.session.flush()
    post = Post(thread_id=t.id, author_name='', author_username='x', body='b')
    db.session.add(post)
    db.session.commit()
    ids = {'p': post.id, 't': t.id, 'a': users[0].id, 'b': users[1].id}
    db.session.close()

    try:
        # Oud schema: post_likes zonder richting, met twee oude likes.
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE post_likes DROP COLUMN stance'))
            for uid in (ids['a'], ids['b']):
                conn.execute(text('INSERT INTO post_likes (post_id, user_id) VALUES (:p, :u)'),
                             {'p': ids['p'], 'u': uid})
            conn.commit()
        db.engine.dispose()  # SQLite: verse verbindingen na DROP COLUMN

        _migrate_columns()
        rows = db.session.execute(text('SELECT user_id, stance FROM post_likes WHERE post_id = :p ORDER BY user_id'),
                                  {'p': ids['p']}).all()
        assert [r[1] for r in rows] == ['eens', 'eens']

        # Iemand kiest daarna oneens; een volgende start laat dat staan.
        db.session.execute(text("UPDATE post_likes SET stance = 'oneens' WHERE user_id = :b"), {'b': ids['b']})
        db.session.commit()
        _migrate_columns()
        rows = dict(db.session.execute(text('SELECT user_id, stance FROM post_likes WHERE post_id = :p'),
                                       {'p': ids['p']}).all())
        assert rows == {ids['a']: 'eens', ids['b']: 'oneens'}
    finally:
        db.session.execute(text('DELETE FROM post_likes WHERE post_id = :p'), {'p': ids['p']})
        db.session.execute(text('DELETE FROM posts WHERE id = :p'), {'p': ids['p']})
        db.session.execute(text('DELETE FROM threads WHERE id = :t'), {'t': ids['t']})
        db.session.execute(text('DELETE FROM users WHERE id IN (:a, :b)'), {'a': ids['a'], 'b': ids['b']})
        db.session.commit()
