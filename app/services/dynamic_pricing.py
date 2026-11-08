"""Dynamic category pricing layered on top of the existing room/seasonal base rate."""
import datetime as dtm
from ..db import setting, set_setting
from ..util import d, today

DEFAULTS = {
    'dynamic_enabled': '0',
    'dynamic_max_increase_pct': '50',
    'dynamic_max_discount_pct': '50',
    'dynamic_rounding_rupees': '1',
    'dynamic_peak_threshold_pct': '10',
}
KINDS = {'weekend', 'occupancy', 'last_minute', 'early_bird', 'long_stay', 'event'}


def settings(db):
    out = {}
    for key, default in DEFAULTS.items():
        value = setting(db, key, default)
        out[key] = int(value) if key != 'dynamic_enabled' else value == '1'
    return out


def save_settings(db, enabled, max_increase_pct, max_discount_pct, rounding_rupees, peak_threshold_pct):
    values = {
        'dynamic_enabled': '1' if enabled else '0',
        'dynamic_max_increase_pct': str(int(max_increase_pct)),
        'dynamic_max_discount_pct': str(int(max_discount_pct)),
        'dynamic_rounding_rupees': str(int(rounding_rupees)),
        'dynamic_peak_threshold_pct': str(int(peak_threshold_pct)),
    }
    if not 0 <= int(max_increase_pct) <= 300:
        raise ValueError('Maximum increase must be from 0 to 300%.')
    if not 0 <= int(max_discount_pct) <= 100:
        raise ValueError('Maximum discount must be from 0 to 100%.')
    if not 1 <= int(rounding_rupees) <= 10000:
        raise ValueError('Rounding step must be from ₹1 to ₹10,000.')
    if not 0 <= int(peak_threshold_pct) <= 300:
        raise ValueError('Peak threshold must be from 0 to 300%.')
    for key, value in values.items():
        set_setting(db, key, value)
    return settings(db)


def occupancy(db, category_id, day):
    """Return the percentage of active rooms booked for the supplied night."""
    d(day)
    args = []
    where = ''
    if category_id is not None:
        where = ' AND category_id=?'
        args.append(int(category_id))
    available = db.execute(
        "SELECT COUNT(*) FROM rooms WHERE status='active'" + where, args
    ).fetchone()[0]
    if not available:
        return 0
    booked = db.execute(
        "SELECT COUNT(DISTINCT r.id) FROM rooms r JOIN room_nights n ON n.room_id=r.id "
        "WHERE r.status='active' AND n.reservation_id IS NOT NULL AND n.night=?" + where.replace('category_id', 'r.category_id'),
        [day] + args,
    ).fetchone()[0]
    return round(booked * 100 / available)


def _weekdays(value):
    try:
        return {int(x) for x in (value or '').split(',') if x.strip()}
    except ValueError:
        return set()


def _matches(rule, category_id, day, nights, days_ahead, occ):
    if rule['category_id'] is not None and rule['category_id'] != int(category_id):
        return False
    kind = rule['kind']
    if kind == 'weekend':
        return d(day).weekday() in _weekdays(rule['dow'])
    if kind == 'occupancy':
        return occ >= (rule['threshold'] or 0)
    if kind == 'last_minute':
        return 0 <= days_ahead <= (rule['threshold'] or 0)
    if kind == 'early_bird':
        return days_ahead >= (rule['threshold'] or 0)
    if kind == 'long_stay':
        return nights >= (rule['threshold'] or 0)
    if kind == 'event':
        return bool(rule['start_date'] and rule['end_date'] and rule['start_date'] <= day <= rule['end_date'])
    return False


