"""Read-only profile filtering; metadata does not establish token safety."""
import argparse
import json
import time
from urllib.request import Request, urlopen
from urllib.error import URLError

API_URL = 'https://api.dexscreener.com/token-profiles/latest/v1'


def matches_profile(profile):
    """Original visual/social heuristic, explicitly not a fraud detector."""
    if profile.get('chainId') != 'solana':
        return False
    if not profile.get('icon') or not profile.get('header'):
        return False
    links = profile.get('links') or []
    socials = {str(link.get('type', '')).lower() for link in links if isinstance(link, dict)}
    count = len(socials & {'twitter', 'telegram'})
    description = str(profile.get('description') or '').strip()
    return (count >= 2 and len(description) >= 20) or (count == 1 and len(description) >= 40)


def fetch_profiles():
    request = Request(API_URL, headers={'User-Agent': 'token-profile-radar/1.0'})
    with urlopen(request, timeout=15) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        raise ValueError('Expected a list of profiles')
    return [p for p in payload if isinstance(p, dict)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=float, default=60)
    args = parser.parse_args()
    if args.interval < 15:
        parser.error('--interval must be at least 15 seconds')
    previous = set()
    try:
        while True:
            try:
                profiles = fetch_profiles()
                current = set()
                for profile in profiles:
                    address = profile.get('tokenAddress')
                    if not address or not matches_profile(profile):
                        continue
                    current.add(address)
                    if address not in previous:
                        print(json.dumps({'address': address, 'url': profile.get('url'),
                                          'description': profile.get('description')}, ensure_ascii=False))
                previous = current  # Bounded to the latest response, not an infinite set.
            except (URLError, ValueError, TimeoutError) as exc:
                if not args.watch:
                    parser.exit(1, f'Fetch failed: {exc}\n')
                print(f'Fetch failed: {exc}')
            if not args.watch:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
