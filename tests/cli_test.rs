use assert_cmd::Command;

#[test]
fn test_init_command() {
    let mut cmd = Command::cargo_bin("codebased").unwrap();
    cmd.arg("init")
        .assert()
        .success()
        .stdout(predicates::str::contains("Initializing..."))
        .stdout(predicates::str::contains("Initialization completed successfully."));
}

#[test]
fn test_search_command_with_query() {
    let mut cmd = Command::cargo_bin("codebased").unwrap();
    cmd.args(&["search", "test query"])
        .assert()
        .success()
        .stdout(predicates::str::contains("Searching for: test query"));
}

#[test]
fn test_search_command_with_limit() {
    let mut cmd = Command::cargo_bin("codebased").unwrap();
    cmd.args(&["search", "--limit", "10"])
        .assert()
        .success()
        .stdout(predicates::str::contains("Limit: 10"));
}

#[test]
fn test_invalid_command() {
    let mut cmd = Command::cargo_bin("codebased").unwrap();
    cmd.arg("invalid_command")
        .assert()
        .failure()
        .stderr(predicates::str::contains("error: Found argument 'invalid_command' which wasn't expected, or isn't valid in this context"))
        .stderr(predicates::str::contains("USAGE:"))
        .stderr(predicates::str::contains("codebased [SUBCOMMAND]"))
        .stderr(predicates::str::contains("For more information try --help"));
}