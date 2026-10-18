from django.core.cache import cache


def too_many_attempts(key, limit=5, window_seconds=3600):
    """True if `key` has exceeded `limit` attempts in the window. Counts this attempt."""
    cache_key = f"ratelimit:{key}"
    cache.add(cache_key, 0, window_seconds)
    try:
        count = cache.incr(cache_key)
    except ValueError:
        cache.set(cache_key, 1, window_seconds)
        count = 1
    return count > limit
