Feature: Equity Scoring
  As a portfolio builder
  I want transparent, factor-driven equity scores
  So that construction can rank securities with and without fundamentals coverage

  Scenario: Symbols without fundamentals are scored and flagged as degraded
    Given a storage backend with price snapshots for "AAPL" and "NOVN"
    And a fundamentals snapshot exists only for "AAPL"
    When I score equities for as-of date "2026-03-23"
    Then both symbols appear in the scored output
    And "AAPL" has fundamentals flag true
    And "NOVN" has fundamentals flag false
    And "NOVN" fundamentals factor contributions are all zero

  Scenario: Fundamentals factors contribute to rank differences
    Given a storage backend with price snapshots for "HIGH" and "LOW"
    And "HIGH" has strong fundamentals and "LOW" has weak fundamentals
    When I score equities for as-of date "2026-03-23"
    Then "HIGH" ranks above "LOW" in composite score

  Scenario: Partial fundamentals row does not crash scoring
    Given a storage backend with a price snapshot for "PARTIAL"
    And "PARTIAL" has a fundamentals snapshot with some fields missing
    When I score equities for as-of date "2026-03-23"
    Then "PARTIAL" is scored without error
    And "PARTIAL" has fundamentals flag true
