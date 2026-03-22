"""
plugins_router.py — OpenVSX Plugin Marketplace Proxy
=====================================================
All external OpenVSX API calls are made from here; the frontend never
talks to OpenVSX directly, following clean architecture principles.

Endpoints
---------
GET /plugins/search        – Search extensions (cached, paginated, filterable)
GET /plugins/details/{publisher}/{extension}   – Fetch full metadata
GET /plugins/download/{publisher}/{extension}/{version} – Download .vsix file
"""

import asyncio
import logging
import os
import shutil   
import time
import zipfile
from pathlib import Path
from typing import Any, cast

import httpx  # type: ignore
from fastapi import APIRouter, HTTPException, Query  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

OPENVSX_BASE = "https://open-vsx.org/api"
OPENVSX_TOKEN = os.getenv("OPENVSX_TOKEN", "")          # optional auth token

# Storage path for downloaded .vsix files
STORAGE_DIR = Path(__file__).parents[3] / "storage" / "plugins"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

# HTTP client shared across requests (connection pool reuse)
# OpenVSX can occasionally be slow to respond. Giving it 30 seconds.
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# ---------------------------------------------------------------------------
# Simple in-memory cache (TTL = 5 minutes)
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL = 300  # seconds

def _cache_get(key: str) -> Any | None:
    """Return cached value if still valid, else None."""
    if key in _cache:
        ts, value = _cache[key]
        if time.monotonic() - ts < _CACHE_TTL:
            return value
        _cache.pop(key, None)
    return None

def _cache_set(key: str, value: Any) -> None:
    """Store a value with the current timestamp."""
    _cache[key] = (time.monotonic(), value)

# ---------------------------------------------------------------------------
# Download deduplication — prevents concurrent duplicate downloads
# ---------------------------------------------------------------------------

_download_locks: dict[str, asyncio.Lock] = {}

def _get_download_lock(key: str) -> asyncio.Lock:
    if key not in _download_locks:
        _download_locks[key] = asyncio.Lock()
    return _download_locks[key]


def _is_valid_vsix(path: Path) -> bool:
    """Best-effort validation so we never reuse a partial/corrupt VSIX."""
    if not path.exists() or not path.is_file():
        return False

    try:
        if path.stat().st_size <= 0:
            return False
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path, "r") as zf:
            return len(zf.namelist()) > 0
    except (OSError, zipfile.BadZipFile):
        return False

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_headers() -> dict[str, str]:
    """Build common request headers, injecting the OpenVSX token if set."""
    headers = {"Accept": "application/json"}
    if OPENVSX_TOKEN:
        headers["Authorization"] = f"Bearer {OPENVSX_TOKEN}"
    return headers


async def _get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict:
    """Make a GET request and raise an HTTPException on any failure."""
    try:
        response = await client.get(url, params=params, headers=_build_headers())

        # Respect rate-limit responses from OpenVSX
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "60")
            raise HTTPException(
                status_code=429,
                detail=f"OpenVSX rate limit exceeded. Retry after {retry_after}s.",
            )

        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Extension not found on OpenVSX.")

        response.raise_for_status()
        return response.json()

    except httpx.TimeoutException:
        logger.error("Timeout while contacting OpenVSX: %s", url)
        raise HTTPException(status_code=504, detail="OpenVSX request timed out.")
    except httpx.RequestError as exc:
        logger.error("Network error while contacting OpenVSX: %s", exc)
        raise HTTPException(status_code=502, detail="Could not reach OpenVSX.")
    except HTTPException:
        raise   # re-raise FastAPI exceptions as-is
    except Exception as exc:
        logger.exception("Unexpected error contacting OpenVSX: %s", exc)
        raise HTTPException(status_code=500, detail="Internal proxy error.")


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter()


