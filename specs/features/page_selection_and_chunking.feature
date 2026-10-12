# Feature Specification: Page Selection & Chunk Partitioning
# File: specs/features/page_selection_and_chunking.feature

@stage1 @partition
Feature: Page Selection & Chunk Partitioning
  As a user converting a PDF document
  I want to specify exact page ranges using print syntax or Python slice notation
  So that I can extract subsets of the document efficiently partitioned into chunks of at most 4 pages (or adaptive)

  Background:
    Given a source PDF document "sample.pdf" with 500 total pages

  Scenario: Default full document chunking with 4 pages per chunk
    When I partition the document without specifying a page range
    Then all 500 pages from 1 to 500 are selected
    And the pages are partitioned into 125 chunks
    And each chunk contains exactly 4 pages

  Scenario: Adaptive chunk sizing on dense pages
    Given a document where pages 1 to 4 have heavy diagrams and 1500 words per page
    When the adaptive chunker evaluates pages 1 to 4
    Then the chunk size dynamically scales down to 1 page per chunk
    And 4 independent chunks are generated instead of 1

  Scenario Outline: Print-style page range parsing
    When I specify the page range "<range_spec>"
    Then the resolved pages match "<expected_pages>"
    And the total count of resolved pages is <expected_count>

    Examples:
      | range_spec         | expected_pages     | expected_count |
      | 1-10               | 1 to 10            | 10             |
      | 5                  | 5                  | 1              |
      | 1, 3, 5-7          | 1, 3, 5, 6, 7      | 5              |
      | 1-200, 401-500     | 1..200 and 401..500| 300            |
      | 10-15, 12-18       | 10 to 18 (deduped) | 9              |

  Scenario Outline: Python slice notation parsing
    When I specify the page range using Python slice "<slice_spec>"
    Then the resolved 1-based pages match "<expected_pages>"

    Examples:
      | slice_spec         | expected_pages     |
      | [0:10]             | 1 to 10            |
      | [:5]               | 1 to 5             |
      | [-10:]             | 491 to 500         |
      | [0:5] + [9:12]     | 1..5, 10..12       |
      | [0:10:2]           | 1, 3, 5, 7, 9      |

  Scenario: Disjoint ranges produce separated chunks
    When I specify the page range "1-5, 20-25"
    Then chunk 0 contains pages [1, 2, 3, 4, 5]
    And chunk 1 contains pages [20, 21, 22, 23, 24, 25]
    And chunk 0 and chunk 1 are marked as independent contiguous sub-chunks

  Scenario: Out of bounds page specification raises error
    When I specify the page range "600-700"
    Then the partitioner raises an "InvalidPageRangeException"
    And no rendering or extraction is performed

  Scenario: Unselected pages are never loaded or extracted
    When I specify the page range "1-4"
    Then only pages 1, 2, 3, and 4 are rendered to images
    And pages 5 through 500 are never loaded into memory
