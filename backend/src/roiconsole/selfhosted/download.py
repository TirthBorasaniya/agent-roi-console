"""Download the self-hosted GGUF and verify its SHA-256."""

# ============= Standard Library =============
import hashlib
import logging
from pathlib import Path

# ============= Third-Party =============
import httpx

# ============= Constants =============
logger = logging.getLogger(__name__)
CHUNK_BYTES = 1 << 20
DOWNLOAD_TIMEOUT_S = 60.0


# ============= Download =============


def sha256_of(path: Path) -> str:
    """Return the hex SHA-256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_model(url: str, dest: Path, sha256: str, o_run_from_scratch: bool = False) -> Path:
    """
    Download a model file once and verify it.

    Parameters
    ----------
    url : str
        Pinned download URL (a fixed revision).
    dest : Path
        Target file path.
    sha256 : str
        Expected digest; a mismatch deletes the file and raises.
    o_run_from_scratch : bool
        Re-download even when a verified file exists.

    Returns
    -------
    path : Path
        Verified file.
    """
    if dest.is_file() and not o_run_from_scratch:
        if sha256_of(dest) == sha256:
            logger.info("model already present and verified", extra={"path": str(dest)})
            return dest
        logger.warning("existing model failed verification, downloading again")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_S) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(CHUNK_BYTES):
                handle.write(chunk)
    actual = sha256_of(partial)
    if actual != sha256:
        partial.unlink()
        raise ValueError(f"sha256 mismatch for {url}: expected {sha256}, got {actual}")
    partial.rename(dest)
    return dest
