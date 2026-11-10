(function(){
var t=document.querySelector('.mt'),n=document.getElementById('nav');
if(t&&n){
 function setMenu(open){n.classList.toggle('open',open);t.setAttribute('aria-expanded',String(open));t.setAttribute('aria-label',open?'Close navigation':'Open navigation')}
 t.addEventListener('click',function(){setMenu(!n.classList.contains('open'))});
 n.querySelectorAll('a').forEach(function(link){link.addEventListener('click',function(){setMenu(false)})});
 document.addEventListener('keydown',function(e){if(e.key==='Escape'&&n.classList.contains('open')){setMenu(false);t.focus()}});
 document.addEventListener('click',function(e){if(n.classList.contains('open')&&!n.contains(e.target)&&!t.contains(e.target))setMenu(false)});
}
document.querySelectorAll('[data-confirm]').forEach(function(el){el.addEventListener('click',function(e){if(!confirm(el.getAttribute('data-confirm')))e.preventDefault()})});
document.querySelectorAll('form[data-confirm]').forEach(function(f){f.addEventListener('submit',function(e){if(!confirm(f.getAttribute('data-confirm')))e.preventDefault()})});
document.querySelectorAll('input[data-after]').forEach(function(co){var ci=document.getElementById(co.getAttribute('data-after'));if(!ci)return;
 ci.addEventListener('change',function(){if(!ci.value)return;var d=new Date(ci.value);d.setDate(d.getDate()+1);var s=d.toISOString().slice(0,10);co.min=s;if(!co.value||co.value<=ci.value)co.value=s})});
document.querySelectorAll('[data-price-calendar]').forEach(function(panel){
 var form=panel.closest('form'),grid=panel.querySelector('[data-calendar-grid]'),monthLabel=panel.querySelector('[data-calendar-month]'),message=panel.querySelector('[data-calendar-message]');
 if(!form||!grid)return;
 var checkin=form.querySelector('input[name="ci"]'),checkout=form.querySelector('input[name="co"]'),category=form.querySelector('select[name="cat"]'),fixedCategory=form.querySelector('[data-price-category]');
 var focusInput=checkin,month=new Date();month.setDate(1);
 [checkin,checkout].forEach(function(input){if(input)input.addEventListener('focus',function(){focusInput=input})});
 function monthStart(){return month.getFullYear()+'-'+String(month.getMonth()+1).padStart(2,'0')+'-01'}
 function draw(){
  if(!panel.open)return;
  var selected=category&&category.value?category.value:(fixedCategory?fixedCategory.value:(category&&category.options.length>1?category.options[1].value:''));
  if(!selected){message.textContent='Room rates are not available yet.';grid.replaceChildren();return}
  monthLabel.textContent=new Intl.DateTimeFormat(undefined,{month:'long',year:'numeric'}).format(month);
  message.textContent='Loading prices…';grid.replaceChildren();
  fetch('/api/price-calendar?category_id='+encodeURIComponent(selected)+'&start='+encodeURIComponent(monthStart())+'&months=1')
   .then(function(r){if(!r.ok)throw new Error('Prices could not be loaded.');return r.json()})
   .then(function(data){
    if(data.error)throw new Error(data.error);
    grid.replaceChildren();
    ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].forEach(function(day){var el=document.createElement('span');el.className='rate-calendar-weekday';el.textContent=day;grid.appendChild(el)});
    var days=data.days||[];
    if(!days.length){message.textContent='No prices are available for this month.';return}
    var offset=(new Date(days[0].date+'T00:00:00').getDay()+6)%7;
    for(var i=0;i<offset;i++){var blank=document.createElement('span');blank.className='rate-calendar-blank';grid.appendChild(blank)}
    var todayText=new Date().toISOString().slice(0,10);
    days.forEach(function(day){
     var date=new Date(day.date+'T00:00:00'),button=document.createElement('button');
     button.type='button';button.className='rate-calendar-day';button.setAttribute('role','gridcell');
     button.disabled=day.notify_me||day.date<todayText;
     var number=document.createElement('span');number.className='calendar-number';number.textContent=String(date.getDate());button.appendChild(number);
     var price=document.createElement('span');price.className='calendar-price';price.textContent='₹'+Math.round(day.price_paise/100).toLocaleString('en-IN');button.appendChild(price);
     if(day.peak){var dot=document.createElement('i');dot.className='peak-dot';dot.setAttribute('aria-label','Peak date');button.appendChild(dot)}
     if(day.notify_me){var full=document.createElement('span');full.className='calendar-full';full.textContent='Full';button.appendChild(full)}
     button.title=day.date+(day.peak?' · Peak price':'')+(day.notify_me?' · No rooms available':'');
     button.addEventListener('click',function(){
      var target=focusInput||checkin;if(!target)return;
      target.value=day.date;target.dispatchEvent(new Event('change',{bubbles:true}));
     });
     grid.appendChild(button);
    });
    message.textContent='Select a date to fill the focused date field. Peak dates are marked with a dot; full dates cannot be selected.';
   }).catch(function(e){message.textContent=e.message;grid.replaceChildren()});
 }
 panel.addEventListener('toggle',draw);
 panel.querySelector('[data-calendar-prev]').addEventListener('click',function(){month.setMonth(month.getMonth()-1);draw()});
 panel.querySelector('[data-calendar-next]').addEventListener('click',function(){month.setMonth(month.getMonth()+1);draw()});
 if(category)category.addEventListener('change',draw);
});
document.querySelectorAll('[data-slider]').forEach(function(slider){
 var slides=Array.prototype.slice.call(slider.querySelectorAll('.hero-slide')),index=0,cta=slider.querySelector('[data-slide-cta]'),timer;
 if(slides.length<2)return;
 function show(next){
  slides[index].classList.remove('active');index=(next+slides.length)%slides.length;slides[index].classList.add('active');
  var link=slides[index].getAttribute('data-link'),title=slides[index].getAttribute('data-title');
  if(cta){cta.hidden=!link;if(link){cta.href=link;cta.textContent=title||'Explore'}}
 }
 function restart(){if(timer)clearInterval(timer);timer=setInterval(function(){show(index+1)},6000)}
 slider.querySelector('[data-slide-prev]').addEventListener('click',function(){show(index-1);restart()});
 slider.querySelector('[data-slide-next]').addEventListener('click',function(){show(index+1);restart()});
 restart();
});
var lightbox=document.querySelector('[data-lightbox-dialog]'),lightboxImage=document.querySelector('[data-lightbox-image]');
if(lightbox&&lightboxImage){
 document.querySelectorAll('[data-lightbox]').forEach(function(link){link.addEventListener('click',function(e){e.preventDefault();lightboxImage.src=link.href;lightboxImage.alt=link.getAttribute('data-alt')||'';lightbox.showModal()})});
 lightbox.querySelector('[data-lightbox-close]').addEventListener('click',function(){lightbox.close()});
 lightbox.addEventListener('click',function(e){if(e.target===lightbox)lightbox.close()});
}
})();
