"""Bounded public HTTP(S) page ingestion; fetching remains optional."""
import asyncio
import ipaddress
import logging
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from fnmatch import fnmatch
from urllib.parse import urljoin, urlsplit, urlunsplit

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5


class DownloadError(RuntimeError):
    """A public page was unavailable; do not expose remote response details."""


def _public_address(host):
    address = ipaddress.ip_address(host)
    return address.is_global and not address.is_multicast and not address.is_reserved and not getattr(address, 'is_site_local', False)


def public_url(url):
    """Literal IPs bypass aiohttp's resolver, so check them here too."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError('Use a public HTTP(S) URL without credentials')
        if parsed.port not in (None, 80, 443):
            raise ValueError('Only HTTP(S) ports 80 and 443 are supported')
        host = parsed.hostname.rstrip('.')
        if host.lower() == 'localhost' or host.lower().endswith('.localhost'):
            raise ValueError('Local destinations are not supported')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is None and (host.replace('.', '').isdigit() or ':' in host or '%' in host):
            raise ValueError('Noncanonical IP destinations are not supported')
        if address is not None and not _public_address(host):
            raise ValueError('Private and special-use destinations are not supported')
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or '/', parsed.query, ''))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Use a public HTTP(S) URL on port 80 or 443 without credentials') from exc


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Validate the very addresses handed to the connection, avoiding DNS rebinding."""
    def __init__(self):
        self.resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=0):
        addresses = await self.resolver.resolve(host, port, family)
        if not addresses or any(not _public_address(a['host']) for a in addresses):
            raise ValueError('Destination must resolve only to public IP addresses')
        return addresses

    async def close(self):
        await self.resolver.close()


@dataclass
class ScrapedDocument:
    url: str
    title: str
    content: str
    metadata: dict
    scraped_at: datetime
    links: list[str] = field(default_factory=list)


class DocumentScraper:
    def __init__(self, timeout=10, user_agent='Lassy/1.0', crawl_timeout=120):
        self.timeout = timeout
        self.crawl_timeout = crawl_timeout
        self.user_agent = user_agent
        self.session = None
        self.resolver = None

    async def _get_session(self):
        if self.session is None or self.session.closed:
            self.resolver = PublicResolver()
            self.session = aiohttp.ClientSession(headers={'User-Agent': self.user_agent},
                timeout=aiohttp.ClientTimeout(total=self.timeout), trust_env=False,
                connector=aiohttp.TCPConnector(resolver=self.resolver, use_dns_cache=False, limit=2))
        return self.session

    async def _download(self, url):
        session = await self._get_session()
        async with asyncio.timeout(self.timeout):
            for redirects in range(MAX_REDIRECTS + 1):
                url = public_url(url)
                async with session.get(url, allow_redirects=False) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        if redirects == MAX_REDIRECTS or not response.headers.get('Location'):
                            raise ValueError('Too many redirects or missing redirect destination')
                        url = urljoin(url, response.headers['Location'])
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                    if content_type not in ('text/html', 'application/xhtml+xml', 'text/plain'):
                        raise ValueError('URL must return HTML or plain text')
                    if response.content_length is not None and response.content_length > MAX_DOWNLOAD_BYTES:
                        raise ValueError('Page exceeds the 5 MiB download limit')
                    body = bytearray()
                    async for block in response.content.iter_chunked(65536):
                        if len(body) + len(block) > MAX_DOWNLOAD_BYTES:
                            raise ValueError('Page exceeds the 5 MiB download limit')
                        body.extend(block)
                    return bytes(body), url, dict(response.headers), response.charset or 'utf-8'
        raise ValueError('Unable to download page')

    async def scrape_url(self, url):
        try:
            body, resolved, headers, encoding = await self._download(public_url(url))
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise DownloadError('Page could not be downloaded; check its availability or retry later') from exc
        return await asyncio.to_thread(self._parse, body, resolved, headers, encoding)

    def _parse(self, body, url, headers, encoding):
        headers = {key.lower(): value for key, value in headers.items()}
        try:
            text = body.decode(encoding, errors='replace')
        except LookupError:
            text = body.decode('utf-8', errors='replace')
        soup = BeautifulSoup(text, 'lxml')
        title_node = soup.title or soup.find('h1') or soup.find('meta', property='og:title')
        title = (title_node.get('content') or title_node.get_text()).strip() if title_node else url
        links = []
        for anchor in soup.find_all('a', href=True):
            try:
                link = public_url(urljoin(url, anchor['href']))
                if urlsplit(link).netloc == urlsplit(url).netloc and link not in links:
                    links.append(link)
                if len(links) >= 500:
                    break
            except ValueError:
                continue
        modified_node = soup.find('meta', attrs={'name': 'last-modified'}) or soup.find('meta', property='article:modified_time')
        modified = modified_node.get('content') if modified_node else None
        if headers.get('last-modified'):
            try:
                modified = parsedate_to_datetime(headers['last-modified']).isoformat()
            except (ValueError, TypeError, OverflowError):
                pass
        for node in soup(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            node.decompose()
        main = soup.find('main') or soup.find('article') or soup.find('div', class_=re.compile(r'content|main|body')) or soup
        content = re.sub(r'[ \t]+', ' ', main.get_text(separator='\n', strip=True))
        content = re.sub(r'\n{3,}', '\n\n', content).strip()
        if not content:
            raise ValueError('Page contains no readable text')
        now = datetime.now(timezone.utc)
        metadata = {'source_url': url, 'source_type': 'documentation', 'source_domain': urlsplit(url).netloc,
                    'title': title, 'content_type': headers.get('content-type', ''), 'scraped_at': now.isoformat()}
        if modified:
            metadata['last_modified'] = modified
        return ScrapedDocument(url, title, content, metadata, now, links)

    async def scrape_recursive(self, start_url, max_depth=2, max_pages=50,
                               url_patterns=None, exclude_patterns=None):
        start_url = public_url(start_url)
        domain = urlsplit(start_url).netloc
        queue, queued, visited, documents = deque([(start_url, 1)]), {start_url}, set(), []
        try:
            async with asyncio.timeout(self.crawl_timeout):
                while queue and len(visited) < max_pages:
                    url, depth = queue.popleft()
                    if url in visited or any(fnmatch(url, pattern) for pattern in exclude_patterns or []):
                        continue
                    visited.add(url)  # Failed and nonmatching pages count toward the request budget.
                    try:
                        doc = await self.scrape_url(url)
                    except (DownloadError, ValueError) as exc:
                        logger.warning('Skipping crawl page (%s)', type(exc).__name__)
                        continue
                    if url == start_url:
                        domain = urlsplit(doc.url).netloc
                    if url == start_url or not url_patterns or any(fnmatch(url, p) for p in url_patterns):
                        documents.append(doc)
                    if depth < max_depth:
                        for link in doc.links:
                            if urlsplit(link).netloc == domain and link not in queued and len(queued) < max_pages:
                                queued.add(link)
                                queue.append((link, depth+1))
        except TimeoutError:
            logger.warning('Crawl time budget exhausted; returning completed pages')
        return documents

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
        if self.resolver:
            await self.resolver.close()
            self.resolver = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
