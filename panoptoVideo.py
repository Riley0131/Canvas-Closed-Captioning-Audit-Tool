"""Panopto caption auditor.

For every Panopto session linked from a Canvas course this module answers two
questions:

1. Does the recording have captions at all?
2. If it does, are those captions Panopto's automatic speech recognition output
   or have they been uploaded/corrected by a person?

Answers are gathered from the cheapest source first:

* the Panopto REST API (``/Panopto/api/v1/sessions/{id}/captions``) when OAuth
  client credentials are configured;
* the viewer's ``DeliveryInfo.aspx`` payload and the generated SRT file, both
  fetched through the already authenticated browser session;
* finally the player DOM, which only proves that a caption control exists.

The verdict, its confidence, and the signals behind it are written to
``data/audited_videos.json`` by :func:`main`.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
from requests.auth import HTTPBasicAuth

import pullModules
from auditCore import (
    CAPTION_NONE,
    CAPTION_UNKNOWN,
    ResultWriter,
    isCancelled,
    loadCourseIds,
    loadJson,
    sortedModulesPath,
)
from browser import BrowserSession
from panoptoCaptions import (
    CaptionClassification,
    classifyCaptions,
    mergeClassifications,
    parseSrt,
)

try:
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except Exception:  # pragma: no cover - Selenium optional for unit tests
    TimeoutException = WebDriverException = Exception  # type: ignore
    By = None  # type: ignore
    EC = None  # type: ignore
    WebDriverWait = None  # type: ignore

try:
    from config import panoptoKey as panopto_config
except Exception:  # pragma: no cover - configuration file may be missing
    panopto_config = None


CLIENT_ID: str = os.environ.get("PANOPTO_CLIENT_ID") or (
    getattr(panopto_config, "Client_ID", "") if panopto_config else ""
)
CLIENT_SECRET: str = os.environ.get("PANOPTO_CLIENT_SECRET") or (
    getattr(panopto_config, "Client_Secret", "") if panopto_config else ""
)

MARKER_PARAM = "_panopto_video"


# ----------------------------------------------------------------------
# URL helpers
def normalizePanoptoUrl(url: str) -> str:
    """Convert embed links to the viewer format and drop the internal marker."""

    try:
        parsed = urlparse(url)
    except Exception:
        return url

    if MARKER_PARAM in (parsed.query or ""):
        params = parse_qs(parsed.query, keep_blank_values=True)
        params.pop(MARKER_PARAM, None)
        parsed = parsed._replace(query=urlencode(params, doseq=True))

    path = parsed.path or ""
    fragment = (parsed.fragment or "").lower()

    if "embed.aspx" in path.lower() and "access_token" not in fragment:
        parsed = parsed._replace(
            path=re.sub(r"embed\.aspx", "Viewer.aspx", path, flags=re.IGNORECASE)
        )

    return urlunparse(parsed)


def extractSessionId(url: str) -> Optional[str]:
    """Return the Panopto session GUID embedded in ``url`` if there is one."""

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    query = parse_qs(parsed.query)
    for key in ("id", "sessionId", "deliveryId"):
        values = query.get(key)
        if values and values[0]:
            return values[0]

    for segment in reversed([s for s in parsed.path.split("/") if s]):
        if len(segment) >= 32 and segment.count("-") >= 4:
            return segment

    return None


def isPanoptoPlayerUrl(url: Any) -> bool:
    """True when ``url`` points at a Panopto player or a Panopto LTI launch."""

    if not isinstance(url, str):
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if MARKER_PARAM in (parsed.query or ""):
        return True

    netloc = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()

    if "panopto" not in netloc or "/panopto/pages/" not in path:
        return False

    return any(key in path for key in ("embed.aspx", "viewer.aspx"))


def baseUrlOf(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if not parsed.scheme or not parsed.netloc:
        return None

    return f"{parsed.scheme}://{parsed.netloc}"


# ----------------------------------------------------------------------
# link discovery
def _panoptoLinksFromCache(course_id: str) -> List[str]:
    payload = loadJson(sortedModulesPath(course_id))
    if not isinstance(payload, dict):
        return []

    urls = payload.get("panopto")
    if not isinstance(urls, list):
        return []

    return [url for url in urls if isPanoptoPlayerUrl(url)]


def _panoptoLinksFromCanvas(course_id: str) -> List[str]:
    try:
        module_urls = pullModules.getCourseModules(course_id)
    except Exception as exc:
        print(f"Error retrieving module URLs from Canvas for course {course_id}: {exc}")
        return []

    if not module_urls:
        return []

    sorted_urls = pullModules.sortUrls(module_urls)
    if not isinstance(sorted_urls, dict):
        return []

    return [url for url in sorted_urls.get("panopto", []) if isPanoptoPlayerUrl(url)]


def collectPanoptoLinks(courses: Iterable[Any]) -> List[Tuple[str, str]]:
    """Return ``[(course_id, url), ...]`` for every unique Panopto session.

    The sorted-modules cache written by :mod:`pullModules` is preferred; Canvas
    is only re-queried when a course has no cached data. The previous version
    did the opposite and re-downloaded every module of every course.
    """

    results: List[Tuple[str, str]] = []
    seen_globally: Set[str] = set()

    for course in courses:
        course_id = str(course)

        urls = _panoptoLinksFromCache(course_id)
        if not urls:
            urls = _panoptoLinksFromCanvas(course_id)

        for url in urls:
            if not isinstance(url, str):
                continue

            normalized = normalizePanoptoUrl(url)
            canonical = extractSessionId(url) or extractSessionId(normalized) or normalized
            if canonical in seen_globally:
                continue

            seen_globally.add(canonical)
            results.append((course_id, url))

    return results


# ----------------------------------------------------------------------
@dataclass
class _ApiToken:
    token: str
    expires_at: float


@dataclass
class PanoptoAuditResult:
    """Everything an audit learned about one Panopto session."""

    url: str
    has_captions: bool = False
    classification: CaptionClassification = field(default_factory=CaptionClassification)
    session_id: Optional[str] = None

    def asEntry(self, course_id: Optional[str] = None) -> Dict[str, Any]:
        entry: Dict[str, Any] = {
            "type": "panopto",
            "url": self.url,
            "has_captions": self.has_captions,
        }
        entry.update(self.classification.asDict())
        if self.session_id:
            entry["session_id"] = self.session_id
        if course_id is not None:
            entry["course_id"] = course_id
        return entry


class PanoptoAuditor:
    """Audits Panopto sessions, caching API tokens and the browser session."""

    def __init__(
        self,
        client_id: str = CLIENT_ID,
        client_secret: str = CLIENT_SECRET,
        timeout: int = 15,
        browser: Optional[BrowserSession] = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self._tokens: Dict[str, _ApiToken] = {}
        self._session = requests.Session()
        self._browser = browser or BrowserSession()
        self._owns_browser = browser is None
        self._authenticated_hosts: Set[str] = set()

    # ------------------------------------------------------------------
    def audit(self, url: str) -> PanoptoAuditResult:
        """Classify one Panopto session's captions."""

        visit_url = normalizePanoptoUrl(url)
        base_url = baseUrlOf(visit_url)
        session_id = extractSessionId(url) or extractSessionId(visit_url)

        attempts: List[Optional[CaptionClassification]] = []

        # 1. REST API - cheapest and most authoritative when credentials exist.
        if base_url and session_id:
            attempts.append(self._classifyViaApi(base_url, session_id))

        if _isDecisive(attempts):
            return self._result(url, session_id, attempts)

        # 2/3. Viewer endpoints through the authenticated browser session.
        browser_result = self._classifyViaBrowser(base_url, visit_url, session_id)
        attempts.append(browser_result)

        return self._result(url, session_id, attempts)

    def close(self) -> None:
        if self._owns_browser:
            self._browser.close()
        try:
            self._session.close()
        except Exception:
            pass

    def __enter__(self) -> "PanoptoAuditor":
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    @staticmethod
    def _result(
        url: str,
        session_id: Optional[str],
        attempts: Sequence[Optional[CaptionClassification]],
    ) -> PanoptoAuditResult:
        classification = mergeClassifications(*attempts)
        return PanoptoAuditResult(
            url=url,
            has_captions=classification.kind != CAPTION_NONE,
            classification=classification,
            session_id=session_id,
        )

    # ------------------------------------------------------------------
    # REST API
    def _classifyViaApi(
        self, base_url: str, session_id: str
    ) -> Optional[CaptionClassification]:
        if not self.client_id or not self.client_secret:
            return None

        token = self._getToken(base_url)
        if not token:
            return None

        headers = {"Authorization": f"Bearer {token}"}
        payloads: List[Any] = []

        for endpoint in (
            f"{base_url}/Panopto/api/v1/sessions/{session_id}/captions",
            f"{base_url}/Panopto/api/v1/sessions/{session_id}",
        ):
            try:
                response = self._session.get(endpoint, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                print(f"Error contacting Panopto API for {session_id}: {exc}")
                continue

            if response.status_code == 200:
                try:
                    payloads.append(response.json())
                except ValueError:
                    if response.text.strip():
                        payloads.append(response.text)
            elif response.status_code in {204, 404}:
                payloads.append([])
            else:
                print(
                    f"Panopto API call for session {session_id} returned "
                    f"{response.status_code}: {response.text[:120]}"
                )

        if not payloads:
            return None

        merged = payloads[0] if len(payloads) == 1 else payloads
        return classifyCaptions(merged, method="panopto_api")

    def _getToken(self, base_url: str) -> Optional[str]:
        cached = self._tokens.get(base_url)
        if cached and time.time() < cached.expires_at:
            return cached.token

        try:
            response = self._session.post(
                f"{base_url}/Panopto/oauth2/connect/token",
                data={"grant_type": "client_credentials", "scope": "api"},
                auth=HTTPBasicAuth(self.client_id, self.client_secret),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            print(f"Error requesting Panopto token: {exc}")
            return None

        if response.status_code != 200:
            print(
                f"Panopto token request failed ({response.status_code}): "
                f"{response.text[:120]}"
            )
            return None

        try:
            payload = response.json()
        except ValueError:
            print("Panopto token response was not valid JSON.")
            return None

        token_value = payload.get("access_token")
        if not token_value:
            return None

        expires_in = int(payload.get("expires_in", 0) or 0)
        self._tokens[base_url] = _ApiToken(
            token_value, time.time() + max(expires_in - 30, 0)
        )
        return token_value

    # ------------------------------------------------------------------
    # Browser backed checks
    def _classifyViaBrowser(
        self,
        base_url: Optional[str],
        visit_url: str,
        session_id: Optional[str],
    ) -> Optional[CaptionClassification]:
        driver = self._browser.driver()
        if driver is None:
            return None

        if base_url and base_url not in self._authenticated_hosts:
            self._browser.ensureLogin(f"{base_url}/Panopto/Pages/Auth/Login.aspx", "Panopto")
            self._authenticated_hosts.add(base_url)

        try:
            driver.get(visit_url)
        except WebDriverException as exc:
            print(f"Error loading Panopto URL {visit_url}: {exc}")
            return None

        if WebDriverWait is not None and By is not None:
            try:
                WebDriverWait(driver, self.timeout).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except TimeoutException:
                print(f"Timed out waiting for the Panopto player at {visit_url}")

        attempts: List[Optional[CaptionClassification]] = []

        if base_url and session_id:
            delivery = self._fetchDeliveryInfo(driver, base_url, session_id)
            if delivery is not None:
                attempts.append(classifyCaptions(delivery, method="delivery_info"))

            if not _isDecisive(attempts):
                srt = self._fetchSrt(driver, base_url, session_id)
                if srt:
                    attempts.append(
                        classifyCaptions(None, cues=parseSrt(srt), method="srt")
                    )

        if not _isDecisive(attempts):
            dom = self._captionControlPresent(driver)
            if dom:
                attempts.append(
                    CaptionClassification(
                        kind=CAPTION_UNKNOWN,
                        confidence="low",
                        signals=["player exposes a caption control"],
                        method="player_dom",
                    )
                )
            elif dom is False and not attempts:
                attempts.append(
                    CaptionClassification(
                        kind=CAPTION_NONE,
                        confidence="low",
                        signals=["player exposes no caption control"],
                        method="player_dom",
                    )
                )

        return mergeClassifications(*attempts) if attempts else None

    def _fetchDeliveryInfo(
        self, driver: Any, base_url: str, session_id: str
    ) -> Optional[Any]:
        """Ask the viewer for its delivery metadata using the browser's cookies."""

        script = """
        const [base, id, done] = [arguments[0], arguments[1], arguments[2]];
        fetch(base + '/Panopto/Pages/Viewer/DeliveryInfo.aspx', {
            method: 'POST',
            credentials: 'include',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: 'deliveryId=' + encodeURIComponent(id) +
                  '&isEmbed=true&responseType=json&isLiveNotes=false'
        }).then(r => r.ok ? r.text() : null).then(done).catch(() => done(null));
        """
        return self._runAsyncScript(driver, script, base_url, session_id, parse=True)

    def _fetchSrt(self, driver: Any, base_url: str, session_id: str) -> Optional[str]:
        """Download the generated transcript through the browser session."""

        script = """
        const [base, id, done] = [arguments[0], arguments[1], arguments[2]];
        fetch(base + '/Panopto/Pages/Transcription/GenerateSRT.ashx?id=' +
              encodeURIComponent(id) + '&language=0', {credentials: 'include'})
            .then(r => r.ok ? r.text() : null).then(done).catch(() => done(null));
        """
        return self._runAsyncScript(driver, script, base_url, session_id, parse=False)

    def _runAsyncScript(
        self, driver: Any, script: str, *args: Any, parse: bool
    ) -> Optional[Any]:
        try:
            driver.set_script_timeout(self.timeout)
            raw = driver.execute_async_script(script, *args)
        except Exception as exc:
            print(f"Panopto viewer request failed: {exc}")
            return None

        if not raw:
            return None

        if not parse:
            return raw

        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def _captionControlPresent(self, driver: Any) -> Optional[bool]:
        """Look for a caption control in the player, including inside iframes.

        This runs as a single injected script instead of the old approach of
        pulling thousands of elements across the wire and reading nine
        attributes from each, which dominated the runtime of an audit.
        """

        if By is None:
            return None

        script = """
        const keywords = ['caption', 'subtitle', 'closed caption'];
        const nodes = document.querySelectorAll(
            '[aria-label],[title],[data-tooltip],[data-original-title],' +
            '[data-testid],[data-qa],button,track,[id*="caption" i],[class*="caption" i]'
        );
        for (const node of nodes) {
            if (node.tagName === 'TRACK') {
                const kind = (node.getAttribute('kind') || '').toLowerCase();
                if ((kind === 'captions' || kind === 'subtitles') && node.getAttribute('src')) {
                    return true;
                }
                continue;
            }
            const haystack = [
                node.getAttribute('aria-label'), node.getAttribute('title'),
                node.getAttribute('data-tooltip'), node.getAttribute('data-original-title'),
                node.getAttribute('data-testid'), node.getAttribute('data-qa'),
                node.className && node.className.toString(), node.id,
                node.textContent && node.textContent.slice(0, 80)
            ].filter(Boolean).join(' ').toLowerCase();
            if (keywords.some(k => haystack.includes(k)) || /\\bcc\\b/.test(haystack)) {
                return true;
            }
        }
        return false;
        """

        found = self._evaluateInFrames(driver, script)
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
        return found

    def _evaluateInFrames(self, driver: Any, script: str, depth: int = 0) -> Optional[bool]:
        try:
            if bool(driver.execute_script(script)):
                return True
        except Exception:
            return None

        if depth >= 3:
            return False

        try:
            frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
        except Exception:
            frames = []

        for frame in frames:
            try:
                driver.switch_to.frame(frame)
            except Exception:
                continue

            try:
                if self._evaluateInFrames(driver, script, depth + 1):
                    return True
            finally:
                try:
                    driver.switch_to.parent_frame()
                except Exception:
                    driver.switch_to.default_content()

        return False


def _isDecisive(attempts: Sequence[Optional[CaptionClassification]]) -> bool:
    """True when one attempt already produced a high confidence verdict."""

    return any(
        attempt is not None
        and attempt.confidence == "high"
        and attempt.kind not in {CAPTION_UNKNOWN}
        for attempt in attempts
    )


def main(
    courses: Optional[Sequence[Any]] = None,
    include_course_ids: bool = False,
    browser: Optional[BrowserSession] = None,
    results: Optional[ResultWriter] = None,
) -> int:
    """Audit the Panopto videos of ``courses`` and record the results.

    Returns the number of sessions audited.
    """

    if courses is None:
        courses = loadCourseIds()

    if not courses:
        print("Panopto: no courses supplied.")
        return 0

    videos = collectPanoptoLinks(courses)
    if not videos:
        print("Panopto: no Panopto videos found.")
        return 0

    print(f"Panopto: auditing {len(videos)} video(s).")

    owns_writer = results is None
    writer = results or ResultWriter()

    try:
        with PanoptoAuditor(browser=browser) as auditor:
            for index, (course_id, url) in enumerate(videos, start=1):
                if isCancelled():
                    print("Panopto: audit cancelled.")
                    break

                result = auditor.audit(url)
                writer.add(**result.asEntry(course_id if include_course_ids else None))
                print(
                    f"Panopto [{index}/{len(videos)}] "
                    f"{result.classification.kind} ({result.classification.confidence}) - {url}"
                )
    finally:
        if owns_writer:
            writer.flush()

    return len(videos)


if __name__ == "__main__":  # pragma: no cover - manual invocation helper
    main()
