import io
from PIL import Image

from .base import Base,day


class RoomPanoramaTests(Base):
    def test_search_displays_complete_room_details_and_panorama_upload_requires_photo_approval(self):
        self.db.execute("UPDATE rooms SET floor='Test floor',description=? WHERE id=1",(
            'TEST DATA ONLY. Sample room: one king bed, private bathroom, Wi-Fi and daily housekeeping.',))
        self.db.execute("UPDATE room_categories SET room_type='Demo deluxe room',description='Illustrative room details' WHERE id=1")
        client=self.app.test_client()
        search=client.get('/search',query_string={'ci':day(3),'co':day(4),'adults':2,'children':0,'rooms':1})
        page=search.get_data(as_text=True)
        self.assertEqual(search.status_code,200)
        self.assertIn('Room 101',page)
        self.assertIn('one king bed',page)
        self.assertIn('₹1,800',page)

        admin,_=self.login()
        editor=admin.get('/admin/rooms/1')
        self.assertEqual(editor.status_code,200)
        csrf=self.csrf(admin,'/admin/rooms/1')
        invalid=Image.new('RGB',(512,512),'navy')
        invalid_data=io.BytesIO()
        invalid.save(invalid_data,format='JPEG')
        invalid_data.seek(0)
        rejected=admin.post('/admin/rooms/1/panorama',data={
            'csrf_token':csrf,'panorama':(invalid_data,'not-a-panorama.jpg','image/jpeg')
        },content_type='multipart/form-data')
        self.assertEqual(rejected.status_code,302)
        self.assertIsNone(self.db.execute('SELECT path FROM room_panorama WHERE room_id=1').fetchone())
        self.assertEqual(self.db.execute("SELECT photos_status FROM room_categories WHERE id=1").fetchone()[0],'approved')
        image=Image.new('RGB',(1024,512),'navy')
        data=io.BytesIO()
        image.save(data,format='JPEG')
        data.seek(0)
        uploaded=admin.post('/admin/rooms/1/panorama',data={
            'csrf_token':csrf,'alt':'Test room panorama',
            'panorama':(data,'test-panorama.jpg','image/jpeg')
        },content_type='multipart/form-data')
        self.assertEqual(uploaded.status_code,302)
        panorama=self.db.execute('SELECT path,alt FROM room_panorama WHERE room_id=1').fetchone()
        self.assertIsNotNone(panorama)
        self.assertEqual(panorama['alt'],'Test room panorama')
        self.assertEqual(self.db.execute("SELECT photos_status FROM room_categories WHERE id=1").fetchone()[0],'pending')

        hidden=client.get('/rooms/1').get_data(as_text=True)
        self.assertIn('not been uploaded',hidden)
        self.db.execute("UPDATE room_categories SET photos_status='approved' WHERE id=1")
        visible=client.get('/rooms/1').get_data(as_text=True)
        self.assertIn('Interactive 360-degree room panorama',visible)
        self.assertIn('Auto-rotate',visible)
        self.assertIn('Complete room details',visible)
        self.assertIn('Test floor',visible)
        self.assertIn('one king bed',visible)
