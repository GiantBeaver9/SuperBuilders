# Novel-item Gate — configuration

These keys live in `config.json` and are editable from Anki's add-on manager
(Tools → Add-ons → select this add-on → Config). All three mirror thresholds
committed in the pre-registration; changing them changes the study behaviour and,
if you are running the experiment, invalidates comparability with the registered
arms. Defaults are the registered values.

| Key | Default | Meaning |
|---|---|---|
| `novel_gate_threshold` | `0.7` | **Arm A (`gate`) retirement threshold.** A concept in the gate arm retires only once its *practice* novel-item accuracy reaches this fraction. This is the pre-registration's novel-item gate (POV-1): retirement requires transfer, not card familiarity. Range 0–1. |
| `mastery_R_threshold` | `0.9` | **Arm B (`nogate`) retirement threshold.** A concept in the ablation arm retires on card mastery alone — mean FSRS retrievability `R` across its cards ≥ this value — with the novel gate switched off. Range 0–1. |
| `abstain_min_attempts` | `8` | **Dashboard abstain rule.** The dashboard refuses to show a Performance (novel-accuracy) score for any concept with fewer than this many novel attempts, showing coverage % instead. The honesty rule from the pre-registration's abstain traceability row. Integer ≥ 0. |

Notes:

- The engine reads these thresholds when it evaluates retirement and builds the
  dashboard payload. If you have not run any reviews yet, changing them has no
  visible effect until the next review or dashboard open.
- `novel_gate_threshold` and `mastery_R_threshold` are deliberately different
  gates for different arms — that difference *is* the experiment. Do not set them
  equal expecting the arms to converge; the gate arm still requires novel-item
  evidence regardless of card mastery.
