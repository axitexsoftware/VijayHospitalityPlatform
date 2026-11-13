import io
import os
from datetime import date, timedelta
from PIL import Image
from werkzeug.datastructures import FileStorage
from app.services import booking as B, dynamic_pricing as DP, media as M
from .base import Base, day


def future_weekday(weekday, offset=1):
    day = date.today() + timedelta(days=offset)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day.isoformat()


class DynamicPricingTests(Base):
    def enable(self, **values):
        defaults = dict(enabled=True, max_increase_pct=50, max_discount_pct=50,
                        rounding_rupees=1, peak_threshold_pct=10)
        defaults.update(values)
        DP.save_settings(self.db, **defaults)

    def add_rule(self, kind, pct, **values):
        args = dict(name='Test rule', kind=kind, dow=None, threshold=None,
                    start_date=None, end_date=None, priority=0)
        args.update(values)
        self.db.execute(
            'INSERT INTO dynamic_rules(category_id,name,kind,dow,threshold,start_date,end_date,pct,priority) VALUES(?,?,?,?,?,?,?,?,?)',
            (1, args['name'], kind, args['dow'], args['threshold'], args['start_date'],
             args['end_date'], pct, args['priority']),
        )

    def test_weekend_uplift(self):
        self.enable()
        saturday = future_weekday(5)
        self.add_rule('weekend', 15, dow='5,6')
        result = DP.nightly(self.db, 1, saturday)
        self.assertEqual(result['price_paise'], 207000)
        self.assertTrue(result['peak'])
        self.assertEqual([x['name'] for x in result['rules']], ['Test rule'])

    def test_seasonal_rate_is_dynamic_base(self):
        self.enable()
        saturday = future_weekday(5)
        self.db.execute('INSERT INTO pricing_rules(category_id,label,start_date,end_date,price_paise,approved) VALUES(?,?,?,?,?,1)',
                        (1, 'Peak season', saturday, saturday, 300000))
        self.add_rule('weekend', 10, dow='5,6')
        room = self.db.execute(B.ROOM_SQL + ' AND r.id=?', (1,)).fetchone()
        self.assertEqual(B.price_per_night(self.db, dict(room), saturday), 330000)

    def test_room_override_precedes_seasonal_rate(self):
        self.enable()
        saturday = future_weekday(5)
        self.db.execute('INSERT INTO pricing_rules(category_id,label,start_date,end_date,price_paise,approved) VALUES(?,?,?,?,?,1)',
                        (1, 'Peak season', saturday, saturday, 300000))
        self.db.execute('UPDATE rooms SET price_override_paise=? WHERE id=1', (350000,))
        self.add_rule('weekend', 10, dow='5')
        room = self.db.execute(B.ROOM_SQL + ' AND r.id=?', (1,)).fetchone()
        self.assertEqual(B.price_per_night(self.db, dict(room), saturday), 385000)

    def test_reservation_keeps_booking_time_dynamic_price(self):
        self.enable()
        saturday = future_weekday(5)
        sunday = (date.fromisoformat(saturday) + timedelta(days=1)).isoformat()
        self.add_rule('event', 20, start_date=saturday, end_date=saturday)
        reservation = B.create_reservation(
            self.db, 1, saturday, sunday, 1, 0, 'Asha Kumar', '9876543210', None,
        )
        self.assertEqual(reservation['room_total_paise'], 216000)
        self.db.execute('UPDATE dynamic_rules SET pct=80 WHERE name=?', ('Test rule',))
        stored = B.get_res(self.db, reservation['id'])
        self.assertEqual(stored['room_total_paise'], 216000)

    def test_occupancy_surge(self):
        self.enable()
        stay = future_weekday(2)
        day = date.fromisoformat(stay)
        ci, co = day.isoformat(), (day + timedelta(days=1)).isoformat()
        B.create_reservation(self.db, 1, ci, co, 1, 0, 'Asha Kumar', '9876543210', None)
        self.assertEqual(DP.occupancy(self.db, 1, ci), 50)
        self.add_rule('occupancy', 20, threshold=50)
        self.assertEqual(DP.nightly(self.db, 1, ci)['price_paise'], 216000)

    def test_caps_limit_increase_and_discount(self):
        self.enable(max_increase_pct=10, max_discount_pct=15)
        saturday = future_weekday(5)
        self.add_rule('weekend', 50, dow='5')
        self.assertEqual(DP.nightly(self.db, 1, saturday)['adjustment_pct'], 10)
        self.db.execute('DELETE FROM dynamic_rules')
        self.add_rule('weekend', -50, dow='5')
        self.assertEqual(DP.nightly(self.db, 1, saturday)['adjustment_pct'], -15)

    def test_disabled_mode_keeps_base_rate(self):
        self.enable(enabled=False)
        self.add_rule('weekend', 25, dow='5,6')
        result = DP.nightly(self.db, 1, future_weekday(5))
        self.assertEqual(result['price_paise'], 180000)
        self.assertEqual(result['rules'], [])

    def test_invalid_dates_are_rejected(self):
        self.enable()
        with self.assertRaises(ValueError):
            DP.nightly(self.db, 1, 'not-a-date')
        with self.assertRaises(ValueError):
            DP.quote(self.db, 1, '2026-03-02', '2026-03-01')


