//! `fuzzy_span` op: semantics-identical port of
//! `core.hash_patch._best_fuzzy_span` — same candidate span enumeration
//! (Python `str.splitlines` boundaries), same difflib
//! `SequenceMatcher(autojunk=False)` Ratcliff-Obershelp ratio, same
//! order-independent ambiguity gate. Offsets are Python code-point
//! offsets so the caller can slice `text[start:end]` directly.

use std::collections::HashMap;

use serde_json::{json, Value};

use crate::IndexError;

const DEFAULT_FUZZY_THRESHOLD: f64 = 0.86;
const AMBIGUITY_MARGIN: f64 = 0.015;
const MAX_LINE_DRIFT: usize = 2;

pub fn fuzzy_span(payload: &Value) -> Result<Value, IndexError> {
    let text = payload
        .get("text")
        .and_then(Value::as_str)
        .ok_or_else(|| IndexError("missing required field: text".into()))?;
    let reference = payload
        .get("reference")
        .and_then(Value::as_str)
        .ok_or_else(|| IndexError("missing required field: reference".into()))?;
    match best_fuzzy_span(text, reference) {
        None => Ok(json!({"ok": true, "matched": false})),
        Some((start, end, score, ambiguous)) => Ok(json!({
            "ok": true,
            "matched": true,
            "start": start,
            "end": end,
            "score": score,
            "ambiguous": ambiguous,
        })),
    }
}

/// Mirror of `_best_fuzzy_span`: (start, end, score, ambiguous) in code points.
pub fn best_fuzzy_span(text: &str, reference: &str) -> Option<(usize, usize, f64, bool)> {
    if reference.is_empty() {
        return None;
    }
    let chars: Vec<char> = text.chars().collect();
    let offsets = line_offsets(&chars);
    let line_count = offsets.len() - 1;
    if line_count == 0 {
        return None;
    }
    let ref_chars: Vec<char> = reference.chars().collect();
    let target = std::cmp::max(1, line_offsets(&ref_chars).len() - 1);
    let lower = std::cmp::max(1, target.saturating_sub(MAX_LINE_DRIFT));
    let upper = std::cmp::min(line_count, target + MAX_LINE_DRIFT);

    let matcher = Matcher::new(ref_chars);
    let mut best: Option<(usize, usize, f64)> = None;
    let mut best_lines: (usize, usize) = (0, 0);
    // Same post-scan ambiguity decision as Python: collect every candidate
    // above the threshold, judge against the final winner only.
    let mut contenders: Vec<(f64, usize, usize)> = Vec::new();
    for width in lower..=upper {
        for start_line in 0..=(line_count - width) {
            let start = offsets[start_line];
            let end = offsets[start_line + width];
            let block = &chars[start..end];
            if let Some((_, _, best_score)) = best {
                let prune_below = best_score - AMBIGUITY_MARGIN;
                if matcher.real_quick_ratio(block) < prune_below
                    || matcher.quick_ratio(block) < prune_below
                {
                    continue;
                }
            }
            let score = matcher.ratio(block);
            if best.is_none_or(|(_, _, best_score)| score > best_score) {
                best = Some((start, end, score));
                best_lines = (start_line, start_line + width);
            }
            if score >= DEFAULT_FUZZY_THRESHOLD {
                contenders.push((score, start_line, start_line + width));
            }
        }
    }
    let (start, end, best_score) = best?;
    let ambiguous = contenders.iter().any(|&(score, start_line, end_line)| {
        best_score - score < AMBIGUITY_MARGIN
            && (end_line <= best_lines.0 || start_line >= best_lines.1)
    });
    Some((start, end, best_score, ambiguous))
}

/// Python `str.splitlines` line-break set (PEP 3120 universal newlines).
fn is_line_break(c: char) -> bool {
    matches!(
        c,
        '\n' | '\r'
            | '\u{0b}'
            | '\u{0c}'
            | '\u{1c}'
            | '\u{1d}'
            | '\u{1e}'
            | '\u{85}'
            | '\u{2028}'
            | '\u{2029}'
    )
}

/// Line start offsets plus a final total-length entry, matching the Python
/// `offsets` array built from `splitlines(keepends=True)`.
fn line_offsets(chars: &[char]) -> Vec<usize> {
    let mut offsets = vec![0];
    let n = chars.len();
    let mut i = 0;
    while i < n {
        if is_line_break(chars[i]) {
            if chars[i] == '\r' && i + 1 < n && chars[i + 1] == '\n' {
                i += 2;
            } else {
                i += 1;
            }
            offsets.push(i);
        } else {
            i += 1;
        }
    }
    if *offsets.last().unwrap() != n {
        offsets.push(n);
    }
    offsets
}

/// difflib.SequenceMatcher with isjunk=None, autojunk=False and a fixed
/// second sequence (the patch reference); the block under test is seq1.
struct Matcher {
    b: Vec<char>,
    b2j: HashMap<char, Vec<usize>>,
    fullbcount: HashMap<char, usize>,
}

impl Matcher {
    fn new(b: Vec<char>) -> Self {
        let mut b2j: HashMap<char, Vec<usize>> = HashMap::new();
        let mut fullbcount: HashMap<char, usize> = HashMap::new();
        for (j, &c) in b.iter().enumerate() {
            b2j.entry(c).or_default().push(j);
            *fullbcount.entry(c).or_insert(0) += 1;
        }
        Matcher { b, b2j, fullbcount }
    }

