//! `sm20-schedule` — one review, in and out over stdin/stdout as JSON.
//!
//! The whole interop surface between the journal's Python and SuperMemo's
//! Algorithm Arena is this file. One JSON object in, one JSON object out, one
//! process per rating. That is a few milliseconds against a human who has been
//! staring at a chart for seconds, and it buys a boundary with no ABI, no build
//! toolchain on the Python side, and a protocol you can drive by hand:
//!
//! ```text
//! echo '{"grade":4,"elapsed_days":40}' | ./sm20-schedule
//! ```
//!
//! Both `state` and `collection` are optional on the way in: absent means
//! "fresh". They always come back populated, and the caller is expected to
//! store both — `collection` is where the Arena's learned weights, the M2
//! optimizer and M3's matrices live, and dropping it resets the deck's
//! accumulated tuning to defaults.

use std::io::Read;

use rand::rngs::StdRng;
use rand::SeedableRng;
use serde::{Deserialize, Serialize};

use sm20::{SM20CollectionState, SM20State};

/// One review to schedule.
#[derive(Deserialize)]
struct Request {
    /// The item's prior state. Absent for a card that has never been rated.
    #[serde(default)]
    state: Option<SM20State>,
    /// The deck's shared state. Absent on the very first rating of a deck.
    #[serde(default)]
    collection: Option<SM20CollectionState>,
    /// SuperMemo grade, 0-5. Use `rating_to_grade` on the caller's side to map
    /// a four-button rating onto this scale.
    grade: i32,
    /// Days since this card was last shown. 0 for a first rating.
    elapsed_days: f64,
    /// Requested forgetting index, percent. 10 is SuperMemo's default and the
    /// value at which the retention multiplier is exactly 1.
    #[serde(default = "default_fi")]
    fi: u8,
    /// Today as Unix epoch days. Defaults to the host clock, but the caller
    /// should pass it so the schedule follows the journal's notion of a day.
    #[serde(default)]
    today: Option<i32>,
    /// Seed for interval dispersal. Fixed values make a review reproducible,
    /// which is what the tests want; omit it in production so that cards
    /// falling due on the same day get spread out.
    #[serde(default)]
    seed: Option<u64>,
    /// Skip the blend and schedule from the SM-20 kernel alone.
    #[serde(default)]
    pure_m4: bool,
    /// Spread the committed interval so cards falling due together don't clump.
    /// On by default; turn it off to reproduce an exact blended recommendation,
    /// which is what the parity checks need.
    #[serde(default = "default_true")]
    disperse: bool,
}

fn default_true() -> bool {
    true
}

fn default_fi() -> u8 {
    sm20::DEFAULT_FI
}

/// The scheduled review. `state` and `collection` are the caller's to persist.
#[derive(Serialize)]
struct Response {
    state: SM20State,
    collection: SM20CollectionState,
    interval_days: f64,
    retrievability: f64,
    /// The five candidates the Arena blended, in slot order
    /// SM-2 / SM-15 / SM-19 / SM-20 / FSRS. Diagnostics: it is worth being able
    /// to see that the committed interval is a blend and how far the models
    /// disagreed, rather than trusting one number from a black box.
    model_intervals: [f64; 5],
    /// Live Arena blend weights after this review, same slot order.
    arena_weights: [f64; 5],
}

fn main() {
    match run() {
        Ok(json) => println!("{json}"),
        Err(e) => {
            // stderr, and a non-zero exit: the caller distinguishes "the
            // scheduler said no" from "the scheduler crashed" by exit code, and
            // must never mistake an error string for a schedule.
            eprintln!("sm20-schedule: {e}");
            std::process::exit(1);
        }
    }
}

fn run() -> Result<String, Box<dyn std::error::Error>> {
    let mut raw = String::new();
    std::io::stdin().read_to_string(&mut raw)?;
    if raw.trim().is_empty() {
        return Err("empty request on stdin".into());
    }
    let req: Request = serde_json::from_str(&raw)?;

    if !req.elapsed_days.is_finite() || req.elapsed_days < 0.0 {
        return Err(format!("elapsed_days must be finite and >= 0, got {}", req.elapsed_days).into());
    }

    // A card with no stored state is being rated for the first time, and the
    // kernel seeds stability and difficulty from that first grade rather than
    // from a constant.
    let state = req
        .state
        .unwrap_or_else(|| sm20::init_item(req.grade.clamp(0, 5)));
    let mut collection = req.collection.unwrap_or_default();

    let today = req.today.unwrap_or_else(|| {
        (std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0)
            / 86_400) as i32
    });

    let mut rng = match req.seed {
        Some(s) => StdRng::seed_from_u64(s),
        None => StdRng::from_entropy(),
    };

    let result = sm20::review(
        &state,
        req.grade,
        req.elapsed_days,
        req.fi,
        &mut collection,
        today,
        true, // commit — this is a real review, so M2/M3/Arena state advances
        req.disperse,
        &mut rng,
        req.pure_m4,
        0.0, // post_lapse_x: no element-priority concept in this deck
    );

    let arena_weights = collection.arena.weights;
    Ok(serde_json::to_string(&Response {
        state: result.state,
        collection,
        interval_days: result.interval_days,
        retrievability: result.retrievability,
        model_intervals: result.model_intervals,
        arena_weights,
    })?)
}
