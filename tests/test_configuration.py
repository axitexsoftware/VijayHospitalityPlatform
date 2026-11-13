import copy
import io
import json
import os
import tempfile
from pathlib import Path

from .base import Base
from app import create_app,configuration as HC
from app.db import connect
from app.security import hash_password
from app.services import booking as B,notify


class ConfigurationTests(Base):
    def test_legacy_receptionist_name_defaults_to_vira(self):
        config=copy.deepcopy(HC.DEFAULT_CONFIG)
        config['integrations']['ai']['assistant_name']='Kabita'
        HC.save(self.db,config)
        self.assertEqual(HC.load(self.db)['integrations']['ai']['assistant_name'],'VIRA')

    def test_configuration_rejects_secrets_unsupported_fields_and_unsafe_assets(self):
        config=copy.deepcopy(HC.DEFAULT_CONFIG)
        config['integrations']['payments']['RAZORPAY_KEY_SECRET']='must not be exported'
        with self.assertRaises(HC.ConfigurationError):HC.validate(config)
        config=copy.deepcopy(HC.DEFAULT_CONFIG);config['brand']['logo_path']='/static/../private.png'
        with self.assertRaises(HC.ConfigurationError):HC.validate(config)
        config=copy.deepcopy(HC.DEFAULT_CONFIG);config['unexpected']='unsupported'
        with self.assertRaises(HC.ConfigurationError):HC.validate(config)

    def test_sample_second_hotel_export_is_valid_and_not_live(self):
        path=Path(__file__).resolve().parents[1]/'examples'/'sample-second-hotel-config.json'
        config,properties,categories,rooms=HC.import_payload(path.read_text(encoding='utf-8'))
        self.assertIn('DEMONSTRATION ONLY',config['brand']['name'])
        self.assertEqual(properties[0]['active'],0)
        self.assertEqual(categories[0]['active'],0)
        self.assertEqual(rooms,[])

    def test_sample_configuration_imports_only_into_an_empty_installation(self):
        path=tempfile.mktemp(suffix='.db')
        app=create_app(DATABASE=path,UPLOAD_DIR=self.up,RATELIMIT_ENABLED=False,SECRET_KEY='empty-install-test-secret')
        db=connect(path)
        db.execute('INSERT INTO users(email,name,password_hash,role) VALUES(?,?,?,?)',
            ('owner@example.test','Owner',hash_password('Passw0rd!!x'),'owner'))
        db.close()
        try:
            client=app.test_client()
            with client.session_transaction() as browser_session:
                browser_session['uid']=1;browser_session['csrf']='test-csrf-token'
            sample=(Path(__file__).resolve().parents[1]/'examples'/'sample-second-hotel-config.json').read_bytes()
            response=client.post('/admin/configuration/import',data={
                'csrf_token':'test-csrf-token','config_file':(io.BytesIO(sample),'sample.json'),
            },content_type='multipart/form-data')
            self.assertEqual(response.status_code,302)
            imported=connect(path)
            property_row=imported.execute('SELECT name,active FROM properties').fetchone()
            self.assertEqual((property_row['name'],property_row['active']),('Sample Garden Hotel — DEMONSTRATION ONLY',0))
            self.assertEqual(imported.execute('SELECT COUNT(*) FROM room_categories').fetchone()[0],1)
            self.assertEqual(imported.execute('SELECT COUNT(*) FROM rooms').fetchone()[0],0)
            self.assertEqual(imported.execute('SELECT COUNT(*) FROM reservations').fetchone()[0],0)
            imported.close()
        finally:
            for suffix in ('','-wal','-shm'):
                try:os.remove(path+suffix)
                except OSError:pass

    def test_export_contains_catalog_but_never_environment_secrets(self):
        payload=HC.export_payload(self.db)
        config,properties,categories,rooms=HC.import_payload(json.dumps(payload))
        exported=json.dumps(payload)
        self.assertEqual(properties[0]['slug'],'u')
        self.assertEqual(len(categories),2)
        self.assertEqual(len(rooms),3)
        self.assertEqual(config['schema_version'],1)
        self.assertNotIn('rzp_test_x',exported)
        self.assertNotIn('sekret',exported)
        self.assertNotIn('whsec',exported)

    def test_import_of_changed_catalog_is_rejected_when_hotel_data_exists(self):
        client,_=self.login()
        payload=HC.export_payload(self.db)
        payload['room_categories'][0]['nightly_rate_paise']+=50000
        token=self.csrf(client,'/admin/configuration')
        response=client.post('/admin/configuration/import',data={
            'csrf_token':token,
            'config_file':(io.BytesIO(json.dumps(payload).encode('utf-8')),'configuration.json'),
        },content_type='multipart/form-data')
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.db.execute('SELECT nightly_rate_paise FROM room_categories WHERE id=1').fetchone()[0],180000)
        page=client.get('/admin/configuration').get_data(as_text=True)
        self.assertIn('Import that data only into an empty installation',page)

    def test_onboarding_changes_brand_without_changing_existing_booking_or_property(self):
        reservation=self.book()
        original_total=reservation['total_paise']
        original_property=self.db.execute('SELECT name FROM properties WHERE id=1').fetchone()[0]
        client,_=self.login();token=self.csrf(client,'/admin/configuration/onboarding')
        response=client.post('/admin/configuration/onboarding',data={
            'csrf_token':token,'step':'1','name':'The Garden House','tagline':'Quiet stays, thoughtfully hosted',
            'primary_color':'#315447','accent_color':'#B18A4A','background_color':'#F5F2E9',
            'theme':'garden','heading_font':'Playfair Display','body_font':'Lato',
            'logo_path':'','favicon_path':'','site_url':'https://garden.example.com',
            'primary_domain':'garden.example.com','default_language':'en','seo_title':'The Garden House',
        })
        self.assertEqual(response.status_code,302)
        self.assertIn('/admin/configuration/onboarding?step=2',response.location)
        self.assertEqual(self.db.execute('SELECT total_paise FROM reservations WHERE id=?',(reservation['id'],)).fetchone()[0],original_total)
        self.assertEqual(self.db.execute('SELECT name FROM properties WHERE id=1').fetchone()[0],original_property)
        current=HC.load(self.db)
        self.assertEqual(current['brand']['name'],'The Garden House')
        self.assertEqual(current['brand']['colors']['primary'],'#315447')
        operations=client.get(response.location)
        self.assertEqual(operations.status_code,200)
        self.assertIn('Configure hotel operations',operations.get_data(as_text=True))
        review=client.get('/admin/configuration/onboarding?step=3')
        self.assertEqual(review.status_code,200)
        token=self.csrf(client,'/admin/configuration/onboarding?step=3')
        client.post('/admin/configuration/onboarding',data={'csrf_token':token,'step':'complete'})
        self.assertEqual(self.db.execute('SELECT setup_complete FROM hotel_configuration WHERE id=1').fetchone()[0],1)

    def test_configured_notification_template_is_used(self):
        reservation=self.book()
        config=HC.load(self.db)
        config['notifications']['templates']['booking_confirmed']='Hello $guest, reference $reference.'
        HC.save(self.db,config)
        full=B.get_res(self.db,reservation['id'])
        self.assertEqual(notify._text(full,'booking_confirmed',self.db),f"Hello {full['cname']}, reference {full['ref']}.")

    def test_configuration_admin_and_public_brand_render(self):
        client,_=self.login()
        page=client.get('/admin/configuration')
        self.assertEqual(page.status_code,200)
        self.assertIn('Configuration management',page.get_data(as_text=True))
        config=HC.load(self.db);config['brand']['name']='The Garden House';config['pages']['home']['title']='The Garden House'
        config['brand']['colors']['primary']='#315447'
        HC.save(self.db,config)
        public=client.get('/').get_data(as_text=True)
        self.assertIn('The Garden House',public)
        self.assertIn('--color-royal-wine:#315447',public)
