"""All figures come from the database. Revenue = money collected (payments by paid date, net of refunds); booked value = reservation totals."""
import io,datetime as dtm
from markupsafe import Markup
from ..util import today,d,inr
LIVE="('confirmed','checked_in','checked_out')"
def one(db,sql,a=()):return db.execute(sql,a).fetchone()[0]
def dashboard(db):
    t=today().isoformat();m=t[:7];y=t[:4];tot=one(db,"SELECT COUNT(*) FROM rooms WHERE status='active'")
    busy=lambda cond:one(db,f"SELECT COUNT(DISTINCT n.room_id) FROM room_nights n JOIN rooms r ON r.id=n.room_id AND r.status='active' LEFT JOIN reservations s ON s.id=n.reservation_id WHERE n.night=? AND {cond}",(t,))
    occ=busy("s.status='checked_in'");res=busy("s.status IN('confirmed','pending')");blk=busy('n.reservation_id IS NULL')
    rev=lambda like:one(db,"SELECT COALESCE(SUM(amount_paise-refunded_paise),0) FROM payments WHERE status IN('paid','partially_refunded','refunded') AND paid_at LIKE ?",(like+'%',))
    sold=occ+res
    return dict(total_rooms=tot,occupied=occ,reserved=res,blocked=blk,available=max(0,tot-occ-res-blk),occupancy=round(sold/tot*100) if tot else 0,
     checkins_today=one(db,"SELECT COUNT(*) FROM reservations WHERE status='confirmed' AND check_in=?",(t,)),checkouts_today=one(db,"SELECT COUNT(*) FROM reservations WHERE status='checked_in' AND check_out=?",(t,)),
     upcoming=one(db,"SELECT COUNT(*) FROM reservations WHERE status IN('confirmed','pending') AND check_in>?",(t,)),total_bookings=one(db,'SELECT COUNT(*) FROM reservations'),cancelled=one(db,"SELECT COUNT(*) FROM reservations WHERE status='cancelled'"),
     pending_pay=one(db,"SELECT COUNT(*) FROM reservations WHERE status='pending'"),rev_day=rev(t),rev_month=rev(m),rev_year=rev(y),
     demand=[dict(r) for r in db.execute(f"SELECT c.name,COUNT(*) nights FROM room_nights n JOIN reservations s ON s.id=n.reservation_id AND s.status IN{LIVE} JOIN rooms r ON r.id=n.room_id JOIN room_categories c ON c.id=r.category_id WHERE n.night>=? GROUP BY c.name ORDER BY nights DESC",((today()-dtm.timedelta(days=30)).isoformat(),))])
def _n(group):return {'day':10,'month':7,'year':4}[group]
def series(db,start,end,group='day'):
    n=_n(group);out={}
    def put(k,f,v):out.setdefault(k,dict(label=k,bookings=0,booked=0,collected=0,cancels=0,checkins=0,checkouts=0))[f]+=v
    first=dtm.date.fromisoformat(start);last=dtm.date.fromisoformat(end)
    if group=='day':
        current=first
        while current<=last:
            put(current.isoformat(),'bookings',0);current+=dtm.timedelta(days=1)
    elif group=='month':
        current=first.replace(day=1);last=last.replace(day=1)
        while current<=last:
            put(current.strftime('%Y-%m'),'bookings',0)
            current=dtm.date(current.year+(current.month==12),current.month%12+1,1)
    else:
        for year in range(first.year,last.year+1):put(str(year),'bookings',0)
    for r in db.execute(f"SELECT substr(created_at,1,{n}) k,COUNT(*) c,SUM(CASE WHEN status IN{LIVE} THEN total_paise ELSE 0 END) v FROM reservations WHERE substr(created_at,1,10) BETWEEN ? AND ? GROUP BY k",(start,end)):put(r['k'],'bookings',r['c']);put(r['k'],'booked',r['v'] or 0)
    for r in db.execute(f"SELECT substr(paid_at,1,{n}) k,SUM(amount_paise-refunded_paise) v FROM payments WHERE status IN('paid','partially_refunded','refunded') AND substr(paid_at,1,10) BETWEEN ? AND ? GROUP BY k",(start,end)):put(r['k'],'collected',r['v'] or 0)
    for r in db.execute(f"SELECT substr(at,1,{n}) k,COUNT(*) c FROM cancellations WHERE substr(at,1,10) BETWEEN ? AND ? GROUP BY k",(start,end)):put(r['k'],'cancels',r['c'])
    for r in db.execute(f"SELECT substr(check_in,1,{n}) k,COUNT(*) c FROM reservations WHERE status IN{LIVE} AND check_in BETWEEN ? AND ? GROUP BY k",(start,end)):put(r['k'],'checkins',r['c'])
    for r in db.execute(f"SELECT substr(check_out,1,{n}) k,COUNT(*) c FROM reservations WHERE status IN{LIVE} AND check_out BETWEEN ? AND ? GROUP BY k",(start,end)):put(r['k'],'checkouts',r['c'])
    return [out[k] for k in sorted(out)]
