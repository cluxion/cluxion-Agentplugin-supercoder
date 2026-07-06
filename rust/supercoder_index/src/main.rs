use std::io::{self, Read};
use std::process;

use serde_json::Value;

use supercoder_index_native::run_command;

fn main() {
    if let Err(err) = run() {
        let payload = serde_json::json!({"ok": false, "error": err.to_string()});
        println!(
            "{}",
            serde_json::to_string(&payload).unwrap_or_else(|_| "{\"ok\":false}".into())
        );
        process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        return Err("usage: supercoder-index <hash|scan|syntax-check|outline|fuzzy_span>".into());
    }
    let payload = read_stdin_json()?;
    let result = run_command(&args[1], &payload)?;
    println!("{}", serde_json::to_string(&result)?);
    Ok(())
}

fn read_stdin_json() -> Result<Value, Box<dyn std::error::Error>> {
    let mut raw = String::new();
    io::stdin().read_to_string(&mut raw)?;
    if raw.trim().is_empty() {
        return Ok(Value::Object(serde_json::Map::new()));
    }
    Ok(serde_json::from_str(&raw)?)
}
