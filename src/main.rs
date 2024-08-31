use std::process::ExitCode;
use std::path::{Path, PathBuf};
use std::fs::File;
use std::io::Write;
use clap::{App, Arg, SubCommand};
use rusqlite::Connection;
use rusqlite_migration::{Migrations, M};

fn main() -> ExitCode {
    let matches = App::new("Codebased")
        .version("0.0.1")
        .author("Max Conradt")
        .about("A search engine for code.")
        .subcommand(SubCommand::with_name("init")
            .about("Initialize the application"))
        .subcommand(SubCommand::with_name("search")
            .about("Run a search against the index")
            .arg(Arg::with_name("query")
                .help("The search query")
                .required(false)
                .index(1))
            .arg(Arg::with_name("limit")
                .short("l")
                .long("limit")
                .value_name("NUMBER")
                .help("Limit the number of results")
                .takes_value(true)))
        .get_matches();

    match matches.subcommand() {
        ("init", Some(_)) => {
            println!("Initializing...");
            match run_init() {
                Ok(_) => println!("Initialization completed successfully."),
                Err(e) => {
                    eprintln!("Initialization failed: {}", e);
                    return ExitCode::FAILURE;
                }
            }
        }
        ("search", Some(search_matches)) => {
            if let Some(query) = search_matches.value_of("query") {
                println!("Searching for: {}", query);
            } else {
                println!("Searching without a query.");
            };
            if let Some(limit) = search_matches.value_of("limit").map(|l| l.parse::<usize>().unwrap()) {
                println!("Limit: {}", limit);
            } else {
                println!("Searching without a limit.");
            };
        }
        _ => {
            println!("Please provide a valid command. Use --help for more information.");
            return ExitCode::FAILURE;
        }
    }

    ExitCode::SUCCESS
}

fn run_init() -> Result<(), Box<dyn std::error::Error>> {
    let root = find_git_root()?;
    create_cbignore(&root)?;
    create_database(&root)?;
    Ok(())
}

fn find_git_root() -> Result<PathBuf, Box<dyn std::error::Error>> {
    let mut current_dir = std::env::current_dir()?;
    loop {
        if current_dir.join(".git").is_dir() {
            return Ok(current_dir);
        }
        if !current_dir.pop() {
            return Err("No Git repository found".into());
        }
    }
}

fn create_cbignore(root: &Path) -> Result<(), std::io::Error> {
    let cbignore_path = root.join(".cbignore");
    let mut file = File::create(cbignore_path)?;
    file.write_all(b"# Add patterns to ignore here, similar to .gitignore\n")?;
    Ok(())
}

fn create_database(root: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let db_path = root.join(".codebased.db");
    let mut conn = Connection::open(db_path)?;
    // Define migrations
    let migrations = Migrations::new(vec![
        M::up(include_str!("migrations/000_core.sql")),
    ]);
    // Apply PRAGMA
    conn.pragma_update(None, "journal_mode", &"WAL")?;
    // Apply migrations
    migrations.to_latest(&mut conn)?;
    Ok(())
}