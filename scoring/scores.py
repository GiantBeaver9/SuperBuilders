"""Three separated scores, each with a confidence range and a give-up rule.

The PRD demands that "how ready am I?" is not answered by one number. Three
distinct constructs, at three Depth-of-Knowledge levels, each reported as a value
**plus an interval**, and each allowed to **abstain** (return no value) when the
evidence is too thin:

* **Memory (DOK 1)** — can you retrieve the fact right now? FSRS retrievability,
  averaged over a concept's cards. :func:`memory_score`.
* **Performance (DOK 2/3)** — can you apply it to a *novel* item you have never
  seen? Practice novel accuracy (``is_holdout = 0``). :func:`performance_score`.
* **Readiness (DOK 4)** — what exam score does that project to? An exam-weight
  aggregate of per-concept performance, discounted by how much of the exam is
  actually covered by scored concepts. :func:`readiness_score`.

Design rules that do not bend:

* **No SQL math on the live path.** Retrievability (which needs ``pow``) is
  computed in Python by :mod:`gap.mastery`; everything else here is ``COUNT``/
  ``SUM``/``AVG`` (Anki-SQLite-safe) plus Python arithmetic. See
  ``docs/ENGINE.md`` §2.
* **Statistics are implemented here, in stdlib** — a Wilson score interval and a
  normal-approximation interval, no numpy/scipy.
* **Every threshold is a module constant with a docstring.** The give-up rule is
  :data:`PERFORMANCE_MIN_ATTEMPTS` (the pre-registered abstain line) for
  performance and :data:`READINESS_MIN_COVERAGE` for readiness.

Each score is a :class:`Score` — ``value`` is ``None`` exactly when
``abstained`` is ``True``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from gap import mastery

# --------------------------------------------------------------------------- #
# Thresholds — the give-up rule and every constant, each documented.
# --------------------------------------------------------------------------- #
Z_95: float = 1.959963984540054
"""Standard-normal two-sided 95% critical value (Φ⁻¹(0.975)). Hard-coded so no
scipy is needed; drives both the Wilson and the normal-approx intervals."""

PERFORMANCE_MIN_ATTEMPTS: int = 8
"""The pre-registered abstain line (``PREREGISTRATION.md`` traceability row, and
``gap.stats.ABSTAIN_THRESHOLD``). A concept with fewer than this many PRACTICE
novel attempts gets **no** performance score — the dashboard shows coverage
instead. Refusing a number here is the honesty requirement, not a limitation."""

MEMORY_MIN_CARDS: int = 1
"""A memory score needs at least one concept card carrying FSRS state with a
review (one card that :func:`gap.mastery.card_retrievabilities` yields an R for).
With zero such cards there is nothing to average, so memory abstains."""

MEMORY_SINGLE_CARD_HALFWIDTH: float = 0.5
"""Interval half-width used when a concept has exactly one scored card: the
per-card spread is unknown, so the interval is widened to ±0.5 (half the whole
[0,1] range) rather than pretending to a tight estimate."""

READINESS_MIN_COVERAGE: float = 0.5
"""Readiness abstains unless scored concepts account for at least this fraction of
total exam weight. Projecting an exam score from under half its weight is a
guess, not a projection, so the score gives up and reports coverage instead."""


# --------------------------------------------------------------------------- #
# The score container
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Score:
    """One score with its confidence range.

    * ``value``     — point estimate, or ``None`` when abstained.
    * ``lo`` / ``hi`` — inclusive bounds of the confidence interval. Still
      populated when abstained (they widen to the full plausible range), so the
      UI can always draw a band.
    * ``n``         — the evidence count the score rests on (cards for memory,
      novel attempts for performance, scored concepts for readiness).
    * ``abstained`` — ``True`` iff the give-up rule fired; then ``value is None``.

    Units: memory and performance are proportions in ``[0, 1]``; readiness is a
    projected **percent** in ``[0, 100]`` (see :func:`readiness_score`).
    """

    value: float | None
    lo: float
    hi: float
    n: int
    abstained: bool

    def to_dict(self) -> dict:
        """JSON-serializable form (plain floats / int / bool / None)."""
        return {
            "value": self.value,
            "lo": self.lo,
            "hi": self.hi,
            "n": self.n,
            "abstained": self.abstained,
        }


# --------------------------------------------------------------------------- #
# Stats primitives — implemented here, stdlib only.
# --------------------------------------------------------------------------- #
def wilson_interval(successes: int, n: int, z: float = Z_95
                    ) -> tuple[float | None, float, float]:
    """Wilson score interval for a binomial proportion.

    Returns ``(p_hat, lo, hi)`` with ``p_hat = successes / n`` (or ``None`` when
    ``n == 0``). The Wilson interval is used instead of the normal (Wald)
    interval because it stays inside ``[0, 1]`` and behaves at small ``n`` and at
    ``p_hat`` near 0/1 — exactly the regime a just-crossed abstain line lives in.

        center = (p̂ + z²/2n) / (1 + z²/n)
        half   = (z / (1 + z²/n)) · √( p̂(1-p̂)/n + z²/4n² )
    """
    if n <= 0:
        return (None, 0.0, 1.0)
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return (p, max(0.0, center - half), min(1.0, center + half))


def normal_mean_interval(values: list[float], z: float = Z_95,
                         single_halfwidth: float = MEMORY_SINGLE_CARD_HALFWIDTH,
                         clamp: tuple[float, float] = (0.0, 1.0)
                         ) -> tuple[float | None, float, float, int]:
    """Normal-approximation CI on the mean of ``values``.

    Returns ``(mean, lo, hi, n)``. With ``n >= 2`` the half-width is
    ``z · s / √n`` using the sample standard deviation ``s`` (Bessel-corrected).
    With ``n == 1`` the spread is unknown, so the interval widens to
    ``± single_halfwidth``. Bounds are clamped to ``clamp``; because ``mean``
    already lies inside ``clamp``, the invariant ``lo <= value <= hi`` always
    holds. ``n == 0`` yields ``(None, lo0, hi0, 0)``.
    """
    lo0, hi0 = clamp
    n = len(values)
    if n == 0:
        return (None, lo0, hi0, 0)
    mean = sum(values) / n
    if n >= 2:
        var = sum((v - mean) ** 2 for v in values) / (n - 1)
        half = z * math.sqrt(var / n)
    else:
        half = single_halfwidth
    return (mean, max(lo0, mean - half), min(hi0, mean + half), n)


# --------------------------------------------------------------------------- #
# 1. Memory — DOK 1 (retrieval strength)
# --------------------------------------------------------------------------- #
def memory_score(gapdb: Any, concept_id: int, at_ms: int | None = None) -> Score:
    """DOK 1 — mean FSRS retrievability across ``concept_id``'s cards, with a CI.

    Retrievability per card comes from :func:`gap.mastery.card_retrievabilities`
    (FSRS-5 ``R`` in ``(0, 1]``, computed in Python). The score is their mean; the
    interval is a normal approximation on that mean built from the per-card R
    spread (:func:`normal_mean_interval`) — a concept whose cards disagree widely
    on retrievability gets a wider band. A single scored card widens to
    ±:data:`MEMORY_SINGLE_CARD_HALFWIDTH`.

    Abstains (``value=None``) when the concept has fewer than
    :data:`MEMORY_MIN_CARDS` cards carrying FSRS state with a review — there is
    nothing to average. ``at_ms`` (epoch ms) is the instant R is evaluated at;
    ``None`` means now. Value/bounds are proportions in ``[0, 1]``.
    """
    rs = [r for _cid, cpt, r in mastery.card_retrievabilities(gapdb, at_ms)
          if cpt == concept_id]
    if len(rs) < MEMORY_MIN_CARDS:
        return Score(value=None, lo=0.0, hi=1.0, n=len(rs), abstained=True)
    mean, lo, hi, n = normal_mean_interval(rs)
    return Score(value=mean, lo=lo, hi=hi, n=n, abstained=False)


# --------------------------------------------------------------------------- #
# 2. Performance — DOK 2/3 (transfer to novel items)
# --------------------------------------------------------------------------- #
_PRACTICE_NOVEL_SQL = """
SELECT COUNT(*)          AS n,
       SUM(nr.correct)   AS k
