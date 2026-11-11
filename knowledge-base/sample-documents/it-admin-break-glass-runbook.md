---
title: IT Admin Break-Glass Runbook
allowed_groups: [it-staff]
sensitivity: Confidential
owner: IT Operations
---
# IT Admin Break-Glass Runbook

## When to use break-glass accounts

Emergency access (break-glass) accounts are used only when normal administrator sign-in is impossible, for example during a Microsoft Entra ID conditional access misconfiguration or an MFA service outage.

## Procedure

1. Obtain approval from the IT Operations Lead and the Security Operations Center on the incident bridge.
2. Retrieve the credential envelope from the safe in the Network Operations Center; two IT staff members must be present.
3. Sign in from a privileged access workstation only.
4. Record every action in the incident ticket.
5. After use, rotate the credential and file a post-incident review within 2 business days.

## Monitoring

Every break-glass sign-in triggers a high-severity alert in Microsoft Sentinel.
