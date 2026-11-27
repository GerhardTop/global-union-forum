"""
Interne endpoints voor periodieke taken (fase E, stap 5b).

POST /intern/tik draait elk uur (GitHub Actions, .github/workflows/tik.yml):
de faseovergangen van publieke voorstellen en de dagelijkse samenvatting, en
het vangnet voor de vertalingen van vraagstukken, voorstellen en feiten
(content_jobs.backfill; het aantal gedaan en mislukt staat in de uitvoer).
Alleen met het juiste geheim: de omgevingsvariabele TICK_SECRET, meegestuurd
als "Authorization: Bearer <geheim>". Is TICK_SECRET niet gezet, dan staat
het endpoint uit (403), zodat het nooit onbeschermd open staat.
"""
import hmac
import os

from flask import Blueprint, abort, jsonify, request

intern = Blueprint('intern', __name__)


def _authorized():
    secret = os.environ.get('TICK_SECRET', '')
    if not secret:
        return False
    header = request.headers.get('Authorization', '')
    expected = f'Bearer {secret}'
    return hmac.compare_digest(header.encode('utf-8'), expected.encode('utf-8'))


@intern.route('/intern/tik', methods=['POST'])
def tik():
    if not _authorized():
        abort(403)
    from app import content_jobs, digest
    result = digest.tick()
    # Multilingual phase 2: the safety net for the content translated right
    # away (what the background thread missed). Skipped while the backlog run
    # of /admin/vertalingen is busy: it does the same work.
    if content_jobs.backlog_running():
        result['translations'] = 0
        result['translations_failed'] = 0
        result['translations_skipped'] = 'backlog run busy'
    else:
        tally = content_jobs.Tally()
        result['translations'] = content_jobs.backfill(tally=tally)
        tally.log('tick')
        result['translations_failed'] = tally.failed
    # Background figures: selection rounds that waited or failed (behind
    # BACKGROUND_FIGURES; with the switch off this does nothing).
    from app import background_figures
    result['charts'], result['charts_failed'] = background_figures.backfill()
    # Step 3a: the catalogue of Eurostat's main tables, once a week.
    result['eurostat_catalog'] = background_figures.refresh_eurostat_catalog()
    return jsonify(result)
