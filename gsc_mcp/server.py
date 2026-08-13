"""MCP Server for Google Search Console API.

Provides tools to query search analytics, inspect URLs, manage sitemaps,
and list verified properties via the Google Search Console API.
"""

import os
from datetime import date, timedelta
from typing import Optional

from googleapiclient.discovery import build
from pydantic import BaseModel, ConfigDict, Field, field_validator
from mcp.server.fastmcp import FastMCP

from gsc_mcp.auth import get_credentials
from gsc_mcp.sanitize import format_error, format_response

mcp = FastMCP("gsc_mcp")

# Lazy-initialized API services
_webmasters_service = None
_url_inspection_service = None


def _get_webmasters_service():
    global _webmasters_service
    if _webmasters_service is None:
        creds = get_credentials()
        _webmasters_service = build("webmasters", "v3", credentials=creds)
    return _webmasters_service


def _get_url_inspection_service():
    global _url_inspection_service
    if _url_inspection_service is None:
        creds = get_credentials()
        _url_inspection_service = build("searchconsole", "v1", credentials=creds)
    return _url_inspection_service


def _is_write_enabled() -> bool:
    return os.environ.get("GSC_WRITE_ACCESS", "").lower() == "true"


# --- Input Models ---


class SearchAnalyticsInput(BaseModel):
    """Input for querying search analytics data."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    site_url: str = Field(
        ...,
        description="Site URL as it appears in GSC (e.g., 'https://example.com/' or 'sc-domain:example.com')",
        min_length=1,
    )
    start_date: str = Field(
        ...,
        description="Start date in YYYY-MM-DD format (e.g., '2025-01-01')",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    end_date: Optional[str] = Field(
        default=None,
        description="End date in YYYY-MM-DD format. Defaults to today.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    dimensions: Optional[list[str]] = Field(
        default=None,
        description="Dimensions to group by: 'query', 'page', 'country', 'device', 'date'. Max 3.",
        max_length=3,
    )
    row_limit: Optional[int] = Field(
        default=100,
        description="Max rows to return (1-25000).",
        ge=1,
        le=25000,
    )
    start_row: Optional[int] = Field(
        default=0,
        description="Zero-based row offset for pagination.",
        ge=0,
    )
    search_type: Optional[str] = Field(
        default="web",
        description="Search type: 'web', 'image', 'video', 'news', or 'discover'.",
    )
    dimension_filters: Optional[list[dict]] = Field(
        default=None,
        description=(
            "Filters as list of dicts with keys: 'dimension' (query/page/country/device), "
            "'operator' (contains/equals/notContains/notEquals/includingRegex/excludingRegex), "
            "'expression' (value to match). Example: [{'dimension': 'query', 'operator': 'contains', 'expression': 'python'}]"
        ),
    )

    @field_validator("dimensions")
    @classmethod
    def validate_dimensions(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        valid = {"query", "page", "country", "device", "date", "searchAppearance"}
        for d in v:
            if d not in valid:
                raise ValueError(f"Invalid dimension '{d}'. Must be one of: {', '.join(sorted(valid))}")
        return v

    @field_validator("search_type")
    @classmethod
    def validate_search_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        valid = {"web", "image", "video", "news", "discover"}
        if v not in valid:
            raise ValueError(f"Invalid search_type '{v}'. Must be one of: {', '.join(sorted(valid))}")
        return v


class InspectUrlInput(BaseModel):
    """Input for URL inspection."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    inspection_url: str = Field(
        ...,
        description="Fully qualified URL to inspect (e.g., 'https://example.com/page')",
        min_length=1,
    )
    site_url: str = Field(
        ...,
        description="Site URL as it appears in GSC (e.g., 'https://example.com/' or 'sc-domain:example.com')",
        min_length=1,
    )


