"""Validated, non-secret hotel configuration stored separately from app code."""
import copy
import json
import re
from datetime import date
from string import Template
from urllib.parse import urlparse

SCHEMA_VERSION = 1
MAX_CONFIG_BYTES = 256 * 1024
FONTS = (
    'Cormorant Garamond',
    'Playfair Display',
    'Lora',
    'Georgia',
    'Inter',
    'Lato',
    'Montserrat',
    'Arial',
)
EVENTS = (
    'booking_confirmed',
    'payment_received',
    'booking_cancelled',
    'booking_modified',
    'checkin_reminder',
    'checked_in',
    'checked_out',
)
_NOTIFY_FIELDS = {'guest', 'reference', 'property', 'room', 'category', 'check_in', 'check_out', 'total', 'paid', 'balance'}
_SECRET_KEY_PARTS = ('api_key', 'apikey', 'secret', 'password', 'token', 'credential', 'private_key')
_DOMAIN_RE = re.compile(r'^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))+$')
_DEFAULT_TEMPLATES = {
    'booking_confirmed': 'Hello $guest, your booking is confirmed. Booking $reference at $property, Room $room ($category), $check_in to $check_out. Total Rs $total. Check-in is from 12:00 PM.',
    'payment_received': 'Hello $guest, we received your payment. Booking $reference at $property, Room $room ($category), $check_in to $check_out. Paid Rs $paid; balance Rs $balance.',
    'booking_cancelled': 'Hello $guest, your booking $reference has been cancelled.',
    'booking_modified': 'Hello $guest, your booking was updated. Booking $reference at $property, Room $room ($category), $check_in to $check_out. New total Rs $total.',
    'checkin_reminder': 'Hello $guest, a reminder that your stay begins tomorrow. Booking $reference at $property, Room $room ($category), $check_in to $check_out. Check-in is from 12:00 PM.',
    'checked_in': 'Welcome $guest! You are checked in to Room $room. Booking $reference at $property, $check_in to $check_out.',
    'checked_out': 'Thank you for staying with us, $guest. Your check-out for booking $reference is complete. Balance Rs $balance.',
}

DEFAULT_CONFIG = {
    'schema_version': SCHEMA_VERSION,
    'brand': {
        'name': 'Vijay Utsav Hall',
        'tagline': 'Premium hospitality · Manpur, Gaya',
        'logo_path': '',
        'favicon_path': '',
        'theme': 'heritage',
        'colors': {
            'primary': '#792C38',
            'accent': '#B18A4A',
            'background': '#F8F4EC',
            'surface': '#FFFFFF',
            'text': '#292725',
        },
        'fonts': {'heading': 'Cormorant Garamond', 'body': 'Inter'},
    },
    'website': {
        'site_url': 'http://localhost:5000',
        'primary_domain': '',
        'default_language': 'en',
        'supported_languages': ['en', 'hi'],
        'currency': 'INR',
    },
    'pages': {
        'home': {
            'eyebrow': 'Manpur · Gaya · Bihar',
            'title': 'Vijay Utsav Hall',
            'subtitle': 'Premium hospitality. Elegant stays. Memorable celebrations.',
            'description': 'Located near Sikhar More on Gaya-Nawada Road, Vijay Utsav Hall brings together guest accommodation and event enquiries in one convenient location. Contact our team to discuss your stay or celebration requirements.',
            'hero_image': '',
        },
        'about': {
            'title': 'A local welcome',
            'description': 'Welcome to Vijay Utsav Hall, Manpur, Gaya, a place to ask about accommodation, weddings, receptions and family celebrations.',
            'hero_image': '',
        },
        'events': {
            'title': 'Make room for a celebration.',
            'description': 'Planning a wedding, reception or family gathering? Share your date and requirements to begin an enquiry with the property team.',
            'hero_image': '',
        },
        'policies': {
            'check_in': 'Please confirm check-in and check-out times with the property team.',
            'cancellation': 'Review the cancellation terms shown with your booking or contact the property team.',
            'children': 'Please confirm child occupancy and arrangements with the property team.',
            'pets': 'Please contact the property team to confirm any special arrangements.',
        },
        'metadata': {
            'properties': {'title': '', 'description': ''},
            'rooms': {'title': '', 'description': ''},
            'room': {'title': '', 'description': ''},
            'search': {'title': '', 'description': ''},
            'booking': {'title': '', 'description': ''},
            'lookup': {'title': '', 'description': ''},
            'events': {'title': '', 'description': ''},
            'offers': {'title': '', 'description': ''},
            'gallery': {'title': '', 'description': ''},
            'facilities': {'title': '', 'description': ''},
            'about': {'title': '', 'description': ''},
            'contact': {'title': '', 'description': ''},
            'faq': {'title': '', 'description': ''},
            'privacy': {'title': '', 'description': ''},
        },
        'offers': [],
        'banquet_packages': [],
    },
    'integrations': {
        'email': {'from_name': 'Vijay Hospitality', 'from_email': ''},
        'whatsapp': {'display_phone': ''},
        'ai': {
            'provider': 'openai-compatible',
            'base_url': 'https://api.openai.com/v1',
            'model': 'gpt-4o-mini',
            'assistant_name': 'Kabita',
            'instructions': '',
            'knowledge_base': '',
        },
        'payments': {'provider': 'razorpay', 'currency': 'INR'},
    },
    'notifications': {'templates': copy.deepcopy(_DEFAULT_TEMPLATES)},
    'seo': {
        'title': 'Vijay Utsav Hall · Manpur, Gaya',
        'description': 'Hotel rooms and banquet venue in Manpur, Gaya, near Sikhar More. Check availability and book online.',
        'keywords': '',
        'og_image': '',
    },
    'deployment': {'primary_domain': '', 'site_url': 'http://localhost:5000'},
}


