# Contributing to MovieSort Pro

Thanks for helping improve MovieSort Pro.

## Reporting problems

Check existing issues first. When filing a bug, include:

- Windows version, Python version (when running from source), and MovieSort Pro version.
- Exact non-sensitive steps to reproduce, whether operation was Copy or Move.
- Whether the source and destination were the same folder.
- The expected behaviour and the actual result, plus a redacted CSV report excerpt.

**Never share API tokens, private computer usernames, full personal file paths, actual movie/video files or a complete library listing publicly.**

## Development

- Use Python 3.10+.
- Run `python -m unittest discover -v` before opening a pull request.
- For any file operation fix, test: a fresh run, an identical second run, a changed-sort second run, missing source, existing destination, same-folder sort, and mixed subtitle files.
- Keep Copy mode as the safe default and do not overwrite existing files automatically.
- Keep new metadata sources optional and document their terms, privacy impact, and rate limits.
- Don't add real API credentials or non-public copyrighted movie files to tests.

Submit focused pull requests with a concise description and what you tested. By submitting code you agree to license your contribution under the project's MIT licence.