class MediaUploadTests(Base):
    def upload(self, raw, filename='photo.png'):
        return FileStorage(stream=io.BytesIO(raw), filename=filename)

    def image_bytes(self, fmt='PNG', size=(40, 30)):
        stream = io.BytesIO()
        Image.new('RGB', size, 'navy').save(stream, format=fmt)
        return stream.getvalue()

    def test_valid_image_is_resized_and_thumbnail_created(self):
        result = M.save_upload(self.upload(self.image_bytes(size=(2500, 1000))), self.up)
        main = os.path.join(self.up, os.path.basename(result['path']))
        thumb = os.path.join(self.up, os.path.basename(result['thumb_path']))
        self.assertTrue(os.path.isfile(main))
        self.assertTrue(os.path.isfile(thumb))
        with Image.open(main) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(image.size, (1920, 768))
        with Image.open(thumb) as image:
            self.assertEqual(image.size, (480, 192))

    def test_invalid_file_is_rejected(self):
        with self.assertRaises(M.MediaError):
            M.save_upload(self.upload(b'not an image', 'photo.jpg'), self.up)

    def test_oversized_file_is_rejected(self):
        with self.assertRaises(M.MediaError):
            M.save_upload(self.upload(b'x' * (M.MAX_UPLOAD_BYTES + 1)), self.up)

    def test_delete_removes_main_and_thumbnail(self):
        result = M.save_upload(self.upload(self.image_bytes()), self.up)
        main = os.path.join(self.up, os.path.basename(result['path']))
        thumb = os.path.join(self.up, os.path.basename(result['thumb_path']))
        M.delete_upload(result['path'], self.up)
        self.assertFalse(os.path.exists(main))
        self.assertFalse(os.path.exists(thumb))


