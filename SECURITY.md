# Security Policy

## Supported versions

Security fixes are applied to the latest tagged release and the current `main`
branch. Earlier releases are not maintained separately. Before reporting a problem,
confirm the running version with:

```text
canvas-ddl --version
```

## Report a vulnerability privately

Do not open a public Issue with exploit details, Canvas tokens, `.env` contents,
authenticated URLs, private course documents, student data or raw command output that
may contain them.

Use GitHub's private vulnerability form:

[Report a vulnerability privately](https://github.com/zhangleqi04-dev/canvas-ddl/security/advisories/new)

If that form is unavailable, open a public Issue containing only the sentence
“Private security contact requested.” Do not include technical details until the
maintainer provides a private channel.

Include the following in the private report when available:

- affected release or commit and operating system;
- affected component, such as Canvas transport, document download, parser/OCR,
  evidence store, installer, CLI or Codex Skill;
- impact and the smallest reproducible example;
- whether the issue exposes credentials, private course content or student data;
- redacted logs, with all tokens, signed URLs and personal/course identifiers removed;
- any suggested mitigation.

If a Canvas token may have been exposed, revoke or rotate it immediately. Do not wait
for the project investigation to finish.

## Security boundaries

Reports are especially useful for credential disclosure, authenticated request
forwarding, unsafe URL/redirect handling, path traversal, archive or document parser
resource exhaustion, unsafe file writes, SQL injection, prompt-injection boundary
bypass, unauthorized Canvas writes, or use of unvalidated document text as a canonical
deadline.

Canvas availability problems, incorrect course content, unsupported legacy formats and
ordinary configuration questions are handled through the normal Issue templates unless
they cross a security boundary.

The project is designed for local, single-user, read-only Canvas access. It does not
provide a hosted service, security bounty or guaranteed response time. Reports are
handled on a best-effort basis, and fixes may be released without maintaining older
versions.
