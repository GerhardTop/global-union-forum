"""
Persistente rate-limit-opslag voor Flask-Limiter, bovenop de bestaande
Postgres/Neon-database — in plaats van de standaard in-memory teller.

Aanleiding: in-memory tellers leven in het procesgeheugen van één gunicorn-
worker. Elke container-herstart op Render (redeploy, OOM, health-check-
timeout tijdens een Neon-cold-start) reset ze stilzwijgend naar 0 — zowel de
per-IP-limiet als de globale 'vangnet'-limiet op /uitnodiging (zie
app/routes/social.py) zijn daardoor in productie omzeilbaar gebleken, niet
alleen die ene route.

`limits` (de bibliotheek waar Flask-Limiter zijn opslag-backends van leent)
heeft geen ingebouwde SQL-backend — alleen memory/memcached/mongodb/
redis(-cluster/-sentinel). Een aparte Redis- of Mongo-instantie erbij zetten
is voor deze schaal onnodige infrastructuur; de app heeft al een Postgres-
database, dus die hergebruiken we via een eigen minimale Storage-klasse.

Registratie gebeurt automatisch bij het *importeren* van deze module: de
Storage-metaclass in `limits` registreert elke subklasse onder de schema's
in STORAGE_SCHEME. Zolang dit bestand vóór `limiter.init_app(app)`
geïmporteerd is (zie app/__init__.py), herkent flask-limiter's
`storage_from_string()` een `postgresql://`- of `sqlite://`-
RATELIMIT_STORAGE_URI en gebruikt hij deze klasse i.p.v. in-memory.

Eigen, aparte SQLAlchemy-engine (niet Flask-SQLAlchemy's db.engine): de
limiter-check draait vóór/los van de request-gebonden ORM-sessie en moet ook
buiten een request-context kunnen werken (bv. de check()-health-check) —
een losstaande, klein gehouden connection pool voorkomt verrassingen met
Flask-SQLAlchemy's sessiebeheer.

FAIL-OPEN bij een onbereikbare database (bewuste keuze, geen vergissing):
als de rate-limit-tabel niet te bereiken is (Neon cold start, netwerkhik),
laat incr()/get()/get_expiry() het verzoek gewoon door i.p.v. de fout op te
gooien. Fail-closed zou hier erger zijn dan het probleem dat deze module
oplost: één database-hikje zou dan de hele site blokkeren voor elke
bezoeker, terwijl het ergste gevolg van fail-open een kort venster is
waarin misbruik-bescherming tijdelijk niet afgedwongen wordt. Elke keer dat
dit gebeurt, loggen we een duidelijke `[RATE-LIMIT]`-regel op ERROR-niveau,
zodat een aanhoudende storing zichtbaar blijft i.p.v. stil weg te vallen.
Dezelfde afweging geldt voor het aanmaken van de tabel in __init__: lukt dat
niet bij het opstarten van een container, dan mag de hele app daar niet op
stuklopen — zie de try/except daar.
"""
from __future__ import annotations

import logging
import random
import time

import sqlalchemy as sa
from limits.storage.base import Storage

_logger = logging.getLogger(__name__)

_TABLE_NAME = "rate_limit_counters"

# Kans dat een incr()-aanroep ook meteen verlopen rijen opruimt, i.p.v. bij
# élke aanroep te vegen (dat zou onnodige DB-load geven op het hot path van
# elke rate-limited request). 1 op de 500 is vaak genoeg om de tabel niet te
# laten groeien — elke actieve sleutel (IP, of de vaste globale sleutel)
# ruimt zichzelf toch al op via de ON CONFLICT-upsert hieronder zodra hij
# opnieuw geraakt wordt; dit vangt alleen sleutels die nooit meer terugkomen
# (bv. een IP dat na één bot-poging nooit meer langskomt).
_CLEANUP_PROBABILITY = 1 / 500

_SUPPORTED_DIALECTS = ("postgresql", "sqlite")


