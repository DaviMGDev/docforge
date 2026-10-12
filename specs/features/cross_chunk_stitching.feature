# Feature Specification: Cross-Chunk AST Stitching
# File: specs/features/cross_chunk_stitching.feature

@stage4 @stitching
Feature: Cross-Chunk AST Stitching
  As a document synthesizer
  I want to stitch consecutive chunk ASTs and reconcile boundary splits
  So that sentences, tables, and lists broken across page chunks are unified seamlessly

  Background:
    Given two sequential chunks: Chunk 0 (pages 1-8) and Chunk 1 (pages 9-16)

  Scenario: Healing split paragraph across chunk boundary
    Given the last block of Chunk 0 is a paragraph ending with "in modern distributed"
    And the first block of Chunk 1 is a paragraph starting with "architectures, reliability is essential."
    When the stitcher merges Chunk 0 and Chunk 1
    Then the two paragraphs are joined into a single ParagraphBlock
    And the combined content is "in modern distributed architectures, reliability is essential."
    And the trailing block is marked as a continuation

  Scenario: Preserving separated paragraphs when terminal punctuation exists
    Given the last block of Chunk 0 is a paragraph ending with "the experiment was successful."
    And the first block of Chunk 1 is a paragraph starting with "However, further testing is needed."
    When the stitcher merges Chunk 0 and Chunk 1
    Then the two blocks remain separate ParagraphBlocks

  Scenario: Hyphenated word rejoin at page boundary
    Given the last block of Chunk 0 ends with "transfor-"
    And the first block of Chunk 1 begins with "mation of raw inputs"
    When the stitcher merges Chunk 0 and Chunk 1
    Then the hyphen is removed
    And the joined text reads "transformation of raw inputs"

  Scenario: Reconciling severed table across chunk boundary
    Given the last block of Chunk 0 is a TableBlock with columns ["Name", "Value"] and 5 rows
    And the first block of Chunk 1 is a TableBlock with columns ["Name", "Value"] and 4 rows
    When the stitcher merges Chunk 0 and Chunk 1
    Then the two tables are fused into a single TableBlock with columns ["Name", "Value"] and 9 rows

  Scenario: Suppressing running headers and footers
    Given the phrase "ACM Transactions on Graphics | Vol. 42" appears at the top of pages 2, 3, 4, and 5
    When the stitcher runs chrome suppression
    Then the repeated header blocks are pruned from the final AST
    And the true section headings remain unaffected

  Scenario: Disabling stitching with --no-stitch flag
    Given the "--no-stitch" flag is provided
    And the last block of Chunk 0 is an unpunctuated paragraph
    And the first block of Chunk 1 begins with a lowercase continuation
    When the stitcher runs with no_stitch=true
    Then the blocks are strictly concatenated without merging
    And no running headers or footers are pruned
