# Feature Specification: Caching & Interruption Resumption
# File: specs/features/caching_and_resume.feature

@cache @resilience
Feature: Caching & Interruption Resumption
  As a user running long conversion jobs
  I want intermediate chunk ASTs cached by PDF SHA-256 hash
  So that interrupted jobs can resume without re-running completed LLM calls

  Background:
    Given a 200-page PDF document with SHA-256 checksum "7f83b165...9069"
    And cache directory ".cache/7f83b165...9069/" is initialized

  Scenario: Resuming conversion after interruption
    Given chunks 0 through 14 (pages 1 to 120) have finished and exist in ".cache/.../chunks/"
    And the conversion was terminated via SIGINT at chunk 15
    When the user runs "uv run docforge document.pdf" again
    Then chunks 0 through 14 are loaded immediately from disk cache
    And no LLM vision API calls are made for chunks 0 through 14
    And the progress bar starts at chunk 15
    And the final document is assembled correctly

  Scenario: Invalidation when model or prompt configuration changes
    Given cached chunks exist for model "gpt-4o"
    When the user runs "uv run docforge document.pdf --model claude-3-5-sonnet"
    Then the cache manifest detects model mismatch
    And the previous cache is invalidated
    And all chunks are re-processed using the new model

  Scenario: Forcing cache bypass
    Given valid cached chunks exist on disk
    When the user runs "uv run docforge document.pdf --no-cache"
    Then existing cache files are ignored
    And all chunks are re-rendered and re-parsed from scratch

  Scenario: Sliding window disk and memory cleanup
    Given chunk 3 finishes LLM inference successfully
    When chunk 3 AST is written to the cache
    Then the rendered page PNG images for chunk 3 are purged from disk and memory
    And resident RAM usage remains under 512 MB throughout the run