class SQLStorage(Storage):
    """
    Fixed-window rate-limit-teller in een SQL-tabel i.p.v. in-memory.
    Overleeft een container-herstart/redeploy, en werkt (i.t.t. in-memory)
    ook correct zodra er ooit meer dan één worker/instance draait, omdat
    alle processen dezelfde database delen i.p.v. hun eigen procesgeheugen.

    Ondersteunt alleen postgresql/sqlite (de twee dialecten die deze app
    daadwerkelijk gebruikt: Neon in productie, een sqlite-bestand in de
    persistentie-test) — geen MySQL, want dat is hier alleen een lokale
    dev-fallback zonder DATABASE_URL, waar RATELIMIT_STORAGE_URI toch al op
    'memory://' blijft staan (zie config.py).
    """

    STORAGE_SCHEME = ["postgresql", "postgres", "sqlite"]

    def __init__(self, uri: str, wrap_exceptions: bool = False, **options):
        super().__init__(uri, wrap_exceptions=wrap_exceptions, **options)
        # pool_pre_ping/pool_recycle: zelfde reden als
        # config.py:SQLALCHEMY_ENGINE_OPTIONS — Neon kan een idle connectie
        # laten vallen. Kleine pool: dit is een laag-frequente side-car naast
        # de hoofd-DB-pool, geen reden om evenveel connecties te reserveren.
        #
        # pool_size/max_overflow alleen buiten sqlite-':memory:' meegeven:
        # SQLAlchemy gebruikt daar SingletonThreadPool (één levenslange
        # connectie, want een aparte connectie per checkout zou een lege
        # database zien) i.p.v. QueuePool, en die pool-klasse accepteert
        # deze kwargs niet. Productie (Postgres) en de sqlite-bestand-variant
        # (persistentie-test) gebruiken gewoon QueuePool en krijgen ze wel.
        engine_kwargs = {"pool_pre_ping": True, "pool_recycle": 280}
        parsed = sa.engine.make_url(uri)
        is_sqlite_memory = parsed.drivername.startswith("sqlite") and parsed.database in (
            None, "", ":memory:",
        )
        if not is_sqlite_memory:
            engine_kwargs["pool_size"] = 2
            engine_kwargs["max_overflow"] = 2
        self._engine = sa.create_engine(uri, **engine_kwargs)
        if self._engine.dialect.name not in _SUPPORTED_DIALECTS:
            raise ValueError(
                f"SQLStorage ondersteunt dialect {self._engine.dialect.name!r} niet "
                f"(alleen {', '.join(_SUPPORTED_DIALECTS)})"
            )
        metadata = sa.MetaData()
        self._table = sa.Table(
            _TABLE_NAME,
            metadata,
            sa.Column("limiter_key", sa.String(255), primary_key=True),
            sa.Column("counter", sa.Integer, nullable=False),
            # Index op expires_at (niet alleen de PK op limiter_key) zodat de
            # opruim-DELETE hieronder een indexscan gebruikt i.p.v. de hele
            # tabel te doorlopen.
            sa.Column("expires_at", sa.Float, nullable=False, index=True),
        )
        try:
            with self._engine.begin() as conn:
                # if_not_exists=True i.p.v. checkfirst: dat laatste doet
                # inspecteren-dan-aanmaken als twee losse stappen, wat bij een
                # gelijktijdige opstart van meerdere workers/instances kan
                # racen ("tabel bestaat al"-fout). IF NOT EXISTS is atomair
                # op de DB.
                conn.execute(sa.schema.CreateTable(self._table, if_not_exists=True))
                for index in self._table.indexes:
                    conn.execute(sa.schema.CreateIndex(index, if_not_exists=True))
        except sa.exc.SQLAlchemyError as exc:
            # Zelfde fail-open-afweging als in incr()/get() hieronder, maar
            # dan voor het allereerste moment: als de DB al onbereikbaar is
            # tijdens het opstarten van de container (bv. Neon nog niet
            # wakker), mag de hele app daar niet op stuklopen. De tabel
            # ontbreekt dan gewoon nog — elke incr()/get() daarna faalt op
            # "tabel bestaat niet" en valt terug op diezelfde fail-open-paden
            # (met logging), tot de eerstvolgende herstart het opnieuw
            # probeert. Geen retry-logica hier: dit proces krijgt sowieso
            # vanzelf een nieuwe kans bij de volgende deploy/herstart.
            _logger.error(
                "[RATE-LIMIT] kon %s-tabel niet aanmaken bij opstarten "
                "(DB onbereikbaar?) - rate limiting valt terug op fail-open "
                "tot de volgende herstart: %s",
                _TABLE_NAME, exc,
            )

    @property
    def base_exceptions(self):
        return sa.exc.SQLAlchemyError

    def _upsert_stmt(self, key: str, amount: int, now: float, new_expiry: float):
        dialect = self._engine.dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as _insert
        else:
            from sqlalchemy.dialects.sqlite import insert as _insert

        # Eén round-trip, atomair: als de rij niet bestaat (of verlopen is),
        # begin een nieuw venster met counter=amount; anders tel amount bij
        # de bestaande counter op en laat expires_at ongewijzigd (fixed-
        # window — het venster schuift niet mee). RETURNING geeft de nieuwe
        # stand direct terug, geen aparte SELECT nodig.
        expired = self._table.c.expires_at <= now
        return (
            _insert(self._table)
            .values(limiter_key=key, counter=amount, expires_at=new_expiry)
            .on_conflict_do_update(
                index_elements=[self._table.c.limiter_key],
                set_={
                    "counter": sa.case(
                        (expired, amount), else_=self._table.c.counter + amount
                    ),
                    "expires_at": sa.case(
                        (expired, new_expiry), else_=self._table.c.expires_at
                    ),
                },
            )
            .returning(self._table.c.counter)
        )

    def incr(self, key: str, expiry: int, amount: int = 1) -> int:
        now = time.time()
        stmt = self._upsert_stmt(key, amount, now, now + expiry)
        try:
            with self._engine.begin() as conn:
                count = conn.execute(stmt).scalar_one()
                if random.random() < _CLEANUP_PROBABILITY:
                    conn.execute(
                        sa.delete(self._table).where(self._table.c.expires_at < now)
                    )
            return count
        except sa.exc.SQLAlchemyError as exc:
            # FAIL-OPEN (zie moduledocstring): 0 teruggeven laat dit verzoek
            # altijd door (hit() vergelijkt met '<= limiet'), zonder dat er
            # ooit iets fouts naar de echte teller geschreven wordt zodra de
            # DB terug is — we hebben simpelweg niets geschreven.
            _logger.error(
                "[RATE-LIMIT] DB onbereikbaar bij incr(%s) - verzoek "
                "doorgelaten zonder limiet: %s", key, exc,
            )
            return 0

    def get(self, key: str) -> int:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    sa.select(self._table.c.counter, self._table.c.expires_at).where(
                        self._table.c.limiter_key == key
                    )
                ).first()
        except sa.exc.SQLAlchemyError as exc:
            # Zelfde fail-open-afweging als incr(): "0 hits" i.p.v. de fout
            # opgooien, zodat een test()/deduct_when-check niet alsnog de
            # site blokkeert op een onbereikbare DB.
            _logger.error(
                "[RATE-LIMIT] DB onbereikbaar bij get(%s) - meldt 0: %s", key, exc,
            )
            return 0
        if row is None or row.expires_at <= time.time():
            return 0
        return row.counter

    def get_expiry(self, key: str) -> float:
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    sa.select(self._table.c.expires_at).where(
                        self._table.c.limiter_key == key
                    )
                ).first()
        except sa.exc.SQLAlchemyError as exc:
            # Alleen gebruikt voor rate-limit-headers/diagnostiek, nooit voor
            # de allow/deny-beslissing zelf — 'nu' (dus 'niet verlopen') is
            # hier een onschuldige fail-open-waarde.
            _logger.error(
                "[RATE-LIMIT] DB onbereikbaar bij get_expiry(%s): %s", key, exc,
            )
            return time.time()
        return row.expires_at if row is not None else time.time()

    def check(self) -> bool:
        try:
            with self._engine.connect() as conn:
                conn.execute(sa.select(1))
            return True
        except sa.exc.SQLAlchemyError:
            return False

    def reset(self) -> int | None:
        with self._engine.begin() as conn:
            return conn.execute(sa.delete(self._table)).rowcount

    def clear(self, key: str) -> None:
        with self._engine.begin() as conn:
            conn.execute(sa.delete(self._table).where(self._table.c.limiter_key == key))
