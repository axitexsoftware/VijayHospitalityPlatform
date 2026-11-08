(function(){
var form=document.getElementById('room-search');
if(form){
 form.addEventListener('submit',function(){
  var submit=form.querySelector('[data-search-submit]'),label=form.querySelector('[data-search-label]'),spinner=form.querySelector('.search-spinner'),status=form.querySelector('.search-loading');
  if(submit)submit.disabled=true;
  if(label)label.textContent='Searching…';
  if(spinner)spinner.hidden=false;
  if(status)status.hidden=false;
 });
}

var list=document.querySelector('[data-room-list]');
if(!list)return;
var cards=Array.prototype.slice.call(list.querySelectorAll('[data-room-card]'));
var typeFilter=document.getElementById('filter-room-type'),facilityFilter=document.getElementById('filter-facility'),sortSelect=document.getElementById('sort-rooms');
var filterCount=document.querySelector('[data-filter-count]'),filterEmpty=document.querySelector('[data-filter-empty]'),taxToggle=document.getElementById('show-taxes');
cards.forEach(function(card,index){card.dataset.order=String(index)});

function renderCards(){
 var type=typeFilter?typeFilter.value:'all',facility=facilityFilter?facilityFilter.value:'all',sort=sortSelect?sortSelect.value:'recommended';
 var visible=cards.filter(function(card){
  var typeMatch=type==='all'||card.dataset.ac===type;
  var amenities=(card.dataset.amenities||'').split('|').filter(Boolean);
  var facilityMatch=facility==='all'||amenities.indexOf(facility)>=0;
  return typeMatch&&facilityMatch;
 });
 visible.sort(function(a,b){
  if(sort==='price-low')return Number(a.dataset.price)-Number(b.dataset.price);
  if(sort==='price-high')return Number(b.dataset.price)-Number(a.dataset.price);
  if(sort==='category')return a.dataset.category.localeCompare(b.dataset.category);
  return Number(a.dataset.order)-Number(b.dataset.order);
 });
 visible.forEach(function(card){list.appendChild(card)});
 cards.forEach(function(card){card.hidden=visible.indexOf(card)<0});
 if(filterCount)filterCount.textContent=visible.length+' room'+(visible.length===1?'':'s');
 if(filterEmpty)filterEmpty.hidden=visible.length!==0;
}

if(typeFilter)typeFilter.addEventListener('change',renderCards);
if(facilityFilter)facilityFilter.addEventListener('change',renderCards);
if(sortSelect)sortSelect.addEventListener('change',renderCards);
var clear=document.querySelector('[data-clear-filters]');
if(clear)clear.addEventListener('click',function(){
 if(typeFilter)typeFilter.value='all';
 if(facilityFilter)facilityFilter.value='all';
 if(sortSelect)sortSelect.value='recommended';
 if(taxToggle)taxToggle.checked=true;
 updateTaxDisplay();
 renderCards();
});

function updateTaxDisplay(){
 var show=taxToggle?taxToggle.checked:true;
 cards.forEach(function(card){
  card.querySelectorAll('[data-tax-line]').forEach(function(line){line.hidden=!show});
    card.querySelectorAll('[data-total-with-tax],[data-book-price-with-tax]').forEach(function(value){value.hidden=!show});
    card.querySelectorAll('[data-total-without-tax],[data-book-price-without-tax]').forEach(function(value){value.hidden=show});
    card.querySelectorAll('[data-total-label],[data-rate-total-label]').forEach(function(label){label.textContent=show?'Stay total including applicable taxes':'Room subtotal before taxes'});
 });
}
if(taxToggle)taxToggle.addEventListener('change',updateTaxDisplay);
updateTaxDisplay();

function fallbackImage(image){
 image.addEventListener('error',function(){
  image.hidden=true;
  var placeholder=document.createElement('div');placeholder.className='room-photo-placeholder';
  var title=document.createElement('span');title.textContent='Room photograph';
  var note=document.createElement('small');note.textContent='Image not available';
  placeholder.appendChild(title);placeholder.appendChild(note);image.parentNode.insertBefore(placeholder,image.nextSibling);
 });
}
document.querySelectorAll('[data-room-photo]').forEach(fallbackImage);

cards.forEach(function(card){
 var gallery=card.querySelector('[data-room-gallery]'),photo=gallery&&gallery.querySelector('[data-room-photo]');
 if(!gallery||!photo)return;
 var sources=Array.prototype.slice.call(gallery.querySelectorAll('[data-gallery-image]'));
 if(sources.length<2)return;
 var current=0,indexLabel=gallery.querySelector('[data-gallery-index]');
 function show(next){
  current=(next+sources.length)%sources.length;
  photo.src=sources[current].dataset.src;photo.alt=sources[current].dataset.alt||'';
  photo.hidden=false;if(indexLabel)indexLabel.textContent=String(current+1);
 }
 var previous=gallery.querySelector('[data-gallery-prev]'),next=gallery.querySelector('[data-gallery-next]');
 if(previous)previous.addEventListener('click',function(){show(current-1)});
 if(next)next.addEventListener('click',function(){show(current+1)});
});
})();