class SiteInput(BaseModel):
    """Input requiring only a site URL."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    site_url: str = Field(
        ...,
        description="Site URL as it appears in GSC (e.g., 'https://example.com/' or 'sc-domain:example.com')",
        min_length=1,
    )


class SitemapInput(BaseModel):
    """Input for sitemap operations requiring site URL and feedpath."""

    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    site_url: str = Field(
        ...,
        description="Site URL as it appears in GSC",
        min_length=1,
    )
    feedpath: str = Field(
        ...,
        description="Full URL of the sitemap (e.g., 'https://example.com/sitemap.xml')",
        min_length=1,
    )


# --- Read-Only Tools ---


@mcp.tool(
    name="gsc_list_sites",
    annotations={
        "title": "List Verified Sites",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def gsc_list_sites() -> str:
    """List all verified properties in Google Search Console.

    Returns a list of sites/properties you have access to, including
    their permission level and site URL format.

    Returns:
        str: JSON with list of verified sites, each containing:
            - siteUrl (str): The site URL
            - permissionLevel (str): Your access level (siteOwner, siteFullUser, etc.)
    """
    try:
        service = _get_webmasters_service()
        result = service.sites().list().execute()
        sites = result.get("siteEntry", [])
        return format_response("gsc_list_sites", sites, {"count": len(sites)})
    except Exception as e:
        return format_error("gsc_list_sites", str(e))


@mcp.tool(
    name="gsc_search_analytics",
    annotations={
        "title": "Query Search Analytics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def gsc_search_analytics(params: SearchAnalyticsInput) -> str:
    """Query search performance data (clicks, impressions, CTR, position).

    Returns search analytics data for a verified property, with optional
    grouping by dimensions (query, page, country, device, date) and filtering.

    Args:
        params (SearchAnalyticsInput): Query parameters including:
            - site_url (str): The GSC property URL
            - start_date (str): Start date YYYY-MM-DD
            - end_date (str, optional): End date YYYY-MM-DD, defaults to today
            - dimensions (list[str], optional): Group by query/page/country/device/date
            - row_limit (int, optional): Max rows 1-25000, default 100
            - start_row (int, optional): Pagination offset
            - search_type (str, optional): web/image/video/news/discover
            - dimension_filters (list[dict], optional): Filters for dimensions

    Returns:
        str: JSON with rows containing clicks, impressions, ctr, position, and
             dimension keys. Includes metadata with total row count.
    """
    try:
        service = _get_webmasters_service()

        body: dict = {
            "startDate": params.start_date,
            "endDate": params.end_date or date.today().isoformat(),
            "rowLimit": params.row_limit,
            "startRow": params.start_row,
            "type": params.search_type,
        }
        if params.dimensions:
            body["dimensions"] = params.dimensions
        if params.dimension_filters:
            body["dimensionFilterGroups"] = [{"filters": params.dimension_filters}]

        result = service.searchanalytics().query(siteUrl=params.site_url, body=body).execute()
        rows = result.get("rows", [])
        return format_response(
            "gsc_search_analytics",
            rows,
            {
                "row_count": len(rows),
                "site_url": params.site_url,
                "date_range": f"{params.start_date} to {params.end_date or date.today().isoformat()}",
            },
        )
    except Exception as e:
        return format_error("gsc_search_analytics", str(e))


@mcp.tool(
    name="gsc_inspect_url",
    annotations={
        "title": "Inspect URL",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def gsc_inspect_url(params: InspectUrlInput) -> str:
    """Inspect a URL's indexation status, crawl info, and rich results.

    Uses the URL Inspection API to check whether a URL is indexed,
    its canonical URL, crawl status, mobile usability, and rich results.

    Args:
        params (InspectUrlInput): Parameters including:
            - inspection_url (str): The fully qualified URL to inspect
            - site_url (str): The GSC property URL

    Returns:
        str: JSON with inspection results including:
            - indexStatusResult: verdict, coverageState, crawledAs, lastCrawlTime, pageFetchState
            - mobileUsabilityResult: verdict, issues
            - richResultsResult: detectedItems
    """
    try:
        service = _get_url_inspection_service()
        body = {
            "inspectionUrl": params.inspection_url,
            "siteUrl": params.site_url,
        }
        result = service.urlInspection().index().inspect(body=body).execute()
        return format_response(
            "gsc_inspect_url",
            result.get("inspectionResult", {}),
            {"inspected_url": params.inspection_url},
        )
    except Exception as e:
        return format_error("gsc_inspect_url", str(e))


@mcp.tool(
    name="gsc_list_sitemaps",
    annotations={
        "title": "List Sitemaps",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def gsc_list_sitemaps(params: SiteInput) -> str:
    """List all submitted sitemaps for a verified property.

    Args:
        params (SiteInput): Parameters including:
            - site_url (str): The GSC property URL

    Returns:
        str: JSON with list of sitemaps, each containing:
            - path (str): Sitemap URL
            - lastSubmitted (str): Last submission timestamp
            - isPending (bool): Whether sitemap is pending processing
            - isSitemapsIndex (bool): Whether it's a sitemap index
            - type (str): Sitemap type
            - lastDownloaded (str): Last download timestamp
            - warnings (int): Number of warnings
            - errors (int): Number of errors
    """
    try:
        service = _get_webmasters_service()
        result = service.sitemaps().list(siteUrl=params.site_url).execute()
        sitemaps = result.get("sitemap", [])
        return format_response(
            "gsc_list_sitemaps",
            sitemaps,
            {"count": len(sitemaps), "site_url": params.site_url},
        )
    except Exception as e:
        return format_error("gsc_list_sitemaps", str(e))


@mcp.tool(
    name="gsc_get_sitemap",
    annotations={
        "title": "Get Sitemap Details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def gsc_get_sitemap(params: SitemapInput) -> str:
    """Get details for a specific submitted sitemap.

    Args:
        params (SitemapInput): Parameters including:
            - site_url (str): The GSC property URL
            - feedpath (str): Full URL of the sitemap

    Returns:
        str: JSON with sitemap details including path, status,
             content counts, warnings, and errors.
    """
    try:
        service = _get_webmasters_service()
        result = service.sitemaps().get(siteUrl=params.site_url, feedpath=params.feedpath).execute()
        return format_response(
            "gsc_get_sitemap",
            result,
            {"site_url": params.site_url, "feedpath": params.feedpath},
        )
    except Exception as e:
        return format_error("gsc_get_sitemap", str(e))


# --- Write Tools (conditionally registered) ---

if _is_write_enabled():

    @mcp.tool(
        name="gsc_submit_sitemap",
        annotations={
            "title": "Submit Sitemap",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gsc_submit_sitemap(params: SitemapInput) -> str:
        """Submit a sitemap for a verified property.

        Requires GSC_WRITE_ACCESS=true environment variable.

        Args:
            params (SitemapInput): Parameters including:
                - site_url (str): The GSC property URL
                - feedpath (str): Full URL of the sitemap to submit

        Returns:
            str: JSON confirmation of submission.
        """
        try:
            service = _get_webmasters_service()
            service.sitemaps().submit(siteUrl=params.site_url, feedpath=params.feedpath).execute()
            return format_response(
                "gsc_submit_sitemap",
                {"status": "submitted", "feedpath": params.feedpath},
                {"site_url": params.site_url},
            )
        except Exception as e:
            return format_error("gsc_submit_sitemap", str(e))

    @mcp.tool(
        name="gsc_delete_sitemap",
        annotations={
            "title": "Delete Sitemap",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gsc_delete_sitemap(params: SitemapInput) -> str:
        """Delete a submitted sitemap from a verified property.

        Requires GSC_WRITE_ACCESS=true environment variable.
        This is a destructive operation.

        Args:
            params (SitemapInput): Parameters including:
                - site_url (str): The GSC property URL
                - feedpath (str): Full URL of the sitemap to delete

        Returns:
            str: JSON confirmation of deletion.
        """
        try:
            service = _get_webmasters_service()
            service.sitemaps().delete(siteUrl=params.site_url, feedpath=params.feedpath).execute()
            return format_response(
                "gsc_delete_sitemap",
                {"status": "deleted", "feedpath": params.feedpath},
                {"site_url": params.site_url},
            )
        except Exception as e:
            return format_error("gsc_delete_sitemap", str(e))


if __name__ == "__main__":
    mcp.run()