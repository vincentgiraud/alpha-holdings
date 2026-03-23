Feature: Portfolio Rebalancing
  As a portfolio manager
  I want trade proposals generated from weight changes
  So that the portfolio can be moved towards target allocations efficiently

  Scenario: First rebalance produces only buy orders
    Given a storage backend with target weights for "AAPL" 0.60 and "MSFT" 0.40
    And prices exist for "AAPL" at 175.0 and "MSFT" at 410.0
    And no prior portfolio weights exist
    When I rebalance the portfolio with value 100000
    Then all trade proposals have side "buy"
    And the trade proposals cover "AAPL" and "MSFT"

  Scenario: Rebalance after a weight shift produces buys and sells
    Given a storage backend with prior weights for "AAPL" 0.50 and "MSFT" 0.50
    And new target weights for "AAPL" 0.30 and "MSFT" 0.30 and "GOOGL" 0.40
    And prices exist for "AAPL" at 175.0 and "MSFT" at 410.0 and "GOOGL" at 165.0
    When I rebalance the portfolio with value 100000
    Then the trade proposals include at least one "buy" and one "sell"
    And "AAPL" has a "sell" proposal

  Scenario: Unchanged weight does not generate a trade
    Given a storage backend with prior weights for "AAPL" 0.50 and "MSFT" 0.50
    And new target weights for "AAPL" 0.30 and "MSFT" 0.50 and "GOOGL" 0.20
    And prices exist for "AAPL" at 175.0 and "MSFT" at 410.0 and "GOOGL" at 165.0
    When I rebalance the portfolio with value 100000
    Then "MSFT" does not appear in the trade proposals
