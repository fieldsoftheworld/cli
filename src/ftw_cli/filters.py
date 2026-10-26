"""Field filters shared by every command that reads or writes fields."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Filters:
    """Keep fields within an area range (m²) and above a model score (0-100)."""

    min_area: float | None = None
    max_area: float | None = None
    min_score: float | None = None

    def sql(self, area: str = '"metrics:area"') -> str:
        """A SQL predicate over the ``area`` and ``score`` columns; ``true`` when unset."""
        terms = []
        if self.min_area is not None:
            terms.append(f"{area} >= {float(self.min_area)}")
        if self.max_area is not None:
            terms.append(f"{area} <= {float(self.max_area)}")
        if self.min_score is not None:
            terms.append(f"score >= {float(self.min_score)}")
        return " and ".join(terms) or "true"


NO_FILTERS = Filters()
