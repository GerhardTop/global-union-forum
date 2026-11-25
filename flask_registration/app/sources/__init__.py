"""Background figures (docs/achtergrondcijfers.md): the sources of the
figures. Every source is its own adapter (app/sources/owid.py first). An
adapter fetches on the server, never from a page; the AI only chooses."""


class SourceError(Exception):
    """A source could not deliver a usable chart. reason is one of REASONS."""

    REASONS = ('invalid_slug', 'not_found', 'forbidden', 'network', 'unsupported', 'no_data')

    def __init__(self, reason, detail=''):
        assert reason in self.REASONS, reason
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason
        self.detail = detail
