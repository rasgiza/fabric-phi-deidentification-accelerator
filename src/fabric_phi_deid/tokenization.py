"""
tokenization.py — deterministic, keyed tokenization for PHI de-identification.

Pure Python (no PySpark dependency) so it can be unit-tested locally and reused
as a Spark UDF inside Microsoft Fabric.

Design principles
-----------------
- **Deterministic & keyed**: token = HMAC-SHA256(pepper, namespace + value). The same
  input value always maps to the same token *given the same pepper*, which preserves
  **referential integrity** — a patient tokenizes identically across every table, so
  joins survive de-identification.
- **Stateless**: no per-table state or counters. Tokenization is a pure function of
  (value, pepper, namespace), so it parallelizes cleanly across Spark executors.
- **Irreversible without the crosswalk**: HMAC is one-way. Re-identification is ONLY
  possible via a separately stored crosswalk table (token -> original), which lives in
  a restricted Vault workspace. The pepper alone does not reverse a token.
- **Pepper never in code**: the pepper is a high-entropy secret fetched at runtime from
  Azure Key Vault (see `get_pepper`). Rotating the pepper re-tokenizes everything — the
  breach-recovery story (see docs/pepper_rotation_runbook.md).

The `namespace` argument prevents cross-column collisions and cross-linkage: an MRN and
a provider NPI that happen to share a string value will NOT produce the same token,
because each column tokenizes under its own namespace.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import string

__all__ = [
    "tokenize",
    "tokenize_numeric",
    "tokenize_format_preserving",
    "get_pepper",
    "KEYVAULT_URL_ENV",
    "PEPPER_ENV",
    "DEFAULT_SECRET_NAME",
    "MIN_PEPPER_LENGTH",
]

# Character set used for format-preserving alphanumeric tokens.
_ALNUM = string.ascii_uppercase + string.digits

# Environment variable that supplies the Key Vault URL at runtime. Keeping the URL out of
# source code means the same package works across dev/test/prod tenants without edits and
# nothing tenant-specific is committed to Git.
KEYVAULT_URL_ENV = "PHI_DEID_KEYVAULT_URL"
DEFAULT_SECRET_NAME = "phi-deid-pepper"  # noqa: S105 - Key Vault secret *name*, not a secret value

# Demo/synthetic fallback (Option 2): supply the pepper directly via this environment
# variable when no Fabric managed private endpoint to Key Vault is available (e.g. no F-SKU
# capacity). The production path (Option 1) is Key Vault reached over a managed private
# endpoint; this env var is a convenience for synthetic-data demos and is NEVER used with
# real PHI. It is read from the environment only -- never hardcoded or committed to Git.
PEPPER_ENV = "PHI_DEID_PEPPER"  # noqa: S105 - name of an env var, not a secret value

# A pepper shorter than this is almost certainly not a high-entropy secret. Rejecting short
# peppers is a defense against a misconfigured/placeholder value silently weakening every
# token in the dataset. Generate with e.g. ``secrets.token_urlsafe(48)``.
MIN_PEPPER_LENGTH = 32


def _hmac_digest(pepper: str, namespace: str, value: str) -> bytes:
    """Return the raw HMAC-SHA256 digest for (namespace, value) under `pepper`.

    The namespace is bound into the message with a separator that cannot appear in a
    typical identifier, so ("mrn", "123") and ("mr", "n123") cannot collide.
    """
    if pepper is None or pepper == "":
        raise ValueError(
            "A non-empty pepper is required. Fetch it from Key Vault via get_pepper(); "
            "never call tokenize() with an empty or hardcoded pepper."
        )
    message = f"{namespace}\x1f{value}".encode()
    return hmac.new(pepper.encode("utf-8"), message, hashlib.sha256).digest()


def tokenize(
    value: str | None,
    pepper: str,
    namespace: str = "default",
    length: int = 16,
    prefix: str = "",
) -> str | None:
    """Deterministically tokenize a string value.

    Parameters
    ----------
    value : str | None
        The clear value to tokenize. ``None`` and empty string pass through as-is so that
        missing data is not turned into a spurious token.
    pepper : str
        Secret key from Key Vault. Required; must be non-empty.
    namespace : str
        Column/entity namespace to prevent cross-column collisions (e.g. "mrn", "npi").
    length : int
        Number of hex characters of the digest to keep. 16 hex chars = 64 bits of the
        digest, which is ample for uniqueness at healthcare row counts.
    prefix : str
        Optional human-readable prefix, e.g. "PT-" -> "PT-a1b2c3d4...".

    Returns
    -------
    str | None
        The token, or the original None/empty value unchanged.
    """
    if value is None or value == "":
        return value
    digest = _hmac_digest(pepper, namespace, str(value))
    token = digest.hex()[:length]
    return f"{prefix}{token}"


def tokenize_numeric(
    value: str | None,
    pepper: str,
    namespace: str = "default",
    digits: int = 10,
) -> str | None:
    """Tokenize to a fixed-length numeric string (useful for numeric-looking IDs).

    Produces a zero-padded decimal string derived from the HMAC digest. Deterministic
    and namespace-scoped like :func:`tokenize`.
    """
    if value is None or value == "":
        return value
    digest = _hmac_digest(pepper, namespace, str(value))
    as_int = int.from_bytes(digest[:8], "big") % (10**digits)
    return str(as_int).zfill(digits)


def tokenize_format_preserving(
    value: str | None,
    pepper: str,
    namespace: str = "default",
) -> str | None:
    """Tokenize while preserving length and per-character class (digit/upper/lower).

    Each source character is mapped to a new character of the same class, driven by the
    HMAC digest. Non-alphanumeric characters (dashes, slashes) are preserved in place so
    the token keeps the original shape — handy when a downstream schema expects a
    specific pattern (e.g. an MRN like ``A12-3456`` -> ``Q83-9017``).

    Note: format-preserving tokens carry length/shape information. For maximum
    de-identification prefer plain :func:`tokenize`. This variant is offered for cases
    where a format constraint must be met.
    """
    if value is None or value == "":
        return value
    digest = _hmac_digest(pepper, namespace, str(value))
    out_chars: list[str] = []
    for i, ch in enumerate(str(value)):
        b = digest[i % len(digest)]
        if ch.isdigit():
            out_chars.append(str(b % 10))
        elif ch.isupper():
            out_chars.append(chr(ord("A") + (b % 26)))
        elif ch.islower():
            out_chars.append(chr(ord("a") + (b % 26)))
        else:
            out_chars.append(ch)  # preserve separators/punctuation in place
    return "".join(out_chars)


def get_pepper(
    secret_name: str = DEFAULT_SECRET_NAME,
    key_vault_url: str | None = None,
    *,
    min_length: int = MIN_PEPPER_LENGTH,
) -> str:
    """Fetch the tokenization pepper.

    Two resolution paths are supported, tried in order:

    1. **Demo/synthetic (Option 2)** — if the ``PHI_DEID_PEPPER`` environment variable is
       set, its value is used directly. This is a convenience for synthetic-data demos
       where no Fabric managed private endpoint to Key Vault is available (e.g. no F-SKU
       capacity). It is **never** used with real PHI.
    2. **Production (Option 1)** — otherwise the pepper is read from Azure Key Vault via
       Fabric's ``notebookutils.credentials.getSecret`` over a **managed private
       endpoint**. The ``notebookutils`` import is deliberately local so the module stays
       importable (and unit-testable) outside Fabric.

    For the Key Vault path, the vault URL is resolved, in order, from:
      1. the ``key_vault_url`` argument, or
      2. the ``PHI_DEID_KEYVAULT_URL`` environment variable.
    It is intentionally NOT hardcoded — nothing tenant-specific is committed to Git.

    In a notebook (production)::

        import os
        os.environ["PHI_DEID_KEYVAULT_URL"] = "https://<kv-name>.vault.azure.net/"
        from fabric_phi_deid import get_pepper
        PEPPER = get_pepper()  # never print PEPPER

    In a notebook (synthetic demo)::

        import os
        os.environ["PHI_DEID_PEPPER"] = "<high-entropy value>"  # e.g. secrets.token_urlsafe(48)
        from fabric_phi_deid import get_pepper
        PEPPER = get_pepper()  # never print PEPPER

    Never log, display(), or write the pepper to a table, notebook output, or Git.
    The same ``min_length`` check is applied on both paths so a weak/placeholder value is
    rejected regardless of source.

    Raises
    ------
    RuntimeError
        If neither ``PHI_DEID_PEPPER`` is set nor a Key Vault URL is configured, or if
        notebookutils is unavailable (i.e. not running in Fabric) on the Key Vault path.
    ValueError
        If the resolved pepper is shorter than ``min_length`` (likely a placeholder).
    """
    # Path 1 — demo/synthetic env-var fallback (Option 2).
    env_pepper = os.environ.get(PEPPER_ENV)
    if env_pepper:
        return _validate_pepper(env_pepper, min_length)

    # Path 2 — Key Vault over managed private endpoint (Option 1, production).
    url = key_vault_url or os.environ.get(KEYVAULT_URL_ENV)
    if not url:
        raise RuntimeError(
            "No pepper source configured. Either set the "
            f"{PEPPER_ENV} environment variable (synthetic-data demo), or pass "
            f"key_vault_url=... / set {KEYVAULT_URL_ENV} to your vault URI "
            "(e.g. https://<kv-name>.vault.azure.net/) for the Key Vault path."
        )

    try:
        import notebookutils  # type: ignore  # provided by the Fabric runtime
    except ImportError as exc:  # pragma: no cover - only hit outside Fabric
        raise RuntimeError(
            "get_pepper() must run inside a Microsoft Fabric notebook where "
            "notebookutils is available. For local tests, pass a pepper explicitly "
            f"or set the {PEPPER_ENV} environment variable."
        ) from exc

    pepper = notebookutils.credentials.getSecret(url, secret_name)
    return _validate_pepper(pepper, min_length)


def _validate_pepper(pepper: str | None, min_length: int) -> str:
    """Return `pepper` if it meets the minimum-entropy length bar, else raise.

    Applied to every resolution path so a weak/placeholder value can never silently
    weaken tokenization regardless of whether it came from an env var or Key Vault.
    """
    if pepper is None or len(pepper) < min_length:
        raise ValueError(
            f"Resolved pepper is too short (< {min_length} chars). A production pepper "
            "must be a high-entropy secret; regenerate with secrets.token_urlsafe(48). "
            "Refusing to tokenize with a weak/placeholder pepper."
        )
    return pepper
