# Named source catalog

Every AI-generated card MUST cite one of these named sources by `source_id`
(schema/gap.sql §`novel_items.source_id`: "untraceable AI output zeroes the
section"). A card that cannot name its source is dropped by the pipeline.

The passages are short, factual, public-domain / general-knowledge text authored
for this fixture. Each `.txt` file in this directory is the verbatim source; the
`char span` a card cites is a `[start, end)` offset into that file's text.

## Data cutoff & leakage guard

**Data cutoff: 2026-08-01.** A source dated after the cutoff is *held back*: the
generation pipeline (`ai/generate.py`) and the eval (`ai/eval_generation.py`)
refuse to draw cards from it, so no held-back source can leak into the generated
set. `constitution_branches` is dated after the cutoff on purpose — it is the
held-back control the leakage check verifies is absent from generation, and the
distractor the retrieval eval must *not* return.

## Catalog

The table below is the single source of truth the loader parses (pipe-delimited;
`date > cutoff` ⇒ held back).

| source_id | concept_code | date | title | attribution |
|---|---|---|---|---|
| euclid_point | GEO.1 | 2020-01-01 | Elements, Book I — foundational definitions | Euclid, *Elements* (c. 300 BCE), public domain; paraphrased from the Heath translation |
| newton_motion | PHY.1 | 2020-01-01 | The three laws of motion | Isaac Newton, *Principia* (1687), public domain; paraphrased |
| photosynthesis | BIO.1 | 2021-06-01 | Photosynthesis and the leaf | General biology (factual), author-written summary |
| lr_assumption | LR.1 | 2022-03-01 | Necessary vs. sufficient assumptions | LSAT logical-reasoning study note (factual), author-written |
| rc_main_point | RC.1 | 2022-03-01 | Main point and primary purpose | LSAT reading-comprehension study note (factual), author-written |
| constitution_branches | CIV.1 | 2026-09-01 | The three branches of government | U.S. civics (factual), author-written — **held back (after cutoff)** |

## Concept codes

`LR.1` and `RC.1` reuse `data/outline_lsat.json`. `GEO.1`, `PHY.1`, `BIO.1`,
`CIV.1` are general-knowledge codes local to this fixture.
