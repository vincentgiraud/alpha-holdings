Feature: Asset Allocation
  As an investor
  I want profile-driven sleeve allocation
  So that portfolio risk and optional crypto exposure follow policy rules

  Scenario: Crypto remains disabled for moderate risk even when enabled in profile
    Given an investor profile with fire variant "fat_fire", risk appetite 3, horizon 20 years, withdrawal pattern "regular_drawdown", and crypto enabled true
    When I compute asset allocation
    Then the allocation includes "equity" and "bond" sleeves
    And the allocation excludes "crypto" sleeve
    And the target weights sum to 1.0 within tolerance 0.01

  Scenario: Crypto is included for high-risk profiles when enabled
    Given an investor profile with fire variant "fat_fire", risk appetite 4, horizon 20 years, withdrawal pattern "regular_drawdown", and crypto enabled true
    When I compute asset allocation
    Then the allocation includes "equity" and "bond" sleeves
    And the allocation includes "crypto" sleeve
    And the target weights sum to 1.0 within tolerance 0.01

  Scenario: Shorter horizon increases bond allocation for the same risk profile
    Given two investor profiles with identical fire variant "fat_fire", risk appetite 4, withdrawal pattern "regular_drawdown", and crypto enabled false
    And the first profile has horizon 20 years
    And the second profile has horizon 5 years
    When I compute both asset allocations
    Then the second profile bond target weight is greater than the first profile bond target weight
