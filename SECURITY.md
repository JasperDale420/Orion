# Security

## Reporting Vulnerabilities

Do not file sensitive vulnerabilities in public issues.
Report privately to the project maintainer with:
- affected component/path
- reproduction steps
- impact assessment
- suggested mitigation

## Security Architecture

- Runtime secrets are environment-driven.
- API access for admin endpoints is protected with `x-api-key`.
- Risk controls and execution gates are enforced in execution/core layers.

## Credential Handling

- Never commit real secrets.
- Use `/Users/jacobmcmillan/Empire/Orion/.env.example` for templates.
- Store live credentials outside source control.

## Dependencies

- Keep dependencies current and review Dependabot PRs promptly.
- Run static checks and tests after upgrades.

## Safety-Critical Code

Extra review required for:
- `/Users/jacobmcmillan/Empire/Orion/src/orion/execution/`
- `/Users/jacobmcmillan/Empire/Orion/src/orion/core/` risk and promotion modules
- broker/order submission paths and configuration toggles for trading stage