class ConfigurationError(ValueError):
    pass


def _reject_secrets(value, path='configuration'):
    if isinstance(value, dict):
        for key, child in value.items():
            if any(part in str(key).lower() for part in _SECRET_KEY_PARTS):
                raise ConfigurationError(f'{path}.{key} is not allowed in an exportable configuration.')
            _reject_secrets(child, f'{path}.{key}')
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secrets(child, f'{path}[{index}]')


def _merge(default, supplied, path='configuration'):
    if not isinstance(supplied, dict):
        raise ConfigurationError(f'{path} must be an object.')
    merged = copy.deepcopy(default)
    for key, value in supplied.items():
        if key not in default:
            raise ConfigurationError(f'{path}.{key} is not a supported setting.')
        if isinstance(default[key], dict):
            merged[key] = _merge(default[key], value, f'{path}.{key}')
        elif isinstance(default[key], list):
            if not isinstance(value, list):
                raise ConfigurationError(f'{path}.{key} must be a list.')
            merged[key] = copy.deepcopy(value)
        else:
            merged[key] = value
    return merged


def _text(value, path, limit, required=False):
    if not isinstance(value, str):
        raise ConfigurationError(f'{path} must be text.')
    value = value.strip()
    if required and not value:
        raise ConfigurationError(f'{path} is required.')
    if len(value) > limit:
        raise ConfigurationError(f'{path} must be {limit} characters or fewer.')
    if any(ord(char) < 32 and char not in '\n\t' for char in value):
        raise ConfigurationError(f'{path} contains unsupported control characters.')
    return value


def _url(value, path, allow_empty=False):
    value = _text(value, path, 2048)
    if not value and allow_empty:
        return value
    try:
        parsed = urlparse(value)
        parsed.port
    except ValueError as exc:
        raise ConfigurationError(f'{path} must be a valid HTTP(S) URL.') from exc
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ConfigurationError(f'{path} must be a valid HTTP(S) URL.')
    return value.rstrip('/')


def _asset(value, path):
    value = _text(value, path, 512)
    if value and (not value.startswith('/static/') or not re.fullmatch(r'/static/[A-Za-z0-9_./-]+',value) or '..' in value):
        raise ConfigurationError(f'{path} must point to an asset under /static/.')
    return value


