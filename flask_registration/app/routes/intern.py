"""
Interne endpoints voor periodieke taken (fase E, stap 5b).

POST /intern/tik draait elk uur (GitHub Actions, .github/workflows/tik.yml):
de faseovergangen van publieke voorstellen en de dagelijkse samenvatting.
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
    from app import digest
    return jsonify(digest.tick())
