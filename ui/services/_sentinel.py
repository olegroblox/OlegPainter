"""Shared sentinel object used by PainterService and its mixins.

`_UNSET` is the "value-not-provided" marker for keyword args where `None`
is a meaningful payload distinct from "do not touch this attribute".
"""

_UNSET = object()
