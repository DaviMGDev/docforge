# Feature Specification: Markdown Synthesis & Navigation
# File: specs/features/markdown_synthesis_and_toc.feature

@stage5 @markdown @toc
Feature: Markdown Synthesis & Navigation
  As a document consumer
  I want a clean CommonMark/GFM output document with page navigation markers and an interactive Table of Contents
  So that I can easily navigate large documents and trace content back to original PDF pages

  Background:
    Given a stitched DocumentAST representing a 100-page document

  Scenario Outline: Page boundary marker formatting
    When markdown is rendered with "--page-markers <marker_mode>"
    Then page 42 is prefixed with "<expected_marker_syntax>"

    Examples:
      | marker_mode | expected_marker_syntax                                          |
      | anchor      | <a id="page-42"></a>\n<!-- [Page 42] -->                        |
      | badge       | <small class="pdf-page-indicator">📄 Page 42</small>\n---       |
      | comment     | <!-- [Page 42] -->                                              |
      | none        | (empty string)                                                  |

  Scenario: Functional Table of Contents synthesized from PDF bookmarks
    Given the source PDF has native outline bookmarks:
      | level | title                | page |
      | 1     | 1. Introduction      | 3    |
      | 2     | 1.1 Problem Statement| 4    |
      | 1     | 2. Methodology       | 12   |
    And the AST contains heading "1. Introduction" with slug "1-introduction"
    When the TOC generator synthesizes the document
    Then the generated Markdown begins with:
      """
      ## Table of Contents
      - [1. Introduction](#1-introduction) *(p. 3)*
        - [1.1 Problem Statement](#11-problem-statement) *(p. 4)*
      - [2. Methodology](#2-methodology) *(p. 12)*
      """

  Scenario: Fallback TOC link to page anchor when heading slug is missing
    Given a PDF bookmark points to "Appendix B" on page 85
    And page 85 contains no explicit HeadingBlock
    When the TOC is synthesized
    Then the TOC entry links directly to the page anchor:
      """
      - [Appendix B](#page-85) *(p. 85)*
      """

  Scenario: Dotted leader line transcription in printed TOCs
    Given a printed TOC page containing:
      """
      Chapter 1: Overview ..................... 5
      Chapter 2: Deep Learning ................ 18
      """
    When printed TOC transcription is applied
    Then the lines are transformed to:
      """
      - [Chapter 1: Overview](#chapter-1-overview) *(p. 5)*
      - [Chapter 2: Deep Learning](#chapter-2-deep-learning) *(p. 18)*
      """

  Scenario: Disabling Table of Contents generation
    When markdown is rendered with the "--no-toc" flag
    Then no "## Table of Contents" header is present in the output document
