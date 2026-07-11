//! L1 syntax gate: tree-sitter parse check for patched files.
//!
//! The gate is fail-open by design: an unsupported language reports
//! `checked: false` and never blocks a patch. A supported language that
//! fails to parse reports structured ERROR/MISSING locations so the host
//! model can retry with precise feedback.

use std::fs;

use serde_json::{json, Value};
use tree_sitter::{Language, Node, Parser};

use crate::IndexError;

const MAX_REPORTED_ERRORS: usize = 20;
const SNIPPET_MAX_CHARS: usize = 120;

pub const SUPPORTED_LANGUAGES: [&str; 6] =
    ["python", "rust", "javascript", "typescript", "tsx", "json"];

pub fn syntax_check(payload: &Value) -> Result<Value, IndexError> {
    let content = match payload.get("content").and_then(Value::as_str) {
        Some(text) => text.to_string(),
        None => {
            let path = payload
                .get("path")
                .and_then(Value::as_str)
                .ok_or_else(|| IndexError("missing required field: content or path".into()))?;
            fs::read_to_string(path)?
        }
    };
    let language = payload
        .get("language")
        .and_then(Value::as_str)
        .map(str::to_string)
        .or_else(|| {
            payload
                .get("path")
                .and_then(Value::as_str)
                .and_then(language_for_path)
        })
        .unwrap_or_default();

    let Some(grammar) = grammar_for(&language) else {
        return Ok(json!({
            "ok": true,
            "checked": false,
            "language": language,
            "reason": "no_parser",
            "valid": true,
            "errors": [],
            "error_count": 0,
        }));
    };

    let mut parser = Parser::new();
    parser
        .set_language(&grammar)
        .map_err(|err| IndexError(err.to_string()))?;
    let tree = parser
        .parse(&content, None)
        .ok_or_else(|| IndexError("tree-sitter parse returned no tree".into()))?;

    let mut errors: Vec<Value> = Vec::new();
    collect_errors(tree.root_node(), &content, &mut errors);
    let valid = errors.is_empty();
    Ok(json!({
        "ok": true,
        "checked": true,
        "language": language,
        "valid": valid,
        "errors": errors,
        "error_count": if valid { 0 } else { errors.len() },
    }))
}

pub fn language_for_path(path: &str) -> Option<String> {
    let extension = path.rsplit('.').next()?.to_ascii_lowercase();
    let language = match extension.as_str() {
        "py" => "python",
        "rs" => "rust",
        "js" | "mjs" | "cjs" | "jsx" => "javascript",
        "ts" | "mts" | "cts" => "typescript",
        "tsx" => "tsx",
        "json" => "json",
        "toml" => "toml",
        _ => return None,
    };
    Some(language.to_string())
}

pub(crate) fn grammar_for(language: &str) -> Option<Language> {
    match language {
        "python" => Some(tree_sitter_python::LANGUAGE.into()),
        "rust" => Some(tree_sitter_rust::LANGUAGE.into()),
        "javascript" => Some(tree_sitter_javascript::LANGUAGE.into()),
        "typescript" => Some(tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()),
        "tsx" => Some(tree_sitter_typescript::LANGUAGE_TSX.into()),
        "json" => Some(tree_sitter_json::LANGUAGE.into()),
        _ => None,
    }
}

/// Iterative walk (explicit stack) so deeply nested sources cannot
/// overflow the call stack; capped at MAX_REPORTED_ERRORS findings.
fn collect_errors(root: Node<'_>, source: &str, errors: &mut Vec<Value>) {
    let lines: Vec<&str> = source.lines().collect();
    let mut stack = vec![root];
    while let Some(node) = stack.pop() {
        if errors.len() >= MAX_REPORTED_ERRORS {
            return;
        }
        if node.is_error() || node.is_missing() {
            let start = node.start_position();
            let line_text = lines
                .get(start.row)
                .map(|line| truncate(line, SNIPPET_MAX_CHARS))
                .unwrap_or_default();
            let kind = if node.is_missing() {
                "missing"
            } else {
                "error"
            };
            let message = if node.is_missing() {
                format!("missing {}", node.kind())
            } else {
                "syntax error".to_string()
            };
            errors.push(json!({
                "line": start.row + 1,
                "column": start.column + 1,
                "kind": kind,
                "message": message,
                "snippet": line_text,
            }));
            // ERROR subtrees only repeat the same span; don't descend.
            if node.is_error() {
                continue;
            }
        }
        if node.has_error() {
            for index in (0..node.child_count()).rev() {
                if let Some(child) = node.child(index) {
                    stack.push(child);
                }
            }
        }
    }
}

fn truncate(text: &str, max_chars: usize) -> String {
    text.chars().take(max_chars).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn check(content: &str, language: &str) -> Value {
        syntax_check(&json!({"content": content, "language": language})).unwrap()
    }

    #[test]
    fn valid_python_passes() {
        let result = check("def add(a, b):\n    return a + b\n", "python");
        assert_eq!(result["checked"], true);
        assert_eq!(result["valid"], true);
        assert_eq!(result["error_count"], 0);
    }

    #[test]
    fn broken_python_reports_location() {
        let result = check("def add(a, b:\n    return a + b\n", "python");
        assert_eq!(result["valid"], false);
        let errors = result["errors"].as_array().unwrap();
        assert!(!errors.is_empty());
        assert!(errors[0]["line"].as_u64().unwrap() >= 1);
    }

    #[test]
    fn broken_json_and_rust_detected() {
        assert_eq!(check("{\"a\": 1,}", "json")["valid"], false);
        assert_eq!(check("fn main( { let = ; }", "rust")["valid"], false);
        assert_eq!(check("{\"a\": 1}", "json")["valid"], true);
        assert_eq!(check("fn main() {}", "rust")["valid"], true);
    }

    #[test]
    fn rust_borrow_of_identifier_named_raw_is_valid() {
        let source = "fn f(raw: String) { let _ = foo(&raw); }";
        assert_eq!(check(source, "rust")["valid"], true);
    }

    #[test]
    fn rust_raw_borrow_syntax_is_valid() {
        let source = "fn f(mut x: i32) { let _ = &raw const x; let _ = &raw mut x; }";
        assert_eq!(check(source, "rust")["valid"], true);
    }

    #[test]
    fn unsupported_language_is_fail_open() {
        let result = check("whatever", "toml");
        assert_eq!(result["checked"], false);
        assert_eq!(result["valid"], true);
        assert_eq!(result["reason"], "no_parser");
    }

    #[test]
    fn language_detection_from_path() {
        assert_eq!(language_for_path("src/app.py").unwrap(), "python");
        assert_eq!(language_for_path("ui/View.tsx").unwrap(), "tsx");
        assert_eq!(language_for_path("Cargo.toml").unwrap(), "toml");
        assert!(language_for_path("notes.txt").is_none());
        assert!(language_for_path("README").is_none());
    }

    #[test]
    fn error_list_is_capped() {
        let broken = "def f(:\n".repeat(200);
        let result = check(&broken, "python");
        assert_eq!(result["valid"], false);
        assert!(result["errors"].as_array().unwrap().len() <= MAX_REPORTED_ERRORS);
    }
}
