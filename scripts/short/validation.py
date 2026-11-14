import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path


class ShortDataError(ValueError):
    pass


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def positive(value):
    return finite(value) and value > 0


def validate_bars(rows, *, as_of=None, expected_sessions=None):
    """Fail closed on bad input. No invented prices, dates, or forward filling."""
    issues, seen, last = [], set(), ''
    if not isinstance(rows, list) or not rows:
        return ['EMPTY_BARS']
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            issues.append(f'INVALID_BAR:{i}')
            continue
        day = row.get('date')
        try:
            date.fromisoformat(day)
        except (ValueError, TypeError):
            issues.append(f'INVALID_DATE:{i}')
            continue
        if day in seen:
            issues.append(f'DUPLICATE_BAR:{day}')
        if last and day < last:
            issues.append(f'UNSORTED_BARS:{day}')
        if as_of and day > as_of:
            issues.append(f'INCOMPLETE_OR_FUTURE_BAR:{day}')
        seen.add(day)
        last = day
        if not all(positive(row.get(k)) for k in ('open', 'high', 'low', 'close')):
            issues.append(f'INVALID_OHLC:{day}')
        elif not (row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high']):
            issues.append(f'OHLC_RELATION:{day}')
        if not finite(row.get('volume')) or row['volume'] < 0:
            issues.append(f'INVALID_VOLUME:{day}')
        if not finite(row.get('dividend', 0)) or row.get('dividend', 0) < 0:
            issues.append(f'INVALID_DIVIDEND:{day}')
        if not finite(row.get('split', 0)) or row.get('split', 0) < 0:
            issues.append(f'INVALID_SPLIT:{day}')
    if expected_sessions and seen:
        for day in expected_sessions:
            if min(seen) <= day <= max(seen) and day not in seen:
                issues.append(f'MISSING_SESSION:{day}')
    if as_of and last and last != as_of:
        issues.append(f'STALE_BARS:{last}:expected:{as_of}')
    return list(dict.fromkeys(issues))


def price_metrics(entry, stop, target):
    if not all(positive(v) for v in (entry, stop, target)) or not stop > entry > target:
        return {'validPrices': False, 'rewardPct': None, 'riskPct': None, 'riskRewardRatio': None}
    return {'validPrices': True, 'rewardPct': (entry-target)/entry*100,
            'riskPct': (stop-entry)/entry*100, 'riskRewardRatio': (entry-target)/(stop-entry)}


def trailing_valid_segment(rows):
    """Quarantine corrupt history, never stitch across a bad candle.

    A current signal may use a sufficiently long valid suffix. Historical
    research must disclose the discarded prefix and original bad-bar dates.
    """
    problems=[];cut=0;previous=''
    for i,row in enumerate(rows):
        issues=validate_bars([row])
        day=row.get('date','') if isinstance(row,dict) else ''
        if previous and day<=previous:issues.append('NON_INCREASING_DATE:'+day)
        if issues:problems.extend(issues);cut=i+1
        previous=day
    return rows[cut:],list(dict.fromkeys(problems))


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n'
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default


def refresh_last_good(path, producer, *, attempted_at=None):
    """Keep the complete last-good file byte-for-byte; failures use a sidecar."""
    path = Path(path)
    attempted_at = attempted_at or datetime.now(timezone.utc).isoformat()
    status_path = path.with_name(path.stem + '_status.json')
    try:
        payload = producer()
        if not isinstance(payload, dict) or not payload.get('meta', {}).get('dataDate'):
            raise ShortDataError('Missing source data date')
        current=payload['meta']['dataDate']
        date.fromisoformat(current)
        previous=read_json(path)
        previous_date=(previous or {}).get('meta',{}).get('dataDate')
        if previous_date and current<previous_date:
            raise ShortDataError('Source returned an older completed date; last-good snapshot retained')
        expected=payload['meta'].get('expectedDataDate')
        if expected and current!=expected:
            raise ShortDataError('Completed data date is not the independently verified expected session')
        atomic_json(path, payload)
        atomic_json(status_path, {'lastAttemptAt': attempted_at, 'status': 'SUCCESS', 'dataDate': payload['meta']['dataDate']})
        return payload, True
    except Exception as exc:
        previous = read_json(path)
        atomic_json(status_path, {'lastAttemptAt': attempted_at, 'status': 'FAILED',
                    'stale': True, 'lastGoodDate': (previous or {}).get('meta', {}).get('dataDate'),
                    'error': f'{type(exc).__name__}: {exc}'[:400]})
        return previous, False