def occupancy_series(db,start,end):
    rooms=one(db,"SELECT COUNT(*) FROM rooms WHERE status='active'")
    counts={r['night']:r['occupied'] for r in db.execute(f"SELECT n.night,COUNT(DISTINCT n.room_id) occupied FROM room_nights n JOIN reservations s ON s.id=n.reservation_id AND s.status IN{LIVE} JOIN rooms r ON r.id=n.room_id AND r.status='active' WHERE n.night BETWEEN ? AND ? GROUP BY n.night",(start,end))}
    current=dtm.date.fromisoformat(start);last=dtm.date.fromisoformat(end);out=[]
    while current<=last:
        night=current.isoformat();occupied=counts.get(night,0)
        out.append(dict(label=night,occupancy=round(occupied/rooms*100,1) if rooms else 0,occupied=occupied,rooms=rooms))
        current+=dtm.timedelta(days=1)
    return out
def financial_period(db,start,end,collected=None):
    if collected is None:
        collected=one(db,"SELECT COALESCE(SUM(amount_paise-refunded_paise),0) FROM payments WHERE status IN('paid','partially_refunded','refunded') AND substr(paid_at,1,10) BETWEEN ? AND ?",(start,end))
    operating=one(db,"SELECT COALESCE(SUM(amount_paise),0) FROM expense_entries WHERE kind='operating' AND incurred_on BETWEEN ? AND ?",(start,end))
    investment=one(db,"SELECT COALESCE(SUM(amount_paise),0) FROM expense_entries WHERE kind='capital' AND incurred_on<=?",(end,))
    net=collected-operating
    return dict(collected=collected,operating=operating,investment=investment,net_return=net,roi_pct=round(net/investment*100,1) if investment else None)
def expense_rows(db,start,end,limit=200):
    return [dict(r) for r in db.execute("SELECT e.*,u.name created_by_name FROM expense_entries e LEFT JOIN users u ON u.id=e.created_by WHERE (e.kind='operating' AND e.incurred_on BETWEEN ? AND ?) OR (e.kind='capital' AND e.incurred_on<=?) ORDER BY e.incurred_on DESC,e.id DESC LIMIT ?",(start,end,end,limit))]
def summary(db,start,end):
    a=d(start);b=d(end);days=(b-a).days+1;rooms=one(db,"SELECT COUNT(*) FROM rooms WHERE status='active'")
    sold=one(db,f"SELECT COUNT(*) FROM room_nights n JOIN reservations s ON s.id=n.reservation_id WHERE s.status IN{LIVE} AND n.night BETWEEN ? AND ?",(start,end))
    booked=one(db,f"SELECT COALESCE(SUM(total_paise),0) FROM reservations WHERE status IN{LIVE} AND check_in BETWEEN ? AND ?",(start,end));nb=one(db,f"SELECT COUNT(*) FROM reservations WHERE status IN{LIVE} AND check_in BETWEEN ? AND ?",(start,end))
    allb=one(db,"SELECT COUNT(*) FROM reservations WHERE check_in BETWEEN ? AND ? AND status!='expired'",(start,end));canc=one(db,"SELECT COUNT(*) FROM reservations WHERE status='cancelled' AND check_in BETWEEN ? AND ?",(start,end))
    coll=one(db,"SELECT COALESCE(SUM(amount_paise-refunded_paise),0) FROM payments WHERE status IN('paid','partially_refunded','refunded') AND substr(paid_at,1,10) BETWEEN ? AND ?",(start,end));finance=financial_period(db,start,end,coll)
    cats=[dict(r) for r in db.execute(f"SELECT c.name,c.ac,COUNT(DISTINCT s.id) bookings,COUNT(n.night) nights,COALESCE(SUM(s.total_paise*1.0/s.nights),0) booked FROM room_nights n JOIN reservations s ON s.id=n.reservation_id JOIN rooms r ON r.id=n.room_id JOIN room_categories c ON c.id=r.category_id WHERE s.status IN{LIVE} AND n.night BETWEEN ? AND ? GROUP BY c.id ORDER BY nights DESC",(start,end))]
    return dict(days=days,rooms=rooms,nights_sold=sold,occupancy=round(sold/(rooms*days)*100,1) if rooms else 0,booked=booked,bookings=nb,avg_value=round(booked/nb) if nb else 0,adr=round(booked/sold) if sold else 0,collected=coll,operating=finance['operating'],investment=finance['investment'],net_return=finance['net_return'],roi_pct=finance['roi_pct'],cancelled=canc,cancel_rate=round(canc/allb*100,1) if allb else 0,categories=cats,
     ac=sum(c['nights'] for c in cats if c['ac']),nonac=sum(c['nights'] for c in cats if not c['ac']))
