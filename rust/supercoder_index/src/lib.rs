//! Repo index engine: parallel scan + hash, semantics-identical to the
//! Python fallback in `cluxion_agentplugin_supercoder.rust_bridge`.
//!
//! Hashes match `core.hash_patch.file_hash`: newlines are normalized
//! (CRLF/CR -> LF) before SHA-256, so cursors created by either side
//! verify against the other.

use std::fs;
use std::path::Path;

use rayon::prelude::*;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use walkdir::WalkDir;

pub mod fuzzy;
pub mod outline;
pub mod syntax;

const DEFAULT_MAX_FILES: usize = 256;
const SKIP_DIRS: [&str; 5] = [".git", "node_modules", ".venv", "dist", "target"];
const DEFAULT_EXTENSIONS: [&str; 10] = [
    "py", "rs", "ts", "tsx", "js", "go", "md", "toml", "yaml", "yml",
];

#[derive(Debug)]
pub struct IndexError(pub String);

impl std::fmt::Display for IndexError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for IndexError {}

impl From<std::io::Error> for IndexError {
    fn from(err: std::io::Error) -> Self {
        IndexError(err.to_string())
    }
}

impl From<serde_json::Error> for IndexError {
    fn from(err: serde_json::Error) -> Self {
        IndexError(err.to_string())
    }
}

pub fn run_command(command: &str, payload: &Value) -> Result<Value, IndexError> {
    match command {
        "hash" => hash_file(payload),
        "scan" => scan_repo(payload),
        "syntax-check" => syntax::syntax_check(payload),
        "outline" => outline::outline(payload),
        "fuzzy_span" => fuzzy::fuzzy_span(payload),
        other => Err(IndexError(format!("unknown command: {other}"))),
    }
}

fn hash_file(payload: &Value) -> Result<Value, IndexError> {
    let path = payload
        .get("path")
        .and_then(Value::as_str)
        .ok_or_else(|| IndexError("missing required field: path".into()))?;
    let content = fs::read_to_string(path)?;
    Ok(json!({"ok": true, "hash": file_hash(&content)}))
}

fn scan_repo(payload: &Value) -> Result<Value, IndexError> {
    let root = payload
        .get("root")
        .and_then(Value::as_str)
        .ok_or_else(|| IndexError("missing required field: root".into()))?;
    let max_files = payload
        .get("max_files")
        .and_then(Value::as_u64)
        .map(|v| v as usize)
        .unwrap_or(DEFAULT_MAX_FILES);
    let extensions: Vec<String> = payload
        .get("extensions")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(|s| s.trim_start_matches('.').to_string())
                .collect()
        })
        .unwrap_or_else(|| DEFAULT_EXTENSIONS.iter().map(|s| s.to_string()).collect());

    let mut candidates: Vec<String> = WalkDir::new(root)
        .into_iter()
        .filter_entry(|entry| {
            // SKIP_DIRS only below depth 0 so a root named target/dist/.venv is scanned
            // (matches the Python tier, which skips within-tree parts only).
            if entry.depth() == 0 {
                return true;
            }
            !(entry.file_type().is_dir()
                && entry
                    .file_name()
                    .to_str()
                    .map(|name| SKIP_DIRS.contains(&name))
                    .unwrap_or(false))
        })
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .filter(|entry| {
            entry
                .path()
                .extension()
                .and_then(|ext| ext.to_str())
                .map(|ext| extensions.iter().any(|allowed| allowed == ext))
                .unwrap_or(false)
        })
        .map(|entry| {
            entry
                .path()
                .strip_prefix(root)
                .unwrap_or(entry.path())
                .to_string_lossy()
                .into_owned()
        })
        .collect();
    candidates.sort();
    candidates.truncate(max_files);

    let root_path = Path::new(root);
    let entries: Vec<Value> = candidates
        .par_iter()
        .filter_map(|rel| {
            let content = fs::read_to_string(root_path.join(rel)).ok()?;
            Some(json!({
                "path": rel,
                "file_hash": file_hash(&content),
                "total_lines": line_count(&content),
            }))
        })
        .collect();

    Ok(json!({"ok": true, "entries": entries, "count": entries.len()}))
}

/// Mirror of `core.hash_patch.file_hash`: normalize CRLF/CR to LF, then SHA-256.
pub fn file_hash(content: &str) -> String {
    let normalized = content.replace("\r\n", "\n").replace('\r', "\n");
    let mut hasher = Sha256::new();
    hasher.update(normalized.as_bytes());
    format!("{:x}", hasher.finalize())
}

/// Mirror of the Python cursor_map line count: `text.count("\n") + (1 if text else 0)`.
fn line_count(content: &str) -> usize {
    let newlines = content.matches('\n').count();
    if content.is_empty() {
        0
    } else {
        newlines + 1
    }
}

#[cfg(feature = "python")]
mod python_module {
    use pyo3::exceptions::PyRuntimeError;
    use pyo3::prelude::*;

    #[pyfunction]
    fn run(command: &str, payload_json: &str) -> PyResult<String> {
        let payload: serde_json::Value = serde_json::from_str(payload_json)
            .map_err(|err| PyRuntimeError::new_err(err.to_string()))?;
        let result = super::run_command(command, &payload)
            .map_err(|err| PyRuntimeError::new_err(err.to_string()))?;
        serde_json::to_string(&result).map_err(|err| PyRuntimeError::new_err(err.to_string()))
    }

    #[pymodule]
    fn supercoder_index_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
        module.add_function(wrap_pyfunction!(run, module)?)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hash_normalizes_newlines() {
        assert_eq!(file_hash("a\r\nb"), file_hash("a\nb"));
        assert_eq!(file_hash("a\rb"), file_hash("a\nb"));
    }

    #[test]
    fn line_count_matches_python_semantics() {
        assert_eq!(line_count(""), 0);
        assert_eq!(line_count("a"), 1);
        assert_eq!(line_count("a\n"), 2);
        assert_eq!(line_count("a\nb"), 2);
    }
}
