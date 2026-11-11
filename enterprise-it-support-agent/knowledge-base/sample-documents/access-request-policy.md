---
title: Application Access Request Policy
allowed_groups: [all-employees]
sensitivity: General
owner: Identity and Access Governance
---
# Application Access Request Policy

## Principles

Access to Contoso applications follows the principle of least privilege: request only the role you need to do your job, for only as long as you need it. All access is time-bound and expires automatically.

## How to request access

Ask the IT Support Agent in Microsoft Teams, for example "I need access to Salesforce". The agent collects the application, the role, the duration and a business justification, then sends the request to your manager for approval. You can only request access for yourself.

## Approval rules

Your line manager approves standard roles. Privileged roles, such as administrator, owner, maintainer or any production environment role, also require approval from the application owner. Nobody can approve their own request. If your manager record is missing or incorrect, the request is routed to Access Governance.

## Maximum durations

Standard roles can be granted for up to 180 days (365 days for GitHub and Jira). Privileged roles are limited to 30 days or less, and AWS production roles to a maximum of 14 days. Requests that exceed the maximum are rejected automatically.

## Approval timeout

Approvers have 72 hours to respond. If no decision is made, the request expires and you need to submit a new one.
