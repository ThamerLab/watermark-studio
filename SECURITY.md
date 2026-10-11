# Security policy

## Supported editions

The standalone browser edition and the legacy Docker server edition are maintained in the same repository but have **different trust boundaries**.

- **Standalone:** PDF processing takes place in your own browser. Review extensions and local-device security; a generated PDF is not cryptographically tamper-proof.
- **Server:** document contents are uploaded to the server you deploy. The application does not include a complete authentication/authorization system and **must not be exposed directly to the public internet**. Restrict access at the network boundary, use VPN or HTTPS with authentication if needed, and review data retention and upload limits.

## Reporting vulnerabilities

For a suspected vulnerability, **do not publish proof-of-concept data, API secrets, or confidential PDFs in a public GitHub issue**. Use GitHub's **Report a vulnerability** private reporting option if enabled for this repository. If it is not available, contact the maintainer privately through a verified channel rather than creating a public exploit report.

Include affected version/commit, impact, sanitized reproduction steps, and proposed mitigation if known.

## Deployment checklist

- Keep browser, OS, dependencies, Docker images, Proxmox host, and guest updated.
- Review external contributions and install scripts before running them with elevated privileges.
- Limit public exposure and data retention; back up configurations where necessary.
- Prefer unprivileged accounts/containers and avoid storing secrets in Git.