def booking_rows(db,q='',status='',cat='',created_from='',created_to='',ci_from='',ci_to='',limit=500):
    sql='''SELECT s.*,c.name cname,c.phone cphone,r.number rnumber,k.name cat_name,p.name pname,(SELECT COALESCE(SUM(amount_paise-refunded_paise),0) FROM payments x WHERE x.reservation_id=s.id AND x.status IN('paid','partially_refunded','refunded')) paid_paise
     FROM reservations s JOIN customers c ON c.id=s.customer_id JOIN rooms r ON r.id=s.room_id JOIN room_categories k ON k.id=r.category_id JOIN properties p ON p.id=r.property_id WHERE 1=1''';a=[]
    if q:sql+=' AND (s.ref LIKE ? OR c.name LIKE ? OR c.phone LIKE ?)';a+=[f'%{q.strip()}%']*3
    if status:sql+=' AND s.status=?';a.append(status)
    if cat:sql+=' AND k.id=?';a.append(int(cat))
    if created_from:sql+=' AND substr(s.created_at,1,10)>=?';a.append(created_from)
    if created_to:sql+=' AND substr(s.created_at,1,10)<=?';a.append(created_to)
    if ci_from:sql+=' AND s.check_in>=?';a.append(ci_from)
    if ci_to:sql+=' AND s.check_in<=?';a.append(ci_to)
    return [dict(r) for r in db.execute(sql+' ORDER BY s.id DESC LIMIT ?',a+[limit])]
HEAD=['Reference','Guest','Mobile','Property','Room','Category','Check-in','Check-out','Nights','Status','Source','Total (INR)','Paid (INR)','Booked on']
def _line(r):return [r['ref'],r['cname'],r['cphone'],r['pname'],r['rnumber'],r['cat_name'],r['check_in'],r['check_out'],r['nights'],r['status'],r['source'],r['total_paise']/100,r['paid_paise']/100,r['created_at']]
def xlsx(rows,summ=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb=Workbook();ws=wb.active;ws.title='Bookings';ws.append(HEAD)
    for c in ws[1]:c.font=Font(bold=True)
    for r in rows:ws.append(_line(r))
    if summ:
        s=wb.create_sheet('Summary')
        for k,v in summ.items():
            if not isinstance(v,list):s.append([k.replace('_',' ').title(),v])
    b=io.BytesIO();wb.save(b);return b.getvalue()
def pdf(rows,title):
    from reportlab.lib.pagesizes import A4,landscape
    from reportlab.pdfgen import canvas
    b=io.BytesIO();c=canvas.Canvas(b,pagesize=landscape(A4));W,H=landscape(A4);y=H-40;c.setFont('Helvetica-Bold',13);c.drawString(30,y,title);y-=22
    cols=[('ref',30),('cname',100),('rnumber',200),('check_in',240),('check_out',310),('status',380),('total',450),('paid',520)]
    def head():
        nonlocal y;c.setFont('Helvetica-Bold',8)
        for n,x in zip(['Ref','Guest','Room','Check-in','Check-out','Status','Total','Paid'],[30,100,230,270,340,410,500,570]):c.drawString(x,y,n)
        y-=12
    head();c.setFont('Helvetica',8)
    for r in rows:
        if y<40:c.showPage();y=H-40;head();c.setFont('Helvetica',8)
        for v,x in zip([r['ref'],r['cname'][:22],r['rnumber'],r['check_in'],r['check_out'],r['status'],f"{r['total_paise']/100:,.0f}",f"{r['paid_paise']/100:,.0f}"],[30,100,230,270,340,410,500,570]):c.drawString(x,y,str(v))
        y-=11
    c.save();return b.getvalue()
def svg_bars(data,key,color='#8F1D1E',money=False,h=150):
    if not data:return Markup('<p class="muted">No data for this period.</p>')
    mx=max([x[key] for x in data]+[1]);w=max(320,len(data)*38);bw=w/len(data);o=[f'<svg viewBox="0 0 {w} {h+34}" role="img" aria-label="{key} chart" style="width:100%;height:auto">']
    for i,x in enumerate(data):
        v=x[key];bh=v/mx*h;val=f"{v/100:,.0f}" if money else str(v)
        o.append(f'<rect x="{i*bw+6}" y="{h-bh+10}" width="{bw-12}" height="{bh}" rx="4" fill="{color}"><title>{x["label"]}: {val}</title></rect><text x="{i*bw+bw/2}" y="{h-bh+7}" font-size="8" text-anchor="middle" fill="currentColor">{val}</text><text x="{i*bw+bw/2}" y="{h+26}" font-size="9" text-anchor="middle" fill="currentColor">{x["label"][-5:] if len(x["label"])>7 else x["label"]}</text>')
    o.append('</svg>');return Markup(''.join(o))
