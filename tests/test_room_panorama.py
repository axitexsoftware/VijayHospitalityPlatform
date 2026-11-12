import io
from PIL import Image

from .base import Base,day


class RoomPanoramaTests(Base):
    def test_admin_room_and_category_edits_are_reflected_in_search(self):
        admin,_=self.login()
        room_token=self.csrf(admin,'/admin/rooms/1')
        saved_room=admin.post('/admin/rooms/1',data={
            'csrf_token':room_token,'number':'101','floor':'Floor 2','category_id':'1',
            'price':'','status':'active',
            'description':'Management-entered room description with a balcony.',
            'amenities':'Wi-Fi, Balcony'
        })
        self.assertEqual(saved_room.status_code,302)

        categories_token=self.csrf(admin,'/admin/categories')
        saved_category=admin.post('/admin/categories',data={
            'csrf_token':categories_token,'id':'1','property_id':'1',
            'name':'Admin-managed AC Room','room_type':'Deluxe AC room','ac':'1',
            'price':'4200','max_adults':'2','max_children':'1','max_occupancy':'3',
            'description':'Management-entered category description.','active':'1'
        })
        self.assertEqual(saved_category.status_code,302)
        approval=admin.post('/admin/categories/1/approval',data={
            'csrf_token':self.csrf(admin,'/admin/categories'),
            'approve_details':'1','approve_inventory':'1','approve_occupancy':'1',
            'approve_rates':'1','approve_tax':'1','publish':'1'
        })
        self.assertEqual(approval.status_code,302)

        response=admin.get('/search',query_string={
            'ci':day(3),'co':day(4),'adults':2,'children':0,'rooms':1
        })
        page=response.get_data(as_text=True)
        self.assertEqual(response.status_code,200)
        self.assertIn('Admin-managed AC Room',page)
        self.assertIn('Management-entered room description with a balcony.',page)
        self.assertIn('Management-entered category description.',page)
        self.assertIn('Floor 2',page)
        self.assertIn('₹4,200',page)
        self.assertIn('/admin/categories',page)
        self.assertIn('/admin/rooms',page)

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
