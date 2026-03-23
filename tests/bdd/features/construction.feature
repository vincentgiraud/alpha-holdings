Feature: Portfolio Construction
  As a portfolio builder
  I want target weights derived from equity scores with constraint enforcement
  So that the resulting portfolio respects position limits and minimum diversification

  Scenario: Weights sum to 1.0 after construction
    Given a storage backend with equity scores for "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"
    When I construct a portfolio for as-of date "2026-03-23"
    Then the constructed portfolio target weights sum to 1.0 within tolerance 0.001

  Scenario: No single position exceeds the max weight constraint
    Given a storage backend with equity scores for "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"
    When I construct a portfolio with max single name weight 0.20
    Then no symbol in the portfolio has weight greater than 0.20

  Scenario: Minimum holdings floor is respected
    Given a storage backend with equity scores for 7 symbols where one dominates
    When I construct a portfolio requiring at least 5 holdings
    Then the constructed portfolio contains at least 5 holdings

  Scenario: Higher-scoring symbol receives greater weight than lower-scoring symbol
    Given a storage backend with equity scores where "STRONG" scores higher than "WEAK"
    When I construct a portfolio for as-of date "2026-03-23"
    Then "STRONG" has a higher target weight than "WEAK"
