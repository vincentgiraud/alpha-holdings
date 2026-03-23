Feature: Portfolio Holdings State
  As a portfolio manager
  I want holdings tracked with cost basis and realized gains
  So that I can monitor performance and tax position accurately

  Scenario: Buying into an empty portfolio sets book cost to trade price
    Given an empty holdings state
    When I apply a buy of 100 shares of "AAPL" at 150.0
    Then "AAPL" has 100.0 shares with book cost 150.0 per share

  Scenario: Adding to an existing position uses weighted-average book cost
    Given a holdings state with 100 shares of "AAPL" at book cost 100.0
    When I apply a buy of 100 shares of "AAPL" at 200.0
    Then "AAPL" has 200.0 shares with book cost 150.0 per share

  Scenario: Selling a position crystallises realized gain
    Given a holdings state with 100 shares of "AAPL" at book cost 100.0
    When I apply a sell of 50 shares of "AAPL" at 150.0
    Then "AAPL" has 50.0 shares remaining
    And the realized gain for "AAPL" is 2500.0

  Scenario: Selling at a loss shows negative realized gain
    Given a holdings state with 100 shares of "AAPL" at book cost 200.0
    When I apply a sell of 100 shares of "AAPL" at 150.0
    Then the realized gain for "AAPL" is -5000.0

  Scenario: Rebalance persists a holdings snapshot with unrealized gains
    Given a portfolio with target weights for "AAPL" 0.50 and "MSFT" 0.50
    And prices exist for "AAPL" at 200.0 and "MSFT" at 400.0
    When I rebalance the portfolio with value 100000
    Then a holdings snapshot is persisted for the portfolio
    And each position in the snapshot has a non-negative unrealized gain field
