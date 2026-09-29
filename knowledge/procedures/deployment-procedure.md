---
id: proc-deploy
title: Deployment procedure
document_type: deployment_procedure
team: platform-sre
version: 3
---
# Deployment procedure

All services deploy through the CD pipeline with a rolling strategy (maxUnavailable 0, maxSurge 1).
Every deployment gets an incrementing deployment number (e.g. #842) and records the commit SHA and
author. ORM or query changes must include an EXPLAIN plan in the pull request. Database migrations
must be backwards compatible with the previous application revision so that rollbacks stay safe.
