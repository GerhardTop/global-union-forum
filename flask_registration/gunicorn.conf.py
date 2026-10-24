"""
Gunicorn-instellingen. Gunicorn laadt dit bestand vanzelf als het vanuit
deze map start (zoals `gunicorn run:app` in de Procfile); de Procfile en
render.yaml geven het ook expliciet mee met -c.

De app wacht vooral op de database (Neon), niet op de CPU. Daarom de
gthread-worker: meerdere threads per worker, zodat gelijktijdige bezoekers
niet op elkaar wachten terwijl een verzoek op de database wacht. Twee workers
van ongeveer 115 MB passen ruim in 512 MB. Overschrijfbaar via
WEB_CONCURRENCY en GUNICORN_THREADS in Render.

preload_app: de app (en dus de opstartmigraties en het seeden in
create_app) laadt één keer in het hoofdproces, voordat de workers forken.
Zonder dit zouden twee workers tegelijk migreren en elkaar in de weg kunnen
zitten. Na het forken krijgt elke worker eigen databaseverbindingen
(post_fork): een verbinding van voor de fork mag niet gedeeld worden.

De poort komt uit $PORT (standaardgedrag van gunicorn, gezet door Render).
"""
import os

worker_class = 'gthread'
workers = int(os.environ.get('WEB_CONCURRENCY', '2'))
threads = int(os.environ.get('GUNICORN_THREADS', '4'))
preload_app = True


def post_fork(server, worker):
    from app import db, limiter
    from run import app

    with app.app_context():
        # close=False: de verbindingen van het hoofdproces niet sluiten (die
        # zijn van het hoofdproces), alleen niet meer gebruiken in deze worker.
        db.engine.dispose(close=False)
        engine = getattr(limiter.storage, '_engine', None) if limiter.enabled else None
        if engine is not None:
            engine.dispose(close=False)
