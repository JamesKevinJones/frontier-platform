---
doc_id: it-access-control
title: Access Control Standard
dept: it
doc_type: standard
version: 3.2
---

All production systems require multi-factor authentication (MFA) for human users.
Service accounts must use short-lived credentials rotated at least every 90 days and must not be shared across applications.
Privileged access is granted via just-in-time elevation and expires within 8 hours.
Remote access to the corporate network is permitted only through the approved VPN with device posture checks.
Employees must not store production secrets in source control, chat, or personal devices.
Access reviews for critical systems are conducted quarterly by application owners with IT Security oversight.
Lost or stolen devices must be reported to the Service Desk within 1 hour of discovery.
