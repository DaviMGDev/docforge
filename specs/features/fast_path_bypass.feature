# Feature Specification: Fast-Path Hybrid Bypass Classifier
# File: specs/features/fast_path_bypass.feature

@stage2_5 @auto_bypass
Feature: Fast-Path Hybrid Bypass Classifier
  As an optimization engine
  I want to detect pure single-column prose pages with no complex visuals or formulas
  So that I can bypass multimodal LLM vision calls and reduce conversion cost and time

  Background:
    Given the "--auto-bypass" flag is enabled

  Scenario: Trivial prose page bypasses LLM
    Given a chunk of 8 pages containing:
      | attribute               | value |
      | embedded images         | 0     |
      | vector drawing paths    | 0     |
      | mathematical symbols    | 0     |
      | tabular delimiters      | 0     |
      | column count            | 1     |
    When the classifier evaluates the chunk
    Then the chunk is classified as "TRIVIAL_PROSE"
    And zero network requests are made to the LLM endpoint
    And the chunk AST is generated directly via deterministic text layout analysis
    And the chunk AST metadata records "bypass_used: true"

  Scenario: Complex page forces multimodal LLM routing
    Given a chunk where page 3 contains a 2-column layout and a vector diagram
    When the classifier evaluates the chunk
    Then page 3 is classified as "COMPLEX"
    And the entire chunk is classified as "COMPLEX"
    And the chunk is submitted to the multimodal LLM worker pool

  Scenario: Mathematical symbols prevent auto-bypass
    Given a page containing inline math symbols like "∑" and "√"
    When the classifier evaluates the page
    Then the page is classified as "COMPLEX"
    And auto-bypass is disabled for this chunk

  Scenario: Auto-bypass disabled by default
    Given the "--auto-bypass" flag is NOT enabled
    And a chunk containing pure single-column text
    When the pipeline processes the chunk
    Then the chunk is still submitted to the multimodal LLM worker pool
