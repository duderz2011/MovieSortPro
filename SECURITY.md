# Security policy

## Supported versions

The current community preview is **2.1**; no formal security support SLA is offered.

## Reporting a vulnerability

Please do not post working exploit details, API keys, personal file listings or sensitive log entries in public issues.

If a private vulnerability reporting route is enabled in the GitHub repository, use **Security → Report a vulnerability**. Otherwise contact the repository maintainer privately rather than sharing sensitive details in an issue.

## Safety model

MovieSort Pro operates on local files with the privileges of your Windows account. Always keep backups, prefer **Copy** while testing, inspect the preview before moving, and run downloads only from trusted project releases or build them yourself.

API credentials should be entered through the UI. Do not paste them in screenshots, issues, source files or public reports. Configuration files, caches, and local logs are excluded from Git by `.gitignore`.