def _color(value, path):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ConfigurationError(f'{path} must be a six-digit hex color.')
    return value.upper()


def _domain(value, path):
    value = _text(value, path, 253).lower().rstrip('.')
    if value and not _DOMAIN_RE.fullmatch(value):
        raise ConfigurationError(f'{path} must be a valid domain name without a protocol or path.')
    return value


def _validate_entries(entries, path, keys, max_items=30):
    if not isinstance(entries, list) or len(entries) > max_items:
        raise ConfigurationError(f'{path} must be a list with at most {max_items} items.')
    result = []
    for index, entry in enumerate(entries):
        item_path = f'{path}[{index}]'
        if not isinstance(entry, dict) or set(entry) - set(keys):
            raise ConfigurationError(f'{item_path} contains unsupported fields.')
        result.append(entry)
    return result


def validate(raw):
    if isinstance(raw, str):
        if len(raw.encode('utf-8')) > MAX_CONFIG_BYTES:
            raise ConfigurationError('Configuration file is too large (maximum 256 KB).')
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ConfigurationError('Configuration must be valid JSON.') from exc
    if not isinstance(raw, dict):
        raise ConfigurationError('Configuration must be a JSON object.')
    _reject_secrets(raw)
    config = _merge(DEFAULT_CONFIG, raw)
    if config['schema_version'] != SCHEMA_VERSION:
        raise ConfigurationError(f'Unsupported configuration schema version: {config["schema_version"]}.')

    brand = config['brand']
    brand['name'] = _text(brand['name'], 'brand.name', 100, True)
    brand['tagline'] = _text(brand['tagline'], 'brand.tagline', 160)
    brand['logo_path'] = _asset(brand['logo_path'], 'brand.logo_path')
    brand['favicon_path'] = _asset(brand['favicon_path'], 'brand.favicon_path')
    if brand['theme'] not in ('heritage', 'modern', 'garden'):
        raise ConfigurationError('brand.theme must be heritage, modern, or garden.')
    for key in ('primary', 'accent', 'background', 'surface', 'text'):
        brand['colors'][key] = _color(brand['colors'][key], f'brand.colors.{key}')
    for key in ('heading', 'body'):
        if brand['fonts'][key] not in FONTS:
            raise ConfigurationError(f'brand.fonts.{key} is not a supported font.')

    website = config['website']
    website['site_url'] = _url(website['site_url'], 'website.site_url')
    website['primary_domain'] = _domain(website['primary_domain'], 'website.primary_domain')
    if website['default_language'] not in ('en', 'hi'):
        raise ConfigurationError('website.default_language must be en or hi.')
    languages = website['supported_languages']
    if not isinstance(languages, list) or not languages or len(languages) > 5 or any(x not in ('en', 'hi') for x in languages):
        raise ConfigurationError('website.supported_languages must contain one or more supported languages (en, hi).')
    if website['default_language'] not in languages:
        raise ConfigurationError('The default language must also be listed as a supported language.')
    if website['currency'] != 'INR':
        raise ConfigurationError('Only INR is currently supported by the booking and payment system.')

    pages = config['pages']
    for name in ('home', 'about', 'events'):
        pages[name]['title'] = _text(pages[name]['title'], f'pages.{name}.title', 120, True)
        pages[name]['description'] = _text(pages[name]['description'], f'pages.{name}.description', 2000)
        pages[name]['hero_image'] = _asset(pages[name]['hero_image'], f'pages.{name}.hero_image')
    pages['home']['eyebrow'] = _text(pages['home']['eyebrow'], 'pages.home.eyebrow', 120)
    pages['home']['subtitle'] = _text(pages['home']['subtitle'], 'pages.home.subtitle', 240)
    for key in ('check_in', 'cancellation', 'children', 'pets'):
        pages['policies'][key] = _text(pages['policies'][key], f'pages.policies.{key}', 2000)
    for page, metadata in pages['metadata'].items():
        metadata['title'] = _text(metadata['title'], f'pages.metadata.{page}.title', 160)
        metadata['description'] = _text(metadata['description'], f'pages.metadata.{page}.description', 300)
    offers = _validate_entries(pages['offers'], 'pages.offers', ('title', 'description', 'start_date', 'end_date', 'discount_label', 'active'))
    for index, item in enumerate(offers):
        for key, limit in (('title', 100), ('description', 1000), ('start_date', 10), ('end_date', 10), ('discount_label', 40)):
            item[key] = _text(item.get(key, ''), f'pages.offers[{index}].{key}', limit, key == 'title')
        if item['start_date'] or item['end_date']:
            try:
                start = date.fromisoformat(item['start_date'])
                end = date.fromisoformat(item['end_date'])
            except ValueError as exc:
                raise ConfigurationError(f'pages.offers[{index}] dates must use YYYY-MM-DD.') from exc
            if end < start:
                raise ConfigurationError(f'pages.offers[{index}].end_date must not be before start_date.')
        item['active'] = item.get('active', True)
        if not isinstance(item['active'], bool):
            raise ConfigurationError(f'pages.offers[{index}].active must be true or false.')
    packages = _validate_entries(pages['banquet_packages'], 'pages.banquet_packages', ('name', 'description', 'inclusions', 'price_label', 'active'))
    for index, item in enumerate(packages):
        for key, limit in (('name', 100), ('description', 1000), ('inclusions', 1000), ('price_label', 80)):
            item[key] = _text(item.get(key, ''), f'pages.banquet_packages[{index}].{key}', limit, key == 'name')
        item['active'] = item.get('active', True)
        if not isinstance(item['active'], bool):
            raise ConfigurationError(f'pages.banquet_packages[{index}].active must be true or false.')

    integrations = config['integrations']
    integrations['email']['from_name'] = _text(integrations['email']['from_name'], 'integrations.email.from_name', 100)
    integrations['email']['from_email'] = _text(integrations['email']['from_email'], 'integrations.email.from_email', 254)
    if integrations['email']['from_email'] and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',integrations['email']['from_email']):
        raise ConfigurationError('integrations.email.from_email must be a valid email address.')
    integrations['whatsapp']['display_phone'] = _text(integrations['whatsapp']['display_phone'], 'integrations.whatsapp.display_phone', 30)
    ai = integrations['ai']
    if ai['provider'] != 'openai-compatible':
        raise ConfigurationError('Only the OpenAI-compatible AI provider is currently supported.')
    ai['base_url'] = _url(ai['base_url'], 'integrations.ai.base_url')
    ai['model'] = _text(ai['model'], 'integrations.ai.model', 100, True)
    ai['assistant_name'] = _text(ai['assistant_name'], 'integrations.ai.assistant_name', 60, True)
    ai['instructions'] = _text(ai['instructions'], 'integrations.ai.instructions', 4000)
    ai['knowledge_base'] = _text(ai['knowledge_base'], 'integrations.ai.knowledge_base', 12000)
    if integrations['payments']['provider'] != 'razorpay':
        raise ConfigurationError('Razorpay is the only online payment gateway currently supported.')
    if integrations['payments']['currency'] != 'INR':
        raise ConfigurationError('The configured payment currency must be INR.')

    templates = config['notifications']['templates']
    if set(templates) - set(EVENTS):
        raise ConfigurationError('notifications.templates contains an unsupported event.')
    for event, message in templates.items():
        templates[event] = _text(message, f'notifications.templates.{event}', 2000, True)
        for match in Template(message).pattern.finditer(message):
            if match.group('invalid') or (match.group('named') and match.group('named') not in _NOTIFY_FIELDS):
                raise ConfigurationError(f'notifications.templates.{event} contains an unsupported placeholder.')

    seo = config['seo']
    seo['title'] = _text(seo['title'], 'seo.title', 160, True)
    seo['description'] = _text(seo['description'], 'seo.description', 300)
    seo['keywords'] = _text(seo['keywords'], 'seo.keywords', 500)
    seo['og_image'] = _asset(seo['og_image'], 'seo.og_image')
    deployment = config['deployment']
    deployment['primary_domain'] = _domain(deployment['primary_domain'], 'deployment.primary_domain')
    deployment['site_url'] = _url(deployment['site_url'], 'deployment.site_url')
    return config


