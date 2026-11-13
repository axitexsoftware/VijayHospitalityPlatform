(function(){
  var grid=document.querySelector('[data-rooms-grid]');
  if(!grid)return;

  var cards=Array.prototype.slice.call(grid.querySelectorAll('[data-room-card]'));
  var filters=Array.prototype.slice.call(document.querySelectorAll('[data-room-filter]'));
  var count=document.querySelector('[data-room-count]');
  var empty=document.querySelector('[data-room-empty]');

  function applyFilter(value){
    var visible=0;
    cards.forEach(function(card){
      var matches=value==='all'||card.dataset.roomType===value;
      card.hidden=!matches;
      if(matches)visible+=1;
    });
    filters.forEach(function(filter){
      var active=filter.dataset.roomFilter===value;
      filter.classList.toggle('is-active',active);
      filter.setAttribute('aria-pressed',String(active));
    });
    if(count)count.textContent=visible+' room'+(visible===1?'':'s')+' shown';
    if(empty)empty.hidden=visible!==0;
  }

  filters.forEach(function(filter){
    filter.addEventListener('click',function(){
      applyFilter(filter.dataset.roomFilter);
    });
  });
})();
