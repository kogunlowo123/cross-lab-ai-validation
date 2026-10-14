# Security Policy

## Supported versions

Security fixes are released for the latest minor version.

## Reporting a vulnerability

Do not open a public issue. Use GitHub private vulnerability reporting
(Security tab, "Report a vulnerability"). Include affected version, a
description of the issue, reproduction steps and impact.

Expected handling:

| Stage | Target |
|---|---|
| Acknowledgement | 3 business days |
| Triage and severity (CVSS v4) | 10 business days |
| Fix for critical/high | 30 days from triage |
| Coordinated disclosure | After a fixed release is available |

## Scope

In scope: code in this repository, including the statistical engine, the
approval and signature path, authentication and authorization, the MCP and
A2A endpoints and the site agent. Issues that could let a model be used at a
site without a valid medical-director signature, or let case-level data
reach the platform, are treated as critical.