def load(db, runtime=None):
    row = db.execute('SELECT config_json FROM hotel_configuration WHERE id=1').fetchone()
    if row:
        return validate(row['config_json'])
    config = copy.deepcopy(DEFAULT_CONFIG)
    if runtime:
        config['website']['site_url'] = runtime.get('SITE_URL', config['website']['site_url'])
        config['deployment']['site_url'] = config['website']['site_url']
        config['integrations']['ai']['base_url'] = runtime.get('LLM_BASE_URL', config['integrations']['ai']['base_url'])
        config['integrations']['ai']['model'] = runtime.get('LLM_MODEL', config['integrations']['ai']['model'])
    return validate(config)


def save(db, config, user_id=None, complete=True):
    config = validate(config)
    db.execute(
        '''INSERT INTO hotel_configuration(id,config_json,setup_complete,updated_by)
           VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json,
           setup_complete=MAX(hotel_configuration.setup_complete,excluded.setup_complete),
           updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP''',
        (json.dumps(config, ensure_ascii=False, separators=(',', ':')),int(bool(complete)),user_id),
    )
    return config


def export_payload(db):
    return {
        'format': 'vijay-hospitality-configuration',
        'configuration': load(db),
        'properties': [
            dict(row) for row in db.execute(
                '''SELECT slug,name,kind,address,phone,email,whatsapp,map_url,description,active
                   FROM properties ORDER BY id'''
            )
        ],
        'room_categories': [
            dict(row) for row in db.execute(
                '''SELECT p.slug property_slug,c.name,c.room_type,c.ac,c.description,c.base_price_paise,
                          c.max_adults,c.max_children,c.nightly_rate_paise,c.max_adults_approved,
                          c.max_children_approved,c.max_occupancy,c.currency,c.tax_percent,c.active
                   FROM room_categories c JOIN properties p ON p.id=c.property_id
                   ORDER BY p.id,c.id'''
            )
        ],
        'rooms': [
            dict(row) for row in db.execute(
                '''SELECT p.slug property_slug,c.name category_name,r.number,r.floor,
                          r.price_override_paise,r.description,r.status
                   FROM rooms r JOIN properties p ON p.id=r.property_id
                   JOIN room_categories c ON c.id=r.category_id
                   ORDER BY p.id,c.id,r.number'''
            )
        ],
    }


