Feature: Performance Reporting
  As a portfolio manager
  I want detailed performance reports from NAV series
  So that I can assess risk-adjusted returns and benchmark-relative metrics

  Scenario: Report from uptrending NAV shows positive Sharpe ratio
    Given a NAV series with consistent daily gains of 0.1% over 60 days
    When I compute a performance report
    Then the Sharpe ratio is positive
    And the volatility is positive

  Scenario: Report includes benchmark-relative metrics when benchmark is available
    Given a NAV series with daily gains of 0.2% and benchmark gains of 0.1% over 60 days
    When I compute a performance report
    Then excess return is not None
    And tracking error is not None
    And information ratio is not None

  Scenario: Report without benchmark omits relative metrics
    Given a NAV series with daily gains of 0.1% over 60 days and no benchmark
    When I compute a performance report
    Then excess return is None
    And tracking error is None
    And information ratio is None

  Scenario: Report surfaces degraded assumptions from backtest metadata
    Given a NAV series with consistent daily gains of 0.1% over 60 days
    And degraded assumptions including "Free-source data warning"
    When I compute a performance report with those assumptions
    Then the report degraded assumptions list is not empty
    And the report degraded assumptions contain "Free-source data warning"
