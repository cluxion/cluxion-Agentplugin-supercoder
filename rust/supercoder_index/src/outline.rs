//! L0 repo outline: top-level symbol extraction for the repo map.
//!
//! Fail-open like the syntax gate: a language without a grammar reports
//! `checked: false` and an empty symbol list. Symbols cover one nesting
//! level (module items plus class/impl members) — enough for a compact
//! map a small model can hold, not a full AST dump.

use std::fs;

use serde_json::{json, Value};
use tree_sitter::{Node, Parser};

use crate::syntax::{grammar_for, language_for_path};
use crate::IndexError;

const MAX_SYMBOLS: usize = 200;
const SIGNATURE_MAX_CHARS: usize = 120;

pub fn outline(payload: &Value) -> Result<Value, IndexError> {
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

    // JSON parses but has no symbols worth mapping; treat like no grammar.
    let grammar = if language == "json" { None } else { grammar_for(&language) };
    let Some(grammar) = grammar else {
        return Ok(json!({
            "ok": true,
            "checked": false,
            "language": language,
            "reason": "no_outline",
            "symbols": [],
            "symbol_count": 0,
        }));
    };

    let mut parser = Parser::new();
    parser
        .set_language(&grammar)
        .map_err(|err| IndexError(err.to_string()))?;
    let tree = parser
        .parse(&content, None)
        .ok_or_else(|| IndexError("tree-sitter parse returned no tree".into()))?;

    let mut symbols: Vec<Value> = Vec::new();
    collect_level(tree.root_node(), &content, &language, 0, &mut symbols);
    Ok(json!({
        "ok": true,
        "checked": true,
        "language": language,
        "symbols": symbols,
        "symbol_count": symbols.len(),
    }))
}

/// Walk one container level: module items at depth 0, class/impl/mod
/// members at depth 1. Deeper nesting is deliberately not mapped.
fn collect_level(container: Node<'_>, source: &str, language: &str, depth: usize, out: &mut Vec<Value>) {
    let mut cursor = container.walk();
    for child in container.children(&mut cursor) {
        if out.len() >= MAX_SYMBOLS {
            return;
        }
        let node = unwrap_wrapper(child);
        let Some(kind) = symbol_kind(&node, language, depth) else {
            continue;
        };
        out.push(symbol_entry(&node, source, kind, depth));
        if depth == 0 {
            if let Some(body) = member_container(&node, language) {
                collect_level(body, source, language, 1, out);
            }
        }
    }
}

/// Unwrap transparent wrappers (decorators, exports) to the inner definition.
fn unwrap_wrapper(node: Node<'_>) -> Node<'_> {
    match node.kind() {
        "decorated_definition" => node
            .child_by_field_name("definition")
            .unwrap_or(node),
        "export_statement" => node
            .child_by_field_name("declaration")
            .unwrap_or(node),
        _ => node,
    }
}

fn symbol_kind(node: &Node<'_>, language: &str, depth: usize) -> Option<&'static str> {
    let kind = node.kind();
    match language {
        "python" => match kind {
            "function_definition" => Some(if depth == 0 { "function" } else { "method" }),
            "class_definition" => Some("class"),
            _ => None,
        },
        "rust" => match kind {
            "function_item" => Some(if depth == 0 { "fn" } else { "method" }),
            "function_signature_item" => Some("fn"),
            "struct_item" => Some("struct"),
            "enum_item" => Some("enum"),
            "trait_item" => Some("trait"),
            "impl_item" => Some("impl"),
            "mod_item" => Some("mod"),
            _ => None,
        },
        "javascript" | "typescript" | "tsx" => match kind {
            "function_declaration" | "generator_function_declaration" => Some("function"),
            "class_declaration" | "abstract_class_declaration" => Some("class"),
            "interface_declaration" => Some("interface"),
            "enum_declaration" => Some("enum"),
            "type_alias_declaration" => Some("type"),
            "method_definition" if depth > 0 => Some("method"),
            _ => None,
        },
        _ => None,
    }
}

/// Container node whose direct members are depth-1 symbols.
fn member_container<'tree>(node: &Node<'tree>, language: &str) -> Option<Node<'tree>> {
    let expected = match (language, node.kind()) {
        ("python", "class_definition") => "block",
        ("rust", "impl_item" | "trait_item") => "declaration_list",
        ("javascript" | "typescript" | "tsx", "class_declaration" | "abstract_class_declaration") => "class_body",
        _ => return None,
    };
    node.child_by_field_name("body")
        .filter(|body| body.kind() == expected)
}

