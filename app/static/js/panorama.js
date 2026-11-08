(function(){
var viewers=document.querySelectorAll('[data-room-panorama]');
viewers.forEach(function(viewer){
 var track=viewer.querySelector('[data-panorama-track]');
 var source=track&&track.querySelector('img');
 var previous=viewer.querySelector('[data-panorama-prev]');
 var next=viewer.querySelector('[data-panorama-next]');
 var toggle=viewer.querySelector('[data-panorama-auto]');
 var angles=Array.prototype.slice.call(viewer.querySelectorAll('[data-room-angle]'));
 if(!track&&angles.length){
  var current=0,startX=null,rotationTimer=null,count=viewer.querySelector('[data-panorama-count]');
  function showAngle(index){
   current=(index+angles.length)%angles.length;
   angles.forEach(function(image,i){image.hidden=i!==current});
   if(count)count.textContent=String(current+1);
  }
  if(previous)previous.addEventListener('click',function(){showAngle(current-1)});
  if(next)next.addEventListener('click',function(){showAngle(current+1)});
  viewer.addEventListener('pointerdown',function(event){
   if(event.target.closest('button'))return;
   startX=event.clientX;viewer.setPointerCapture(event.pointerId);
  });
  viewer.addEventListener('pointerup',function(event){
   if(startX===null)return;
   var distance=event.clientX-startX;startX=null;
   if(Math.abs(distance)>30)showAngle(current+(distance<0?1:-1));
  });
  viewer.addEventListener('pointercancel',function(){startX=null});
  if(toggle)toggle.addEventListener('click',function(){
   var active=toggle.getAttribute('aria-pressed')!=='true';
   toggle.setAttribute('aria-pressed',String(active));
   toggle.textContent=active?'Pause rotation':'Auto-rotate';
   if(active)rotationTimer=window.setInterval(function(){showAngle(current+1)},2500);
   else if(rotationTimer){window.clearInterval(rotationTimer);rotationTimer=null}
  });
  return;
 }
 if(!track||!source)return;

 var offset=0,dragStart=null,dragOffset=0,frame=null,lastTime=0;
 var copies=Array.prototype.slice.call(track.querySelectorAll('img'));
 function maxOffset(){return Math.max(0,source.getBoundingClientRect().width-viewer.clientWidth)}
 function render(){track.style.transform='translate3d('+offset+'px,0,0)'}
 function move(delta){
  var maximum=maxOffset();
  if(!maximum)return;
  offset-=delta;
  if(offset< -maximum)offset=0;
  if(offset>0)offset=-maximum;
  render();
 }
 function resize(){
  copies.forEach(function(image){image.style.width='auto';image.style.height='100%'});
  offset=Math.max(-maxOffset(),Math.min(0,offset));
  render();
 }
 viewer.addEventListener('pointerdown',function(event){
  if(event.target.closest('button'))return;
  dragStart=event.clientX;dragOffset=offset;viewer.setPointerCapture(event.pointerId);viewer.classList.add('is-dragging');
 });
 viewer.addEventListener('pointermove',function(event){
  if(dragStart===null)return;
  offset=dragOffset+event.clientX-dragStart;
  var maximum=maxOffset();
  if(offset>0)offset-=maximum;
  if(offset< -maximum)offset+=maximum;
  render();
 });
 function stopDrag(){dragStart=null;viewer.classList.remove('is-dragging')}
 viewer.addEventListener('pointerup',stopDrag);
 viewer.addEventListener('pointercancel',stopDrag);
 if(previous)previous.addEventListener('click',function(){move(-viewer.clientWidth*.55)});
 if(next)next.addEventListener('click',function(){move(viewer.clientWidth*.55)});
 function rotate(time){
  if(!toggle||toggle.getAttribute('aria-pressed')!=='true'){frame=null;return}
  if(lastTime)move((time-lastTime)*.025);
  lastTime=time;frame=requestAnimationFrame(rotate);
 }
 if(toggle)toggle.addEventListener('click',function(){
  var active=toggle.getAttribute('aria-pressed')!=='true';
  toggle.setAttribute('aria-pressed',String(active));
  toggle.textContent=active?'Pause rotation':'Auto-rotate';
  lastTime=0;
  if(active&&!frame)frame=requestAnimationFrame(rotate);
  else if(!active&&frame){cancelAnimationFrame(frame);frame=null}
 });
 source.addEventListener('load',resize);
 window.addEventListener('resize',resize);
 if(source.complete)resize();
});
})();
