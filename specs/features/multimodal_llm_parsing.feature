# Feature Specification: Multimodal LLM Structural Parsing
# File: specs/features/multimodal_llm_parsing.feature

@stage3 @llm
Feature: Multimodal LLM Structural Parsing
  As an AST generator
  I want to submit page images alongside raw ground-truth text to a multimodal LLM
  So that tables, LaTeX formulas, code listings, and image placements are parsed into structured AST blocks

  Background:
    Given a multimodal LLM worker pool configured with model "gpt-4o"
    And an asynchronous semaphore with capacity 3

  Scenario: Strict concurrency bounding to 3 concurrent requests
    Given 10 document chunks ready for LLM analysis
    When all 10 chunks are submitted concurrently to the worker pool
    Then at most 3 requests are in-flight simultaneously across the network
    And remaining chunks wait in queue until an active worker releases its semaphore slot

  Scenario: Structured JSON Output conforming to schema
    Given a chunk containing page images, raw text, and extracted asset metadata
    When the LLM finishes processing the chunk
    Then the response is validated against the DocumentChunkAST schema
    And the returned chunk contains an ordered list of typed blocks
    And all blocks contain valid "type", "page", and "content" fields

  Scenario: Complex table transcription into GFM and HTML
    Given a page image containing a table with merged header cells across two columns
    When the LLM parses the table
    Then a TableBlock is emitted
    And the table is rendered using clean HTML "<table>" syntax with "colspan=2"
    And cell contents match the raw ground-truth text

  Scenario: Mathematical equation conversion to standard LaTeX
    Given a page containing inline formula "E = mc^2" and display equation "f(x) = \int_0^1 x dx"
    When the LLM parses the mathematics
    Then the inline formula is emitted as a MathBlock with math_mode="inline" and content "E = mc^2"
    And the display equation is emitted as a MathBlock with math_mode="block" and content "f(x) = \int_0^1 x dx"
    And raw ASCII approximations are replaced with proper LaTeX syntax

  Scenario: Code block language inference
    Given a page displaying a Python function with syntax highlighting
    When the LLM parses the code block
    Then a CodeBlock is emitted with language="python"
    And indentation and special characters are preserved exactly from the raw text

  Scenario: Visual asset placement matching
    Given an extracted asset "assets/asset_p2_01.png" in the inventory manifest
    And page 2 shows Figure 1 matching this visual graphic
    When the LLM identifies the figure
    Then an ImageBlock is emitted with:
      | field      | value                   |
      | asset_path | assets/asset_p2_01.png  |
      | caption    | Figure 1: Architecture  |
      | alt_text   | Architecture diagram    |
