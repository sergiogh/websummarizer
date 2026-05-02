"""
Configuration for URL downloading strategies.
"""

# Timeout configurations
TIMEOUTS = {
    'short': 15,
    'standard': 30,
    'long': 45,
    'very_long': 60
}

# Retry configurations
RETRY_CONFIG = {
    'max_retries': 3,
    'backoff_factor': 1,
    'status_forcelist': [429, 500, 502, 503, 504],
    'allowed_methods': ["HEAD", "GET", "OPTIONS"]
}

# User agents for rotation
USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 14_7_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Mobile/15E148 Safari/604.1',
    'curl/7.68.0'
]

# Problematic domains that need special handling
PROBLEMATIC_DOMAINS = [
    'english.news.cn',
    'xinhuanet.com',
    'people.com.cn',
    'china.org.cn',
    'cctv.com'
]

# Short URL patterns
SHORT_URL_PATTERNS = [
    'share.google',
    'goo.gl',
    'bit.ly',
    'tinyurl.com',
    't.co',
    'ow.ly'
]

# Headers for different strategies
HEADER_STRATEGIES = {
    'standard': {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    },
    'mobile': {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    },
    'api': {
        'Accept': 'application/json, text/html, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache'
    },
    'minimal': {
        'Accept': '*/*'
    }
}

# Delay ranges for rate limiting avoidance
DELAY_RANGES = {
    'short': (0.5, 1.5),
    'medium': (1, 3),
    'long': (2, 5)
}