FROM gap.novel_revlog nr
JOIN gap.novel_items ni          ON ni.id = nr.item_id
JOIN gap.novel_item_concepts nic ON nic.item_id = nr.item_id
WHERE nic.concept_id = ? AND ni.is_holdout = 0
"""


def performance_score(gapdb: Any, concept_id: int) -> Score:
    """DOK 2/3 — PRACTICE novel accuracy for ``concept_id``, with a Wilson CI.

    The point estimate is ``correct / attempts`` over the concept's practice
    novel attempts (``is_holdout = 0`` — the held-out set is never touched here).
    The interval is a Wilson score interval (:func:`wilson_interval`) on that
    binomial, so it stays in ``[0, 1]`` and is honest at small ``n``.

    **Give-up rule:** abstains (``value=None``, ``abstained=True``) when fewer
    than :data:`PERFORMANCE_MIN_ATTEMPTS` practice attempts exist. This is the
    pre-registered abstain line — the dashboard shows coverage, not a number,
    below it. Value/bounds are proportions in ``[0, 1]``.
    """
    row = gapdb.all(_PRACTICE_NOVEL_SQL, concept_id)
    n = int(row[0][0] or 0) if row else 0
    k = int(row[0][1] or 0) if row and row[0][1] is not None else 0
    if n < PERFORMANCE_MIN_ATTEMPTS:
        # widen to the full plausible range while abstaining, so a band still draws
        _, lo, hi = wilson_interval(k, n) if n > 0 else (None, 0.0, 1.0)
        return Score(value=None, lo=lo, hi=hi, n=n, abstained=True)
    p, lo, hi = wilson_interval(k, n)
    return Score(value=p, lo=lo, hi=hi, n=n, abstained=False)


# --------------------------------------------------------------------------- #
# 3. Readiness — DOK 4 (projected exam score)
# --------------------------------------------------------------------------- #
def _outline_weights(gapdb: Any, outline: Mapping[int, float] | None
                     ) -> list[tuple[int, float]]:
    """Resolve the exam outline to ``[(concept_id, weight), ...]``.

    ``outline`` is an explicit ``{concept_id: exam_weight}`` map (e.g. an official
    exam blueprint). When ``None``, it falls back to every concept in
    ``gap.concepts`` with its stored ``weight`` column — the pre-authored exam
    weight the queue already uses. Non-positive or missing weights are treated as
    ``1.0`` so a concept always carries some mass.
    """
    if outline is not None:
        return [(int(cid), float(w) if w and w > 0 else 1.0)
                for cid, w in outline.items()]
    rows = gapdb.all("SELECT id, weight FROM gap.concepts ORDER BY id")
    return [(int(cid), float(w) if w and w > 0 else 1.0) for cid, w in rows]


def readiness_score(gapdb: Any, outline: Mapping[int, float] | None = None
                    ) -> Score:
    """DOK 4 — projected exam score (percent) with a coverage-discounted CI.

    **Projection model (transparent, stated — not a black box).**
    Let the exam outline be concepts ``i`` with exam weights ``wᵢ`` (from
    ``outline`` or ``gap.concepts.weight``). A concept is *covered* iff it has a
    non-abstained :func:`performance_score` ``pᵢ`` (Wilson bounds
    ``[loᵢ, hiᵢ]``). Let ``C`` be the covered set,
    ``W_cov = Σ_{i∈C} wᵢ`` and ``W_tot = Σ_i wᵢ``.

    * **Point projection** — the exam-weighted mean performance of the covered
      concepts, i.e. we project the whole exam at the covered average::

          value = 100 · Σ_{i∈C} (wᵢ / W_cov) · pᵢ

      This states its assumption plainly: uncovered concepts are projected to
      perform like the covered ones. That assumption is exactly what the interval
      then charges for.

    * **Confidence interval** — two additive half-widths (in proportion units,
      scaled to percent)::

          hw_sample   = Σ_{i∈C} (wᵢ / W_cov) · (hiᵢ − loᵢ) / 2      (binomial noise)
          coverage    = 1 − W_cov / W_tot                            (uncovered weight)
          hw_coverage = coverage · 0.5                               (the guess tax)
          value ± 100 · (hw_sample + hw_coverage), clamped to [0, 100]

      ``hw_sample`` is the exam-weighted average of the per-concept Wilson widths;
      ``hw_coverage`` grows the band by up to the full uncovered weight fraction
      (a concept with no data could sit anywhere in ``[0, 1]``, i.e. ±0.5). So the
      **less of the exam is covered, the wider readiness gets** — the honest
      penalty for extrapolating.

    **Give-up rule:** abstains when coverage of scored concepts is below
    :data:`READINESS_MIN_COVERAGE` of total exam weight — under half the exam
    unmeasured is a guess, not a projection. ``n`` is the covered-concept count.
    Value/bounds are percents in ``[0, 100]``.
    """
    weights = _outline_weights(gapdb, outline)
    w_tot = sum(w for _cid, w in weights)
    if w_tot <= 0:
        return Score(value=None, lo=0.0, hi=100.0, n=0, abstained=True)

    covered: list[tuple[float, float, float, float]] = []  # (w, p, lo, hi)
    for cid, w in weights:
        ps = performance_score(gapdb, cid)
        if not ps.abstained and ps.value is not None:
            covered.append((w, ps.value, ps.lo, ps.hi))

    w_cov = sum(w for w, *_ in covered)
    coverage = w_cov / w_tot
    n_cov = len(covered)

    if coverage < READINESS_MIN_COVERAGE or w_cov <= 0:
        return Score(value=None, lo=0.0, hi=100.0, n=n_cov, abstained=True)

    value_frac = sum((w / w_cov) * p for w, p, _lo, _hi in covered)
    hw_sample = sum((w / w_cov) * ((hi - lo) / 2.0) for w, _p, lo, hi in covered)
    hw_coverage = (1.0 - coverage) * 0.5
    hw = hw_sample + hw_coverage

    lo = max(0.0, value_frac - hw)
    hi = min(1.0, value_frac + hw)
    return Score(value=100.0 * value_frac, lo=100.0 * lo, hi=100.0 * hi,
                 n=n_cov, abstained=False)


# --------------------------------------------------------------------------- #
# Rollup helper
# --------------------------------------------------------------------------- #
def all_scores(gapdb: Any, at_ms: int | None = None,
               outline: Mapping[int, float] | None = None) -> dict:
    """Every score in one call.

    Returns::

        {
          "concepts": { concept_id: {"memory": Score, "performance": Score} },
          "readiness": Score,          # the DOK-4 exam-level rollup
        }

    Memory (DOK 1) and performance (DOK 2/3) are per concept; readiness (DOK 4) is
    a single exam-level projection over the same outline, so it is returned once
    as a rollup rather than per concept. ``Score`` objects are returned as-is;
    call ``.to_dict()`` for a JSON payload.
    """
    concept_ids = [int(cid) for cid in
                   gapdb.list("SELECT id FROM gap.concepts ORDER BY id")]
    per_concept = {
        cid: {
            "memory": memory_score(gapdb, cid, at_ms=at_ms),
            "performance": performance_score(gapdb, cid),
        }
        for cid in concept_ids
    }
    return {"concepts": per_concept, "readiness": readiness_score(gapdb, outline)}
