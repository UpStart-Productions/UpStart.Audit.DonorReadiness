"""
config.py — Donor Readiness Audit
Single source of truth for values that were previously hardcoded in multiple
places and had drifted out of sync (e.g. the CLI and the Lambda path were
running two different Claude models without anyone deciding that on purpose).

Import DEFAULT_MODEL wherever a model string is needed instead of hardcoding
a literal or duplicating an `os.environ.get(...)` default.
"""

import os

# The model used for report generation. Override with the AUDIT_MODEL env var
# if needed, but the fallback here is the only fallback — don't add a second
# hardcoded default somewhere else, that's exactly how the CLI and Lambda
# paths ended up on different models before.
DEFAULT_MODEL = os.environ.get('AUDIT_MODEL', 'claude-opus-4-8')