def nightly(db, category_id, day, base=None, nights=1, check_in=None):
    try:
        target = d(day)
    except (TypeError, ValueError):
        raise ValueError('Choose a valid date.')
    if int(nights) < 1:
        raise ValueError('Nights must be at least one.')
    try:
        arrival = d(check_in) if check_in else target
    except (TypeError, ValueError):
        raise ValueError('Choose a valid check-in date.')
    category_id = int(category_id)
    if base is None:
        row = db.execute(
            'SELECT nightly_rate_paise FROM room_categories WHERE id=?', (category_id,)
        ).fetchone()
        if not row:
            raise ValueError('Choose a valid room category.')
        seasonal = db.execute(
            'SELECT price_paise FROM pricing_rules WHERE category_id=? AND approved=1 AND start_date<=? AND end_date>=? ORDER BY id DESC LIMIT 1',
            (category_id, day, day),
        ).fetchone()
        base = seasonal[0] if seasonal else row[0]
    base = int(base or 0)
    if base < 0:
        raise ValueError('Base price cannot be negative.')
    cfg = settings(db)
    if not cfg['dynamic_enabled']:
        return {
            'price_paise': base, 'base_paise': base, 'adjustment_pct': 0,
            'peak': False, 'occupancy_pct': 0,
            'rules': [],
        }
    rules = db.execute(
        "SELECT * FROM dynamic_rules WHERE active=1 AND (category_id IS NULL OR category_id=?) "
        "ORDER BY priority DESC,id",
        (category_id,),
    ).fetchall()
    occ = occupancy(db, category_id, day) if any(r['kind'] == 'occupancy' for r in rules) else 0
    days_ahead = (arrival - today()).days
    applied = [dict(r) for r in rules if _matches(r, category_id, day, int(nights), days_ahead, occ)]
    raw_pct = sum(int(r['pct']) for r in applied)
    pct = max(-cfg['dynamic_max_discount_pct'], min(cfg['dynamic_max_increase_pct'], raw_pct))
    price = round(base * (100 + pct) / 100)
    step = cfg['dynamic_rounding_rupees'] * 100
    if step:
        price = round(price / step) * step
    return {
        'price_paise': max(0, price), 'base_paise': base, 'adjustment_pct': pct,
        'peak': pct >= cfg['dynamic_peak_threshold_pct'] and pct > 0,
        'occupancy_pct': occ, 'rules': applied,
    }


def quote(db, category_id, check_in, check_out, base=None):
    try:
        start, end = d(check_in), d(check_out)
    except (TypeError, ValueError):
        raise ValueError('Choose valid check-in and check-out dates.')
    nights = (end - start).days
    if nights < 1:
        raise ValueError('Check-out must be after check-in.')
    if not db.execute('SELECT 1 FROM room_categories WHERE id=? AND active=1', (int(category_id),)).fetchone():
        raise ValueError('Choose an active room category.')
    prices = []
    day = start
    while day < end:
        base_price = base(day.isoformat()) if callable(base) else base
        prices.append({'date': day.isoformat(), **nightly(db, category_id, day.isoformat(), base_price, nights, start.isoformat())})
        day += dtm.timedelta(days=1)
    total = sum(x['price_paise'] for x in prices)
    return {
        'nights': nights, 'per_night': prices, 'room_total_paise': total,
        'avg_paise': round(total / nights), 'peak': any(x['peak'] for x in prices),
    }


def calendar(db, category_id, start, months):
    try:
        first = d(start)
        months = int(months)
        category_id = int(category_id)
    except (TypeError, ValueError):
        raise ValueError('Choose a valid category, start date, and month count.')
    if not 1 <= months <= 12:
        raise ValueError('Month count must be between 1 and 12.')
    if not db.execute('SELECT 1 FROM room_categories WHERE id=? AND active=1', (category_id,)).fetchone():
        raise ValueError('Choose an active room category.')
    month_index = first.month - 1 + months
    year = first.year + month_index // 12
    month = month_index % 12 + 1
    end = dtm.date(year, month, 1)
    days = []
    cursor = first
    while cursor < end:
        day = cursor.isoformat()
        room = db.execute(
            "SELECT COUNT(*) FROM rooms r WHERE r.category_id=? AND r.status='active' "
            "AND NOT EXISTS(SELECT 1 FROM room_nights n WHERE n.room_id=r.id AND n.night=?)",
            (category_id, day),
        ).fetchone()[0]
        result = nightly(db, category_id, day)
        days.append({
            'date': day, 'price_paise': result['price_paise'],
            'peak': result['peak'], 'notify_me': room == 0,
        })
        cursor += dtm.timedelta(days=1)
    return days
