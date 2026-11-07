---
"@agent-wechat/cli": patch
---

Preserve the full 32-byte P-256 shared secret when deriving handshake keys, fixing intermittent authentication failures when its first byte is zero. Retry a transient authenticated-RPC handshake once before any business submission. Preserve safe failure-stage diagnostics, distinguish proven pre-submission failures from unknown delivery, and retain the no-resend rule after a possible submission.