class FeatureRoutesAndReceptionTests(Base):
    def test_public_pricing_media_and_admin_pages_render(self):
        client, _ = self.login()
        self.assertEqual(client.get('/admin/pricing/dynamic').status_code, 200)
        media = client.get('/admin/media').get_data(as_text=True)
        self.assertIn('admin-filter-bar media-kind-tabs', media)
        self.assertIn('media-upload-form', media)
        self.assertIn('media-library', media)
        self.assertEqual(client.get('/admin/media?kind=slide').status_code, 200)
        enquiries = client.get('/admin/enquiries').get_data(as_text=True)
        self.assertIn('enquiry-filters admin-filter-bar', enquiries)
        self.assertEqual(client.get('/').status_code, 200)
        self.assertEqual(client.get('/rooms').status_code, 200)
        self.assertEqual(client.get('/gallery').status_code, 200)
        month_start = date.today().replace(day=1).isoformat()
        calendar = client.get('/api/price-calendar', query_string={
            'category_id': 1, 'start': month_start, 'months': 1,
        })
        self.assertEqual(calendar.status_code, 200)
        self.assertIn('notify_me', calendar.get_json()['days'][0])
        quote = client.get('/api/price-quote', query_string={
            'category_id': 1, 'check_in': day(10), 'check_out': day(12),
        })
        self.assertEqual(quote.status_code, 200)
        self.assertEqual(quote.get_json()['nights'], 2)
        self.assertEqual(client.get('/api/media?kind=gallery').status_code, 200)

    def test_reception_modify_cancel_reports_and_exports(self):
        client, _ = self.login()
        ci, co = day(10), day(12)
        token = self.csrf(client, '/admin/reception')
        response = client.post('/admin/reception', data={
            'csrf_token': token, 'room_id': 1, 'ci': ci, 'co': co,
            'adults': 2, 'children': 0, 'name': 'Reception Guest',
            'phone': '9876543210', 'email': '', 'source': 'walk-in',
            'advance': '0',
        })
        self.assertEqual(response.status_code, 302)
        reservation = self.db.execute('SELECT id FROM reservations').fetchone()
        rid = reservation['id']
        modified_ci, modified_co = day(13), day(15)
        response = client.post(f'/admin/bookings/{rid}/modify', data={
            'csrf_token': token, 'ci': modified_ci, 'co': modified_co,
            'adults': 2, 'children': 0,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(B.get_res(self.db, rid)['check_in'], modified_ci)
        response = client.post(f'/admin/bookings/{rid}/cancel', data={
            'csrf_token': token, 'reason': 'Test cancellation',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(B.get_res(self.db, rid)['status'], 'cancelled')
        self.assertEqual(client.get('/admin/reports').status_code, 200)
        self.assertTrue(client.get('/admin/reports/export.xlsx').data.startswith(b'PK'))
        self.assertTrue(client.get('/admin/reports/export.pdf').data.startswith(b'%PDF'))

    def test_admin_saves_and_audits_dynamic_rule_and_media(self):
        client, _ = self.login()
        token = self.csrf(client, '/admin/pricing/dynamic')
        response = client.post('/admin/pricing/dynamic/settings', data={
            'csrf_token': token, 'enabled': 'on', 'max_increase_pct': '40',
            'max_discount_pct': '25', 'rounding_rupees': '10',
            'peak_threshold_pct': '12',
        })
        self.assertEqual(response.status_code, 302)
        response = client.post('/admin/pricing/dynamic/rules', data={
            'csrf_token': token, 'name': 'Weekend offer', 'kind': 'weekend',
            'pct': '15', 'priority': '2', 'dow': ['5', '6'],
        })
        self.assertEqual(response.status_code, 302)
        rule_id = self.db.execute('SELECT id FROM dynamic_rules').fetchone()['id']
        self.assertEqual(client.get('/admin/pricing/dynamic').status_code, 200)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dynamic_rules').fetchone()[0], 1)
        client.post(f'/admin/pricing/dynamic/rules/{rule_id}/toggle', data={'csrf_token': token})
        self.assertEqual(self.db.execute('SELECT active FROM dynamic_rules WHERE id=?', (rule_id,)).fetchone()[0], 0)

        image = io.BytesIO()
        Image.new('RGB', (32, 24), 'navy').save(image, format='PNG')
        response = client.post('/admin/media/upload', data={
            'csrf_token': token, 'kind': 'gallery', 'title': 'Test image',
            'images': (io.BytesIO(image.getvalue()), 'test.png'),
        }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 302)
        media = self.db.execute("SELECT * FROM site_media WHERE kind='gallery'").fetchone()
        self.assertIsNotNone(media)
        self.assertEqual(client.get('/admin/media?kind=gallery').status_code, 200)
        client.post(f"/admin/media/{media['id']}/save", data={
            'csrf_token': token, 'title': 'Updated test image', 'caption': 'Caption',
            'sort': '3', 'active': 'on',
        })
        self.assertEqual(self.db.execute('SELECT title,sort FROM site_media WHERE id=?', (media['id'],)).fetchone()['title'], 'Updated test image')
        main_path = os.path.join(self.up, os.path.basename(media['path']))
        thumb_path = os.path.splitext(main_path)[0] + '-480.jpg'
        self.assertTrue(os.path.isfile(main_path))
        client.post(f"/admin/media/{media['id']}/delete", data={'csrf_token': token})
        self.assertFalse(os.path.exists(main_path))
        self.assertFalse(os.path.exists(thumb_path))
        client.post(f'/admin/pricing/dynamic/rules/{rule_id}/delete', data={'csrf_token': token})
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dynamic_rules').fetchone()[0], 0)
        self.assertGreaterEqual(self.db.execute("SELECT COUNT(*) FROM audit_logs WHERE entity IN('settings','dynamic_rule','site_media')").fetchone()[0], 3)
