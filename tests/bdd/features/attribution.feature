Feature: Factor Attribution
  As a portfolio analyst
  I want returns-based factor decomposition
  So that I can understand how much of portfolio performance comes from systematic factors

  Scenario: Attribution decomposes return into three named factors
    Given a storage backend with a backtest NAV series and price data for 4 symbols over 60 days
    And a seed universe CSV for those symbols
    When I compute factor attribution from "2025-01-02" to "2025-03-28"
    Then the attribution result contains exactly 3 factor exposures
    And the factor names are "momentum", "low_volatility", and "liquidity"

  Scenario: R-squared is bounded between 0 and 1
    Given a storage backend with a backtest NAV series and price data for 4 symbols over 60 days
    And a seed universe CSV for those symbols
    When I compute factor attribution from "2025-01-02" to "2025-03-28"
    Then the R-squared is between 0.0 and 1.0

  Scenario: Factor contributions plus alpha are finite
    Given a storage backend with a backtest NAV series and price data for 4 symbols over 60 days
    And a seed universe CSV for those symbols
    When I compute factor attribution from "2025-01-02" to "2025-03-28"
    Then the sum of factor contributions plus alpha is a finite number
