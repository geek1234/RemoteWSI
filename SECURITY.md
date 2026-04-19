# Security Policy

## Reporting a Vulnerability

Please do not open a public issue for security vulnerabilities.

Instead, contact maintainers privately with:

1. Affected component and version/commit
2. Reproduction steps or proof-of-concept
3. Potential impact
4. Suggested remediation (if available)

We will acknowledge receipt within 3 business days and provide status updates as triage progresses.

## Scope

This policy covers:

- API endpoints (`app/local_main.py`, `app/main.py`)
- Package import/decompression workflow
- Path traversal and file access controls
- Authentication/proxy boundaries in deployments
