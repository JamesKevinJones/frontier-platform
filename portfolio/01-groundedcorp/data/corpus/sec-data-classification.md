---
doc_id: sec-data-classification
title: Data Classification and Handling Standard
dept: security
doc_type: standard
version: 2.4
---

Data is classified into four tiers: Public, Internal, Confidential and Restricted.
Restricted data includes customer personally identifiable information, payment card data, authentication secrets and health records.
Restricted data must be encrypted at rest using AES-256 and in transit using TLS 1.2 or higher.
Confidential and Restricted data may not be copied to removable media or personal cloud storage under any circumstances.
Access to Restricted data requires manager approval, a documented business need and quarterly recertification of the access list.
Restricted data must be retained for 7 years and then destroyed using a certified secure deletion process.
Any transfer of Restricted data outside the country of origin requires a completed cross-border transfer assessment approved by the privacy office.
Sharing Confidential data with a third party requires an executed non-disclosure agreement and a vendor security review.