@router.get("/search")
async def search_plugins(
    q: str = Query("", description="Search query"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    size: int = Query(18, ge=1, le=50, description="Results per page"),
    category: str = Query("", description="Extension category filter"),
) -> JSONResponse:
    """
    Proxy the OpenVSX search API with 5-minute in-memory caching.

    Cache key includes all query parameters so different searches
    produce independent cache entries.
    """
    cache_key = f"search:{q}:{offset}:{size}:{category}"
    cached = _cache_get(cache_key)
    if cached:
        logger.debug("Cache HIT for search key: %s", cache_key)
        return JSONResponse(content=cached)

    logger.info("Searching OpenVSX: q=%r offset=%d size=%d category=%r", q, offset, size, category)

    params: dict[str, Any] = {
        "query": q,
        "offset": offset,
        "size": size,
    }
    if category:
        params["category"] = category

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        data = await _get(client, f"{OPENVSX_BASE}/-/search", params=params)

    _cache_set(cache_key, data)
    return JSONResponse(content=data)


@router.get("/details/{publisher}/{extension}")
async def get_plugin_details(publisher: str, extension: str) -> JSONResponse:
    """
    Fetch full metadata for a specific extension from OpenVSX.

    Results are cached for 5 minutes to reduce API calls.
    """
    cache_key = f"details:{publisher}:{extension}"
    cached = _cache_get(cache_key)
    if cached:
        logger.debug("Cache HIT for details: %s/%s", publisher, extension)
        return JSONResponse(content=cached)

    logger.info("Fetching extension details: %s/%s", publisher, extension)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        data = await _get(client, f"{OPENVSX_BASE}/{publisher}/{extension}")

    _cache_set(cache_key, data)
    return JSONResponse(content=data)


@router.get("/download/{publisher}/{extension}/{version}")
async def download_plugin(publisher: str, extension: str, version: str):
    """
    Download and extract a .vsix extension from OpenVSX with real-time progress.
    Returns Server-Sent Events (SSE) with download percentage and extraction status.
    """
    import json as _json
    from starlette.responses import StreamingResponse  # type: ignore

    for part in (publisher, extension, version):
        if not all(c.isalnum() or c in "-._" for c in part):
            raise HTTPException(status_code=400, detail="Invalid publisher/extension/version characters.")

    filename = f"{publisher}.{extension}-{version}.vsix"
    dest_path = STORAGE_DIR / filename
    part_path = STORAGE_DIR / f"{filename}.part"
    extracted_dir = STORAGE_DIR / "extracted" / f"{publisher}.{extension}-{version}"
    extracted_tmp_dir = extracted_dir.with_name(f"{extracted_dir.name}.tmp")

    async def progress_stream():
        def event(stage: str, progress: int, message: str, **extra):
            data = {"stage": stage, "progress": progress, "message": message, **extra}
            return f"data: {_json.dumps(data)}\n\n"

        if dest_path.exists() and extracted_dir.exists() and (extracted_dir / "extension" / "package.json").exists():
            yield event("complete", 100, "Already installed.")
            return

        lock = _get_download_lock(filename)
        async with lock:
            if dest_path.exists() and extracted_dir.exists() and (extracted_dir / "extension" / "package.json").exists():
                yield event("complete", 100, "Already installed.")
                return

            yield event("downloading", 0, "Fetching extension metadata...")
            try:
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
                    meta = await _get(client, f"{OPENVSX_BASE}/{publisher}/{extension}/{version}")
            except Exception as exc:
                yield event("error", 0, f"Failed to fetch metadata: {exc}")
                return

            download_url = meta.get("files", {}).get("download")
            if not download_url:
                yield event("error", 0, "No download URL found.")
                return

            if dest_path.exists() and not _is_valid_vsix(dest_path):
                logger.warning("Removing invalid cached VSIX before redownload: %s", dest_path)
                try:
                    dest_path.unlink()
                except OSError:
                    pass

            need_download = not dest_path.exists()
            if need_download:
                yield event("downloading", 2, "Starting download...")
                try:
                    if part_path.exists():
                        part_path.unlink(missing_ok=True)

                    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0), follow_redirects=True) as client:
                        async with client.stream("GET", download_url, headers=_build_headers()) as stream:
                            if stream.status_code != 200:
                                yield event("error", 0, f"Download failed (HTTP {stream.status_code})")
                                return

                            total = int(stream.headers.get("content-length", 0))
                            downloaded: int = 0
                            last_pct: int = 0
                            checked_first_chunk = False

                            with open(part_path, "wb") as f:
                                async for chunk in stream.aiter_bytes(chunk_size=32768):
                                    chunk_bytes = cast(bytes, chunk)

                                    if not checked_first_chunk and len(chunk_bytes) > 0:
                                        checked_first_chunk = True
                                        if not chunk_bytes.startswith(b"PK"):
                                            preview = chunk_bytes[:120].decode("utf-8", errors="replace")  # type: ignore
                                            raise ValueError(
                                                "Download endpoint returned a non-VSIX payload "
                                                f"(starts with: {preview!r})"
                                            )

                                    f.write(chunk_bytes)  # type: ignore
                                    downloaded += len(chunk_bytes)

                                    if total > 0:
                                        pct = min(int(downloaded * 100 / total), 99)
                                    else:
                                        pct = min(int(downloaded / 1024), 99)

                                    if pct > last_pct + 2:  # type: ignore
                                        last_pct = pct
                                        size_str = f"{downloaded / 1048576:.1f}MB"
                                        total_str = f" / {total / 1048576:.1f}MB" if total > 0 else ""
                                        yield event("downloading", pct, f"Downloading... {size_str}{total_str}")

                            if total > 0 and downloaded != total:
                                raise ValueError(
                                    f"Incomplete download: expected {total} bytes, got {downloaded} bytes"
                                )

                    if dest_path.exists():
                        try:
                            dest_path.unlink()
                        except OSError:
                            pass
                    part_path.replace(dest_path)

                    if not _is_valid_vsix(dest_path):
                        raise ValueError("Downloaded file is not a valid VSIX archive")

                except asyncio.CancelledError:
                    try:
                        part_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise
                except (httpx.TimeoutException, httpx.RequestError, OSError, ValueError) as exc:
                    if part_path.exists():
                        try:
                            part_path.unlink()
                        except OSError:
                            pass
                    if dest_path.exists() and not _is_valid_vsix(dest_path):
                        try:
                            dest_path.unlink()
                        except OSError:
                            pass
                    yield event("error", 0, f"Download failed: {exc}")
                    return

                yield event("downloading", 100, "Download complete")

            yield event("extracting", 0, "Extracting extension package...")
            try:
                if extracted_tmp_dir.exists():
                    shutil.rmtree(extracted_tmp_dir, ignore_errors=True)
                if extracted_dir.exists():
                    shutil.rmtree(extracted_dir, ignore_errors=True)
                extracted_tmp_dir.mkdir(parents=True, exist_ok=True)

                def _extract() -> None:
                    with zipfile.ZipFile(dest_path, "r") as zf:
                        members = [m for m in zf.namelist() if ".." not in m and not m.startswith("/")]
                        for member in members:
                            zf.extract(member, extracted_tmp_dir)

                await asyncio.to_thread(_extract)  # type: ignore
                extracted_tmp_dir.replace(extracted_dir)
            except asyncio.CancelledError:
                shutil.rmtree(extracted_tmp_dir, ignore_errors=True)
                raise
            except Exception as exc:
                logger.error("Failed to extract %s: %s", filename, exc)
                shutil.rmtree(extracted_tmp_dir, ignore_errors=True)
                shutil.rmtree(extracted_dir, ignore_errors=True)
                try:
                    dest_path.unlink(missing_ok=True)
                except OSError:
                    pass
                yield event("error", 0, f"Extraction failed: {exc}")
                return

            yield event("extracting", 100, "Extraction complete")

            file_size = dest_path.stat().st_size
            logger.info("Downloaded and extracted %s (%d bytes)", filename, file_size)
            yield event("complete", 100, f"Installed {publisher}.{extension} successfully", size_bytes=file_size)

    return StreamingResponse(
        progress_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/uninstall/{publisher}/{extension}")
async def uninstall_plugin(publisher: str, extension: str) -> JSONResponse:
    """
    Uninstall an extension by deleting its .vsix archive and extracted folder.
    """
    logger.info("Uninstalling extension: %s/%s", publisher, extension)
    
    deleted_files: int = 0
    deleted_dirs: int = 0

    # Prefix to look for in both /plugins and /plugins/extracted
    prefix = f"{publisher}.{extension}-"

    # 1. Delete .vsix files
    for vsix_file in STORAGE_DIR.glob(f"{prefix}*.vsix"):
        try:
            vsix_file.unlink()
            deleted_files += 1  # type: ignore
            logger.debug("Deleted VSIX: %s", vsix_file.name)
        except OSError as e:
            logger.warning("Failed to delete %s: %s", vsix_file, e)

    # 2. Delete extracted directories
    extracted_base = STORAGE_DIR / "extracted"
    if extracted_base.exists():
        for ext_dir in extracted_base.iterdir():
            if ext_dir.is_dir() and ext_dir.name.startswith(prefix):
                try:
                    shutil.rmtree(ext_dir)
                    deleted_dirs += 1  # type: ignore
                    logger.debug("Deleted extracted directory: %s", ext_dir.name)
                except OSError as e:
                    logger.warning("Failed to delete dir %s: %s", ext_dir, e)

    if deleted_files == 0 and deleted_dirs == 0:
        # Check if there's a locked VSIX file we couldn't delete
        locked_vsix = list(STORAGE_DIR.glob(f"{prefix}*.vsix"))
        if locked_vsix:
            return JSONResponse(status_code=409, content={
                "message": f"VSIX file is locked by another process. Restart the server and try again.",
            })
        return JSONResponse(status_code=404, content={"message": "Plugin not found on disk."})

    return JSONResponse(content={
        "status": "ok",
        "message": f"Uninstalled {publisher}.{extension}",
        "deleted_vsix_count": deleted_files,
        "deleted_dirs_count": deleted_dirs,
    })


@router.get("/package-json/{publisher}/{extension}/{version}")
async def get_plugin_package_json(publisher: str, extension: str, version: str) -> JSONResponse:
    """
    Serve the package.json file from the extracted extension directory.
    This provides the frontend with the extension's available themes/contributions.
    """
    extracted_dir = STORAGE_DIR / "extracted" / f"{publisher}.{extension}-{version}"
    if not extracted_dir.exists():
        raise HTTPException(status_code=404, detail="Extension version not extracted on server.")

    target_file = extracted_dir / "extension" / "package.json"
    
    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(status_code=404, detail="package.json not found inside extension.")

    try:
        import json
        with open(target_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return JSONResponse(content=data)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse package.json %s: %s", target_file, e)
        raise HTTPException(status_code=500, detail="package.json contains invalid JSON.")
    except Exception as e:
        logger.error("Failed to read package.json %s: %s", target_file, e)
        raise HTTPException(status_code=500, detail="Failed to read package.json.")


@router.get("/theme/{publisher}/{extension}/{version}/{theme_path:path}")
async def get_plugin_theme(publisher: str, extension: str, version: str, theme_path: str) -> JSONResponse:
    """
    Serve a theme JSON file directly from the extracted extension directory.
    Uses version in the path to ensure we hit the correct extracted folder.
    """
    if ".." in theme_path or theme_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid theme path")

    extracted_dir = STORAGE_DIR / "extracted" / f"{publisher}.{extension}-{version}"
    if not extracted_dir.exists():
        raise HTTPException(status_code=404, detail="Extension version not extracted on server.")

    # Extension files inside VSIX are stored in an "extension" subfolder
    target_file = extracted_dir / "extension" / theme_path
    
    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(status_code=404, detail="Theme file not found inside extension.")

    try:
        import json
        with open(target_file, "r", encoding="utf-8") as f:
            # VS Code theme JSON files sometimes contain comments, which standard JSON parser hates.
            # Realistically we should use a JSONC parser, but for Phase 2 we use simple regex strip
            import re
            content = f.read()
            # Extremely basic JSON comment stripper for single and multi-line matching
            content = re.sub(r"//.*", "", content)
            content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
            data = json.loads(content)
            
        return JSONResponse(content=data)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse theme JSON %s: %s", target_file, e)
        raise HTTPException(status_code=500, detail="Theme file contains invalid JSON.")
    except Exception as e:
        logger.error("Failed to read theme file %s: %s", target_file, e)
        raise HTTPException(status_code=500, detail="Failed to read theme file.")