fn symbol_entry(node: &Node<'_>, source: &str, kind: &str, depth: usize) -> Value {
    let name = node
        .child_by_field_name("name")
        .or_else(|| node.child_by_field_name("type"))
        .and_then(|n| n.utf8_text(source.as_bytes()).ok())
        .unwrap_or("")
        .to_string();
    let signature = node
        .utf8_text(source.as_bytes())
        .ok()
        .and_then(|text| text.lines().next())
        .map(|line| line.trim().chars().take(SIGNATURE_MAX_CHARS).collect::<String>())
        .unwrap_or_default();
    json!({
        "kind": kind,
        "name": name,
        "line": node.start_position().row + 1,
        "end_line": node.end_position().row + 1,
        "depth": depth,
        "signature": signature,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn symbols(content: &str, language: &str) -> Vec<Value> {
        let result = outline(&json!({"content": content, "language": language})).unwrap();
        result["symbols"].as_array().unwrap().clone()
    }

    #[test]
    fn python_classes_methods_functions() {
        let src = "import os\n\nclass Foo:\n    def bar(self):\n        pass\n\n@deco\ndef baz(x: int) -> int:\n    return x\n";
        let found = symbols(src, "python");
        let triples: Vec<(String, String, u64)> = found
            .iter()
            .map(|s| {
                (
                    s["kind"].as_str().unwrap().into(),
                    s["name"].as_str().unwrap().into(),
                    s["line"].as_u64().unwrap(),
                )
            })
            .collect();
        assert!(triples.contains(&("class".into(), "Foo".into(), 3)));
        assert!(triples.contains(&("method".into(), "bar".into(), 4)));
        assert!(triples.contains(&("function".into(), "baz".into(), 8)));
    }

    #[test]
    fn rust_items_and_impl_members() {
        let src = "pub struct Foo;\n\nimpl Foo {\n    pub fn bar(&self) {}\n}\n\nfn main() {}\n";
        let found = symbols(src, "rust");
        let kinds: Vec<&str> = found.iter().map(|s| s["kind"].as_str().unwrap()).collect();
        assert!(kinds.contains(&"struct"));
        assert!(kinds.contains(&"impl"));
        assert!(kinds.contains(&"method"));
        assert!(kinds.contains(&"fn"));
        let method = found.iter().find(|s| s["kind"] == "method").unwrap();
        assert_eq!(method["name"], "bar");
        assert_eq!(method["depth"], 1);
    }

    #[test]
    fn typescript_exports_and_interfaces() {
        let src = "export interface Props { a: number }\n\nexport class View {\n    render(): void {}\n}\n\nexport function helper(): void {}\n\ntype Alias = string;\n";
        let found = symbols(src, "typescript");
        let pairs: Vec<(String, String)> = found
            .iter()
            .map(|s| (s["kind"].as_str().unwrap().into(), s["name"].as_str().unwrap().into()))
            .collect();
        assert!(pairs.contains(&("interface".into(), "Props".into())));
        assert!(pairs.contains(&("class".into(), "View".into())));
        assert!(pairs.contains(&("method".into(), "render".into())));
        assert!(pairs.contains(&("function".into(), "helper".into())));
        assert!(pairs.contains(&("type".into(), "Alias".into())));
    }

    #[test]
    fn signature_is_first_line_trimmed() {
        let found = symbols("def add(a: int,\n        b: int) -> int:\n    return a + b\n", "python");
        assert_eq!(found[0]["signature"], "def add(a: int,");
    }

    #[test]
    fn unsupported_language_fails_open() {
        let result = outline(&json!({"content": "key = 1", "language": "toml"})).unwrap();
        assert_eq!(result["checked"], false);
        assert_eq!(result["reason"], "no_outline");
        assert_eq!(result["symbol_count"], 0);
    }

    #[test]
    fn json_has_no_symbols() {
        let result = outline(&json!({"content": "{\"a\": 1}", "language": "json"})).unwrap();
        assert_eq!(result["checked"], false);
    }

    #[test]
    fn symbol_list_is_capped() {
        let src = "def f():\n    pass\n\n".repeat(500);
        let found = symbols(&src, "python");
        assert_eq!(found.len(), MAX_SYMBOLS);
    }
}
