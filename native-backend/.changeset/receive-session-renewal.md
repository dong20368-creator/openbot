---
"@agent-wechat/cli": patch
---

Try one bounded renewal for a rejected receive session before requiring a new login. Preserve account identity, logout and credential-generation checks, stop on renewal failure or rapid repeated rejection, and never replay the failed request or any send. Record safe renewal outcome events without credentials.
