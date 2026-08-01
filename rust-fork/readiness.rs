// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Points-at-stake / readiness-gap ordering — the SuperBuilders novel-item gate,
//! implemented natively in the scheduling engine.
//!
//! The thesis (see the project's PREREGISTRATION.md) is that card mastery and
//! performance on novel items decouple past a few exposures. This RPC ranks
//! concepts by the *size of that gap*: for each concept it computes
//! `card_mastery` = the mean FSRS retrievability of the concept's cards — using
//! Anki's own [`fsrs::current_retrievability`], the exact function the stats
//! graphs use, so the number stays consistent with the scheduler — then
//! `points = (card_mastery - novel_accuracy) * exam_weight`, and returns the
//! concepts ordered by `points` descending (largest gap first).
//!
//! It lives in Rust rather than the Python add-on because it is a hot-path query
//! over the whole due set that reads each card's FSRS memory state: computing it
//! natively avoids a per-card Python↔backend round-trip and reuses the engine's
//! retrievability implementation. It is read-only — it never mutates the
//! collection, so it cannot corrupt it and takes no part in undo. See
//! docs/RUST_RATIONALE.md in the SuperBuilders repo.

use anki_proto::scheduler::ComputeReadinessGapRequest;
use anki_proto::scheduler::ComputeReadinessGapResponse;
use anki_proto::scheduler::ConceptStake;
use fsrs::current_retrievability;
use fsrs::FSRS5_DEFAULT_DECAY;

use crate::card::FsrsMemoryState;
use crate::prelude::*;

/// The points-at-stake key: how much the card score overstates readiness,
/// scaled by exam weight. Negative when novel accuracy exceeds card mastery.
pub(crate) fn readiness_points(card_mastery: f32, novel_accuracy: f32, exam_weight: f32) -> f32 {
    (card_mastery - novel_accuracy) * exam_weight
}

/// FSRS retrievability of a single card's memory state after `elapsed_days`,
/// via Anki's own fsrs function. Elapsed is clamped at 0 (a just-reviewed card
/// reads R = 1, never > 1).
pub(crate) fn card_retrievability(state: FsrsMemoryState, elapsed_days: f32, decay: f32) -> f32 {
    current_retrievability(state.into(), elapsed_days.max(0.0), decay)
}

impl Collection {
    pub(crate) fn compute_readiness_gap(
        &mut self,
        input: ComputeReadinessGapRequest,
    ) -> Result<ComputeReadinessGapResponse> {
        let timing = self.timing_today()?;
        let mut ordered: Vec<ConceptStake> = Vec::with_capacity(input.concepts.len());

        for concept in input.concepts {
            let mut sum_r = 0.0_f32;
            let mut count = 0_u32;
            for cid in &concept.card_ids {
                if let Some(card) = self.storage.get_card(CardId(*cid))? {
                    if let Some(state) = card.memory_state {
                        let elapsed_secs =
                            card.seconds_since_last_review(&timing).unwrap_or_default();
                        sum_r += card_retrievability(
                            state,
                            elapsed_secs as f32 / 86_400.0,
                            card.decay.unwrap_or(FSRS5_DEFAULT_DECAY),
                        );
                        count += 1;
                    }
                }
            }
            // Cards without FSRS memory state contribute no R; a concept with no
            // scored card has mastery 0.0 (matching the add-on's engine).
            let card_mastery = if count > 0 { sum_r / count as f32 } else { 0.0 };
            let points = readiness_points(
                card_mastery,
                concept.novel_accuracy as f32,
                concept.exam_weight as f32,
            );
            ordered.push(ConceptStake {
                concept_code: concept.concept_code,
                card_mastery: card_mastery as f64,
                novel_accuracy: concept.novel_accuracy,
                exam_weight: concept.exam_weight,
                points: points as f64,
            });
        }

        // Largest gap first; stable tiebreak on concept_code for determinism.
        ordered.sort_by(|a, b| {
            b.points
                .partial_cmp(&a.points)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.concept_code.cmp(&b.concept_code))
        });

        Ok(ComputeReadinessGapResponse { ordered })
    }
}

#[cfg(test)]
mod test {
    use anki_proto::scheduler::ComputeReadinessGapRequest;
    use anki_proto::scheduler::ConceptGap;

    use super::*;

    #[test]
    fn points_is_gap_times_weight() {
        // 0.9 mastery, 0.6 novel, weight 2.0 -> (0.3)*2 = 0.6
        assert!((readiness_points(0.9, 0.6, 2.0) - 0.6).abs() < 1e-6);
        // novel accuracy above card mastery -> negative points (the good case)
        assert!(readiness_points(0.5, 0.8, 1.0) < 0.0);
        // zero exam weight -> zero stake
        assert_eq!(readiness_points(0.9, 0.1, 0.0), 0.0);
    }

    #[test]
    fn retrievability_is_one_at_zero_elapsed_and_decreases() {
        let state = FsrsMemoryState {
            stability: 40.0,
            difficulty: 5.0,
        };
        let r0 = card_retrievability(state, 0.0, FSRS5_DEFAULT_DECAY);
        let r10 = card_retrievability(state, 10.0, FSRS5_DEFAULT_DECAY);
        let r100 = card_retrievability(state, 100.0, FSRS5_DEFAULT_DECAY);
        assert!((r0 - 1.0).abs() < 1e-4, "R at t=0 should be 1.0, got {r0}");
        assert!(r10 < r0 && r100 < r10, "R must fall as time elapses");
        assert!(r100 > 0.0 && r100 < 1.0);
    }

    #[test]
    fn mean_mastery_and_points_compose() {
        let s = FsrsMemoryState {
            stability: 40.0,
            difficulty: 5.0,
        };
        let r_fresh = card_retrievability(s, 0.0, FSRS5_DEFAULT_DECAY);
        let r_old = card_retrievability(s, 40.0, FSRS5_DEFAULT_DECAY);
        let mean = (r_fresh + r_old) / 2.0;
        assert!(mean < r_fresh && mean > r_old);
        let pts = readiness_points(mean, 0.5, 1.5);
        assert!((pts - (mean - 0.5) * 1.5).abs() < 1e-6);
    }

    #[test]
    fn orders_by_gap_and_handles_empty_concepts() {
        // Fresh collection, no cards: every concept has mastery 0.0, so
        // points = (0 - novel)*weight. Concept A (novel 0.2) outranks B (0.9).
        let mut col = Collection::new();
        let out = col
            .compute_readiness_gap(ComputeReadinessGapRequest {
                concepts: vec![
                    ConceptGap {
                        card_ids: vec![],
                        novel_accuracy: 0.2,
                        exam_weight: 1.0,
                        concept_code: "A".into(),
                    },
                    ConceptGap {
                        card_ids: vec![],
                        novel_accuracy: 0.9,
                        exam_weight: 1.0,
                        concept_code: "B".into(),
                    },
                ],
            })
            .unwrap();
        assert_eq!(out.ordered.len(), 2);
        assert_eq!(out.ordered[0].concept_code, "A");
        assert!((out.ordered[0].card_mastery - 0.0).abs() < 1e-6);
        assert!(out.ordered[0].points > out.ordered[1].points);
    }
}
