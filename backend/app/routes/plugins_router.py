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
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

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
        del _cache[key]
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

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
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

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        data = await _get(client, f"{OPENVSX_BASE}/{publisher}/{extension}")

    _cache_set(cache_key, data)
    return JSONResponse(content=data)


@router.get("/download/{publisher}/{extension}/{version}")
async def download_plugin(publisher: str, extension: str, version: str) -> JSONResponse:
    """
    Download a .vsix extension file from OpenVSX and store it locally.

    Storage path: storage/plugins/{publisher}.{extension}-{version}.vsix

    Uses per-file async locks to prevent duplicate concurrent downloads of
    the same extension version.
    """
    # Sanitise inputs to prevent path traversal attacks
    for part in (publisher, extension, version):
        if not all(c.isalnum() or c in "-._" for c in part):
            raise HTTPException(status_code=400, detail="Invalid publisher/extension/version characters.")

    filename = f"{publisher}.{extension}-{version}.vsix"
    dest_path = STORAGE_DIR / filename

    # Return early if already downloaded
    if dest_path.exists():
        logger.info("Plugin already stored: %s", filename)
        return JSONResponse(content={
            "status": "ok",
            "message": "Already downloaded.",
            "path": str(dest_path.relative_to(Path(__file__).parents[3])),
        })

    lock = _get_download_lock(filename)
    async with lock:
        # Double-check after acquiring lock (another coroutine may have just finished)
        if dest_path.exists():
            return JSONResponse(content={
                "status": "ok",
                "message": "Already downloaded.",
                "path": str(dest_path.relative_to(Path(__file__).parents[3])),
            })

        # First, fetch extension metadata to get the real download URL
        logger.info("Fetching download URL for %s/%s@%s", publisher, extension, version)
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            meta = await _get(client, f"{OPENVSX_BASE}/{publisher}/{extension}/{version}")

        # OpenVSX returns the .vsix download URL in the response
        download_url = meta.get("files", {}).get("download")
        if not download_url:
            raise HTTPException(
                status_code=404,
                detail=f"No download URL found for {publisher}.{extension}-{version}.",
            )

        # Download the .vsix file with streaming to avoid loading it all into memory
        logger.info("Downloading .vsix from %s", download_url)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0), follow_redirects=True) as client:
                async with client.stream("GET", download_url, headers=_build_headers()) as stream:
                    if stream.status_code != 200:
                        raise HTTPException(
                            status_code=stream.status_code,
                            detail="Failed to download .vsix from OpenVSX.",
                        )
                    with open(dest_path, "wb") as f:
                        async for chunk in stream.aiter_bytes(chunk_size=8192):
                            f.write(chunk)
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            # Clean up partial file on error
            if dest_path.exists():
                dest_path.unlink()
            logger.error("Download failed for %s: %s", filename, exc)
            raise HTTPException(status_code=502, detail="Failed to download extension file.")

        # Extract the .vsix file to storage/plugins/extracted/{publisher}.{extension}-{version}
        extracted_dir = STORAGE_DIR / "extracted" / f"{publisher}.{extension}-{version}"
        extracted_dir.mkdir(parents=True, exist_ok=True)
        try:
            logger.info("Extracting %s to %s", filename, extracted_dir)
            def _extract():
                with zipfile.ZipFile(dest_path, "r") as zf:
                    # Very basic zip-slip mitigation
                    for member in zf.namelist():
                        if ".." in member or member.startswith("/"):
                            continue
                        zf.extract(member, extracted_dir)
            await asyncio.to_thread(_extract)
        except Exception as exc:
            logger.error("Failed to extract %s: %s", filename, exc)
            shutil.rmtree(extracted_dir, ignore_errors=True)
            # Ensure the corrupt/unextractable vsix is deleted so we can retry next time
            dest_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Failed to extract extension package.")

    file_size = dest_path.stat().st_size
    logger.info("Downloaded and extracted %s (%d bytes)", filename, file_size)

    return JSONResponse(content={
        "status": "ok",
        "message": f"Extension {publisher}.{extension}-{version} downloaded and extracted successfully.",
        "path": str(dest_path.relative_to(Path(__file__).parents[3])),
        "size_bytes": file_size,
    })