def import_payload(raw):
    if isinstance(raw, str) and len(raw.encode('utf-8')) > MAX_CONFIG_BYTES:
        raise ConfigurationError('Configuration file is too large (maximum 256 KB).')
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigurationError('Configuration must be valid JSON.') from exc
    if not isinstance(payload, dict) or payload.get('format') != 'vijay-hospitality-configuration':
        raise ConfigurationError('This is not a Vijay Hospitality configuration export.')
    allowed = {'format', 'configuration', 'properties', 'room_categories', 'rooms'}
    if set(payload) - allowed:
        raise ConfigurationError('The export contains unsupported top-level fields.')
    config = validate(payload.get('configuration'))
    properties = payload.get('properties', [])
    categories = payload.get('room_categories', [])
    rooms = payload.get('rooms', [])
    if not isinstance(properties, list) or len(properties) > 100:
        raise ConfigurationError('properties must be a list with at most 100 entries.')
    if not isinstance(categories, list) or len(categories) > 1000:
        raise ConfigurationError('room_categories must be a list with at most 1000 entries.')
    if not isinstance(rooms, list) or len(rooms) > 5000:
        raise ConfigurationError('rooms must be a list with at most 5000 entries.')
    for index, prop in enumerate(properties):
        if not isinstance(prop, dict) or set(prop) != {'slug', 'name', 'kind', 'address', 'phone', 'email', 'whatsapp', 'map_url', 'description', 'active'}:
            raise ConfigurationError(f'properties[{index}] has an invalid shape.')
        _text(prop['slug'], f'properties[{index}].slug', 80, True)
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*',prop['slug']):
            raise ConfigurationError(f'properties[{index}].slug must contain lowercase letters, digits and hyphens only.')
        _text(prop['name'], f'properties[{index}].name', 120, True)
        _text(prop['kind'], f'properties[{index}].kind', 40, True)
        for key in ('address', 'phone', 'email', 'whatsapp', 'map_url', 'description'):
            if prop[key] is not None:
                _text(prop[key], f'properties[{index}].{key}', 1000)
        if prop['map_url']:
            _url(prop['map_url'],f'properties[{index}].map_url')
        if prop['email'] and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',prop['email']):
            raise ConfigurationError(f'properties[{index}].email must be a valid email address.')
        if not isinstance(prop['active'], int) or prop['active'] not in (0, 1):
            raise ConfigurationError(f'properties[{index}].active must be 0 or 1.')
    for index, category in enumerate(categories):
        required = {'property_slug', 'name', 'room_type', 'ac', 'description', 'base_price_paise', 'max_adults', 'max_children', 'nightly_rate_paise', 'max_adults_approved', 'max_children_approved', 'max_occupancy', 'currency', 'tax_percent', 'active'}
        if not isinstance(category, dict) or set(category) != required:
            raise ConfigurationError(f'room_categories[{index}] has an invalid shape.')
        _text(category['property_slug'], f'room_categories[{index}].property_slug', 80, True)
        _text(category['name'], f'room_categories[{index}].name', 100, True)
        if category['room_type'] is not None:_text(category['room_type'],f'room_categories[{index}].room_type',80)
        if category['description'] is not None:_text(category['description'],f'room_categories[{index}].description',2000)
        if category['ac'] not in (0,1,False,True):raise ConfigurationError(f'room_categories[{index}].ac must be true or false.')
        for field in ('base_price_paise','max_adults','max_children'):
            if not isinstance(category[field],int) or category[field]<0:
                raise ConfigurationError(f'room_categories[{index}].{field} must be a non-negative integer.')
        if category['max_adults']<1:raise ConfigurationError(f'room_categories[{index}].max_adults must be at least one.')
        for field in ('nightly_rate_paise','max_adults_approved','max_children_approved','max_occupancy'):
            value=category[field]
            if value is not None and (not isinstance(value,int) or value<0 or (field in ('nightly_rate_paise','max_adults_approved','max_occupancy') and value==0)):
                raise ConfigurationError(f'room_categories[{index}].{field} must be a positive/non-negative integer or null.')
        tax=category['tax_percent']
        if tax is not None and (not isinstance(tax,(int,float)) or not 0<=tax<=100):
            raise ConfigurationError(f'room_categories[{index}].tax_percent must be from 0 to 100 or null.')
        if category['currency'] != 'INR' or not isinstance(category['active'], int) or category['active'] not in (0, 1):
            raise ConfigurationError(f'room_categories[{index}] contains an unsupported currency or active state.')
    for index, room in enumerate(rooms):
        required = {'property_slug', 'category_name', 'number', 'floor', 'price_override_paise', 'description', 'status'}
        if not isinstance(room, dict) or set(room) != required:
            raise ConfigurationError(f'rooms[{index}] has an invalid shape.')
        _text(room['property_slug'], f'rooms[{index}].property_slug', 80, True)
        _text(room['category_name'], f'rooms[{index}].category_name', 100, True)
        _text(room['number'], f'rooms[{index}].number', 10, True)
        _text(room['floor'] or '', f'rooms[{index}].floor', 10)
        _text(room['description'] or '', f'rooms[{index}].description', 500)
        if room['status'] not in ('active', 'inactive'):
            raise ConfigurationError(f'rooms[{index}].status must be active or inactive.')
        if room['price_override_paise'] is not None and (not isinstance(room['price_override_paise'],int) or room['price_override_paise']<=0):
            raise ConfigurationError(f'rooms[{index}].price_override_paise must be a positive integer or null.')
    return config, properties, categories, rooms
