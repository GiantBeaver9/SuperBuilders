# Pre-registration — POV-1 thesis test

**Committed before any outcome data was inspected. Deviations logged at the bottom, dated.**

## POV-1

Consensus: card accuracy measures learning, so more cards mastered means more ready.

I think: card accuracy and novel-item accuracy on the same concept **decouple past ~4 exposures**. Card accuracy keeps rising because familiarity strips ambiguity from the item; novel-passage accuracy on that concept flattens. Every tool scores the first and implies the second.

Mechanism: Bjork & Bjork, retrieval strength ceilings; storage strength does not. Cards seen is a retrieval-strength counter sold as a storage-strength number.

## Design

Within-student, **between-concept** randomization. No crossover — learning has no washout, so a concept cannot serve in two arms.

Each student's concept pool is randomly split into three arms before first exposure, stratified by exam weight and by baseline difficulty (FSRS initial difficulty, binned into terciles) so the scheduler's own lapse-selection can't masquerade as an effect.

| Arm | Build | Retirement rule |
|---|---|---|
| A `gate` | Full app | Concept retires only when novel-item accuracy ≥ 0.7 |
| B `nogate` | Ablation | Concept retires on card mastery alone (feature off) |
| C `vanilla` | Unmodified Anki | Anki default |

Equal study minutes per arm, enforced by timer, not by card count. All arms tested on the same held-out novel items.

```sql
arms(user_id, concept_id, arm, assigned_ts)   -- 'gate'|'nogate'|'vanilla', written before first exposure
```

## Primary endpoint — crossover interaction

Arm × exposure-bucket interaction on novel-item accuracy. Buckets: exposures 1–4, exposures 5+. **The prediction is a sign flip, and the flip point is committed here in advance.**

| Bucket | Prediction (Arm A − Arm B) |
|---|---|
| Exposures 1–4 | **−5 to −12 pp** — Arm A is worse. This is the cost, and it is predicted, not excused. |
| Exposures 5+ | **+6 to +15 pp** — Arm A overtakes. |

Crossover point committed at **exposure 5**.

**Dead if any of:**

- No early cost — Arm A is not worse at exposures 1–4. The gate never bound, so the feature is inert and the ablation is measuring nothing.
- No flip — Arm A is worse at 1–4 and still ≤ Arm B at 5+. Paid the price, bought nothing.
- Late flip — the crossover appears only at exposure 9 or later. Reported as a partial result, not a win. This clause exists to stop me shopping for a flattering crossover point after the fact.

Binary `correct` is not the mastery signal — it ceilings after 4 exposures, and range restriction would collapse the correlation *mechanically*, confirming the thesis with an artifact. Mastery is FSRS retrievability, `revlog.ease` (1–4), and `revlog.time`.

## Secondary endpoints

- **Terminal novel accuracy**, Arm A − Arm B on held-out items. Predicted **+8 pp, 90% interval [0, +16]**. Underpowered; reported directional with interval, never as a headline.
- **Cost in throughput**, stated up front: Arm A retires **25–35% fewer concepts** in equal minutes. Expected price, not a failure.
- **Arm C sanity check**: if C ≈ A, the app does not beat unmodified Anki and POV-1 does not justify the build.
- **Latency dissociation**: `revlog.time` falls monotonically across exposures while novel-item latency does not. Dead if they fall in lockstep (r > 0.8 within student).

## Analysis

Mixed-effects logistic regression on attempt-level data. Random intercepts for student and for concept. Unit of analysis is student × concept, not student.

Target n: ≥ 120 student × concept units (e.g. 3 students × 40 concepts). Stated honestly: this powers the divergence slope, which uses every attempt. It does **not** power the terminal endpoint, which is reported as directional with an interval, not as a result.

## Data discipline

- `is_holdout` set at insert time, before any model touches the row. Never assigned by a later query.
- Leakage script (§8) run and reported clean before endpoints are computed.
- Cutoffs above were set before looking at outcome data.

## Deviations log

| Date | Change | Reason |
|---|---|---|
| — | none yet | |

---

# Traceability rows (Brainlift §7)

| POV | What it forced me to build | How I'll know it was wrong |
|---|---|---|
| POV-1 | Novel-item gate on concept retirement: `concept.retired` requires novel accuracy ≥ 0.7, not card mastery. This is the ablation switch. | Arm A ≤ Arm B on terminal novel accuracy at equal minutes → the gate cost time and bought nothing. |
| POV-1 | Rust points-at-stake queue: orders due cards by `(card_mastery − novel_accuracy) × exam_weight`, new protobuf `ConceptGapStats`, FSRS intervals and undo preserved. | Gap-ordered queue shows no advantage over FSRS default ordering on held-out novel items → the gap carries no scheduling signal. |
| POV-1 | `attempts.latency_ms` + grade 1–4 stored instead of binary `correct`; all mastery regressions run on continuous signal. | Novel-item latency tracks card latency 1:1 past exposure 4 → nothing is diverging and binary correct was fine all along. |
| POV-1 | Abstain rule: dashboard refuses a Performance score for any concept with < 8 novel attempts; coverage % shown instead. | Concepts below the abstain line show the same novel accuracy as those above → the threshold was tracking nothing and the honesty rule was theater. |

The database schema backing this pre-registration lives in [`schema/gap.sql`](schema/gap.sql).