@router.delete("/uninstall/{publisher}/{extension}")
async def uninstall_plugin(publisher: str, extension: str) -> JSONResponse:
    """
    Uninstall an extension by deleting its .vsix archive and extracted folder.
    """
    logger.info("Uninstalling extension: %s/%s", publisher, extension)
    
    deleted_files = 0
    deleted_dirs = 0

    # Prefix to look for in both /plugins and /plugins/extracted
    prefix = f"{publisher}.{extension}-"

    # 1. Delete .vsix files
    for vsix_file in STORAGE_DIR.glob(f"{prefix}*.vsix"):
        try:
            vsix_file.unlink()
            deleted_files += 1
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
                    deleted_dirs += 1
                    logger.debug("Deleted extracted directory: %s", ext_dir.name)
                except OSError as e:
                    logger.warning("Failed to delete dir %s: %s", ext_dir, e)

    if deleted_files == 0 and deleted_dirs == 0:
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


@router.get("/file/{publisher}/{extension}/{version}/{file_path:path}", summary="Read arbitrary file from extracted extension")
async def get_plugin_file(publisher: str, extension: str, version: str, file_path: str):
    """
    Serves a raw file from the extracted extension directory.
    Useful for serving JS/browser files for web workers.
    """
    try:
        # Prevent path traversal
        clean_file_path = os.path.normpath(file_path.strip("/"))
        if clean_file_path.startswith("..") or os.path.isabs(clean_file_path):
            raise HTTPException(status_code=400, detail="Invalid file path")
            
        plugin_id = f"{publisher}.{extension}-{version}"
        extracted_dir = STORAGE_DIR / "extracted" / plugin_id # Assuming PLUGINS_EXTRACTED_DIR is STORAGE_DIR / "extracted"
        
        target_file = extracted_dir / "extension" / clean_file_path
        
        if not target_file.exists() or not target_file.is_file():
            # Sometimes things are not under 'extension/' but at the root or another structure
            target_file_fallback = extracted_dir / clean_file_path
            if target_file_fallback.exists() and target_file_fallback.is_file():
                target_file = target_file_fallback
            else:
                logger.warning(f"File not found: {target_file} or {target_file_fallback}")
                raise HTTPException(status_code=404, detail="File not found in extension")
                
        # Determine strict MIME types for JS/CSS/etc to avoid browser strict MIME type errors
        media_type = "text/plain"
        if target_file.suffix == ".js":
            media_type = "application/javascript"
        elif target_file.suffix == ".css":
            media_type = "text/css"
        elif target_file.suffix == ".json":
            media_type = "application/json"
        elif target_file.suffix == ".html":
            media_type = "text/html"

        content = target_file.read_bytes()
        return Response(content=content, media_type=media_type)
        
    except Exception as e: # Catch all exceptions for logging and consistent error response
        logger.error("Failed to read file %s from extension %s: %s", file_path, plugin_id, e)
        raise HTTPException(status_code=500, detail="Failed to read file from extension.")

@router.get("/icon/{publisher}/{extension}/{version}")
async def get_plugin_icon(publisher: str, extension: str, version: str):
    """
    Proxy the extension icon from OpenVSX API. This bypasses browser CORS/CSP blocks 
    that sometimes prevent loading images directly from open-vsx.org.
    """
    cache_key = f"icon:{publisher}:{extension}:{version}"
    # Wait, the frontend doesn't actually need to fetch it this way, we can just proxy 
    # the image download and stream it back.
    
    # First get the actual icon URL from the metadata
    meta_url = f"{OPENVSX_BASE}/{publisher}/{extension}/{version}"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            meta = await _get(client, meta_url)
            
            icon_url = meta.get("files", {}).get("icon")
            if not icon_url:
                raise HTTPException(status_code=404, detail="No icon found for extension")
                
            if icon_url.startswith("/"):
                icon_url = f"https://open-vsx.org{icon_url}"
                
            # Fetch the actual icon image
            img_res = await client.get(icon_url, headers=_build_headers())
            if img_res.status_code != 200:
                raise HTTPException(status_code=404, detail="Failed to fetch icon image")
                
            from fastapi.responses import Response
            return Response(content=img_res.content, media_type=img_res.headers.get("content-type", "image/png"))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to proxy icon for %s/%s: %s", publisher, extension, e)
        raise HTTPException(status_code=500, detail="Failed to fetch icon")
