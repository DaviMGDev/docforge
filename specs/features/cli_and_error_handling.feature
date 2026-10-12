# Feature Specification: CLI & Error Handling
# File: specs/features/cli_and_error_handling.feature

@cli @error_handling
Feature: CLI & Error Handling
  As an operator executing pdf2md
  I want informative terminal feedback, graceful failure recovery, and standard exit codes
  So that I have full operational visibility and resilience in automated pipelines

  Scenario: Live terminal progress dashboard with worker status
    When the CLI executes on a 50-page document
    Then the terminal displays Rich progress bars for all 4 pipeline stages
    And the LLM stage displays dynamic worker slots:
      """
      [Worker 1: Chunk 0] [Worker 2: Chunk 1] [Worker 3: Chunk 2]
      """

  Scenario: Graceful shutdown on SIGINT (Ctrl+C)
    Given 3 LLM worker tasks in flight
    When the user sends a SIGINT signal (Ctrl+C)
    Then the active worker tasks cancel pending requests
    And completed chunk ASTs are saved to the cache directory
    And the application exits cleanly with status code 130
    And a resume hint is printed to stderr

  Scenario: Deterministic raw-text fallback upon repeated LLM errors
    Given an LLM endpoint returning HTTP 500 errors repeatedly for chunk 4
    When chunk 4 exhausts all 3 retry attempts
    Then the worker does not crash the entire conversion job
    And chunk 4 automatically falls back to raw extracted text
    And an admonition warning is prepended:
      """
      > [!WARNING] Page rendered via deterministic raw-text fallback.
      """
    And the conversion job completes with exit code 0

  Scenario: Corrupted PDF document error handling
    Given an invalid, truncated PDF file "damaged.pdf"
    When the user runs "uv run pdf2md damaged.pdf"
    Then the process attempts PyMuPDF repair
    And if unrecoverable, exits with code 2 and message "Error: Corrupted PDF file"

  Scenario: Output format selection (Markdown, JSON, or Both)
    When the user runs "uv run pdf2md document.pdf --format both -o output.md"
    Then "output.md" is generated containing formatted CommonMark
    And "output.json" is generated containing the stitched DocumentAST
    And both files are saved successfully

  Scenario: Output JSON only
    When the user runs "uv run pdf2md document.pdf --format json -o output.json"
    Then "output.json" is generated containing the stitched DocumentAST
    And no ".md" file is emitted

  Scenario: Missing API key validation
    Given no "OPENAI_API_KEY" set in the environment
    When the user runs "uv run pdf2md document.pdf --model gpt-4o"
    Then the process halts before rendering pages
    And exits with code 5 and message "Error: OPENAI_API_KEY is required for model 'gpt-4o'"
