Feature: Historical Backtesting
  As a portfolio researcher
  I want walk-forward backtests over stored price data
  So that I can evaluate strategy performance with realistic simulation mechanics

  Scenario: Walk-forward backtest produces positive return for uptrending universe
    Given a storage backend with uptrending price histories for 3 symbols over 60 days
    And a seed universe CSV for those symbols
    When I run a backtest from "2025-01-02" to "2025-03-28" with monthly rebalancing
    Then the backtest total return is positive
    And the backtest completed at least 1 rebalance
    And the NAV series contains more than 1 row

  Scenario: Backtest with benchmark computes benchmark total return
    Given a storage backend with uptrending price histories for 2 symbols and a benchmark over 60 days
    And a seed universe CSV for those symbols
    When I run a backtest from "2025-01-02" to "2025-03-28" with benchmark "SPY"
    Then the backtest benchmark total return is not None

  Scenario: Degraded-data warning appears when fundamentals are missing
    Given a storage backend with price histories for 2 symbols but no fundamentals
    And a seed universe CSV for those symbols
    When I run a backtest from "2025-01-02" to "2025-03-28" with monthly rebalancing
    Then the backtest warnings contain a free-source data notice

  Scenario: Monthly rebalance produces more events than quarterly
    Given a storage backend with uptrending price histories for 3 symbols over 120 days
    And a seed universe CSV for those symbols
    When I run a monthly backtest from "2025-01-02" to "2025-06-30"
    And I run a quarterly backtest from "2025-01-02" to "2025-06-30"
    Then the monthly backtest has more rebalance events than the quarterly backtest

  Scenario: Backtest with fundamentals flags degraded symbols without snapshots
    Given a storage backend with price histories for "AAPL" and "MSFT" over 60 days
    And fundamentals snapshots exist only for "AAPL" dated before the first rebalance
    And a seed universe CSV for those symbols
    When I run a backtest from "2025-01-02" to "2025-03-28" with monthly rebalancing
    Then the backtest warnings mention degraded execution for missing fundamentals
