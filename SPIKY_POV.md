# The Spiky POV

> **Every spaced-repetition app ships a rising line and calls it learning.
> Past four exposures, that line is measuring familiarity — not knowledge.
> I built, and pre-registered, the app that refuses to believe it.**

---

## Consensus (what every SRS app sells)

Learning is *cards mastered*. Master more cards → know more → more ready.
The progress bar going up and to the right **is** the product. Anki, and every
tool built on it, scores your recall of a card and implies you've learned the
concept behind it.

## My spiky POV

**Card accuracy stops measuring learning at about the fourth exposure.**

After that, your accuracy on the card keeps climbing — but only because the
*item* has become familiar, not because the *concept* has been learned. Ask a
**novel** question on the same concept and accuracy flatlines. Every tool scores
the familiar item and quietly implies the concept. It is measuring **recognition
and calling it knowledge.**

## Why everyone believes the comfortable version

Because the number goes up and to the right, and up-and-to-the-right *feels* like
progress. Bjork & Bjork already named the trap: **retrieval strength** (how
easily you can pull this exact item right now) rises fast and ceilings;
**storage strength** (durable, transferable knowledge) does not. "Cards seen" is
a retrieval-strength counter wearing a storage-strength costume.

## The uncomfortable consequence

The metric and the goal actively **diverge**. A learner who grinds a concept to
95% card accuracy can be *less* able to answer a fresh question than someone at
70% — because the 95% is 95% *familiarity*. Optimizing the shipped metric can
make you worse at the very thing the metric claims to track.

## What it forced me to build

A version of Anki that **refuses to retire a concept on card mastery alone.** It
demands ≥ 0.70 accuracy on **novel items the model has never scored.** Concretely:

- **A novel-item retirement gate** — `concept.retired` requires novel accuracy,
  not card mastery. This is the ablation switch the whole experiment turns on.
- **A points-at-stake queue** — ranks due cards by `(card_mastery − novel_accuracy) × exam_weight`,
  i.e. by the *size of the lie* between what your card score implies and what a
  novel question shows.
- **Continuous mastery signal** — grade 1–4 and latency, not binary `correct`,
  because binary correct ceilings after 4 exposures and would confirm the thesis
  with an artifact.
- **An honesty rule** — the dashboard **abstains** from a Performance score for
  any concept with < 8 novel attempts, and shows coverage % instead of faking
  confidence.

## How I'll know I'm wrong (committed *before* any data)

This is a pre-registration, not a pitch. It is dead if any of:

- **No decoupling** — novel-item accuracy tracks card accuracy 1:1 past exposure 4.
  Then binary `correct` was fine all along and there is nothing here.
- **No cost, no flip** — the gate never binds, or it costs study time and buys no
  terminal advantage. Then the feature is theater.
- **The flip lands late** — the predicted crossover is committed at **exactly
  exposure 5**; if it only appears at exposure 9+, it is reported as a partial
  result, not a win. That clause exists to stop me shopping for a flattering
  crossover point after the fact.

The full pre-registration — arms, endpoints, kill criteria, and the exact
predicted effect sizes, all fixed before outcome data was inspected — is in
[`PREREGISTRATION.md`](PREREGISTRATION.md). What each belief forced me to build,
and how each build could prove me wrong, is the traceability table at the bottom
of that file.

## The one-liner

*Card mastery is a retrieval-strength counter sold as a storage-strength number.
I built the app that gates on the number the industry refuses to measure — and
pre-committed the exact point where the consensus metric detaches from reality.*