    fn calculate_ratio(&self, matches: usize, a_len: usize) -> f64 {
        let length = a_len + self.b.len();
        if length == 0 {
            return 1.0;
        }
        2.0 * matches as f64 / length as f64
    }

    fn ratio(&self, a: &[char]) -> f64 {
        self.calculate_ratio(self.match_total(a), a.len())
    }

    fn quick_ratio(&self, a: &[char]) -> f64 {
        let mut avail: HashMap<char, i64> = HashMap::new();
        let mut matches = 0usize;
        for &c in a {
            let numb = match avail.get(&c) {
                Some(&n) => n,
                None => self.fullbcount.get(&c).copied().unwrap_or(0) as i64,
            };
            avail.insert(c, numb - 1);
            if numb > 0 {
                matches += 1;
            }
        }
        self.calculate_ratio(matches, a.len())
    }

    fn real_quick_ratio(&self, a: &[char]) -> f64 {
        self.calculate_ratio(std::cmp::min(a.len(), self.b.len()), a.len())
    }

    /// Sum of matching-block sizes (difflib get_matching_blocks queue walk).
    fn match_total(&self, a: &[char]) -> usize {
        let mut queue = vec![(0usize, a.len(), 0usize, self.b.len())];
        let mut matches = 0;
        while let Some((alo, ahi, blo, bhi)) = queue.pop() {
            let (i, j, k) = self.find_longest_match(a, alo, ahi, blo, bhi);
            if k > 0 {
                matches += k;
                if alo < i && blo < j {
                    queue.push((alo, i, blo, j));
                }
                if i + k < ahi && j + k < bhi {
                    queue.push((i + k, ahi, j + k, bhi));
                }
            }
        }
        matches
    }

    /// difflib find_longest_match with an empty junk set: the two junk
    /// extension loops are no-ops and are omitted.
    fn find_longest_match(
        &self,
        a: &[char],
        alo: usize,
        ahi: usize,
        blo: usize,
        bhi: usize,
    ) -> (usize, usize, usize) {
        let mut besti = alo;
        let mut bestj = blo;
        let mut bestsize = 0usize;
        let mut j2len: HashMap<usize, usize> = HashMap::new();
        for i in alo..ahi {
            let mut newj2len: HashMap<usize, usize> = HashMap::new();
            if let Some(indices) = self.b2j.get(&a[i]) {
                for &j in indices {
                    if j < blo {
                        continue;
                    }
                    if j >= bhi {
                        break;
                    }
                    let k = if j > blo {
                        j2len.get(&(j - 1)).copied().unwrap_or(0) + 1
                    } else {
                        1
                    };
                    newj2len.insert(j, k);
                    if k > bestsize {
                        besti = i + 1 - k;
                        bestj = j + 1 - k;
                        bestsize = k;
                    }
                }
            }
            j2len = newj2len;
        }
        while besti > alo && bestj > blo && a[besti - 1] == self.b[bestj - 1] {
            besti -= 1;
            bestj -= 1;
            bestsize += 1;
        }
        while besti + bestsize < ahi
            && bestj + bestsize < bhi
            && a[besti + bestsize] == self.b[bestj + bestsize]
        {
            bestsize += 1;
        }
        (besti, bestj, bestsize)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ratio_of(a: &str, b: &str) -> f64 {
        let matcher = Matcher::new(b.chars().collect());
        matcher.ratio(&a.chars().collect::<Vec<_>>())
    }

    #[test]
    fn ratio_matches_difflib_reference_values() {
        // Values computed with CPython difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
        assert_eq!(ratio_of("abcd", "bcde"), 0.75);
        assert_eq!(ratio_of("", "x"), 0.0);
        assert_eq!(
            ratio_of(
                "private Thread currentThread;",
                "private volatile Thread currentThread;"
            ),
            0.8656716417910447
        );
        assert_eq!(ratio_of("qabxcd", "abycdf"), 0.6666666666666666);
    }

    #[test]
    fn splitlines_matches_python_boundaries() {
        let chars: Vec<char> = "a\r\nb\rc\nd\u{2028}e".chars().collect();
        assert_eq!(line_offsets(&chars), vec![0, 3, 5, 7, 9, 10]);
        assert_eq!(line_offsets(&[]), vec![0]);
        let no_trailing: Vec<char> = "ab".chars().collect();
        assert_eq!(line_offsets(&no_trailing), vec![0, 2]);
    }

    #[test]
    fn exactish_target_found_with_offsets_in_code_points() {
        let text = "감자\nline two\ndef target():\n    return 42\ntail\n";
        let reference = "def target():\n    return 43\n";
        let (start, end, score, ambiguous) = best_fuzzy_span(text, reference).unwrap();
        let chars: Vec<char> = text.chars().collect();
        let block: String = chars[start..end].iter().collect();
        assert_eq!(block, "def target():\n    return 42\n");
        assert!(score > 0.9 && !ambiguous);
    }

    #[test]
    fn duplicate_blocks_are_ambiguous() {
        let block = "def f():\n    return 1\n";
        let text = format!("{block}\n{block}");
        let reference = "def f():\n    return 2\n";
        let (_, _, _, ambiguous) = best_fuzzy_span(&text, reference).unwrap();
        assert!(ambiguous);
    }

    #[test]
    fn empty_inputs_yield_no_match() {
        assert!(best_fuzzy_span("text\n", "").is_none());
        assert!(best_fuzzy_span("", "ref").is_none());
    }
}
