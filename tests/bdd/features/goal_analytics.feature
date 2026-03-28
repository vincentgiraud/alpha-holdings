Feature: Goal Analytics
  As an investor
  I want goal-based portfolio analysis
  So that I can assess whether my portfolio is on track for my financial targets

  Scenario: Higher wealth target reduces probability of success
    Given an investor profile with 20-year horizon and regular drawdown
    And a portfolio valued at 1000000 with 7% annual return and 15% volatility
    When I compute goal analytics with a target of 2000000
    And I compute goal analytics with a target of 5000000
    Then the probability of reaching 2000000 is greater than the probability of reaching 5000000

  Scenario: Compound-only profile has no safe withdrawal rate
    Given an investor profile with compound-only withdrawal pattern
    And a portfolio valued at 1000000 with 7% annual return and 15% volatility
    When I compute goal analytics
    Then the safe withdrawal rate is None

  Scenario: Shorter horizon widens sequence-of-returns risk band
    Given an investor profile with 5-year horizon and regular drawdown
    And another investor profile with 20-year horizon and regular drawdown
    And a portfolio valued at 1000000 with 7% annual return and 15% volatility
    When I compute goal analytics for both profiles
    Then the 5-year profile has a wider sequence-of-returns risk spread than the 20-year profile

  Scenario: Conservative profile produces lower or equal SWR than aggressive profile
    Given a conservative profile with risk appetite 2 and 10-year horizon
    And an aggressive profile with risk appetite 5 and 10-year horizon
    And a portfolio valued at 1000000 with 7% annual return and 15% volatility
    When I compute goal analytics for both risk profiles
    Then the conservative SWR is less than or equal to the aggressive SWR
