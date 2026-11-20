"""The review file of a machine-translated language (i18n_review.py): export
for a native speaker, and corrections back into the catalog, on a copy of the
catalogs in a temporary folder."""
import csv
import gettext
import os
import shutil

import pytest

import i18n_review

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANG = 'de'


@pytest.fixture
def root(tmp_path):
    for lang in (LANG, 'nl'):
        src = os.path.join(ROOT, 'app', 'translations', lang, 'LC_MESSAGES')
        shutil.copytree(src, tmp_path / 'app' / 'translations' / lang / 'LC_MESSAGES')
    return str(tmp_path)


def _rows(path):
    with open(path, encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def _write(path, rows):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=i18n_review.FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _po_bytes(root):
    with open(i18n_review.catalog_path(LANG, root), 'rb') as f:
        return f.read()


def test_the_committed_review_file_is_current():
    """docs/i18n/review_de.csv matches the catalog (re-export after a change)."""
    assert _rows(i18n_review.review_path(LANG, ROOT)) == [
        {k: v for k, v in r.items()} for r in i18n_review.rows(LANG, ROOT)]


def test_manifest_and_ground_rules_come_first(root):
    rows = _rows(i18n_review.export(LANG, root))
    files = [r['source'] for r in rows]
    first_other = next(i for i, s in enumerate(files)
                       if not any(n in s for n in ('manifest.html', 'spelregels.html', '_rules_list.html')))
    assert 'manifest.html' in files[0]
    assert not any('manifest.html' in s or 'spelregels.html' in s for s in files[first_other:])


def test_an_unchanged_review_file_changes_nothing(root):
    path = i18n_review.export(LANG, root)
    before = _po_bytes(root)
    assert i18n_review.apply(LANG, root, path) == 0
    assert _po_bytes(root) == before


def test_a_correction_goes_into_the_po_and_the_mo(root):
    path = i18n_review.export(LANG, root)
    rows = _rows(path)
    row = next(r for r in rows if r['en'] == 'Read the manifest')
    row['translation'] = 'Manifest lesen'
    plural = next(r for r in rows if r['en'] == '%(num)d proposal')
    plural['translation_plural'] = '%(num)d Vorschläge insgesamt'
    _write(path, rows)
    assert i18n_review.apply(LANG, root, path) == 2
    with open(i18n_review.catalog_path(LANG, root)[:-3] + '.mo', 'rb') as f:
        mo = gettext.GNUTranslations(f)
    assert mo.gettext('Read the manifest') == 'Manifest lesen'
    assert mo.ngettext('%(num)d proposal', '%(num)d proposals', 3) == '%(num)d Vorschläge insgesamt'


@pytest.mark.parametrize('bad', ['Hallo %(nom)s,', 'Hallo, Welt – du', ''])
def test_a_broken_correction_writes_nothing(root, bad):
    path = i18n_review.export(LANG, root)
    rows = _rows(path)
    next(r for r in rows if r['en'] == 'Hi %(name)s,')['translation'] = bad
    _write(path, rows)
    before = _po_bytes(root)
    with pytest.raises(ValueError):
        i18n_review.apply(LANG, root, path)
    assert _po_bytes(root) == before
