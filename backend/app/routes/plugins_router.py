"""
plugins_router.py â€” OpenVSX Plugin Marketplace Proxy
=====================================================
All external OpenVSX API calls are made from here; the frontend never
talks to OpenVSX directly, following clean architecture principles.

Endpoints
---------
GET /plugins/search        â€“ Search extensions (cached, paginated, filterable)
GET /plugins/details/{publisher}/{extension}   â€“ Fetch full metadata
GET /plugins/download/{publisher}/{extension}/{version} â€“ Download .vsix file
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
from fastapi import APIRouter, HTTPException, Query, Response  # type: ignore
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
# Download deduplication â€” prevents concurrent duplicate downloads
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
    sortBy: str = Query("", description="Sort by: relevance, downloadCount, averageRating, timestamp"),
    sortOrder: str = Query("", description="Sort order: asc or desc"),
) -> JSONResponse:
    """
    Proxy the OpenVSX search API with 5-minute in-memory caching.

    Cache key includes all query parameters so different searches
    produce independent cache entries.
    """
    cache_key = f"search:{q}:{offset}:{size}:{category}:{sortBy}:{sortOrder}"
    cached = _cache_get(cache_key)
    if cached:
        logger.debug("Cache HIT for search key: %s", cache_key)
        return JSONResponse(content=cached)

    logger.info("Searching OpenVSX: q=%r offset=%d size=%d category=%r sortBy=%r", q, offset, size, category, sortBy)

    params: dict[str, Any] = {
        "query": q,
        "offset": offset,
        "size": size,
    }
    if category:
        params["category"] = category
    if sortBy:
        params["sortBy"] = sortBy
    if sortOrder:
        params["sortOrder"] = sortOrder

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
async def download_plugin(publisher: str, extension: str, version: str):
    """
    Download and extract a .vsix extension from OpenVSX with real-time progress.
    Returns Server-Sent Events (SSE) with download percentage and extraction status.
    """
    import json as _json
    from starlette.responses import StreamingResponse  # type: ignore

    # Sanitise inputs to prevent path traversal attacks
    for part in (publisher, extension, version):
        if not all(c.isalnum() or c in "-._" for c in part):
            raise HTTPException(status_code=400, detail="Invalid publisher/extension/version characters.")

    filename = f"{publisher}.{extension}-{version}.vsix"
    dest_path = STORAGE_DIR / filename
    part_path = STORAGE_DIR / f"{filename}.part"
    extracted_dir = STORAGE_DIR / "extracted" / f"{publisher}.{extension}-{version}"
    extracted_tmp_dir = extracted_dir.with_name(f"{extracted_dir.name}.tmp")

    async def progress_stream():
        """Generator that yields SSE events for download + extraction progress."""

        def event(stage: str, progress: int, message: str, **extra):
            data = {"stage": stage, "progress": progress, "message": message, **extra}
            return f"data: {_json.dumps(data)}\n\n"

        # Already installed? Quick response
        if dest_path.exists() and extracted_dir.exists() and (extracted_dir / "extension" / "package.json").exists():
            yield event("complete", 100, "Already installed.")
            return

        lock = _get_download_lock(filename)
        async with lock:
            # Double-check after lock
            if dest_path.exists() and extracted_dir.exists() and (extracted_dir / "extension" / "package.json").exists():
                yield event("complete", 100, "Already installed.")
                return

            # Fetch metadata
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

            # Download with progress
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
                                        pct = min(int(downloaded / 1024), 99)  # estimate by KB

                                    # Send updates at meaningful intervals (every 3%)
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

            # Extract
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

            # Done
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


@router.get("/installed")
async def get_installed_plugins() -> JSONResponse:
    """
    List all locally installed extensions by scanning the extracted directory.
    Reads metadata from package.json for each installed extension.
    """
    installed = []
    extracted_base = STORAGE_DIR / "extracted"
    
    if not extracted_base.exists():
        return JSONResponse(content={"extensions": installed})
        
    for ext_dir in extracted_base.iterdir():
        if not ext_dir.is_dir():
            continue
            
        # Directory format is {publisher}.{extension}-{version}
        folder_name = ext_dir.name
        
        # Try to read package.json
        package_json_path = ext_dir / "extension" / "package.json"
        if package_json_path.exists():
            try:
                import json
                with open(package_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                publisher = data.get("publisher", "")
                name = data.get("name", "")
                version = data.get("version", "")
                
                # Build icon URL from OpenVSX if package.json has icon field
                icon_url = ""
                if publisher and name and version:
                    icon_url = f"{OPENVSX_BASE}/{publisher}/{name}/{version}/file/icon.png"
                
                installed.append({
                    "namespace": publisher,
                    "publisher": publisher,
                    "name": name,
                    "version": version,
                    "displayName": data.get("displayName", data.get("name", "")),
                    "description": data.get("description", ""),
                    "installed": True,
                    "files": {"icon": icon_url} if icon_url else {},
                })
            except Exception as e:
                logger.warning(f"Failed to read package.json for {folder_name}: {e}")
                # Fallback to parsing folder name if package.json is unreadable
                try:
                    parts = folder_name.split("-")
                    version = parts[-1]
                    name_parts = "-".join(parts[:-1]).split(".")  # type: ignore
                    publisher = name_parts[0]
                    name = ".".join(name_parts[1:])  # type: ignore
                    installed.append({
                        "namespace": publisher,
                        "publisher": publisher,
                        "name": name,
                        "version": version,
                        "displayName": name,
                        "description": "",
                        "installed": True,
                        "files": {},
                    })
                except Exception:
                    pass
    
    return JSONResponse(content={"extensions": installed})


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
    plugin_id = f"{publisher}.{extension}-{version}"
    try:
        # Prevent path traversal
        clean_file_path = os.path.normpath(file_path.strip("/"))
        if clean_file_path.startswith("..") or os.path.isabs(clean_file_path):
            raise HTTPException(status_code=400, detail="Invalid file path")

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
                
        # Determine strict MIME types for JS/CSS/media to avoid browser strict MIME type errors
        media_type = "text/plain"
        if target_file.suffix == ".js":
            media_type = "application/javascript"
        elif target_file.suffix == ".css":
            media_type = "text/css"
        elif target_file.suffix == ".json":
            media_type = "application/json"
        elif target_file.suffix == ".html":
            media_type = "text/html"
        elif target_file.suffix == ".svg":
            media_type = "image/svg+xml"
        elif target_file.suffix == ".png":
            media_type = "image/png"
        elif target_file.suffix in [".jpg", ".jpeg"]:
            media_type = "image/jpeg"
        elif target_file.suffix == ".gif":
            media_type = "image/gif"
        elif target_file.suffix == ".webp":
            media_type = "image/webp"
        elif target_file.suffix == ".ico":
            media_type = "image/x-icon"

        content = target_file.read_bytes()
        return Response(content=content, media_type=media_type)
        
    except Exception as e: # Catch all exceptions for logging and consistent error response
        logger.error("Failed to read file %s from extension %s: %s", file_path, plugin_id, e)
        raise HTTPException(status_code=500, detail="Failed to read file from extension.")

# Semaphore to limit concurrent outbound image fetches (avoids connection pool exhaustion)
_img_semaphore = asyncio.Semaphore(6)

# Headers to add to all proxied image responses for COEP compatibility
_PROXY_HEADERS = {
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Access-Control-Allow-Origin": "*",
    "Cache-Control": "public, max-age=86400",
}


async def _fetch_image_with_retry(url: str, max_retries: int = 2) -> tuple[bytes, str] | None:
    """Fetch an image URL with retry logic and concurrency limiting."""
    async with _img_semaphore:
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
                    res = await client.get(url)
                    if res.status_code == 200:
                        return res.content, res.headers.get("content-type", "image/png")
                    elif res.status_code == 404:
                        return None
            except Exception as e:
                if attempt < max_retries:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    logger.warning("Failed to fetch image %s after %d attempts: %s", url, max_retries + 1, e)
    return None


@router.get("/icon/{publisher}/{extension}/{version}")
async def get_plugin_icon(publisher: str, extension: str, version: str):
    """
    Proxy the extension icon from OpenVSX API. This bypasses browser COEP blocks.
    Uses in-memory caching to avoid repeated fetches.
    """
    cache_key = f"icon_bin:{publisher}:{extension}:{version}"
    cached = _cache_get(cache_key)
    if cached:
        from fastapi.responses import Response  # type: ignore
        return Response(content=cached["bytes"], media_type=cached["content_type"], headers=_PROXY_HEADERS)

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            meta = await _get(client, f"{OPENVSX_BASE}/{publisher}/{extension}/{version}")
            icon_url = meta.get("files", {}).get("icon")
            if not icon_url:
                raise HTTPException(status_code=404, detail="No icon found")

        result = await _fetch_image_with_retry(icon_url)
        if not result:
            raise HTTPException(status_code=404, detail="Failed to fetch icon")

        img_bytes, content_type = result
        _cache_set(cache_key, {"bytes": img_bytes, "content_type": content_type})

        from fastapi.responses import Response  # type: ignore
        return Response(content=img_bytes, media_type=content_type, headers=_PROXY_HEADERS)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to proxy icon for %s/%s: %s", publisher, extension, e)
        raise HTTPException(status_code=500, detail="Failed to fetch icon")


@router.get("/proxy-image")
async def proxy_image(url: str = Query(..., description="Image URL to proxy")):
    """
    General-purpose image proxy to bypass COEP restrictions.
    Only proxies URLs from allowed domains for security.
    Results are cached for 5 minutes.
    """
    # Security: only allow proxying from trusted domains
    allowed_domains = ["open-vsx.org", "raw.githubusercontent.com", "github.com"]
    from urllib.parse import urlparse
    parsed = urlparse(url)
    hostname = str(parsed.hostname) if parsed.hostname else ""
    if not hostname or not any(hostname.endswith(domain) for domain in allowed_domains):
        raise HTTPException(status_code=403, detail="URL domain not allowed")

    cache_key = f"proxy_img:{url}"
    cached = _cache_get(cache_key)
    if cached:
        from fastapi.responses import Response  # type: ignore
        return Response(content=cached["bytes"], media_type=cached["content_type"], headers=_PROXY_HEADERS)

    result = await _fetch_image_with_retry(url)
    if not result:
        raise HTTPException(status_code=404, detail="Image not found or unreachable")

    img_bytes, content_type = result
    _cache_set(cache_key, {"bytes": img_bytes, "content_type": content_type})

    from fastapi.responses import Response  # type: ignore
    return Response(content=img_bytes, media_type=content_type, headers=_PROXY_HEADERS)


@router.get("/readme/{publisher}/{extension}")
async def get_plugin_readme(publisher: str, extension: str) -> JSONResponse:
    """
    Fetch the README content for an extension from OpenVSX.
    Returns rendered HTML or raw markdown text.
    The README URL is obtained from the extension metadata's files.readme field.
    """
    cache_key = f"readme:{publisher}:{extension}"
    cached = _cache_get(cache_key)
    if cached:
        return JSONResponse(content=cached)

    logger.info("Fetching README for %s/%s", publisher, extension)

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            # First get the metadata to find the readme URL and repository
            meta = await _get(client, f"{OPENVSX_BASE}/{publisher}/{extension}")
            readme_url = meta.get("files", {}).get("readme")
            changelog_url = meta.get("files", {}).get("changelog")
            repository = meta.get("repository", "")

            # Build base URL for resolving relative image paths
            # Convert GitHub repo URL to raw content URL
            raw_base_url = ""
            if repository:
                repo_url = repository.rstrip("/").replace(".git", "")
                if "github.com" in repo_url:
                    # https://github.com/user/repo -> https://raw.githubusercontent.com/user/repo/HEAD
                    raw_base_url = repo_url.replace("github.com", "raw.githubusercontent.com") + "/HEAD/"
                elif "gitlab.com" in repo_url:
                    raw_base_url = repo_url + "/-/raw/main/"

            readme_content = ""
            changelog_content = ""

            if readme_url:
                try:
                    readme_res = await client.get(readme_url, headers=_build_headers())
                    if readme_res.status_code == 200:
                        readme_content = readme_res.text
                except Exception as e:
                    logger.warning("Failed to fetch README for %s/%s: %s", publisher, extension, e)

            if changelog_url:
                try:
                    changelog_res = await client.get(changelog_url, headers=_build_headers())
                    if changelog_res.status_code == 200:
                        changelog_content = changelog_res.text
                except Exception as e:
                    logger.warning("Failed to fetch changelog for %s/%s: %s", publisher, extension, e)

            def _fix_relative_urls(md_content: str, base_url: str) -> str:
                """Rewrite relative image/link URLs in markdown to absolute URLs."""
                import re
                if not base_url:
                    return md_content
                # Fix markdown image syntax: ![alt](relative/path)
                def replace_md_img(m):
                    url = m.group(2)
                    if url.startswith(("http://", "https://", "data:", "//")):
                        return m.group(0)
                    url = url.lstrip("./")
                    return f"![{m.group(1)}]({base_url}{url})"
                md_content = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', replace_md_img, md_content)
                return md_content

            def _fix_html_img_urls(html_content: str, base_url: str) -> str:
                """Rewrite relative src= attributes in <img> tags to absolute URLs."""
                import re
                if not base_url:
                    return html_content
                def replace_src(m):
                    prefix = m.group(1)
                    url = m.group(2)
                    if url.startswith(("http://", "https://", "data:", "//")):
                        return m.group(0)
                    url = url.lstrip("./")
                    return f'{prefix}{base_url}{url}"'
                html_content = re.sub(r'(src=["\'])([^"\']+)(["\'])', lambda m: replace_src(m), html_content)
                # Also fix href for relative links (not anchors)
                def replace_href(m):
                    prefix = m.group(1)
                    url = m.group(2)
                    if url.startswith(("http://", "https://", "data:", "//", "#", "mailto:")):
                        return m.group(0)
                    url = url.lstrip("./")
                    return f'{prefix}{base_url}{url}"'
                html_content = re.sub(r'(href=["\'])([^"\']+)(["\'])', lambda m: replace_href(m), html_content)
                return html_content

            # Fix relative URLs in markdown BEFORE converting to HTML
            if raw_base_url:
                readme_content = _fix_relative_urls(readme_content, raw_base_url)
                changelog_content = _fix_relative_urls(changelog_content, raw_base_url)

            # Convert markdown to HTML
            readme_html = ""
            changelog_html = ""
            try:
                import markdown  # type: ignore
                if readme_content:
                    readme_html = markdown.markdown(
                        readme_content,
                        extensions=['tables', 'fenced_code', 'codehilite', 'toc', 'attr_list']
                    )
                if changelog_content:
                    changelog_html = markdown.markdown(
                        changelog_content,
                        extensions=['tables', 'fenced_code', 'codehilite', 'toc', 'attr_list']
                    )
            except ImportError:
                readme_html = f"<pre>{readme_content}</pre>" if readme_content else ""
                changelog_html = f"<pre>{changelog_content}</pre>" if changelog_content else ""

            # Fix any remaining relative URLs in the HTML output
            if raw_base_url:
                readme_html = _fix_html_img_urls(readme_html, raw_base_url)
                changelog_html = _fix_html_img_urls(changelog_html, raw_base_url)

            result = {
                "readme": readme_html,
                "readmeRaw": readme_content,
                "changelog": changelog_html,
                "changelogRaw": changelog_content,
            }

            _cache_set(cache_key, result)
            return JSONResponse(content=result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to fetch README for %s/%s: %s", publisher, extension, e)
        raise HTTPException(status_code=500, detail="Failed to fetch README")



