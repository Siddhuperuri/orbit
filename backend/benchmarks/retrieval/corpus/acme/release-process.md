# Release Process

## Feature flags

New behaviour ships behind a LaunchDarkly feature flag, off by default in production.

## Canary

Every release goes to a canary receiving 5 percent of traffic for thirty minutes. If the error rate exceeds 2 percent, the pipeline rolls the release back automatically.

## Freeze

No releases after 14:00 on Fridays. The current release train is v3.14.2; release notes are published with every train.
