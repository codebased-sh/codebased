use std::process::ExitCode;
use clap::{App, Arg, SubCommand};

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
            // Add your init logic here
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