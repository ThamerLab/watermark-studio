# Contributing to Watermark Studio

Thanks for helping make offline PDF watermarking more reliable.

## Before opening a pull request

1. Check existing issues and reproduce the problem on the current `main` branch.
2. Identify the relevant edition: **standalone HTML** (`standalone/`) or **server** (`app/`).
3. Keep changes focused. Avoid replacing working PDF/Arabic rendering engines without a clear compatibility plan.
4. Never commit real customer documents, sensitive PDF content, private keys, tokens, or credentials.
5. Update the README if user-facing behavior, compatibility, or the deployment procedure changes.

## Local development

```bash
npm ci
npm run build:standalone
npm run test:standalone
```

Browser smoke tests require Chrome or Edge. Always commit the regenerated `watermark-studio.html` when you modify `standalone/src/`.

For the server edition, install `requirements-dev.txt` in a virtual environment and run `python -m pytest -q`. Docker and Proxmox changes should be tested separately because Python unit tests cannot validate an LXC deployment.

## Pull-request checklist

- [ ] I identified the affected edition(s).
- [ ] I tested Arabic and mixed RTL/LTR watermark content where applicable.
- [ ] I checked preview and generated PDF output, including page selection.
- [ ] I verified CSV/ZIP output behavior if touching batch processing.
- [ ] I updated documentation for behavior or operational changes.
- [ ] I did not include confidential input data or secrets.

## Reporting bugs

Include expected vs. observed behavior, browser/OS or Docker/Proxmox versions, reproduction steps, and sanitized example inputs. For performance problems, provide file size and page count without attaching sensitive originals.
