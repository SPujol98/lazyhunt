# Security policy

## Supported versions

Only the latest 0.x release receives security fixes.

## Reporting a vulnerability

Report vulnerabilities privately through GitHub private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Please do not open public issues, discussions or pull requests for security problems, and do not include real tokens, `.env` contents, CVs or job postings in a report.

You can expect an acknowledgement, an assessment, and a coordinated fix and disclosure.

## Scope notes

- Secrets (Telegram bot token, bridge secret, optional Gmail OAuth tokens) live only in the user's local `.env` and token files. Reports about secrets committed to the repository are in scope.
- The project never handles Claude credentials: it runs the official `claude` CLI that the user installed and authenticated.
- The local bridge listens on loopback only and is protected by a shared secret; bypasses of either are in scope.
- Terms-of-service questions about third-party portals are not security vulnerabilities.
