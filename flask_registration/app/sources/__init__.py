"""Background figures (docs/achtergrondcijfers.md): the sources of the
figures. Every source is its own adapter (app/sources/owid.py first,
app/sources/eurostat.py second). An
adapter fetches on the server, never from a page; the AI only chooses."""


class SourceError(Exception):
    """A source could not deliver a usable chart. reason is one of REASONS."""

    # needs_choice (step 3a, Eurostat): a dimension has more than one value; the
    # choice per dimension follows in step 3b (eurostat.NeedsChoice has them).
    REASONS = ('invalid_slug', 'not_found', 'forbidden', 'network', 'unsupported', 'no_data', 'needs_choice')

    def __init__(self, reason, detail=''):
        assert reason in self.REASONS, reason
        super().__init__(f'{reason}: {detail}' if detail else reason)
        self.reason = reason
        self.detail = detail
