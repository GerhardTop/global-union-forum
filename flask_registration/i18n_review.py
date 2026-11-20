"""The review file of a machine-translated language: export it for a native
speaker, and put their corrections back into the catalog.

    python i18n_review.py export de    # writes docs/i18n/review_de.csv
    python i18n_review.py apply de     # reads it back into the de catalog

Run from flask_registration/. The CSV has one row per catalog item: where it
comes from (file and translator comment), the context, the English msgid, the
Dutch translation (for the intent and the tone) and the translation to review.
Manifest and ground rules come first. A reviewer only edits the last column(s);
`apply` copies every changed translation into
app/translations/<lang>/LC_MESSAGES/messages.po, checks the placeholders and
the dashes, and compiles the .mo. Then run the full suite and commit both.
"""
import csv
import io
import os
import sys

from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po, write_po

HERE = os.path.dirname(os.path.abspath(__file__))
FIELDS = ['source', 'comment', 'context', 'en', 'en_plural', 'nl', 'nl_plural', 'translation', 'translation_plural']
# Sort order: the manifest first, then the ground and conduct rules, then the rest.
_FIRST = ('manifest.html', 'spelregels.html', '_rules_list.html')
DASHES = ('–', '—')


def catalog_path(lang, root=HERE):
    return os.path.join(root, 'app', 'translations', lang, 'LC_MESSAGES', 'messages.po')


def review_path(lang, root=HERE):
    return os.path.join(root, 'docs', 'i18n', f'review_{lang}.csv')


def _read(path):
    with open(path, 'rb') as f:
        return read_po(f)


def _forms(value):
    if isinstance(value, (list, tuple)):
        return list(value) + [''] * (2 - len(value))
    return [value or '', '']


def _order(message):
    """(rank, file, line): the manifest, the rules, then the rest, each in page order."""
    for rank, name in enumerate(_FIRST):
        lines = [line for f, line in message.locations if os.path.basename(f) == name]
        if lines:
            return rank, name, min(lines)
    first = message.locations[0] if message.locations else ('', 0)
    return len(_FIRST), first[0], first[1]


def rows(lang, root=HERE):
    """The review rows of a language, in review order."""
    target, dutch = _read(catalog_path(lang, root)), _read(catalog_path('nl', root))
    messages = sorted((m for m in target if m.id), key=_order)
    out = []
    for m in messages:
        nl = dutch.get(m.id, context=m.context)
        en, en_plural = _forms(m.id)
        nl_one, nl_more = _forms(nl.string if nl else '')
        tr_one, tr_more = _forms(m.string)
        out.append({'source': ' '.join(f'{f}:{n}' for f, n in m.locations),
                    'comment': ' '.join(m.auto_comments), 'context': m.context or '',
                    'en': en, 'en_plural': en_plural, 'nl': nl_one, 'nl_plural': nl_more,
                    'translation': tr_one, 'translation_plural': tr_more})
    return out


def export(lang, root=HERE):
    path = review_path(lang, root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows(lang, root))
    return path


def apply(lang, root=HERE, csv_file=None):
    """Copy the reviewed translations into the catalog. Returns the number of
    changed items; raises ValueError (and writes nothing) when a row does not
    match a catalog item, breaks a placeholder or contains a long or short dash."""
    po = catalog_path(lang, root)
    catalog = _read(po)
    with open(csv_file or review_path(lang, root), encoding='utf-8', newline='') as f:
        reviewed = list(csv.DictReader(f))
    changed, errors = 0, []
    for row in reviewed:
        key = row['en'] if not row['en_plural'] else (row['en'], row['en_plural'])
        message = catalog.get(key, context=row['context'] or None)
        if message is None:
            errors.append(f'not in the catalog: {row["en"]!r}')
            continue
        new = (row['translation'], row['translation_plural']) if row['en_plural'] else row['translation']
        texts = new if isinstance(new, tuple) else (new,)
        if not all(t.strip() for t in texts):
            errors.append(f'empty translation: {row["en"]!r}')
            continue
        if any(d in t for t in texts for d in DASHES):
            errors.append(f'long or short dash: {row["en"]!r}')
            continue
        if new != message.string:
            message.string = new
            changed += 1
    for message, problems in catalog.check():
        errors.extend(f'{message.id!r}: {p}' for p in problems)
    if errors:
        raise ValueError('\n'.join(errors))
    with open(po, 'wb') as f:
        write_po(f, catalog, width=None)
    buffer = io.BytesIO()
    write_mo(buffer, catalog)
    with open(po[:-3] + '.mo', 'wb') as f:
        f.write(buffer.getvalue())
    return changed


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] not in ('export', 'apply'):
        sys.exit(__doc__)
    if sys.argv[1] == 'export':
        print('written', export(sys.argv[2]))
    else:
        try:
            print('changed', apply(sys.argv[2]), 'items; run the full suite and commit the .po and .mo')
        except ValueError as exc:
            sys.exit(f'nothing written:\n{exc}')
